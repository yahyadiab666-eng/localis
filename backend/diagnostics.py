"""Diagnóstico automático del sistema y alertas por correo ante errores críticos."""

import smtplib
import threading
import traceback
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import (
    APP_ENV,
    ENABLE_ERROR_EMAILS,
    ERROR_EMAIL_COOLDOWN_SEC,
    ERROR_REPORT_EMAIL,
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USE_TLS,
    SMTP_USER,
)
from database import DATABASE_URL, diagnosticar_postgresql

_bloqueo_envio = threading.Lock()
_ultimos_envios = {}
_cache_diagnostico_supabase = None


def _diagnostico_supabase_red(*, forzar: bool = False):
    """Conectividad Supabase (DNS + HTTP directo + SDK); no bloquea el arranque."""
    global _cache_diagnostico_supabase
    if _cache_diagnostico_supabase is not None and not forzar:
        return _cache_diagnostico_supabase

    try:
        from backend.supabase_connectivity import (
            diagnosticar_conectividad_supabase,
            imprimir_diagnostico_conectividad,
        )
        from backend.supabase_client import SUPABASE_URL

        if not SUPABASE_URL:
            _cache_diagnostico_supabase = {
                'ok': False,
                'omitido': True,
                'motivo': 'SUPABASE_URL no configurada',
            }
            return _cache_diagnostico_supabase

        informe = diagnosticar_conectividad_supabase(probar_sdk=True)
        imprimir_diagnostico_conectividad(informe)
        _cache_diagnostico_supabase = informe
        return informe
    except Exception as error:
        print(f'[Localis Supabase Red] No se pudo ejecutar diagnóstico: {error}')
        _cache_diagnostico_supabase = {'ok': False, 'error': str(error)}
        return _cache_diagnostico_supabase


def obtener_estado_sistema(*, probar_supabase: bool = False):
    """Estado consolidado para health checks y arranque."""
    diag_bd = diagnosticar_postgresql()
    diag_supabase = _diagnostico_supabase_red(forzar=probar_supabase)
    ok_bd = diag_bd.get('ok', False)
    ok_supabase = (
        diag_supabase.get('ok', True)
        if diag_supabase.get('omitido')
        else diag_supabase.get('ok', False)
    )
    return {
        'ok': ok_bd,
        'entorno': APP_ENV,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'database': diag_bd,
        'database_url_configurada': bool(DATABASE_URL),
        'supabase': diag_supabase,
        'supabase_conectado': ok_supabase,
    }


def ejecutar_diagnostico_inicio():
    """Ejecuta diagnóstico al iniciar la app e imprime resumen en consola."""
    estado = obtener_estado_sistema(probar_supabase=True)
    bd = estado['database']
    if bd.get('ok'):
        print(
            f"[Localis Diagnóstico] PostgreSQL OK "
            f"({bd.get('latencia_ms')} ms) · tablas: {', '.join(bd.get('tablas_criticas', {}).keys())}"
        )
    else:
        print(
            f"[Localis Diagnóstico] PostgreSQL FALLO: {bd.get('error') or 'tablas incompletas'}"
        )

    supabase = estado.get('supabase') or {}
    if supabase.get('omitido'):
        print('[Localis Diagnóstico] Supabase: omitido (URL no configurada).')
    elif supabase.get('ok'):
        print(
            f"[Localis Diagnóstico] Supabase OK "
            f"host={supabase.get('host')} "
            f"rest={(supabase.get('http_rest') or {}).get('latencia_ms')}ms"
        )
    else:
        print(
            f"[Localis Diagnóstico] Supabase FALLO capa={supabase.get('capa_fallo')}: "
            f"{supabase.get('recomendacion') or supabase.get('error') or 'sin detalle'}"
        )
    return estado


def _clave_error(exc, ruta):
    return f'{type(exc).__name__}:{ruta or "/"}'


def _puede_enviar(clave):
    ahora = datetime.now().timestamp()
    with _bloqueo_envio:
        ultimo = _ultimos_envios.get(clave, 0)
        if ahora - ultimo < ERROR_EMAIL_COOLDOWN_SEC:
            return False
        _ultimos_envios[clave] = ahora
    return True


def _construir_cuerpo_correo(exc, request=None):
    tb = traceback.format_exc()
    if not tb or tb.strip() == 'NoneType: None':
        tb = ''.join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )

    ruta = getattr(request, 'path', None) if request else None
    metodo = getattr(request, 'method', None) if request else None
    usuario_id = None
    if request is not None:
        try:
            from flask import session

            usuario_id = session.get('usuario_id')
        except Exception:
            usuario_id = None

    hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S %Z')
    ubicacion = ''
    if exc.__traceback__:
        ultimo = traceback.extract_tb(exc.__traceback__)[-1]
        ubicacion = f'{ultimo.filename}:{ultimo.lineno} en {ultimo.name}()'

    return f"""Localis — reporte de error crítico
================================

Entorno: {APP_ENV}
Hora: {hora}
Ruta: {metodo or '?'} {ruta or '(desconocida)'}
Usuario en sesión: {usuario_id or 'anónimo'}
Tipo: {type(exc).__name__}
Mensaje: {exc}
Archivo / línea: {ubicacion or 'no disponible'}

Traceback completo:
{tb}
"""


def _enviar_correo_smtp(asunto, cuerpo):
    if not ENABLE_ERROR_EMAILS:
        return False, 'alertas deshabilitadas (ENABLE_ERROR_EMAILS=false)'
    if not ERROR_REPORT_EMAIL:
        return False, 'ERROR_REPORT_EMAIL no configurado'
    if not SMTP_USER or not SMTP_PASSWORD:
        return False, 'SMTP_USER/SMTP_PASSWORD no configurados'

    mensaje = MIMEMultipart()
    mensaje['From'] = SMTP_FROM
    mensaje['To'] = ERROR_REPORT_EMAIL
    mensaje['Subject'] = asunto
    mensaje.attach(MIMEText(cuerpo, 'plain', 'utf-8'))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as servidor:
        if SMTP_USE_TLS:
            servidor.starttls()
        servidor.login(SMTP_USER, SMTP_PASSWORD)
        servidor.sendmail(SMTP_FROM, [ERROR_REPORT_EMAIL], mensaje.as_string())
    return True, 'enviado'


def reportar_error_critico(exc, request=None):
    """
    Registra en consola y envía correo con traza, ruta y hora.
    Respeta cooldown para no saturar la bandeja.
    """
    ruta = getattr(request, 'path', 'unknown') if request else 'startup'
    clave = _clave_error(exc, ruta)
    cuerpo = _construir_cuerpo_correo(exc, request)

    print(f'[Localis ERROR CRÍTICO] {type(exc).__name__} en {ruta}: {exc}')
    print(cuerpo)

    if not _puede_enviar(clave):
        print('[Localis] Alerta omitida (cooldown activo para este tipo de error).')
        return

    asunto = f'[Localis {APP_ENV}] {type(exc).__name__} — {ruta}'
    try:
        ok, detalle = _enviar_correo_smtp(asunto, cuerpo)
        if ok:
            print(f'[Localis] Reporte enviado a {ERROR_REPORT_EMAIL}')
        else:
            print(f'[Localis] No se envió correo: {detalle}. Traceback registrado arriba.')
    except Exception as error_envio:
        print(f'[Localis] Fallo al enviar correo de error: {error_envio}')


def reportar_error_critico_async(exc, request=None):
    """Envía el reporte en un hilo daemon para no bloquear la respuesta HTTP."""
    hilo = threading.Thread(
        target=reportar_error_critico,
        args=(exc, request),
        daemon=True,
    )
    hilo.start()

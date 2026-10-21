"""Reporte de tráfico y PWA para administradores (por correo o consola).

Construye un resumen legible a partir de ``backend.trafico`` (tabla
``metricas_diarias``) y lo envía por correo a **todos** los ``ADMIN_EMAILS``,
reutilizando ``backend.correo``.

Idempotente por día: guarda la fecha del último envío en
``configuracion_sistema`` y no reenvía el mismo día salvo ``forzar=True``.

Nunca lanza hacia el llamador HTTP: si algo falla, devuelve ``(False, detalle)``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

from config import ADMIN_EMAILS
from backend import trafico
from backend.correo import correo_configurado, enviar_correo

logger = logging.getLogger('localis.reporte_trafico')

_LOG = '[Localis Reporte Trafico]'
_CLAVE_MARCA = 'reporte_trafico_ultimo_envio'

# Planificador diario dentro de la app (sin cron externo). El hilo se arranca de
# forma perezosa desde el request (post-fork): un hilo creado al importar el
# módulo no sobrevive al fork de gunicorn en producción.
_INTERVALO_REVISION = int(os.environ.get('LOCALIS_REPORTE_REVISION_SEG', '600') or 600)
_envio_lock = threading.Lock()
_iniciado = False
_ultimo_intento_arranque = 0.0
_inicio_lock = threading.Lock()

# Orden y etiquetas de las claves mostradas en el reporte.
_ETIQUETAS = (
    ('peticiones', 'Peticiones totales (incl. salud y estáticos)'),
    ('peticiones_app', 'Tráfico de la app (sin estáticos)'),
    ('visitas_unicas', 'Visitantes únicos (aprox.)'),
    ('pwa_instalaciones', 'Instalaciones de PWA'),
    ('pwa_aperturas', 'Aperturas desde la PWA'),
)


def _fmt(valor):
    try:
        return f'{int(valor):,}'.replace(',', '.')
    except (TypeError, ValueError):
        return str(valor)


def _ahora_utc():
    return datetime.now(timezone.utc)


def construir_reporte(dias=1):
    """Devuelve ``{'texto', 'datos', 'disponible'}``. Nunca lanza."""
    hoy = _ahora_utc().date().isoformat()
    datos = trafico.resumen_trafico(dias=dias)
    lineas = []
    lineas.append('Localis — Reporte de tráfico y PWA')
    lineas.append('=' * 44)
    lineas.append(f'Generado: {_ahora_utc().strftime("%Y-%m-%d %H:%M UTC")}')
    lineas.append(f'Período: últimos {dias} día(s) (días en UTC)')
    lineas.append('')

    if not datos.get('disponible'):
        lineas.append('⚠ Aún no hay datos de tráfico persistidos.')
        lineas.append('  (La tabla metricas_diarias estará vacía hasta que la app')
        lineas.append('   reciba tráfico tras el despliegue.)')
        return {'texto': '\n'.join(lineas), 'datos': datos, 'disponible': False}

    diario = {d['fecha']: d['claves'] for d in datos.get('por_dia', [])}
    hoy_claves = diario.get(hoy, {})

    lineas.append(f'--- Hoy ({hoy}) ---')
    if hoy_claves:
        for clave, etiqueta in _ETIQUETAS:
            lineas.append(f'  {etiqueta}: {_fmt(hoy_claves.get(clave, 0))}')
    else:
        lineas.append('  (sin tráfico registrado hoy todavía)')
    lineas.append('')

    lineas.append(f'--- Totales del período ({dias} día(s)) ---')
    totales = datos.get('totales_periodo', {})
    for clave, etiqueta in _ETIQUETAS:
        lineas.append(f'  {etiqueta}: {_fmt(totales.get(clave, 0))}')
    lineas.append('')

    historicos = datos.get('totales_historicos', {})
    if historicos:
        lineas.append('--- Totales históricos (desde el inicio) ---')
        for clave, etiqueta in _ETIQUETAS:
            lineas.append(f'  {etiqueta}: {_fmt(historicos.get(clave, 0))}')
        lineas.append('')

    por_dia = datos.get('por_dia', [])
    if len(por_dia) > 1:
        lineas.append('--- Desglose por día ---')
        for dia in por_dia:
            c = dia.get('claves', {})
            lineas.append(
                f'  {dia.get("fecha")}  '
                f'pet={_fmt(c.get("peticiones", 0))}  '
                f'app={_fmt(c.get("peticiones_app", 0))}  '
                f'únicos={_fmt(c.get("visitas_unicas", 0))}  '
                f'inst_pwa={_fmt(c.get("pwa_instalaciones", 0))}  '
                f'apert_pwa={_fmt(c.get("pwa_aperturas", 0))}'
            )
        lineas.append('')

    lineas.append('Notas:')
    lineas.append('- "Visitantes únicos" es aproximado (huella IP+navegador; se')
    lineas.append('  reinicia con el worker y al cambiar de día, en UTC).')
    lineas.append('- Instalaciones de PWA: evento appinstalled. En iPhone (iOS)')
    lineas.append('  instalar desde Safari NO es detectable, así que no se cuenta.')
    lineas.append('- Descargas de la app Android (Google Play) no se incluyen:')
    lineas.append('  solo están disponibles en Google Play Console.')
    lineas.append('')
    lineas.append('— Localis')

    return {
        'texto': '\n'.join(lineas),
        'datos': datos,
        'disponible': True,
    }


def _leer_marca():
    """Fecha (ISO) del último envío, o None. Nunca lanza."""
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT valor FROM configuracion_sistema WHERE clave = ?',
                (_CLAVE_MARCA,),
            )
            fila = cursor.fetchone()
            if fila is None:
                return None
            return fila.get('valor') if isinstance(fila, dict) else fila[0]
    except Exception:
        return None


def _guardar_marca(valor):
    """Guarda la fecha del envío. Nunca lanza."""
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                INSERT INTO configuracion_sistema (clave, valor)
                VALUES (?, ?)
                ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor
                """,
                (_CLAVE_MARCA, valor),
            )
            conexion.commit()
        return True
    except Exception as error:
        logger.error(f'{_LOG} no se pudo guardar la marca: {type(error).__name__}: {error}')
        return False


def enviar_reporte_admin(dias=1, forzar=False, destinatarios=None):
    """Genera y envía el reporte. Devuelve ``(ok, detalle, texto)``.

    Si ya se envió hoy y ``forzar`` es False, no reenvía (idempotencia). El envío
    se serializa con un lock para que el planificador interno y el endpoint de
    cron nunca disparen dos correos a la vez.
    """
    reporte = construir_reporte(dias=dias)
    texto = reporte['texto']

    with _envio_lock:
        if not forzar:
            hoy = _ahora_utc().date().isoformat()
            if _leer_marca() == hoy:
                return False, 'ya enviado hoy (sin reenvío)', texto

        if not correo_configurado():
            return False, 'SMTP no configurado (SMTP_USER/SMTP_PASSWORD)', texto

        destinatarios = destinatarios or list(ADMIN_EMAILS)
        if not destinatarios:
            return False, 'sin administradores configurados (LOCALIS_ADMIN_EMAILS)', texto

        asunto = f'[Localis] Reporte de tráfico {_ahora_utc().date().isoformat()}'
        enviados = 0
        fallos = []
        for destino in destinatarios:
            try:
                ok, detalle = enviar_correo(destino, asunto, texto)
            except Exception as error:
                ok, detalle = False, str(error)
            if ok:
                enviados += 1
            else:
                fallos.append(f'{destino}: {detalle}')

        if enviados:
            _guardar_marca(_ahora_utc().date().isoformat())

        detalle = f'enviados={enviados} fallos={len(fallos)}'
        if fallos:
            detalle += ' | ' + '; '.join(fallos)
        return (enviados > 0), detalle, texto


# ==========================================================
# Planificador diario dentro de la app (sin cron externo)
# ==========================================================

def _reporte_diario_habilitado():
    valor = (os.environ.get('LOCALIS_REPORTE_DIARIO') or '1').strip().lower()
    return valor not in ('0', 'false', 'no', 'off')


def _hora_objetivo_utc():
    try:
        return max(0, min(23, int(os.environ.get('LOCALIS_REPORTE_HORA_UTC', '13') or 13)))
    except (TypeError, ValueError):
        return 13


def quizas_enviar_reporte_diario():
    """Envía el reporte si ya pasó la hora diaria y no se envió hoy. Nunca lanza.

    Reintenta solo: si la app estaba dormida a la hora objetivo, envía en el
    primer ciclo posterior a despertar (idempotente por día).
    """
    if not _reporte_diario_habilitado():
        return
    try:
        ahora = _ahora_utc()
        if ahora.hour < _hora_objetivo_utc():
            return
        if _leer_marca() == ahora.date().isoformat():
            return
        enviar_reporte_admin(forzar=False)
    except Exception as error:
        logger.error(f'{_LOG} envío diario falló: {type(error).__name__}: {error}')


def _bucle_reporte():
    while True:
        time.sleep(max(60, _INTERVALO_REVISION))
        try:
            quizas_enviar_reporte_diario()
        except Exception as error:
            logger.error(f'{_LOG} ciclo del planificador falló: {type(error).__name__}: {error}')


def iniciar_reporte_diario():
    """Arranca el planificador del reporte (hilo daemon). Idempotente. Nunca lanza.

    NO llamar al importar el módulo (el hilo no sobreviviría al fork de gunicorn).
    """
    global _iniciado
    if not _reporte_diario_habilitado():
        logger.info(f'{_LOG} desactivado (LOCALIS_REPORTE_DIARIO=0)')
        return None
    with _inicio_lock:
        if _iniciado:
            return None
        try:
            hilo = threading.Thread(
                target=_bucle_reporte, name='localis-reporte', daemon=True
            )
            hilo.start()
        except Exception as error:
            logger.error(
                f'{_LOG} no se pudo iniciar el planificador: {type(error).__name__}: {error}'
            )
            return None
        _iniciado = True
        logger.info(
            f'{_LOG} planificador diario activo (objetivo {_hora_objetivo_utc()}:00 UTC)'
        )
        return hilo


def asegurar_reporte_diario_activo():
    """Arranca el planificador desde el request (post-fork). Throttle 30 s. Nunca lanza."""
    global _ultimo_intento_arranque
    try:
        if _iniciado or not _reporte_diario_habilitado():
            return
        ahora = time.time()
        if ahora - _ultimo_intento_arranque < 30:
            return
        _ultimo_intento_arranque = ahora
        iniciar_reporte_diario()
    except Exception:
        pass

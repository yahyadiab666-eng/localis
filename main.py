import logging
import mimetypes
import os
import threading
import time
import traceback
from dotenv import load_dotenv

# override=False: en Render las variables del panel ganan.
# Un .env local (o vacio) no debe pisar SUPABASE_URL de produccion.
load_dotenv(override=False)

from backend.observabilidad import configurar_logging, registrar_request_logging

configurar_logging()
_log = logging.getLogger('localis')


def _auditar_variables_entorno():
    """Registra presencia de secretos sin imprimir valores."""
    for clave in ('DATABASE_URL', 'SUPABASE_URL'):
        presente = bool((os.environ.get(clave) or '').strip())
        if presente:
            _log.info('%s=presente', clave)
            print(f'[Localis Config] {clave}=presente', flush=True)
        else:
            _log.error('%s ausente o vacia', clave)
            print(f'[Localis Config] ERROR {clave} ausente o vacia', flush=True)
    canon = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or '').strip()
    typo = (os.environ.get('SUPABASE_SERVICE_ROL_KEY') or '').strip()
    secret = (os.environ.get('SUPABASE_SECRET_KEY') or '').strip()
    if canon:
        _log.info('SUPABASE_SERVICE_ROLE_KEY=presente')
        print('[Localis Config] SUPABASE_SERVICE_ROLE_KEY=presente', flush=True)
    elif typo or secret:
        _log.warning(
            'SUPABASE_SERVICE_ROLE_KEY ausente; hay alias/typo. Usa el nombre exacto.'
        )
        print(
            '[Localis Config] ADVERTENCIA: usa SUPABASE_SERVICE_ROLE_KEY (con E en ROLE). '
            'Mientras tanto el catalogo opera en modo hibrido.',
            flush=True,
        )
    else:
        _log.warning('SUPABASE_SERVICE_ROLE_KEY ausente: modo hibrido (fotos oficiales)')
        print(
            '[Localis Config] ADVERTENCIA SUPABASE_SERVICE_ROLE_KEY ausente: '
            'modo hibrido, el catalogo no se bloquea.',
            flush=True,
        )


_auditar_variables_entorno()

print(
    f"[Localis] Arranque PORT={os.environ.get('PORT', '10000')} "
    '(Gunicorn debe escuchar en 0.0.0.0:$PORT).',
    flush=True,
)

GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '').strip()
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '').strip()

if not GOOGLE_CLIENT_ID:
    print('Aviso: GOOGLE_CLIENT_ID no configurado en .env')
else:
    print('Google OAuth configurado correctamente.')


from backend.supabase_client import (
    SUPABASE_BUCKET_IMAGENES,
    obtener_cliente_storage,
    supabase_storage_habilitado,
)
if supabase_storage_habilitado():
    print('Supabase Storage configurado con SUPABASE_SERVICE_ROLE_KEY.')
else:
    print(
        'Aviso: Storage en modo hibrido (sin SUPABASE_SERVICE_ROLE_KEY valida). '
        'Las fotos manuales se comprimen y quedan en /static/uploads/; '
        'el catalogo automatico sigue con fotos oficiales.'
    )


from authlib.integrations.flask_client import OAuth
from config import (
    DEFAULT_BANNER_URL,
    MAX_UPLOAD_BYTES,
    MENSAJE_PAGOS_CONGELADOS,
    WHATSAPP_SOPORTE,
    WHATSAPP_SOPORTE_URL,
    aplicar_config_sesion_flask,
    es_entorno_produccion,
    obtener_secret_key,
    pagos_habilitados,
    validar_config_arranque,
)
from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from flask_sqlalchemy import SQLAlchemy
from werkzeug.exceptions import RequestEntityTooLarge

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from backend.admin import (
    obtener_categorias_admin,
)
from backend.auth import obtener_o_crear_usuario_google, obtener_usuario_por_id
from backend.diagnostics import ejecutar_diagnostico_inicio, obtener_estado_sistema
from backend.error_handlers import registrar_manejadores_errores
from utils.images import (
    PLACEHOLDER_PRODUCTO,
    url_hero_inicio,
    url_imagen_producto_segura,
    url_storage_o_vacio,
)
from backend.image_lookup import (
    obtener_imagen_url_producto,
)
from database import BaseDatosNoDisponible, calentar_pool_bd, init_db, normalize_database_url
try:
    from backend.payment_ocr import validar_comprobante_pago_movil
except Exception:
    print('[Localis] No se pudo importar OCR de comprobantes (no bloquea el arranque):', flush=True)
    traceback.print_exc()

    def validar_comprobante_pago_movil(*_args, **_kwargs):
        return False, 'OCR de comprobantes no disponible en este worker.'


from backend.subscriptions import (
    verificar_vencimiento_comercio,
    verificar_vencimientos_comercios,
)
from backend.utils import (
    formatear_fecha,
    normalizar_telefono_whatsapp,
    url_maps_comercio,
    url_whatsapp_comercio,
)
from backend.session_comercio import (
    asegurar_contexto_comercio,
    destino_panel_usuario,
    es_usuario_comerciante,
    limpiar_contexto_comercio,
    vincular_comercio_en_sesion,
)
from backend.web_seguridad import (
    login_requerido,
    peticion_acepta_json as _peticion_acepta_json,
    registrar_csrf,
)
from backend.rate_limit import configurar_rate_limit, limite
from backend.vistas_imagenes import (
    debug_imagenes_antes_de_render as _debug_imagenes_antes_de_render,
    normalizar_imagenes_comercio as _normalizar_imagenes_comercio,
)
from backend.vistas_comercio import (
    cargar_datos_comercio_usuario as _cargar_datos_comercio_usuario,
)
from backend.import_queue import (
    configurar_cola,
)
from backend.stores import (
    buscar_y_filtrar_productos,
    listar_comercios_por_usuario,
    listar_comercios_publicos_para_sitemap,
    listar_portada_hibrida,
    listar_productos_publicos_para_sitemap,
    obtener_comercio_por_id,
    obtener_comercio_por_slug,
    obtener_comercio_por_usuario,
    obtener_producto_publico,
    obtener_producto_publico_por_slug,
    obtener_tasa_dolar,
)
from backend.tasa_bcv import obtener_tasa_bcv
from backend.apariencia import (
    color_banner,
    gradiente_banner,
    opciones_banner,
)

print('[Localis] Creando aplicación Flask...', flush=True)

mimetypes.add_type('image/webp', '.webp')

app = Flask(__name__)
db_url = ''
db = None
csrf = None


def _sqlalchemy_url(url):
    """URI para SQLAlchemy forzando el driver instalado ``psycopg2``.

    SQLAlchemy 2.x puede resolver el dialecto a ``psycopg`` (v3) si la URL trae
    ``+psycopg`` o por su heurística, y el paquete no está instalado →
    ``ModuleNotFoundError: No module named 'psycopg'``. Aquí se normaliza a
    ``postgresql+psycopg2://``. No afecta a la conexión directa de psycopg2
    (que sigue usando ``DATABASE_URL`` sin el sufijo ``+psycopg2``).
    """
    valor = (url or '').strip() or 'postgresql://localhost/localis'
    if valor.startswith('postgresql+psycopg2://'):
        return valor
    for prefijo in (
        'postgresql+psycopg://',
        'postgresql+psycopg3://',
        'postgresql://',
        'postgres://',
    ):
        if valor.startswith(prefijo):
            return 'postgresql+psycopg2://' + valor[len(prefijo):]
    return valor


try:
    db_url = normalize_database_url(os.environ.get('DATABASE_URL', ''))
    app.config['SQLALCHEMY_DATABASE_URI'] = _sqlalchemy_url(db_url)
    db = SQLAlchemy(app)
    try:
        app.secret_key = obtener_secret_key()
    except Exception:
        print('[Localis] LOCALIS_SECRET_KEY no disponible; el worker sigue vivo con clave de respaldo.', flush=True)
        traceback.print_exc()
        app.secret_key = (
            (os.environ.get('LOCALIS_SECRET_KEY') or '').strip()
            or 'localis-arranque-sin-secret'
        )
    aplicar_config_sesion_flask(app)
    app.config['SUPABASE_CLIENT'] = obtener_cliente_storage()
    app.config['SUPABASE_BUCKET_IMAGENES'] = SUPABASE_BUCKET_IMAGENES
    app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.jinja_env.auto_reload = True
    app.jinja_env.cache.clear()
    csrf = CSRFProtect(app)
    registrar_csrf(csrf)
    configurar_rate_limit(app)
    registrar_request_logging(app)
    registrar_manejadores_errores(app)
except Exception:
    print('[Localis] Error al configurar Flask (el proceso no se detiene para que Gunicorn enlace el puerto):', flush=True)
    traceback.print_exc()
    if not app.secret_key:
        app.secret_key = 'localis-arranque-degradado'

# Cola de importación asíncrona: guarda la app para el contexto de Flask en
# los workers. Los hilos se crean de forma perezosa en la primera importación.
configurar_cola(app)

# Reintento periódico de imágenes pendientes/rechazadas (sin falsos positivos).
try:
    from backend.image_backfill import iniciar_backfill_periodico

    iniciar_backfill_periodico()
except Exception as _error_backfill:
    print(f'[Localis] backfill periódico no iniciado: {_error_backfill}')


@app.template_filter('fecha_corta')
def _filtro_fecha_corta(valor):
    return formatear_fecha(valor) or '—'


@app.template_filter('emoji_categoria')
def _filtro_emoji_categoria(nombre):
    """Emoji representativo de una categoría (defensivo: 📦 por defecto)."""
    try:
        from backend.categorias_producto import emoji_categoria

        return emoji_categoria(nombre)
    except Exception:
        return '📦'


@app.template_filter('url_imagen_producto')
def _filtro_url_imagen_producto(producto):
    """Storage persistida o placeholder. Nunca None hacia Jinja2."""
    try:
        url = url_imagen_producto_segura(producto)
        if url:
            return url
        logging.getLogger('localis.images').error(
            'Filtro url_imagen_producto sin URL usable; placeholder.'
        )
        return PLACEHOLDER_PRODUCTO
    except Exception:
        logging.getLogger('localis.images').error(
            'Filtro url_imagen_producto fallo', exc_info=True
        )
        traceback.print_exc()
        return PLACEHOLDER_PRODUCTO


@app.context_processor
def _injectar_placeholders_imagen():
    return {
        'placeholder_producto': PLACEHOLDER_PRODUCTO,
        'placeholder_banner': DEFAULT_BANNER_URL,
        'hero_local': DEFAULT_BANNER_URL,
        'default_banner': DEFAULT_BANNER_URL,
        'asset_v': 'catalogo-180',
        'paleta_banner': opciones_banner(),
    }


@app.context_processor
def _injectar_estado_pagos():
    """Disponible en todas las plantillas: congelamiento temporal de pagos.

    Localis es 100% gratuito en fase de lanzamiento. El flag de entorno
    ``LOCALIS_PAGOS_HABILITADOS`` permite reactivar la monetización sin tocar
    plantillas ni rutas (todo el código de pago permanece intacto).
    """
    try:
        habilitados = pagos_habilitados()
    except Exception:
        habilitados = False
    return {
        'pagos_habilitados': habilitados,
        'pagos_congelados': not habilitados,
        'mensaje_pagos_congelados': MENSAJE_PAGOS_CONGELADOS,
    }


@app.context_processor
def _injectar_usuario_publico():
    """Datos del usuario en sesión disponibles en TODAS las plantillas (Fase 3).

    Se exponen solo claves de sesión (sin consultar la BD) para que la barra
    pública pueda pintar el avatar y el menú sin tocar rutas ni lógica.
    """
    try:
        usuario_id = session.get('usuario_id')
        return {
            'usuario_id': usuario_id,
            'username': session.get('username') or '',
            'correo': session.get('correo') or '',
            'foto_url': session.get('foto_url') or '',
            'es_admin': bool(session.get('es_admin')),
            # Comerciante si la sesión lo marca o si ya tiene comercio vinculado.
            'es_comerciante': bool(session.get('es_comerciante'))
            or bool(session.get('comercio_id')),
        }
    except Exception:
        return {
            'usuario_id': None,
            'username': '',
            'correo': '',
            'foto_url': '',
            'es_admin': False,
            'es_comerciante': False,
        }


@app.context_processor
def _inyectar_tasa_bcv():
    """Tasa oficial del BCV disponible en TODAS las plantillas como ``tasa_bcv``.

    Usa la caché diaria en memoria (sin coste por petición). El primer render de
    la jornada no bloquea: el módulo precalienta la caché en segundo plano al
    arrancar el worker. Nunca rompe el render.
    """
    try:
        return {'tasa_bcv': obtener_tasa_bcv()}
    except Exception as error:  # defensivo: una tasa ausente no debe tumbar la vista
        print(f'[Localis Tasa] contexto BCV no disponible: {type(error).__name__}: {error}')
        return {
            'tasa_bcv': {
                'ok': False,
                'tasa': 1.0,
                'fecha': None,
                'fuente': 'fallback',
                'error': 'tasa BCV no disponible',
            }
        }




def _diagnosticar_serper():
    """Diagnóstico opcional del buscador de imágenes al arrancar.

    Se activa con ``LOCALIS_SERPER_DIAGNOSTICO=1`` y nunca bloquea la
    inicialización: verifica la clave de Serper.dev y clasifica el fallo.
    """
    activo = str(os.getenv('LOCALIS_SERPER_DIAGNOSTICO', '0')).strip().lower()
    if activo not in ('1', 'true', 'yes', 'on'):
        return
    try:
        from backend.serper_images import diagnosticar

        informe = diagnosticar(imprimir=False)
        if informe.get('ok'):
            print('[Localis Serper] Credenciales válidas (HTTP 200).', flush=True)
        elif informe.get('categoria') == 'clave_invalida':
            print(
                '[Localis Serper] La clave SERPER_API_KEY NO es válida. '
                'Revisa la credencial de Serper.dev.',
                flush=True,
            )
        else:
            print(
                f'[Localis Serper] Diagnóstico: {informe.get("categoria")} '
                f'- {informe.get("mensaje")}',
                flush=True,
            )
    except Exception as error:
        print(f'[Localis Serper] diagnóstico omitido: {type(error).__name__}: {error}')


def _inicializar_aplicacion():
    """Migraciones y diagnóstico fuera del hilo que importa el WSGI (Gunicorn ya puede bind)."""
    print('[Localis] Inicialización en segundo plano...', flush=True)
    try:
        advertencias = validar_config_arranque()
        for aviso in advertencias:
            print(f'[Localis Config] {aviso}', flush=True)
        if db_url:
            print('Base de datos: PostgreSQL (DATABASE_URL).', flush=True)
        init_db()
        # Warm-up explícito: deja el pool y la conexión verificados antes de
        # que la primera petición real pague el connect en frío del pooler.
        calentar_pool_bd()
        print(
            '[Localis] Catálogo maestro: sin relleno ni sincronización al arrancar.',
            flush=True,
        )
        try:
            from backend.comercio_schema import invalidar_cache_columnas_comercios

            invalidar_cache_columnas_comercios()
        except Exception:
            traceback.print_exc()
        ejecutar_diagnostico_inicio()
        _diagnosticar_serper()
        # --- RESTAURAR_PAGOS_INICIO ---
        # En la fase gratuita no se expiran tiendas por suscripción al arrancar.
        if pagos_habilitados():
            verificar_vencimientos_comercios()
        # --- RESTAURAR_PAGOS_FIN ---
        print('[Localis] Inicialización completada.', flush=True)
    except Exception as error:
        print(f'[Localis] Error al inicializar la aplicación: {error}', flush=True)
        traceback.print_exc()
        try:
            from backend.diagnostics import reportar_error_critico_async

            reportar_error_critico_async(error, request=None)
        except Exception:
            traceback.print_exc()


_hilo_init = threading.Thread(
    target=_inicializar_aplicacion,
    name='localis-init',
    daemon=True,
)
_hilo_init.start()
print(
    '[Localis] Worker listo para aceptar tráfico; DB/diagnóstico/service_role no bloquean el bind.',
    flush=True,
)

_ultima_verificacion_vencimientos_global = 0.0
_INTERVALO_VENCIMIENTOS_SEG = 300
_INTERVALO_VENCIMIENTO_COMERCIO_SEG = 600
_vencimientos_lock = threading.Lock()
_vencimientos_en_vuelo = False


def _mantenimiento_vencimientos_async():
    """Ejecuta la verificación global fuera del hilo de la petición HTTP.

    Evita que un UPDATE lento (o un lock de BD) bloquee la primera carga de la
    página mientras Render mantiene el puerto abierto ("healthy" pero sin servir).
    """
    global _vencimientos_en_vuelo
    try:
        # --- RESTAURAR_PAGOS_INICIO ---
        if pagos_habilitados():
            verificar_vencimientos_comercios()
        # --- RESTAURAR_PAGOS_FIN ---
    except Exception as error:
        print(f'Aviso verificación vencimientos (async): {error}')
    finally:
        _vencimientos_en_vuelo = False
        try:
            _vencimientos_lock.release()
        except RuntimeError:
            pass


@app.before_request
def sincronizar_vencimientos_suscripcion():
    """Revisa vencimientos globalmente (cada 5 min) y por comercio en pagos (cada 10 min)."""
    global _ultima_verificacion_vencimientos_global, _vencimientos_en_vuelo

    # --- RESTAURAR_PAGOS_INICIO ---
    # Con los pagos ocultos la app es nativamente gratuita: no se expiran ni
    # ocultan tiendas por suscripción.
    if not pagos_habilitados():
        return
    # --- RESTAURAR_PAGOS_FIN ---

    try:
        ahora = time.time()
        if (
            ahora - _ultima_verificacion_vencimientos_global >= _INTERVALO_VENCIMIENTOS_SEG
            and not _vencimientos_en_vuelo
            and _vencimientos_lock.acquire(blocking=False)
        ):
            # Nunca bloquea la petición: el mantenimiento corre en un hilo daemon.
            _ultima_verificacion_vencimientos_global = ahora
            _vencimientos_en_vuelo = True
            threading.Thread(
                target=_mantenimiento_vencimientos_async,
                name='localis-vencimientos',
                daemon=True,
            ).start()

        if 'usuario_id' not in session:
            return

        if not request.path.startswith('/api/pagos'):
            return

        ultimo_comercio = session.get('_venc_comercio_ts', 0)
        if ahora - ultimo_comercio < _INTERVALO_VENCIMIENTO_COMERCIO_SEG:
            return

        comercio_id = asegurar_contexto_comercio(session.get('usuario_id'))
        if comercio_id:
            verificar_vencimiento_comercio(comercio_id)
            session['_venc_comercio_ts'] = ahora
    except Exception as error:
        print(f'Aviso sincronización de vencimientos: {error}')


@app.after_request
def agregar_cache_estaticos(response):
    """Encabezados de caché para imágenes y assets estáticos."""
    if request.path.startswith('/static/'):
        extensiones_cache = (
            '.webp', '.jpg', '.jpeg', '.png', '.gif', '.svg', '.css', '.js', '.woff2'
        )
        if request.path.lower().endswith(extensiones_cache):
            response.headers['Cache-Control'] = 'public, max-age=604800'
    return response


@app.after_request
def agregar_cabeceras_seguridad(response):
    """Cabeceras de seguridad HTTP (endurecimiento aditivo y reversible).

    No activa Content-Security-Policy por defecto porque las plantillas usan
    estilos y onclick inline; se puede probar con LOCALIS_CSP en el entorno.
    HSTS solo se envia en produccion (requiere HTTPS).
    """
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault(
        'Referrer-Policy', 'strict-origin-when-cross-origin'
    )
    response.headers.setdefault(
        'Permissions-Policy', 'geolocation=(), microphone=(), camera=()'
    )
    if es_entorno_produccion():
        response.headers.setdefault(
            'Strict-Transport-Security', 'max-age=31536000; includeSubDomains'
        )
    csp = (os.environ.get('LOCALIS_CSP') or '').strip()
    if csp:
        response.headers.setdefault('Content-Security-Policy', csp)
    return response

oauth = OAuth(app)
google = None

if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    google = oauth.register(
        name='google',
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )


def _url_imagen_comercio_usable(valor):
    """Logo/banner de tienda: Storage persistida o None (icono/degradado en plantilla)."""
    try:
        return url_storage_o_vacio(valor) or None
    except Exception:
        traceback.print_exc()
        return None


# _normalizar_imagenes_comercio / _normalizar_logo_completo viven en
# backend/vistas_imagenes.py


# procesar_imagen_subida vive ahora en backend/vistas_imagenes.py






# _sincronizar_foto_local_si_aplica / _registrar_imagen_manual /
# _quitar_imagen_manual viven ahora en backend/vistas_imagenes.py


# _comercio_sesion_validado vive ahora en backend/web_seguridad.py




# _sesion_usuario_activa, login_requerido y login_requerido_api viven ahora en
# backend/web_seguridad.py (para reutilizarlos desde los blueprints).


# admin_requerido y admin_requerido_api viven ahora en backend/web_seguridad.py




# _peticion_acepta_json y _exento_csrf viven ahora en backend/web_seguridad.py


@app.errorhandler(CSRFError)
def error_csrf(e):
    flash(
        'Tu sesión de formulario expiró o la petición no es segura. Inténtalo de nuevo.',
        'error',
    )
    if request.path.startswith('/api/') or _peticion_acepta_json():
        return jsonify({'ok': False, 'error': 'Token CSRF inválido o expirado.'}), 400
    return redirect(request.referrer or url_for(destino_panel_usuario()))


@app.errorhandler(RequestEntityTooLarge)
def error_archivo_demasiado_grande(e):
    max_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
    mensaje = f'El archivo supera el tamaño máximo permitido ({max_mb} MB).'

    if request.path.startswith('/api/') or _peticion_acepta_json():
        return jsonify({'ok': False, 'error': mensaje}), 413

    flash(mensaje, 'error')
    return redirect(request.referrer or url_for(destino_panel_usuario()))


# ==========================================
# CUMPLIMIENTO PLAY STORE / PWA
# (rutas nuevas de soporte; no modifican la lógica existente)
# ==========================================


@app.route('/sw.js')
def service_worker():
    """Sirve el Service Worker en la RAÍZ para que su scope sea '/'.

    Necesario para que la PWA sea instalable y cubra toda la app (no solo
    /static/). Nunca cachea zonas privadas (ver static/sw.js).
    """
    ruta = os.path.join(BASE_DIR, 'static', 'sw.js')
    try:
        with open(ruta, 'r', encoding='utf-8') as archivo:
            contenido = archivo.read()
    except OSError:
        return Response('', status=404, mimetype='application/javascript')
    respuesta = Response(contenido, mimetype='application/javascript')
    respuesta.headers['Service-Worker-Allowed'] = '/'
    respuesta.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return respuesta


@app.route('/offline')
def offline():
    """Página de respaldo sin conexión (la usa el Service Worker)."""
    return render_template('offline.html')


@app.route('/privacidad')
def privacidad():
    """Política de privacidad pública (requisito de Google Play)."""
    return render_template('privacidad.html', whatsapp_url=WHATSAPP_SOPORTE_URL)


@app.route('/eliminar-cuenta')
def eliminar_cuenta():
    return '''
    <h2>Eliminación de cuenta - Localis</h2>
    <p>Para solicitar la eliminación de tu cuenta y datos asociados en Localis, por favor comunícate con nuestro soporte técnico indicando el correo electrónico con el que iniciaste sesión.</p>
    '''


@app.route('/.well-known/assetlinks.json')
def assetlinks():
    """Digital Asset Links para la vinculación TWA <-> dominio.

    Sirve el archivo versionado ``.well-known/assetlinks.json`` (contenido
    exacto para la app Android TWA). Si el archivo no existiera, lo genera
    desde variables de entorno como respaldo:
      PLAY_ASSETLINKS_PACKAGE  (por defecto com.onrender.localis.twa)
      PLAY_ASSETLINKS_SHA256   huella(s) SHA-256 (separadas por coma).
    """
    directorio = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), '.well-known'
    )
    ruta = os.path.join(directorio, 'assetlinks.json')
    if os.path.exists(ruta):
        with open(ruta, 'r', encoding='utf-8') as archivo:
            contenido = archivo.read()
        return Response(contenido, mimetype='application/json')

    paquete = (os.getenv('PLAY_ASSETLINKS_PACKAGE') or 'com.onrender.localis.twa').strip()
    huella = (os.getenv('PLAY_ASSETLINKS_SHA256') or '').strip()
    huellas = [h.strip() for h in huella.split(',') if h.strip()] or [
        'E8:FD:FD:CE:EE:F9:4C:30:DA:9E:23:80:7F:DC:F9:32:D3:F0:95:DC:F4:19:4D:C1:31:35:DB:F2:CF:80:A7:61'
    ]
    return jsonify(
        [
            {
                'relation': ['delegate_permission/common.handle_all_urls'],
                'target': {
                    'namespace': 'android_app',
                    'package_name': paquete,
                    'sha256_cert_fingerprints': huellas,
                },
            }
        ]
    )


@app.route('/health')
def health_check():
    """Estado consolidado (BD + Supabase) para monitoreo (Render, uptime, etc.)."""
    estado = obtener_estado_sistema()
    codigo = 200 if estado.get('ok') else 503
    return jsonify(estado), codigo


@app.route('/health/live')
def health_live():
    """Liveness: responde 200 en cuanto el proceso está escuchando.

    Es el endpoint recomendado para el *health check* de Render: no depende de
    la base de datos y evita reinicios por un pico transitorio de PostgreSQL.
    """
    return jsonify(
        {
            'ok': True,
            'servicio': 'localis',
            'tipo': 'liveness',
            # Render inyecta estas variables: permiten verificar exactamente
            # que commit y que rama estan desplegados en produccion.
            'commit': (os.environ.get('RENDER_GIT_COMMIT') or '')[:7],
            'branch': os.environ.get('RENDER_GIT_BRANCH') or '',
        }
    ), 200


@app.route('/health/ready')
def health_ready():
    """Readiness: 200 solo si la base de datos responde."""
    estado = obtener_estado_sistema()
    return jsonify(estado), (200 if estado.get('ok') else 503)


# /api/tasa-bcv y /api/actualizar-tasa viven ahora en backend/blueprints/api.py


def inicializar_base_de_datos():
    if init_db():
        print('\nBase de datos e índices verificados/actualizados.\n')
    else:
        print('\nNo se pudo completar init_db().\n')


# ==========================================
# RUTAS PÚBLICAS (CLIENTE)
# ==========================================


@app.route('/')
def index():
    palabra_clave = request.args.get('q', '').strip()
    categoria = request.args.get('categoria', '').strip()

    hay_filtros = bool(palabra_clave or categoria)
    catalogo_degradado = False
    try:
        crudo_pagina = (
            os.getenv('LOCALIS_CATALOGO_PAGINA')
            or os.getenv('LOCALIS_CATALOGO_INICIO_LIMITE')
            or '60'
        )
        por_pagina = max(1, int(crudo_pagina))
    except (TypeError, ValueError):
        por_pagina = 60
    try:
        pagina = max(1, int(request.args.get('pagina', 1) or 1))
    except (TypeError, ValueError):
        pagina = 1

    if hay_filtros:
        # Búsqueda/filtro: se muestran TODOS los resultados (sin truncar).
        productos = buscar_y_filtrar_productos(
            palabra_clave=palabra_clave,
            categoria_nombre=categoria,
            limit=None,
            orden_aleatorio=False,
        )
        hay_mas = False
        pagina = 1
    else:
        # Portada: destacados fijos arriba + "revoltijo" justo de no destacados
        # (mezcla aleatoria por carga, con tope por comercio).
        from backend.runtime_cache import get_or_load

        try:
            ttl_portada = max(
                0, int(os.getenv('LOCALIS_CACHE_PORTADA_SEG', '15') or '15')
            )
        except (TypeError, ValueError):
            ttl_portada = 15

        def _cargar_portada():
            return listar_portada_hibrida(
                limit=por_pagina,
                offset=(pagina - 1) * por_pagina,
            )

        try:
            if ttl_portada > 0:
                productos, hay_mas = get_or_load(
                    f'portada:{por_pagina}:{pagina}',
                    _cargar_portada,
                    ttl_portada,
                )
            else:
                productos, hay_mas = _cargar_portada()
        except BaseDatosNoDisponible as error:
            # BD no disponible tras los reintentos: se renderiza sin productos
            # pero marcando la respuesta para que el Service Worker NO la
            # cachee, evitando "envenenar" la portada tras inactividad.
            print(f'[Localis] portada degradada (BD no disponible): {error}')
            productos, hay_mas = [], False
            catalogo_degradado = True
        except Exception as error:
            print(f'[Localis] portada fallo: {type(error).__name__}: {error}')
            traceback.print_exc()
            productos, hay_mas = [], False

    from backend.stores import obtener_configs

    try:
        configs = obtener_configs({
            'tasa_dolar': '36.50',
            'whatsapp_soporte': WHATSAPP_SOPORTE,
            'banner_principal': '',
        })
        try:
            tasa_actual = float(configs['tasa_dolar'])
        except (TypeError, ValueError):
            tasa_actual = obtener_tasa_dolar() or 1.0
        banner_url = url_hero_inicio(configs.get('banner_principal'))
        whatsapp = configs['whatsapp_soporte']
    except Exception:
        traceback.print_exc()
        logging.getLogger('localis.images').error(
            'Fallo al resolver banner de inicio; se usa DEFAULT_BANNER_URL',
            exc_info=True,
        )
        tasa_actual = obtener_tasa_dolar() or 1.0
        banner_url = DEFAULT_BANNER_URL
        whatsapp = WHATSAPP_SOPORTE

    # Fase 1: chips de categorías dinámicos (obtenidos de la BD) + emoji.
    # OJO: usar una variable distinta a ``categoria`` para no sobrescribir el
    # filtro de la URL (string) con el dict de cada categoría.
    try:
        categorias = obtener_categorias_admin()
        from backend.categorias_producto import emoji_categoria

        for cat in categorias:
            cat['emoji'] = emoji_categoria(cat.get('nombre'))
    except Exception:
        categorias = []

    _debug_imagenes_antes_de_render(productos, 'index')
    respuesta = render_template(
        'cliente.html',
        productos=productos,
        categorias=categorias,
        tasa=tasa_actual,
        q=palabra_clave,
        categoria_actual=categoria,
        banner_url=banner_url or DEFAULT_BANNER_URL,
        default_banner=DEFAULT_BANNER_URL,
        whatsapp=whatsapp,
        whatsapp_url=WHATSAPP_SOPORTE_URL,
        pagina=pagina,
        hay_mas=hay_mas,
    )
    if catalogo_degradado:
        # Señal para el Service Worker: no cachear esta respuesta degradada.
        respuesta.headers['X-Localis-No-Cache'] = '1'
        respuesta.headers['Retry-After'] = '5'
    return respuesta


@app.route('/como-funciona')
@app.route('/ayuda')
def como_funciona():
    """Guía de uso para consumidores y comercios."""
    comercio = None
    es_comerciante = False
    usuario_id = session.get('usuario_id')
    if usuario_id:
        try:
            comercio_db, _, _, categorias = _cargar_datos_comercio_usuario(usuario_id)
            if categorias is None and comercio_db:
                comercio = _normalizar_imagenes_comercio(dict(comercio_db))
                es_comerciante = True
        except Exception:
            pass

    return render_template(
        'como_funciona.html',
        comercio=comercio,
        es_comerciante=es_comerciante,
        nav_activo='ayuda',
        whatsapp_url=WHATSAPP_SOPORTE_URL,
    )


# api_producto vive ahora en backend/blueprints/api.py


@app.route('/imagen-producto')
def imagen_producto_respaldo():
    """Devuelve la URL exacta guardada en BD para un producto (sin cruzar catálogo)."""
    producto_id = request.args.get('producto_id', type=int)
    if not producto_id:
        return ('', 404)
    url = obtener_imagen_url_producto(producto_id)
    if url:
        return redirect(url)
    return redirect(PLACEHOLDER_PRODUCTO)


@app.route('/producto/<int:producto_id>')
@app.route('/producto/<string:slug>')
def producto_publico(producto_id=None, slug=None):
    """URL individual y canónica de un producto (por ID o por slug SEO).

    ``/producto/<id>`` y ``/producto/<slug>`` redirigen a la ficha del producto
    dentro de su tienda pública para que se abra resaltada y lista para compartir.
    """
    if producto_id is None and slug:
        producto = obtener_producto_publico_por_slug(slug)
    else:
        producto = obtener_producto_publico(producto_id)
    if not producto or not producto.get('comercio_id'):
        flash('Producto no encontrado o no disponible.', 'error')
        return redirect(url_for('index'))
    producto_id_real = producto.get('id') or producto_id
    # Destino amigable: se prefiere el slug de la tienda si existe.
    tienda_slug = None
    try:
        comercio = obtener_comercio_por_id(producto['comercio_id'])
        tienda_slug = (comercio or {}).get('slug')
    except Exception:
        tienda_slug = None
    if tienda_slug:
        destino = url_for('tienda_publica', slug=tienda_slug, producto=producto_id_real)
    else:
        destino = url_for(
            'tienda_publica',
            comercio_id=producto['comercio_id'],
            producto=producto_id_real,
        )
    return redirect(destino + f'#producto-{producto_id_real}')


@app.route('/tienda/<int:comercio_id>')
@app.route('/tienda/<string:slug>')
def tienda_publica(comercio_id=None, slug=None):
    if comercio_id is None and slug:
        comercio = obtener_comercio_por_slug(slug)
    else:
        comercio = obtener_comercio_por_id(comercio_id, solo_visible=True)
    if not comercio:
        flash('Tienda no encontrada o no disponible.', 'error')
        return redirect(url_for('index'))

    comercio_id = comercio.get('id') or comercio_id
    palabra_clave = request.args.get('q', '').strip()
    origen = (request.args.get('origen') or 'web').strip().lower()[:20]
    productos = buscar_y_filtrar_productos(
        palabra_clave=palabra_clave,
        comercio_id=comercio_id,
    )
    tasa_actual = obtener_tasa_dolar() or 1.0

    comercio = _normalizar_imagenes_comercio(dict(comercio))

    telefono = (comercio.get('telefono') or '').strip()
    color = color_banner(comercio.get('banner_color'))
    comercio['telefono'] = telefono
    comercio['telefono_disponible'] = bool(telefono)
    comercio['whatsapp_url'] = (
        url_whatsapp_comercio(telefono, 'Hola, vi tu tienda en Localis')
        if telefono
        else None
    )
    comercio['whatsapp_numero'] = (
        normalizar_telefono_whatsapp(telefono) if telefono else None
    )
    comercio['maps_link'] = url_maps_comercio(comercio)
    comercio['banner_color_id'] = color['id']
    comercio['banner_color_hex'] = color['hex']
    comercio['banner_color_texto'] = color['texto']
    comercio['banner_gradiente'] = gradiente_banner(comercio.get('banner_color'))

    # Enlace de contacto por ficha: cada producto puede iniciar un WhatsApp al
    # comercio con el nombre del artículo en el mensaje. Solo si hay teléfono.
    if telefono:
        for producto in productos:
            nombre_producto = (producto.get('nombre') or '').strip()
            if nombre_producto:
                texto_producto = (
                    f'¡Hola! Vi "{nombre_producto}" en tu tienda de Localis '
                    'y me interesa. ¿Está disponible?'
                )
            else:
                texto_producto = '¡Hola! Vi tu tienda en Localis y me interesa.'
            producto['whatsapp_url'] = url_whatsapp_comercio(telefono, texto_producto)
    else:
        for producto in productos:
            producto['whatsapp_url'] = None

    # SEO: si la URL trae ?producto=<id>, se usan sus metadatos para Open Graph.
    producto_seleccionado = None
    producto_param = request.args.get('producto')
    if producto_param and str(producto_param).strip().isdigit():
        try:
            producto_seleccionado = obtener_producto_publico(int(producto_param))
        except Exception:
            producto_seleccionado = None

    _debug_imagenes_antes_de_render(productos, 'tienda_publica')
    return render_template(
        'tienda_publica.html',
        comercio=comercio,
        productos=productos,
        producto_seleccionado=producto_seleccionado,
        tasa=tasa_actual,
        q=palabra_clave,
        origen=origen,
    )


@app.route('/login')
def login():
    if session.get('usuario_id'):
        # Fase 2: admin -> /admin; comerciante con negocio -> /comercio;
        # cliente sin negocio -> catálogo público (no al panel de comercio).
        if session.get('es_admin'):
            return redirect(url_for('admin.panel_admin'))
        if es_usuario_comerciante():
            return redirect(url_for('comercio.panel_comercio'))
        return redirect(url_for('index'))
    return render_template('login.html')


@app.route('/registro')
def registro_legacy():
    """Ruta legacy: el registro de comercio pasa por login Google + /comercio/crear."""
    return redirect(url_for('login'))


def _oauth_debug_activo():
    """Permite ver el traceback OAuth en la respuesta (LOCALIS_OAUTH_DEBUG=0 lo oculta)."""
    valor = str(os.getenv('LOCALIS_OAUTH_DEBUG', '1')).strip().lower()
    return valor not in ('0', 'false', 'no', 'off')


def _oauth_error(etapa, error):
    """Registra y (por defecto) devuelve el traceback exacto en texto plano.

    Sustituye la página genérica de Error 500 por el detalle real de Python para
    diagnosticar el callback de Google. Es aislado: solo se usa en estas rutas.
    """
    traza = traceback.format_exc()
    print(f'[Localis OAuth] fallo en {etapa}: {type(error).__name__}: {error}', flush=True)
    print(traza, flush=True)
    logging.getLogger('localis.oauth').error(
        'OAuth %s: %s', etapa, error, exc_info=True
    )
    if _oauth_debug_activo():
        try:
            from database import diagnosticar_postgresql

            diag_bd = diagnosticar_postgresql()
        except Exception as error_diag:
            diag_bd = {'error': f'{type(error_diag).__name__}: {error_diag}'}
        cuerpo = (
            f'Localis · Error 500 en autenticación Google\n'
            f'Etapa: {etapa}\n'
            f'Tipo: {type(error).__name__}\n'
            f'Mensaje: {error}\n\n'
            f'===== TRACEBACK COMPLETO =====\n{traza}\n'
            f'===== ESTADO DE BASE DE DATOS =====\n{diag_bd}\n'
            '===== FIN =====\n'
            'Para ocultar este detalle y volver a un mensaje amable: LOCALIS_OAUTH_DEBUG=0\n'
        )
        return Response(cuerpo, status=500, mimetype='text/plain; charset=utf-8')
    flash('No se pudo iniciar sesión con Google. Intenta de nuevo.', 'error')
    return redirect(url_for('login'))


def _oauth_redirect_uri():
    """Redirect URI correcto detrás del proxy de Render (https).

    Sin esto, ``url_for(_external=True)`` puede devolver ``http://`` detrás del
    proxy TLS, y la cookie de sesión ``Secure`` (que guarda el ``state``) no se
    envía en el callback → ``MismatchingStateError``. Aislado al flujo OAuth.
    """
    uri = url_for('google_callback', _external=True)
    proto = (request.headers.get('X-Forwarded-Proto') or '').split(',')[0].strip().lower()
    if proto == 'https' and uri.startswith('http://'):
        uri = 'https://' + uri[len('http://'):]
    return uri


@app.route('/login/google')
@limite('30 per minute')
def login_google():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        print('[Localis OAuth] GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET ausentes.', flush=True)
        flash('Inicio de sesión con Google no está configurado.', 'error')
        return redirect(url_for('login'))
    if google is None:
        print('[Localis OAuth] cliente OAuth no inicializado.', flush=True)
        flash('No se pudo inicializar el cliente OAuth de Google.', 'error')
        return redirect(url_for('login'))
    try:
        redirect_uri = _oauth_redirect_uri()
        print(f'[Localis OAuth] authorize_redirect uri={redirect_uri}', flush=True)
        return google.authorize_redirect(redirect_uri)
    except Exception as error:
        return _oauth_error('authorize_redirect', error)


@app.route('/login/google/callback')
def google_callback():
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        print('[Localis OAuth] callback sin credenciales configuradas.', flush=True)
        flash('Inicio de sesión con Google no está configurado.', 'error')
        return redirect(url_for('login'))
    if google is None:
        print('[Localis OAuth] callback sin cliente OAuth.', flush=True)
        flash('No se pudo inicializar el cliente OAuth de Google.', 'error')
        return redirect(url_for('login'))

    # 1) Intercambio del código por token (errores de red/estado/token).
    try:
        token = google.authorize_access_token()
    except Exception as error:
        return _oauth_error('authorize_access_token', error)

    # 2) Perfil del usuario (con respaldo por si el token no trae ``userinfo``).
    try:
        google_info = token.get('userinfo') if token else None
        if not google_info:
            try:
                google_info = google.userinfo(token=token) if token else None
            except Exception as error_perfil:
                print(
                    f'[Localis OAuth] userinfo fallback fallo: '
                    f'{type(error_perfil).__name__}: {error_perfil}',
                    flush=True,
                )
                traceback.print_exc()
                google_info = None
        if not google_info:
            flash('No se recibió información de perfil desde Google.', 'error')
            return redirect(url_for('login'))
    except Exception as error:
        return _oauth_error('userinfo', error)

    # 3) Alta/actualización del usuario y sesión.
    try:
        exito, usuario_o_error = obtener_o_crear_usuario_google(google_info)

        if not exito or not isinstance(usuario_o_error, dict) or not usuario_o_error.get('id'):
            # Se muestra el motivo real (p. ej. error de Supabase) en lugar de la
            # página genérica: ayuda a diagnosticar la causa exacta.
            return _oauth_error('usuario', RuntimeError(str(usuario_o_error)))

        session['usuario_id'] = usuario_o_error['id']
        session['username'] = usuario_o_error.get('nombre') or 'Usuario'
        session['correo'] = usuario_o_error.get('correo') or ''
        session['foto_url'] = usuario_o_error.get('foto_url', '') or ''
        session['rol'] = usuario_o_error.get('rol', 'user')
        session['es_admin'] = usuario_o_error.get('rol') == 'admin'

        # --- FASE 1: persistencia real de la sesión -------------------------
        # Marca la sesión como permanente para que la cookie respete
        # PERMANENT_SESSION_LIFETIME (14 días) y no muera al cerrar el navegador/app.
        session.permanent = True
        session.modified = True

        flash(f"¡Bienvenido, {session['username']}!", 'exito')

        # --- FASE 2: redirección por capacidad ------------------------------
        # 1) Admin -> panel de administración.
        if session['es_admin']:
            return redirect(url_for('admin.panel_admin'))

        # 2) Comerciante CON negocio registrado -> panel de comercio.
        comercio = None
        try:
            comercio = obtener_comercio_por_usuario(usuario_o_error['id'])
        except Exception as error_comercio:
            print(
                f'[Localis OAuth] comercio no resuelto: '
                f'{type(error_comercio).__name__}: {error_comercio}',
                flush=True,
            )
            traceback.print_exc()

        if comercio:
            # Al vincular el comercio se marca es_comerciante = True.
            vincular_comercio_en_sesion(comercio['id'])
            return redirect(url_for('comercio.panel_comercio'))

        # 3) Cliente SIN negocio -> catálogo público (no forzar el alta de tienda).
        session['es_comerciante'] = False
        session.modified = True
        return redirect(url_for('index'))
    except Exception as error:
        return _oauth_error('usuario/sesion', error)


@app.route('/perfil')
@login_requerido
def perfil():
    """Vista de cuenta del usuario autenticado.

    Separada por completo del panel de comercios: solo lee el perfil del
    usuario (sin contraseña) y, si es comerciante, sus tiendas vinculadas.
    """
    usuario_id = session.get('usuario_id')
    usuario = None
    try:
        usuario = obtener_usuario_por_id(usuario_id)
    except Exception as error:
        print(f'[Localis] perfil usuario fallo: {type(error).__name__}: {error}')
        traceback.print_exc()

    if not usuario:
        # La sesión apunta a un usuario que ya no existe: se limpia y se
        # redirige al login (mismo criterio que ``login_requerido``).
        session.clear()
        flash('Tu sesión expiró. Inicia sesión nuevamente.', 'error')
        return redirect(url_for('login'))

    soy_comerciante = False
    comercios = []
    try:
        soy_comerciante = es_usuario_comerciante(usuario_id)
        if soy_comerciante:
            comercios = listar_comercios_por_usuario(usuario_id) or []
    except Exception as error:
        print(f'[Localis] perfil comercios fallo: {type(error).__name__}: {error}')
        traceback.print_exc()
        comercios = []

    return render_template(
        'perfil.html',
        usuario=usuario,
        comercios=comercios,
        es_admin=bool(session.get('es_admin')),
        es_comerciante=soy_comerciante,
        has_store=bool(soy_comerciante),
        rol=(
            'admin'
            if session.get('es_admin')
            else ('comerciante' if soy_comerciante else (usuario or {}).get('rol') or 'user')
        ),
    )


# api_me vive ahora en backend/blueprints/api.py


@app.route('/logout', methods=['GET', 'POST'])
def logout():
    """Cierra la sesión del usuario.

    La interfaz usa POST + token CSRF (todas las barras envían un formulario),
    que es el flujo seguro y con confirmación. Se acepta también GET para no
    romper enlaces/llamadas heredados ni devolver un 405 inesperado a un
    usuario con sesión activa. No toca ninguna ruta de pagos ni de comercio.
    """
    limpiar_contexto_comercio()
    session.clear()
    flash('Sesión cerrada correctamente.', 'info')
    return redirect(url_for('index'))


# api_registrar_interaccion vive ahora en backend/blueprints/api.py


# ==========================================
# RUTAS DE COMERCIO
# ==========================================








# Rutas de comercio: viven en backend/blueprints/comercio.py


def _url_tienda_externa(comercio_id, origen=None):
    """URL pública absoluta de la tienda (https detrás del proxy de Render)."""
    url = url_for('tienda_publica', comercio_id=comercio_id, _external=True)
    proto = (request.headers.get('X-Forwarded-Proto') or '').split(',')[0].strip().lower()
    if proto == 'https' and url.startswith('http://'):
        url = 'https://' + url[len('http://'):]
    if origen:
        url = f'{url}{"&" if "?" in url else "?"}origen={origen}'
    return url


@app.route('/tienda/<int:comercio_id>/qr.png')
def tienda_qr_png(comercio_id):
    """QR público de la tienda, generado al vuelo (usado en "Compartir tienda").

    Es público (no requiere sesión) porque codifica la URL pública de la tienda.
    """
    try:
        from backend.qr_codes import generar_qr_png
    except Exception as error:
        print(f'[Localis QR] librería no disponible: {error}')
        return Response('Código QR no disponible.', status=503)

    try:
        png = generar_qr_png(_url_tienda_externa(comercio_id, origen='qr'))
    except Exception as error:
        print(f'[Localis QR] generación fallida: {error}')
        return Response('No se pudo generar el código QR.', status=500)

    return Response(
        png,
        mimetype='image/png',
        headers={
            'Content-Disposition': 'inline; filename="qr-tienda.png"',
            'Cache-Control': 'public, max-age=3600',
        },
    )


# ==========================================
# API REST (JSON)
# ==========================================


# api_crear_producto vive ahora en backend/blueprints/api.py


# api_cotizacion_pago vive ahora en backend/blueprints/api.py


# api_programar_cambio_plan vive ahora en backend/blueprints/api.py


# api_verificar_pago vive ahora en backend/blueprints/api.py


# ==========================================
# RUTAS DE ADMINISTRACIÓN
# ==========================================


# panel_admin y admin_usuarios viven ahora en backend/blueprints/admin.py


# Las rutas de accion de /admin (tasa, banner, estado, suspender, reactivar,
# pre-cuenta, transferir, reasignar, eliminar, plan, ticket) viven ahora en
# backend/blueprints/admin.py


# admin_comercio_detalle vive ahora en backend/blueprints/admin.py


# Rutas admin de edicion de comercio/productos: viven en
# backend/blueprints/admin.py


@app.route('/sitemap.xml')
def sitemap_xml():
    """Mapa del sitio XML (tiendas y productos públicos) para Search Console."""
    from xml.sax.saxutils import escape as _xml_escape

    base = request.url_root.rstrip('/')
    urls = []
    try:
        for comercio in listar_comercios_publicos_para_sitemap() or []:
            slug = str(comercio.get('slug') or '').strip()
            ruta = f'/tienda/{slug}' if slug else f"/tienda/{comercio.get('id')}"
            urls.append(f'{base}{ruta}')
    except Exception:
        traceback.print_exc()
    try:
        for producto in listar_productos_publicos_para_sitemap() or []:
            slug = str(producto.get('slug') or '').strip()
            ruta = f'/producto/{slug}' if slug else f"/producto/{producto.get('id')}"
            urls.append(f'{base}{ruta}')
    except Exception:
        traceback.print_exc()

    lineas = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    lineas.extend(f'  <url><loc>{_xml_escape(u)}</loc></url>' for u in urls)
    lineas.append('</urlset>')
    return Response('\n'.join(lineas), mimetype='application/xml')


@app.route('/robots.txt')
def robots_txt():
    """Guía de rastreo para buscadores + referencia al sitemap dinámico."""
    base = request.url_root.rstrip('/')
    lineas = [
        'User-agent: *',
        'Allow: /',
        'Disallow: /admin',
        'Disallow: /comercio',
        'Disallow: /perfil',
        'Disallow: /api/',
        'Disallow: /logout',
        f'Sitemap: {base}/sitemap.xml',
    ]
    return Response('\n'.join(lineas), mimetype='text/plain')


@app.route('/googled8035da558d7fadc.html')
def google_site_verification_file():
    """Verificación de Google Search Console por archivo HTML en la raíz.

    Sirve ``/googled8035da558d7fadc.html`` con el contenido exacto
    ``google-site-verification: googled8035da558d7fadc.html``.
    """
    directorio = os.path.dirname(os.path.abspath(__file__))
    ruta = os.path.join(directorio, 'googled8035da558d7fadc.html')
    if os.path.exists(ruta):
        with open(ruta, 'r', encoding='utf-8') as archivo:
            contenido = archivo.read()
    else:
        contenido = 'google-site-verification: googled8035da558d7fadc.html\n'
    return Response(contenido, mimetype='text/html')


# admin_cambiar_plan y admin_resolver_ticket viven ahora en
# backend/blueprints/admin.py


# api_confirmar_pago vive ahora en backend/blueprints/api.py


# --- Bloque 4: registrar blueprints (al final, tras definir el CSRF) ---
from backend.blueprints.api import bp as _api_bp
from backend.blueprints.admin import bp as _admin_bp
from backend.blueprints.comercio import bp as _comercio_bp

app.register_blueprint(_api_bp)
app.register_blueprint(_admin_bp)
app.register_blueprint(_comercio_bp)


port = int(os.environ.get('PORT', 10000))

if __name__ == '__main__':
    inicializar_base_de_datos()
    app.run(host='0.0.0.0', port=port)

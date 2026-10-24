import logging
logger = logging.getLogger('localis.app')
import hmac
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
            logger.info(f'[Localis Config] {clave}=presente')
        else:
            _log.error('%s ausente o vacia', clave)
            logger.error(f'[Localis Config] ERROR {clave} ausente o vacia')
    canon = (os.environ.get('SUPABASE_SERVICE_ROLE_KEY') or '').strip()
    typo = (os.environ.get('SUPABASE_SERVICE_ROL_KEY') or '').strip()
    secret = (os.environ.get('SUPABASE_SECRET_KEY') or '').strip()
    if canon:
        _log.info('SUPABASE_SERVICE_ROLE_KEY=presente')
        logger.info('[Localis Config] SUPABASE_SERVICE_ROLE_KEY=presente')
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
    logger.info('Aviso: GOOGLE_CLIENT_ID no configurado en .env')
else:
    logger.info('Google OAuth configurado correctamente.')


from backend.supabase_client import (
    SUPABASE_BUCKET_IMAGENES,
    obtener_cliente_storage,
    supabase_storage_habilitado,
)
if supabase_storage_habilitado():
    logger.info('Supabase Storage configurado con SUPABASE_SERVICE_ROLE_KEY.')
else:
    print(
        'Aviso: Storage en modo hibrido (sin SUPABASE_SERVICE_ROLE_KEY valida). '
        'Las fotos manuales se comprimen y quedan en /static/uploads/; '
        'el catalogo automatico sigue con fotos oficiales.'
    )


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
    make_response,
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
from backend.diagnostics import ejecutar_diagnostico_inicio, obtener_estado_sistema
from backend.error_handlers import registrar_manejadores_errores
from backend.error_tracking import iniciar_error_tracking
from utils.images import (
    PLACEHOLDER_PRODUCTO,
    url_hero_inicio,
    url_imagen_producto_miniatura,
    url_imagen_producto_segura,
    url_storage_o_vacio,
)
from backend.image_lookup import (
    obtener_imagen_url_producto,
)
from database import (
    BaseDatosNoDisponible,
    calentar_pool_bd,
    init_db,
    normalize_database_url,
    programar_keepalive_bd,
)
try:
    from backend.payment_ocr import validar_comprobante_pago_movil
except Exception:
    logger.error('[Localis] No se pudo importar OCR de comprobantes (no bloquea el arranque):')
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
)
from backend.web_seguridad import (
    exento_csrf,
    peticion_acepta_json as _peticion_acepta_json,
    registrar_csrf,
)
from backend.rate_limit import configurar_rate_limit
from backend.compresion import configurar_compresion
from backend.rate_limit import limite
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
    buscar_productos_catalogo,
    listar_comercios_publicos_para_sitemap,
    listar_portada_hibrida,
    listar_productos_publicos_para_sitemap,
    obtener_comercio_por_id,
    obtener_comercio_por_slug,
    obtener_producto_publico,
    obtener_producto_publico_por_slug,
    obtener_tasa_dolar,
    productos_tienda_publica,
)
from backend.tasa_bcv import obtener_tasa_bcv
from backend.apariencia import (
    color_banner,
    gradiente_banner,
    opciones_banner,
)

logger.info('[Localis] Creando aplicación Flask...')

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
        logger.error('[Localis] LOCALIS_SECRET_KEY no disponible; el worker sigue vivo con clave de respaldo.')
        traceback.print_exc()
        app.secret_key = (
            (os.environ.get('LOCALIS_SECRET_KEY') or '').strip()
            or 'localis-arranque-sin-secret'
        )
    aplicar_config_sesion_flask(app)
    app.config['SUPABASE_CLIENT'] = obtener_cliente_storage()
    app.config['SUPABASE_BUCKET_IMAGENES'] = SUPABASE_BUCKET_IMAGENES
    app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES
    # En producción NO se recarga ni recompila plantillas en cada render: se
    # comprueban con el sistema de ficheros y se pierde la caché de Jinja.
    # Override explícito con LOCALIS_TEMPLATES_AUTORELOAD.
    _auto_reload = (os.environ.get('LOCALIS_TEMPLATES_AUTORELOAD') or '').strip().lower()
    if _auto_reload in ('1', 'true', 'yes', 'on'):
        auto_reload = True
    elif _auto_reload in ('0', 'false', 'no', 'off'):
        auto_reload = False
    else:
        auto_reload = not es_entorno_produccion()
    app.config['TEMPLATES_AUTO_RELOAD'] = auto_reload
    app.jinja_env.auto_reload = auto_reload
    app.jinja_env.cache.clear()
    csrf = CSRFProtect(app)
    registrar_csrf(csrf)
    configurar_rate_limit(app)
    # Detrás del proxy de Render, sin esto ``request.remote_addr`` es la IP del
    # proxy: las analíticas mezclan visitantes y el límite por IP sería inútil.
    # ProxyFix toma la IP real del cliente (1 salto de confianza).
    try:
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    except Exception as error:
        logger.warning(f'[Localis] ProxyFix no disponible: {type(error).__name__}: {error}')
    configurar_compresion(app)
    registrar_request_logging(app)
    registrar_manejadores_errores(app)
    iniciar_error_tracking(app)
except Exception:
    logger.error('[Localis] Error al configurar Flask (el proceso no se detiene para que Gunicorn enlace el puerto):')
    traceback.print_exc()
    if not app.secret_key:
        app.secret_key = 'localis-arranque-degradado'

# Cola de importación asíncrona: guarda la app para el contexto de Flask en
# los workers. Los hilos se crean de forma perezosa en la primera importación.
configurar_cola(app)

# El reintento periódico de imágenes NO se arranca aquí: un hilo creado al
# importar el módulo nace antes del fork de gunicorn y no sobrevive en
# producción. Se arranca de forma perezosa desde el primer request
# (backend/observabilidad.py -> image_backfill.asegurar_backfill_activo).

# Métricas de tráfico: se acumulan en memoria (backend/trafico.py) y se vuelcan
# a la BD desde el propio ciclo del request (after_request), sin depender de un
# hilo de fondo (en producción la creación de hilos puede fallar).


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


@app.template_filter('url_imagen_producto_miniatura')
def _filtro_url_imagen_producto_miniatura(producto):
    """Miniatura de tarjeta (400 px) con respaldo a la imagen completa."""
    try:
        return url_imagen_producto_miniatura(producto) or PLACEHOLDER_PRODUCTO
    except Exception:
        logging.getLogger('localis.images').error(
            'Filtro url_imagen_producto_miniatura fallo', exc_info=True
        )
        return PLACEHOLDER_PRODUCTO


@app.template_global()
def url_vista_previa(comercio_id):
    """URL firmada de vista previa (solo lectura) para compartir una tienda."""
    try:
        from backend.pre_cuentas import token_vista_previa

        return url_for(
            'vista_previa_comercio',
            comercio_id=comercio_id,
            token=token_vista_previa(comercio_id),
        )
    except Exception:
        return ''


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
        logger.error(f'[Localis Tasa] contexto BCV no disponible: {type(error).__name__}: {error}')
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
            logger.info('[Localis Serper] Credenciales válidas (HTTP 200).')
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
        logger.error(f'[Localis Serper] diagnóstico omitido: {type(error).__name__}: {error}')


def _calentar_ocr():
    """Precarga el modelo OCR para que el primer pago no pague ese coste.

    Corre en el hilo de inicialización (no bloquea el bind). Se desactiva con
    ``LOCALIS_OCR_WARMUP=0``.
    """
    activo = str(os.getenv('LOCALIS_OCR_WARMUP', '1')).strip().lower()
    if activo in ('0', 'false', 'no', 'off'):
        return
    # Si el OCR se delega a un worker, este contenedor no necesita el modelo
    # (ahorra CPU y, sobre todo, RAM).
    if (os.getenv('LOCALIS_OCR_SERVICE_URL') or '').strip():
        print(
            '[Localis OCR] OCR remoto configurado; no se precarga el modelo local',
            flush=True,
        )
        return
    try:
        inicio = time.perf_counter()
        from backend.payment_ocr import calentar_motor_ocr

        if calentar_motor_ocr():
            ms = (time.perf_counter() - inicio) * 1000
            print(f'[Localis OCR] motor precargado en {ms:.0f} ms', flush=True)
    except Exception as error:
        logger.error(f'[Localis OCR] warm-up omitido: {type(error).__name__}: {error}')


def _programar_tasa_automatica():
    """Mantiene la tasa de cambio oficial del BCV al día (una vez al día).

    Arranca el hilo de fondo y hace un primer intento. Nunca rompe el arranque:
    si el BCV no responde se conserva la tasa anterior.
    """
    try:
        from backend.tasa_automatica import asegurar_tasa_del_dia, programar_hilo_diario

        programar_hilo_diario()
        asegurar_tasa_del_dia()
    except Exception as error:
        logger.error(
            f'[Localis Tasa] no se pudo programar la actualización diaria: '
            f'{type(error).__name__}: {error}'
        )


def _inicializar_aplicacion():
    """Migraciones y diagnóstico fuera del hilo que importa el WSGI (Gunicorn ya puede bind)."""
    logger.info('[Localis] Inicialización en segundo plano...')
    try:
        advertencias = validar_config_arranque()
        for aviso in advertencias:
            logger.info(f'[Localis Config] {aviso}')
        if db_url:
            logger.info('Base de datos: PostgreSQL (DATABASE_URL).')
        init_db()
        # Warm-up explícito: deja el pool y la conexión verificados antes de
        # que la primera petición real pague el connect en frío del pooler.
        calentar_pool_bd()
        # Ping periódico al pool: abrir conexión cuesta ~1 s, así que mantenerlas
        # calientes evita que el primer usuario tras un rato pague el peaje.
        try:
            programar_keepalive_bd()
        except Exception:
            traceback.print_exc()
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
        _calentar_ocr()
        _programar_tasa_automatica()
        # Worker dedicado: arranca el trabajo pesado sin depender de un request.
        _arrancar_tareas_de_worker()
        # --- RESTAURAR_PAGOS_INICIO ---
        # En la fase gratuita no se expiran tiendas por suscripción al arrancar.
        if pagos_habilitados():
            verificar_vencimientos_comercios()
        # --- RESTAURAR_PAGOS_FIN ---
        logger.info('[Localis] Inicialización completada.')
    except Exception as error:
        logger.error(f'[Localis] Error al inicializar la aplicación: {error}')
        traceback.print_exc()
        try:
            from backend.diagnostics import reportar_error_critico_async

            reportar_error_critico_async(error, request=None)
        except Exception:
            traceback.print_exc()


def _es_worker_dedicado():
    """True si este contenedor es el WORKER (hace el trabajo pesado)."""
    valor = str(os.environ.get('LOCALIS_WORKER', '0')).strip().lower()
    return valor in ('1', 'true', 'yes', 'on')


def _arrancar_tareas_de_worker():
    """En un worker dedicado, inicia el trabajo pesado sin depender de requests.

    El backfill de imagenes y el reintento de pagos se arrancan de forma perezosa
    desde el primer request; un worker que recibe pocas peticiones podria no
    arrancarlos nunca. Con ``LOCALIS_WORKER=1`` se inician aqui (hilo de arranque,
    post-fork). Idempotente y nunca lanza.
    """
    if not _es_worker_dedicado():
        return
    try:
        from backend.image_backfill import iniciar_backfill_periodico

        iniciar_backfill_periodico()
        logger.info('[Localis Worker] backfill de imagenes iniciado')
    except Exception as error:
        logger.warning(f'[Localis Worker] backfill no iniciado: {error}')
    try:
        from backend.asistente.pagos import iniciar_reintento_periodico

        iniciar_reintento_periodico()
        logger.info('[Localis Worker] reintento de pagos iniciado')
    except Exception as error:
        logger.warning(f'[Localis Worker] reintento de pagos no iniciado: {error}')


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
        logger.error(f'Aviso verificación vencimientos (async): {error}')
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
        logger.error(f'Aviso sincronización de vencimientos: {error}')


@app.after_request
def agregar_cache_estaticos(response):
    """Encabezados de caché para imágenes y assets estáticos.

    Solo se marcan respuestas 200. Un 404 (p. ej. una foto de
    ``static/uploads`` que un deploy borró) no debe quedar cacheado una semana
    en el navegador del visitante ni en el CDN: eso hacía permanente un error
    temporal.
    """
    if response.status_code == 200 and request.path.startswith('/static/'):
        extensiones_cache = (
            '.webp', '.jpg', '.jpeg', '.png', '.gif', '.svg', '.css', '.js', '.woff2'
        )
        if request.path.lower().endswith(extensiones_cache):
            response.headers['Cache-Control'] = 'public, max-age=604800'
    return response


@app.after_request
def agregar_cabeceras_seguridad(response):
    """Cabeceras de seguridad HTTP (endurecimiento aditivo y reversible).

    No activa Content-Security-Policy *enforcing* por defecto porque las
    plantillas usan estilos y onclick inline. Para medir el impacto SIN riesgo:
    define ``LOCALIS_CSP_REPORT_ONLY`` con la politica y se envia como
    ``Content-Security-Policy-Report-Only`` (el navegador solo reporta, no
    bloquea). Para imponerla: ``LOCALIS_CSP``. HSTS solo en produccion.
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
    csp_report_only = (os.environ.get('LOCALIS_CSP_REPORT_ONLY') or '').strip()
    if csp_report_only:
        response.headers.setdefault(
            'Content-Security-Policy-Report-Only', csp_report_only
        )
    return response

# El cliente OAuth de Google vive en backend/oauth_google.py (lo usa el
# blueprint auth). Se inicializa aqui, tras crear la app.
from backend.oauth_google import crear_oauth

google = crear_oauth(app)


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
    try:
        from backend import trafico

        estado['trafico'] = trafico.estado()
    except Exception:
        pass
    try:
        from backend.correo import correo_configurado
        from config import SMTP_PASSWORD, SMTP_USER
        import backend.reporte_trafico as _rt

        estado['reporte_diario'] = {
            'habilitado': _rt._reporte_diario_habilitado(),
            'hora_utc': _rt._hora_objetivo_utc(),
            'correo_configurado': correo_configurado(),
            'smtp_user_presente': bool(SMTP_USER),
            'smtp_password_presente': bool(SMTP_PASSWORD),
            'iniciado': _rt._iniciado,
            'admins': len(_rt.ADMIN_EMAILS),
        }
    except Exception:
        pass
    return jsonify(estado), (200 if estado.get('ok') else 503)


@app.route('/metrics')
def metrics_prometheus():
    """Metricas Prometheus (contadores, latencia y OCR). Solo si LOCALIS_METRICS=1.

    Si ``LOCALIS_METRICS_TOKEN`` esta definido, exige ``?token=...`` o la
    cabecera ``X-Metrics-Token``: asi se puede publicar sin dejar el endpoint
    abierto. Sin token configurado se mantiene el comportamiento previo.
    """
    from backend import metricas

    if not metricas.habilitado():
        return jsonify({'ok': False, 'error': 'metricas deshabilitadas'}), 404

    token_esperado = (os.environ.get('LOCALIS_METRICS_TOKEN') or '').strip()
    if token_esperado:
        recibido = (
            request.args.get('token')
            or request.headers.get('X-Metrics-Token')
            or ''
        ).strip()
        if not hmac.compare_digest(recibido, token_esperado):
            return jsonify({'ok': False, 'error': 'no autorizado'}), 401

    return Response(
        metricas.render_prometheus(),
        mimetype='text/plain; version=0.0.4; charset=utf-8',
    )


@app.route('/tareas/reporte-trafico', methods=['GET', 'POST'])
@exento_csrf
def tarea_reporte_trafico():
    """Dispara el reporte diario de tráfico (pensado para un cron externo).

    Protegido por un token compartido (``LOCALIS_CRON_TOKEN``). Si no está
    configurado, la ruta responde 404 (no existe). Nunca lanza.
    """
    token = (os.environ.get('LOCALIS_CRON_TOKEN') or '').strip()
    if not token:
        return jsonify({'ok': False, 'error': 'no configurado'}), 404
    recibido = (
        request.headers.get('X-Cron-Token')
        or request.args.get('token')
        or ''
    ).strip()
    try:
        autorizado = hmac.compare_digest(token, recibido)
    except Exception:
        autorizado = False
    if not autorizado:
        return jsonify({'ok': False, 'error': 'no autorizado'}), 401

    from backend import trafico
    from backend.reporte_trafico import enviar_reporte_admin

    # Persistir lo pendiente para que el reporte incluya lo de hoy.
    try:
        trafico.volcar()
    except Exception as error:
        logger.error(f'[Localis] volcado previo al reporte falló: {error}')

    forzar = (request.args.get('forzar') or '').strip().lower() in (
        '1', 'true', 'yes', 'si', 'sí'
    )
    try:
        dias = max(1, int(request.args.get('dias', '1') or 1))
    except (TypeError, ValueError):
        dias = 1

    try:
        ok, detalle, _ = enviar_reporte_admin(dias=dias, forzar=forzar)
    except Exception as error:
        logger.error(f'[Localis] reporte de tráfico falló: {error}')
        return jsonify({'ok': False, 'estado': 'error', 'detalle': str(error)}), 500

    if ok:
        estado = 'enviado'
    elif 'ya enviado' in detalle:
        estado = 'omitido'
    else:
        estado = 'error'
    codigo = 200 if estado != 'error' else 502
    return jsonify({'ok': estado != 'error', 'estado': estado, 'detalle': detalle}), codigo


def _ocr_a_float(valor):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


@app.route('/tareas/ocr/<string:tipo>', methods=['POST'])
@exento_csrf
def tarea_ocr(tipo):
    """Ejecuta el OCR de comprobantes en el WORKER (servicio aparte).

    Lo usa ``backend.ocr_remoto`` cuando ``LOCALIS_OCR_SERVICE_URL`` está
    definida, para que la CPU del OCR no compita con el servidor web. Está
    protegido por token (``LOCALIS_OCR_TOKEN`` o ``LOCALIS_CRON_TOKEN``); sin
    token configurado responde 404 (no existe). Nunca lanza.
    """
    import base64

    token = (
        (os.environ.get('LOCALIS_OCR_TOKEN') or '').strip()
        or (os.environ.get('LOCALIS_CRON_TOKEN') or '').strip()
    )
    if not token:
        return jsonify({'ok': False, 'error': 'no configurado'}), 404
    recibido = (
        request.headers.get('X-Localis-Ocr-Token') or request.args.get('token') or ''
    ).strip()
    try:
        autorizado = hmac.compare_digest(token, recibido)
    except Exception:
        autorizado = False
    if not autorizado:
        return jsonify({'ok': False, 'error': 'no autorizado'}), 401

    datos = request.get_json(silent=True) or {}
    imagen_b64 = datos.get('imagen_b64') or ''
    try:
        data_bytes = base64.b64decode(imagen_b64, validate=False)
    except Exception:
        return jsonify({'ok': False, 'error': 'imagen invalida'}), 400
    if not data_bytes:
        return jsonify({'ok': False, 'error': 'imagen vacia'}), 400

    try:
        from backend.payment_ocr import (
            OCR_SEMAFORO,
            validar_comprobante_binance,
            validar_comprobante_pago_movil,
        )

        with OCR_SEMAFORO:
            if tipo == 'binance':
                resultado = validar_comprobante_binance(
                    data_bytes, _ocr_a_float(datos.get('monto_esperado_usd'))
                )
            elif tipo == 'pago-movil':
                resultado = validar_comprobante_pago_movil(
                    data_bytes,
                    _ocr_a_float(datos.get('monto_esperado_bs')),
                    banco=datos.get('banco'),
                    rif=datos.get('rif'),
                    telefono=datos.get('telefono'),
                )
            else:
                return jsonify({'ok': False, 'error': 'tipo no soportado'}), 400
    except Exception as error:
        logger.error(f'[Localis OCR] fallo en el worker: {type(error).__name__}: {error}')
        return jsonify({'ok': False, 'error': 'ocr fallo'}), 500

    return jsonify({'ok': True, 'resultado': resultado}), 200


# /api/tasa-bcv y /api/actualizar-tasa viven ahora en backend/blueprints/api.py


def inicializar_base_de_datos():
    if init_db():
        logger.info('\nBase de datos e índices verificados/actualizados.\n')
    else:
        logger.error('\nNo se pudo completar init_db().\n')


# ==========================================
# RUTAS PÚBLICAS (CLIENTE)
# ==========================================


@app.route('/')
@limite('600 per minute')
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
        # Búsqueda/filtro: se muestran TODOS los resultados (sin truncar), con
        # caché corta por término+categoría para aguantar picos de tráfico.
        productos = buscar_productos_catalogo(
            palabra_clave=palabra_clave,
            categoria_nombre=categoria,
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
            logger.error(f'[Localis] portada degradada (BD no disponible): {error}')
            productos, hay_mas = [], False
            catalogo_degradado = True
        except Exception as error:
            logger.error(f'[Localis] portada fallo: {type(error).__name__}: {error}')
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
    from backend.seo import json_ld_website

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
        seo_json_ld=json_ld_website(request.url_root),
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


def _contexto_tienda(comercio, palabra_clave, origen, solo_publicos=True, origen_debug='tienda_publica'):
    """Contexto compartido por la tienda publica y la vista previa (solo lectura)."""
    comercio_id = comercio.get('id')
    # Caché corta: ésta es la página pública más visitada y cuesta varias
    # consultas con ~0,7 s de ida y vuelta a la BD en cada visita.
    productos = productos_tienda_publica(
        comercio_id,
        palabra_clave=palabra_clave,
        solo_publicos=solo_publicos,
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

    _debug_imagenes_antes_de_render(productos, origen_debug)
    from backend.seo import json_ld_store

    return {
        'comercio': comercio,
        'productos': productos,
        'producto_seleccionado': producto_seleccionado,
        'tasa': tasa_actual,
        'q': palabra_clave,
        'origen': origen,
        'seo_json_ld': json_ld_store(comercio, request.url_root, producto_seleccionado),
    }


@app.route('/tienda/<int:comercio_id>')
@app.route('/tienda/<string:slug>')
@limite('600 per minute')
def tienda_publica(comercio_id=None, slug=None):
    if comercio_id is None and slug:
        comercio = obtener_comercio_por_slug(slug)
    else:
        comercio = obtener_comercio_por_id(comercio_id, solo_visible=True)
    if not comercio:
        flash('Tienda no encontrada o no disponible.', 'error')
        return redirect(url_for('index'))

    palabra_clave = request.args.get('q', '').strip()
    origen = (request.args.get('origen') or 'web').strip().lower()[:20]
    contexto = _contexto_tienda(comercio, palabra_clave, origen)
    return render_template('tienda_publica.html', **contexto)


@app.route('/vista-previa/<int:comercio_id>/<token>')
def vista_previa_comercio(comercio_id, token):
    """Vista previa de solo lectura de una tienda (incluidas pre-cuentas ocultas).

    Pensada para compartir con un comercio antes de transferirle la propiedad:
    la pre-cuenta no esta publica, asi que esta es la forma de mostrarla sin
    exponerla en el catalogo. Requiere el token firmado (backend/pre_cuentas.py).
    """
    from backend.pre_cuentas import token_vista_previa_valido

    if not token_vista_previa_valido(comercio_id, token):
        return (
            render_template(
                'error_servidor.html',
                codigo=404,
                titulo='Enlace no valido',
                mensaje='El enlace de vista previa no es valido o expiro.',
                sesion_activa=bool(session.get('usuario_id')),
            ),
            404,
        )

    comercio = obtener_comercio_por_id(comercio_id, solo_visible=False)
    if not comercio:
        return (
            render_template(
                'error_servidor.html',
                codigo=404,
                titulo='Tienda no encontrada',
                mensaje='La tienda no existe.',
                sesion_activa=bool(session.get('usuario_id')),
            ),
            404,
        )

    palabra_clave = request.args.get('q', '').strip()
    contexto = _contexto_tienda(
        comercio,
        palabra_clave,
        'previa',
        solo_publicos=False,
        origen_debug='vista_previa',
    )
    contexto['preview'] = True
    respuesta = make_response(render_template('tienda_publica.html', **contexto))
    # No indexar ni cachear: es una vista privada para compartir, no una pagina.
    respuesta.headers['X-Robots-Tag'] = 'noindex, nofollow'
    respuesta.headers['Cache-Control'] = 'no-store'
    return respuesta


















# api_me vive ahora en backend/blueprints/api.py




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
        logger.error(f'[Localis QR] librería no disponible: {error}')
        return Response('Código QR no disponible.', status=503)

    try:
        png = generar_qr_png(_url_tienda_externa(comercio_id, origen='qr'))
    except Exception as error:
        logger.error(f'[Localis QR] generación fallida: {error}')
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
from backend.blueprints.auth import bp as _auth_bp
from backend.blueprints.asistente import bp as _asistente_bp

app.register_blueprint(_api_bp)
app.register_blueprint(_admin_bp)
app.register_blueprint(_comercio_bp)
app.register_blueprint(_auth_bp)
app.register_blueprint(_asistente_bp)


@app.context_processor
def _inyectar_asistente():
    """Estado del asistente de IA disponible en TODAS las plantillas.

    El widget se auto-oculta si no hay clave o si se apago con
    ``LOCALIS_ASISTENTE=0``; no hay que tocar ninguna vista.
    """
    try:
        from config import ASISTENTE_WHATSAPP_URL, asistente_habilitado
        from backend.asistente import version_assets

        return {
            'asistente_habilitado': asistente_habilitado(),
            'asistente_whatsapp_url': ASISTENTE_WHATSAPP_URL,
            'asistente_asset_v': version_assets(),
        }
    except Exception:
        return {
            'asistente_habilitado': False,
            'asistente_whatsapp_url': '',
            'asistente_asset_v': '1',
        }


port = int(os.environ.get('PORT', 10000))

if __name__ == '__main__':
    inicializar_base_de_datos()
    app.run(host='0.0.0.0', port=port)

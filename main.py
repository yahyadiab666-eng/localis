import os
import time
from dotenv import load_dotenv

load_dotenv(override=True)

GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '').strip()
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '').strip()

if not GOOGLE_CLIENT_ID:
    print('Aviso: GOOGLE_CLIENT_ID no configurado en .env')
else:
    print('Google OAuth configurado correctamente.')

from functools import wraps

from backend.supabase_client import SUPABASE_BUCKET_IMAGENES, obtener_cliente_storage, supabase, supabase_api_habilitado
from backend.supabase_storage import (
    SupabaseUploadError,
    subir_bytes_a_supabase,
    subir_imagen_a_supabase,
)
if supabase_api_habilitado():
    print('Supabase Storage configurado correctamente.')
    if not obtener_cliente_storage():
        print(
            'Aviso: no hay cliente Storage para subidas; se usará respaldo local '
            'en static/uploads/.'
        )
else:
    print(
        'Aviso: Supabase Storage no disponible; las subidas manuales usarán '
        'respaldo local en static/uploads/.'
    )

import sqlite3

import psycopg2
from authlib.integrations.flask_client import OAuth
from config import (
    DEFAULT_BANNER_URL,
    MAX_UPLOAD_BYTES,
    WHATSAPP_SOPORTE,
    WHATSAPP_SOPORTE_URL,
    aplicar_config_sesion_flask,
    obtener_secret_key,
    validar_config_arranque,
)
from flask import (
    Flask,
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
    actualizar_banner_principal,
    actualizar_tasa_dolar,
    cambiar_plan_comercio,
    cambiar_visibilidad_comercio,
    confirmar_pago_suscripcion,
    eliminar_comercio_definitivo,
    obtener_banner_principal,
    obtener_bandeja_tecnica,
    obtener_todos_comercios_admin,
    reactivar_comercio,
    resolver_ticket_soporte,
    suspender_comercio_temporal,
)
from backend.auth import obtener_o_crear_usuario_google
from backend.diagnostics import ejecutar_diagnostico_inicio, obtener_estado_sistema
from backend.error_handlers import registrar_manejadores_errores
from backend.image_lookup import (
    imagen_url_para_catalogo,
    imagen_urls_para_catalogo,
    obtener_imagen_url_producto,
    resolver_imagen_url_definitiva,
)
from backend.db import get_db_connection
from database import init_db, normalize_database_url
from backend.plans import PLANES, MENSAJE_LIMITE_PRODUCTOS, es_limite_ilimitado, obtener_beneficios_plan
from backend.payment_ocr import validar_comprobante_pago_movil
from backend.subscriptions import (
    activar_suscripcion_por_comprobante,
    calcular_cotizacion_cambio_plan,
    calcular_monto_pago_plan,
    comercio_puede_gestionar_inventario,
    contar_productos_comercio,
    marcar_bienvenida_vista,
    obtener_avisos_suscripcion,
    obtener_datos_pago_movil,
    obtener_limite_productos_comercio,
    programar_downgrade_plan,
    puede_agregar_producto,
    rechazar_renovacion_vencida,
    verificar_vencimiento_comercio,
    verificar_vencimientos_comercios,
)
from backend.utils import (
    formatear_fecha,
    imagen_url_almacenada,
    imagen_url_para_persistir,
    normalizar_codigo_barras,
    normalizar_telefono_whatsapp,
    parsear_precio_form,
    parsear_entero_form,
    parsear_visible_form,
    url_estatica_existe,
    url_maps_comercio,
    url_banner_principal,
    url_whatsapp_comercio,
    normalizar_url_imagen,
)
from backend.session_comercio import (
    asegurar_contexto_comercio,
    destino_panel_usuario,
    limpiar_contexto_comercio,
    vincular_comercio_en_sesion,
)
from backend.stores import (
    actualizar_datos_comercio,
    actualizar_producto,
    buscar_y_filtrar_productos,
    eliminar_producto,
    obtener_comercio_por_id,
    obtener_comercio_por_usuario,
    obtener_config,
    obtener_producto_publico,
    obtener_tasa_dolar,
    procesar_csv_productos,
    registrar_comercio_completo,
)

app = Flask(__name__)

# Asegurar limpieza directa en el entorno de Flask
db_url = normalize_database_url(os.environ.get('DATABASE_URL', ''))
app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'postgresql://localhost/localis'

db = SQLAlchemy(app)

app.secret_key = obtener_secret_key()
aplicar_config_sesion_flask(app)

app.config['SUPABASE_CLIENT'] = supabase
app.config['SUPABASE_BUCKET_IMAGENES'] = SUPABASE_BUCKET_IMAGENES
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES

csrf = CSRFProtect(app)
registrar_manejadores_errores(app)


@app.template_filter('fecha_corta')
def _filtro_fecha_corta(valor):
    return formatear_fecha(valor) or '—'

DEFAULT_BANNER = DEFAULT_BANNER_URL


def _inicializar_aplicacion():
    """Migraciones, validación de entorno, diagnóstico PostgreSQL y vencimientos."""
    try:
        advertencias = validar_config_arranque()
        for aviso in advertencias:
            print(f'[Localis Config] {aviso}')
        if db_url:
            print('Base de datos: PostgreSQL (DATABASE_URL).')
        init_db()
        ejecutar_diagnostico_inicio()
        verificar_vencimientos_comercios()
    except RuntimeError as error:
        print(f'Error crítico de configuración: {error}')
        raise
    except Exception as error:
        print(f'Error al inicializar la aplicación: {error}')
        from backend.diagnostics import reportar_error_critico

        reportar_error_critico(error, request=None)


_inicializar_aplicacion()

_ultima_verificacion_vencimientos_global = 0.0
_INTERVALO_VENCIMIENTOS_SEG = 300
_INTERVALO_VENCIMIENTO_COMERCIO_SEG = 600


@app.before_request
def sincronizar_vencimientos_suscripcion():
    """Revisa vencimientos globalmente (cada 5 min) y por comercio en pagos (cada 10 min)."""
    global _ultima_verificacion_vencimientos_global

    try:
        ahora = time.time()
        if ahora - _ultima_verificacion_vencimientos_global >= _INTERVALO_VENCIMIENTOS_SEG:
            verificar_vencimientos_comercios()
            _ultima_verificacion_vencimientos_global = ahora

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
    """URL de logo/banner usable en plantillas; descarta /static/ inexistentes."""
    url = imagen_url_almacenada(valor) or normalizar_url_imagen(valor)
    if not url:
        return None
    if url.startswith('/static/') and not url_estatica_existe(url):
        return None
    return url


def _normalizar_imagenes_comercio(comercio):
    """Normaliza logo, banner y fechas para plantillas (PostgreSQL datetime)."""
    if not comercio:
        return comercio
    comercio = dict(comercio)

    comercio['logo_completo'] = _url_imagen_comercio_usable(comercio.get('logo_url'))

    banner = comercio.get('banner_url') or comercio.get('imagen_portada')
    comercio['banner_completo'] = _url_imagen_comercio_usable(banner)

    comercio['tiene_banner'] = bool(comercio.get('banner_completo'))
    for campo in (
        'fecha_vencimiento',
        'fecha_registro',
        'fecha_inicio_suscripcion',
    ):
        if comercio.get(campo) is not None:
            comercio[campo] = formatear_fecha(comercio[campo])

    visible = comercio.get('visible', 1)
    if isinstance(visible, bool):
        comercio['visible'] = 1 if visible else 0
    else:
        try:
            comercio['visible'] = int(visible)
        except (TypeError, ValueError):
            comercio['visible'] = 1
    return comercio


def _normalizar_logo_completo(comercio):
    return _normalizar_imagenes_comercio(comercio)


def procesar_imagen_subida(
    file_storage,
    prefijo,
    carpeta='comercios',
    max_dimension=800,
):
    """
    Subida manual: Supabase Storage con respaldo automático en static/uploads/.
    Retorna URL pública o ruta /static/uploads/...; lanza SupabaseUploadError solo
    si fallan validación, compresión y disco local.
    """
    if not file_storage or not getattr(file_storage, 'filename', ''):
        return None

    return subir_imagen_a_supabase(
        file_storage,
        prefijo=prefijo,
        carpeta=carpeta,
        max_dimension=max_dimension,
    )


def procesar_logo_comercio(file_storage, prefijo):
    """Logo de comercio → bucket Supabase comercios/."""
    return procesar_imagen_subida(
        file_storage,
        prefijo=prefijo,
        carpeta='comercios',
    )


def procesar_imagen_para_producto(
    file_storage, codigo_barras, nombre, descripcion, comercio_id
):
    """Subida manual de archivo al bucket Supabase productos/."""
    if file_storage and getattr(file_storage, 'filename', ''):
        url = procesar_imagen_subida(
            file_storage,
            prefijo=f'manual_{comercio_id}',
            carpeta='productos',
        )
        if url:
            return url
    return None


def _comercio_sesion_validado():
    """Comercio activo validado contra PostgreSQL (HTML y API)."""
    usuario_id = session.get('usuario_id')
    asegurar_contexto_comercio(usuario_id)
    return obtener_comercio_por_usuario(usuario_id)


def _requiere_comercio():
    """Resuelve el comercio del usuario autenticado (primer comercio asociado)."""
    usuario_id = session.get('usuario_id')
    comercio = obtener_comercio_por_usuario(usuario_id)
    if not comercio:
        flash('Debes registrar un comercio primero.', 'error')
        return None, redirect(url_for('panel_comercio'))
    vincular_comercio_en_sesion(comercio['id'])
    return comercio, None


def _sesion_usuario_activa():
    """Verifica que el usuario de la sesión siga existiendo en PostgreSQL."""
    usuario_id = session.get('usuario_id')
    if not usuario_id:
        return False
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute('SELECT id FROM usuarios WHERE id = ?', (usuario_id,))
            return cursor.fetchone() is not None
    except psycopg2.Error:
        raise
    except Exception:
        return False


def login_requerido(f):

    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Debes iniciar sesión para acceder a esta sección.', 'error')
            return redirect(url_for('login'))
        if not _sesion_usuario_activa():
            session.clear()
            flash(
                'Tu sesión expiró o ya no es válida. Inicia sesión nuevamente.',
                'error',
            )
            return redirect(url_for('login'))
        asegurar_contexto_comercio(session.get('usuario_id'))
        return f(*args, **kwargs)

    return decorada


def login_requerido_api(f):

    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario_id' not in session:
            return jsonify({'error': 'Debes iniciar sesión.'}), 401
        if not _sesion_usuario_activa():
            session.clear()
            return jsonify({'error': 'Sesión inválida o expirada.'}), 401
        asegurar_contexto_comercio(session.get('usuario_id'))
        return f(*args, **kwargs)

    return decorada


def admin_requerido(f):

    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario_id' not in session or not session.get('es_admin'):
            flash(
                'Acceso denegado. Se requieren permisos de administrador.',
                'error',
            )
            return redirect(url_for('login'))
        if not _sesion_usuario_activa():
            session.clear()
            flash('Tu sesión expiró. Inicia sesión nuevamente.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorada


def admin_requerido_api(f):

    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario_id' not in session or not session.get('es_admin'):
            return jsonify({'ok': False, 'mensaje': 'Acceso denegado.'}), 403
        if not _sesion_usuario_activa():
            session.clear()
            return jsonify({'ok': False, 'mensaje': 'Sesión inválida o expirada.'}), 401
        return f(*args, **kwargs)

    return decorada


def _bloquear_gestion_inventario(comercio, redirect_url='panel_comercio'):
    ok, mensaje = comercio_puede_gestionar_inventario(comercio['id'])
    if ok:
        return None
    flash(mensaje, 'error')
    return redirect(url_for('comercio_planes', abrir_pago='pro'))


@app.errorhandler(CSRFError)
def error_csrf(e):
    flash(
        'Tu sesión de formulario expiró o la petición no es segura. Inténtalo de nuevo.',
        'error',
    )
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Token CSRF inválido o expirado.'}), 400
    return redirect(request.referrer or url_for(destino_panel_usuario()))


@app.errorhandler(RequestEntityTooLarge)
def error_archivo_demasiado_grande(e):
    max_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
    mensaje = f'El archivo supera el tamaño máximo permitido ({max_mb} MB).'

    if request.path.startswith('/api/'):
        return jsonify({'error': mensaje}), 413

    flash(mensaje, 'error')
    return redirect(request.referrer or url_for(destino_panel_usuario()))


@app.route('/health')
def health_check():
    """Diagnóstico automático para monitoreo (Render, uptime, etc.)."""
    estado = obtener_estado_sistema()
    codigo = 200 if estado.get('ok') else 503
    return jsonify(estado), codigo


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

    productos = buscar_y_filtrar_productos(
        palabra_clave=palabra_clave,
        categoria_nombre=categoria,
        limit=None if hay_filtros else 30,
        orden_aleatorio=not hay_filtros,
    )

    from backend.stores import obtener_configs

    configs = obtener_configs({
        'tasa_dolar': '36.50',
        'whatsapp_soporte': WHATSAPP_SOPORTE,
        'banner_principal': DEFAULT_BANNER,
    })
    try:
        tasa_actual = float(configs['tasa_dolar'])
    except (TypeError, ValueError):
        tasa_actual = obtener_tasa_dolar() or 1.0
    banner_url = url_banner_principal(configs['banner_principal'], default=DEFAULT_BANNER)
    whatsapp = configs['whatsapp_soporte']

    return render_template(
        'cliente.html',
        productos=productos,
        tasa=tasa_actual,
        q=palabra_clave,
        categoria_actual=categoria,
        banner_url=banner_url,
        default_banner=DEFAULT_BANNER,
        whatsapp=whatsapp,
        whatsapp_url=WHATSAPP_SOPORTE_URL,
    )


@app.route('/api/producto/<int:producto_id>')
def api_producto(producto_id):
    producto = obtener_producto_publico(producto_id)
    if not producto:
        return jsonify({'error': 'Producto no encontrado'}), 404
    return jsonify(producto)


@app.route('/imagen-producto')
def imagen_producto_respaldo():
    """Devuelve la URL exacta guardada en BD para un producto (sin cruzar catálogo)."""
    producto_id = request.args.get('producto_id', type=int)
    if not producto_id:
        return ('', 404)
    url = obtener_imagen_url_producto(producto_id)
    if url:
        return redirect(url)
    return ('', 404)


@app.route('/tienda/<int:comercio_id>')
def tienda_publica(comercio_id):
    comercio = obtener_comercio_por_id(comercio_id, solo_visible=True)
    if not comercio:
        flash('Tienda no encontrada o no disponible.', 'error')
        return redirect(url_for('index'))

    palabra_clave = request.args.get('q', '').strip()
    productos = buscar_y_filtrar_productos(
        palabra_clave=palabra_clave,
        comercio_id=comercio_id,
    )
    tasa_actual = obtener_tasa_dolar() or 1.0

    comercio = _normalizar_imagenes_comercio(dict(comercio))

    comercio['whatsapp_url'] = url_whatsapp_comercio(
        comercio.get('telefono'),
        'Hola, vi tu tienda en Localis',
    )
    comercio['whatsapp_numero'] = normalizar_telefono_whatsapp(comercio.get('telefono'))
    comercio['maps_link'] = url_maps_comercio(comercio)

    return render_template(
        'tienda_publica.html',
        comercio=comercio,
        productos=productos,
        tasa=tasa_actual,
        q=palabra_clave,
    )


@app.route('/login')
def login():
    if session.get('usuario_id'):
        if session.get('es_admin'):
            return redirect(url_for('panel_admin'))
        return redirect(url_for('panel_comercio'))
    return render_template('login.html')


@app.route('/registro')
def registro_legacy():
    """Ruta legacy: el registro de comercio pasa por login Google + /comercio/crear."""
    return redirect(url_for('login'))


@app.route('/login/google')
def login_google():
    try:
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            flash('Inicio de sesión con Google no está configurado.', 'error')
            return redirect(url_for('login'))
        if not google:
            flash('No se pudo inicializar el cliente OAuth de Google.', 'error')
            return redirect(url_for('login'))
        redirect_uri = url_for('google_callback', _external=True)
        return google.authorize_redirect(redirect_uri)
    except Exception as error:
        flash(f'No se pudo iniciar sesión con Google: {error}', 'error')
        return redirect(url_for('login'))


@app.route('/login/google/callback')
def google_callback():
    try:
        if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
            flash('Inicio de sesión con Google no está configurado.', 'error')
            return redirect(url_for('login'))
        if not google:
            flash('No se pudo inicializar el cliente OAuth de Google.', 'error')
            return redirect(url_for('login'))

        token = google.authorize_access_token()
        google_info = token.get('userinfo') if token else None

        exito, usuario_o_error = obtener_o_crear_usuario_google(google_info)

        if not exito or not isinstance(usuario_o_error, dict) or not usuario_o_error.get('id'):
            flash(
                f'Error en inicio de sesión con Google: {usuario_o_error}',
                'error',
            )
            return redirect(url_for('login'))

        session['usuario_id'] = usuario_o_error['id']
        session['username'] = usuario_o_error['nombre']
        session['correo'] = usuario_o_error['correo']
        session['foto_url'] = usuario_o_error.get('foto_url', '')
        session['rol'] = usuario_o_error.get('rol', 'comerciante')
        session['es_admin'] = usuario_o_error.get('rol') == 'admin'

        flash(f"¡Bienvenido, {usuario_o_error['nombre']}!", 'exito')

        if usuario_o_error.get('rol') == 'admin':
            return redirect(url_for('panel_admin'))
        if usuario_o_error.get('rol') == 'comerciante':
            comercio = obtener_comercio_por_usuario(usuario_o_error['id'])
            if comercio:
                vincular_comercio_en_sesion(comercio['id'])
            return redirect(url_for('panel_comercio'))

        return redirect(url_for('index'))
    except Exception as error:
        flash(f'Error en inicio de sesión con Google: {error}', 'error')
        return redirect(url_for('login'))


@app.route('/logout')
def logout():
    limpiar_contexto_comercio()
    session.clear()
    flash('Sesión cerrada correctamente.', 'info')
    return redirect(url_for('index'))


# ==========================================
# RUTAS DE COMERCIO
# ==========================================


def _productos_desde_filas(productos_db, tasa_actual):
    productos = []
    for p in productos_db:
        try:
            precio_usd = float(p['precio_usd'] or 0)
        except (TypeError, ValueError):
            precio_usd = 0.0
        productos.append({
            'id': p['id'],
            'nombre': p['nombre'],
            'descripcion': p['descripcion'] or 'Sin descripción',
            'precio_usd': precio_usd,
            'precio_bs': round(precio_usd * tasa_actual, 2),
            'codigo_barras': p['codigo_barras'] or '',
            'imagen_url': p.get('imagen_url'),
        })
    imagen_urls_para_catalogo(productos)
    return productos


def _planes_beneficios_para_comercio(comercio, tasa_actual):
    planes_beneficios = {}
    for codigo in ('basica', 'pro', 'business'):
        info = obtener_beneficios_plan(codigo)
        cotizacion = calcular_cotizacion_cambio_plan(comercio, codigo, tasa=tasa_actual)
        if cotizacion:
            info['monto_bs'] = cotizacion['monto_bs']
            info['monto_usd'] = cotizacion['monto_usd']
            info['tasa'] = cotizacion['tasa']
            info['tipo_cambio'] = cotizacion['tipo_cambio']
            info['requiere_pago'] = cotizacion['requiere_pago']
            info['mensaje_cambio'] = cotizacion.get('mensaje')
        planes_beneficios[codigo] = info
    return planes_beneficios


def _cargar_datos_comercio_usuario(usuario_id):
    """Carga comercio y productos del usuario autenticado."""
    tasa_actual = obtener_tasa_dolar() or 1.0
    with get_db_connection(row_factory=sqlite3.Row) as conn:
        cursor = conn.cursor()
        cursor.execute(
            '''
            SELECT c.*, cat.nombre as categoria
            FROM comercios c
            LEFT JOIN categorias cat ON c.categoria_id = cat.id
            WHERE c.usuario_id = ?
            ''',
            (usuario_id,),
        )
        comercio_db = cursor.fetchone()
        if not comercio_db:
            cursor.execute('SELECT id, nombre FROM categorias')
            categorias = [dict(c) for c in cursor.fetchall()]
            return None, None, tasa_actual, categorias

        vincular_comercio_en_sesion(comercio_db['id'])
        cursor.execute(
            '''
            SELECT id, nombre, descripcion, precio_usd, codigo_barras, imagen_url
            FROM productos WHERE comercio_id = ?
            ORDER BY id DESC
            ''',
            (comercio_db['id'],),
        )
        productos_db = cursor.fetchall()

    comercio = _normalizar_imagenes_comercio(dict(comercio_db))
    comercio['maps_link'] = url_maps_comercio(comercio)
    productos = _productos_desde_filas(productos_db, tasa_actual)
    return comercio, productos, tasa_actual, None


@app.route('/comercio')
@login_requerido
def panel_comercio():
    usuario_id = session.get('usuario_id')
    abrir_pago = request.args.get('abrir_pago')
    if abrir_pago:
        return redirect(url_for('comercio_planes', abrir_pago=abrir_pago))

    try:
        comercio, productos, tasa_actual, categorias = _cargar_datos_comercio_usuario(
            usuario_id
        )
        if categorias is not None:
            return render_template('registro_comercio.html', categorias=categorias)

        plan_info = PLANES.get(comercio.get('plan_tipo', 'gratis'), PLANES['gratis'])
        avisos = obtener_avisos_suscripcion(comercio)

        return render_template(
            'comercio.html',
            comercio=comercio,
            productos=productos,
            tasa=tasa_actual,
            whatsapp=obtener_config('whatsapp_soporte', WHATSAPP_SOPORTE),
            whatsapp_url=WHATSAPP_SOPORTE_URL,
            plan_info=plan_info,
            avisos=avisos,
            nav_activo='panel',
        )
    except psycopg2.Error:
        raise
    except Exception as error:
        print(f'Error al cargar panel de comercio: {error}')
        flash(
            'No se pudo cargar el panel del comercio. Intenta de nuevo en unos segundos.',
            'error',
        )
        return redirect(url_for('panel_comercio'))


@app.route('/comercio/planes')
@login_requerido
def comercio_planes():
    usuario_id = session.get('usuario_id')
    try:
        comercio, productos, tasa_actual, categorias = _cargar_datos_comercio_usuario(
            usuario_id
        )
        if categorias is not None:
            return redirect(url_for('panel_comercio'))

        plan_info = PLANES.get(comercio.get('plan_tipo', 'gratis'), PLANES['gratis'])
        avisos = obtener_avisos_suscripcion(comercio)
        pago_movil = obtener_datos_pago_movil()
        planes_beneficios = _planes_beneficios_para_comercio(comercio, tasa_actual)

        return render_template(
            'comercio_planes.html',
            comercio=comercio,
            productos=productos,
            tasa=tasa_actual,
            plan_info=plan_info,
            avisos=avisos,
            pago_movil=pago_movil,
            planes=PLANES,
            planes_beneficios=planes_beneficios,
            abrir_pago=request.args.get('abrir_pago'),
            nav_activo='planes',
        )
    except psycopg2.Error:
        raise
    except Exception as error:
        print(f'Error al cargar planes del comercio: {error}')
        flash('No se pudo cargar la página de planes.', 'error')
        return redirect(url_for('panel_comercio'))


@app.route('/comercio/panel')
@login_requerido
def panel_comercio_legacy():
    """Compatibilidad con enlaces antiguos a /comercio/panel."""
    return redirect(url_for('panel_comercio'))


@app.route('/comercio/seleccionar', methods=['GET', 'POST'])
@login_requerido
def comercio_seleccionar_legacy():
    """Hub de selección eliminado: redirige al panel directo."""
    return redirect(url_for('panel_comercio'))


@app.route('/comercio/registrar', methods=['GET', 'POST'])
@login_requerido
def registrar_comercio_legacy():
    """Compatibilidad con la ruta /comercio/registrar."""
    return redirect(url_for('crear_comercio'))


@app.route('/comercio/crear', methods=['GET', 'POST'])
@login_requerido
def crear_comercio():
    try:
        if request.method == 'GET':
            with get_db_connection(row_factory=sqlite3.Row) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT id, nombre FROM categorias')
                categorias = [dict(c) for c in cursor.fetchall()]
            return render_template('registro_comercio.html', categorias=categorias)

        usuario_id = session.get('usuario_id')
        nombre = request.form.get('nombre', '').strip()
        descripcion = request.form.get('descripcion', '').strip()
        telefono = request.form.get('telefono', '').strip()
        direccion = request.form.get('direccion', '').strip()
        ciudad = request.form.get('ciudad', '').strip()
        zona = request.form.get('zona', '').strip()
        maps_url = request.form.get('maps_url', '').strip()
        documento_identidad = request.form.get('documento_identidad', '').strip()
        categoria_raw = request.form.get('categoria_id')
        if not categoria_raw or not str(categoria_raw).strip().isdigit():
            flash('Debes seleccionar una categoría válida.', 'error')
            return redirect(url_for('crear_comercio'))
        categoria_id = int(categoria_raw)
        logo_archivo = request.files.get('logo')

        logo_url = None
        if logo_archivo and logo_archivo.filename:
            try:
                logo_url = procesar_logo_comercio(
                    logo_archivo, prefijo=f'logo_nuevo_{usuario_id}'
                )
            except SupabaseUploadError as error:
                flash(str(error), 'error')
                return redirect(url_for('crear_comercio'))

        exito, resultado = registrar_comercio_completo(
            usuario_id,
            nombre,
            descripcion,
            telefono,
            direccion,
            categoria_id,
            logo_url=logo_url,
            ciudad=ciudad or None,
            zona=zona or None,
            maps_url=maps_url or None,
            documento_identidad=documento_identidad or None,
        )

        if exito:
            flash(
                'Comercio registrado con éxito. Tienes 30 días de prueba gratuita.',
                'exito',
            )
        else:
            flash(resultado, 'error')
        return redirect(url_for('panel_comercio'))
    except psycopg2.Error:
        raise
    except Exception as error:
        print(f'Error al registrar comercio: {error}')
        flash('No se pudo registrar el comercio. Intenta de nuevo.', 'error')
        return redirect(url_for('panel_comercio'))


@app.route('/comercio/editar', methods=['GET', 'POST'])
@login_requerido
def editar_comercio():
    comercio, redireccion = _requiere_comercio()
    if redireccion:
        return redireccion

    usuario_id = session.get('usuario_id')

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        telefono = request.form.get('telefono', '').strip()
        direccion = request.form.get('direccion', '').strip()
        descripcion = request.form.get('descripcion', '').strip()
        ciudad = request.form.get('ciudad', '').strip()
        zona = request.form.get('zona', '').strip()
        maps_url = request.form.get('maps_url', '').strip()
        logo_archivo = request.files.get('logo')

        logo_url = None
        if logo_archivo and logo_archivo.filename:
            try:
                logo_url = procesar_logo_comercio(
                    logo_archivo, prefijo=f'logo_{comercio["id"]}'
                )
            except SupabaseUploadError as error:
                flash(str(error), 'error')
                return redirect(url_for('editar_comercio'))

        if not nombre:
            flash('El nombre del comercio es obligatorio.', 'error')
            return redirect(url_for('editar_comercio'))

        exito, mensaje = actualizar_datos_comercio(
            comercio['id'],
            nombre,
            telefono,
            direccion,
            descripcion=descripcion,
            ciudad=ciudad,
            zona=zona,
            maps_url=maps_url,
            logo_url=logo_url,
        )
        flash(mensaje, 'exito' if exito else 'error')
        return redirect(url_for('panel_comercio'))

    return render_template(
        'editar_comercio.html',
        comercio=_normalizar_imagenes_comercio(comercio),
        nav_activo='editar',
    )


@app.route('/comercio/producto/nuevo', methods=['GET', 'POST'])
@login_requerido
def nuevo_producto():
    comercio, redireccion = _requiere_comercio()
    if redireccion:
        return redireccion

    bloqueo = _bloquear_gestion_inventario(comercio)
    if bloqueo:
        return bloqueo

    if request.method == 'POST':
        nombre = request.form.get('nombre')
        descripcion = request.form.get('descripcion')
        precio_raw = request.form.get('precio_usd')
        codigo_barras = normalizar_codigo_barras(request.form.get('codigo_barras'))
        imagen_archivo = request.files.get('imagen')

        precio_usd, error_precio = parsear_precio_form(precio_raw)
        if not nombre or not nombre.strip():
            flash('El nombre es obligatorio.', 'error')
            return redirect(url_for('nuevo_producto'))
        if error_precio:
            flash(error_precio, 'error')
            return redirect(url_for('nuevo_producto'))

        ok, msg_limite = puede_agregar_producto(comercio['id'])
        if not ok:
            flash(msg_limite, 'error')
            destino = url_for('panel_comercio')
            if 'vencido' in msg_limite.lower() or 'límite' in msg_limite.lower():
                destino = url_for('comercio_planes', abrir_pago='pro')
            return redirect(destino)

        try:
            imagen_subida = None
            if imagen_archivo and getattr(imagen_archivo, 'filename', ''):
                imagen_subida = procesar_imagen_para_producto(
                    imagen_archivo,
                    codigo_barras,
                    nombre,
                    descripcion,
                    comercio_id=comercio['id'],
                )
                imagen_url = imagen_url_para_persistir(imagen_subida)
                if not imagen_url:
                    flash(
                        'La imagen no se pudo subir o Supabase no devolvió una URL pública válida.',
                        'error',
                    )
                    return redirect(url_for('nuevo_producto'))
            else:
                imagen_url = None

            if not imagen_url:
                imagen_url = resolver_imagen_url_definitiva(
                    None,
                    codigo_barras,
                )
        except SupabaseUploadError as error:
            flash(str(error), 'error')
            return redirect(url_for('nuevo_producto'))

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    INSERT INTO productos (comercio_id, nombre, descripcion, precio_usd, codigo_barras, imagen_url)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        comercio['id'],
                        nombre,
                        descripcion,
                        float(precio_usd),
                        codigo_barras,
                        imagen_url,
                    ),
                )
            flash('Producto agregado con éxito.', 'exito')
            return redirect(url_for('panel_comercio'))
        except Exception as e:
            flash(f'Error al agregar producto: {str(e)}', 'error')

    return render_template('nuevo_producto.html')


@app.route('/comercio/producto/editar/<int:producto_id>', methods=['GET', 'POST'])
@login_requerido
def editar_producto(producto_id):
    comercio, redireccion = _requiere_comercio()
    if redireccion:
        return redireccion

    comercio_id = comercio['id']

    bloqueo = _bloquear_gestion_inventario(comercio)
    if bloqueo:
        return bloqueo

    if request.method == 'POST':
        nombre = request.form.get('nombre')
        precio_raw = request.form.get('precio_usd')
        descripcion = request.form.get('descripcion')
        codigo_barras = normalizar_codigo_barras(request.form.get('codigo_barras'))
        imagen_archivo = request.files.get('imagen')

        precio_usd, error_precio = parsear_precio_form(precio_raw)
        if not nombre or not nombre.strip():
            flash('El nombre es obligatorio.', 'error')
            return redirect(url_for('editar_producto', producto_id=producto_id))
        if error_precio:
            flash(error_precio, 'error')
            return redirect(url_for('editar_producto', producto_id=producto_id))

        try:
            imagen_url = None
            incluir_imagen = bool(
                imagen_archivo and getattr(imagen_archivo, 'filename', '')
            )
            if incluir_imagen:
                try:
                    imagen_url = procesar_imagen_para_producto(
                        imagen_archivo,
                        codigo_barras,
                        nombre,
                        descripcion,
                        comercio_id=comercio_id,
                    )
                except SupabaseUploadError as error:
                    flash(str(error), 'error')
                    return redirect(
                        url_for('editar_producto', producto_id=producto_id)
                    )
                imagen_persistida = imagen_url_para_persistir(imagen_url)
                if not imagen_persistida:
                    flash(
                        'La imagen no se pudo subir o Supabase no devolvió una URL pública válida.',
                        'error',
                    )
                    return redirect(
                        url_for('editar_producto', producto_id=producto_id)
                    )
                imagen_url = imagen_persistida

            exito, mensaje = actualizar_producto(
                producto_id,
                comercio_id,
                nombre.strip(),
                descripcion,
                precio_usd,
                codigo_barras=codigo_barras,
                imagen_url=imagen_url,
                incluir_imagen=incluir_imagen,
            )
            flash(mensaje, 'exito' if exito else 'error')
            return redirect(url_for('panel_comercio'))
        except Exception as error:
            flash(f'Error al actualizar producto: {error}', 'error')
            return redirect(url_for('editar_producto', producto_id=producto_id))

    try:
        with get_db_connection(row_factory=sqlite3.Row) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT * FROM productos WHERE id = ? AND comercio_id = ?',
                (producto_id, comercio_id),
            )
            producto_row = cursor.fetchone()
    except Exception as error:
        flash(f'Error al cargar el producto: {error}', 'error')
        return redirect(url_for('panel_comercio'))

    if not producto_row:
        flash('El producto no existe o no tienes permiso para modificarlo.', 'error')
        return redirect(url_for('panel_comercio'))

    return render_template('nuevo_producto.html', producto=dict(producto_row))


@app.route('/comercio/producto/eliminar/<int:producto_id>', methods=['POST'])
@login_requerido
def eliminar_producto_ruta(producto_id):
    comercio, redireccion = _requiere_comercio()
    if redireccion:
        return redireccion

    bloqueo = _bloquear_gestion_inventario(comercio)
    if bloqueo:
        return bloqueo

    exito, mensaje = eliminar_producto(producto_id, comercio['id'])
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('panel_comercio'))


@app.route('/comercio/productos/cargar-csv', methods=['POST'])
@login_requerido
def cargar_csv():
    comercio, redireccion = _requiere_comercio()
    if redireccion:
        return redireccion

    bloqueo = _bloquear_gestion_inventario(comercio)
    if bloqueo:
        return bloqueo

    archivo = request.files.get('archivo_csv')
    exito, mensaje, meta = procesar_csv_productos(comercio['id'], archivo)

    if not exito and meta and meta.get('plan_sugerido'):
        flash(mensaje, 'limite_plan')
        return redirect(
            url_for(
                'comercio_planes',
                abrir_pago=meta['plan_sugerido'],
            )
        )

    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('panel_comercio'))


@app.route('/comercio/productos/cargar-csv', methods=['GET'])
@login_requerido
def cargar_csv_get():
    """Evita 405 si el usuario recarga la URL del POST de importación."""
    flash('Selecciona un archivo CSV o Excel desde el panel de comercio.', 'info')
    return redirect(url_for('panel_comercio'))


@app.route('/comercio/suscripcion/marcar-bienvenida', methods=['POST'])
@login_requerido
def suscripcion_marcar_bienvenida():
    comercio = _comercio_sesion_validado()
    if comercio:
        marcar_bienvenida_vista(comercio['id'])
    return redirect(url_for('panel_comercio'))


@app.route('/comercio/suscripcion/rechazar-vencido', methods=['POST'])
@login_requerido
def suscripcion_rechazar_vencido():
    comercio, redireccion = _requiere_comercio()
    if redireccion:
        return redireccion

    exito, mensaje = rechazar_renovacion_vencida(comercio['id'])
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('panel_comercio'))


@app.route('/comercio/suscripcion/solicitar-pago', methods=['GET', 'POST'])
@login_requerido
def suscripcion_solicitar_pago():
    """
    Ruta legacy conservada por compatibilidad.
    Redirige al panel de comercio y abre el modal de pago OCR automático.
    """
    comercio, redireccion = _requiere_comercio()
    if redireccion:
        return redireccion

    plan_tipo = (
        request.form.get('plan_tipo')
        or request.args.get('plan_tipo')
        or 'basica'
    ).lower()
    if plan_tipo not in PLANES or plan_tipo == 'gratis':
        plan_tipo = 'basica'

    flash(
        'Realiza el pago móvil y sube la captura del comprobante en el modal. '
        'El sistema validará referencia, monto y datos oficiales con OCR.',
        'info',
    )
    return redirect(url_for('comercio_planes', abrir_pago=plan_tipo))


# ==========================================
# API REST (JSON)
# ==========================================


@app.route('/api/productos/crear', methods=['POST'])
@login_requerido_api
def api_crear_producto():
    comercio = _comercio_sesion_validado()
    if not comercio:
        return jsonify({'error': 'Selecciona un comercio válido en tu cuenta.'}), 403

    tienda_id = comercio['id']
    limite = obtener_limite_productos_comercio(tienda_id)
    total_productos = contar_productos_comercio(tienda_id)

    if not es_limite_ilimitado(limite) and total_productos >= limite:
        return jsonify({'error': MENSAJE_LIMITE_PRODUCTOS}), 400

    ok_estado, msg_estado = puede_agregar_producto(tienda_id)
    if not ok_estado:
        return jsonify({'error': msg_estado}), 400

    nombre = (request.form.get('nombre') or '').strip()
    descripcion = (request.form.get('descripcion') or '').strip()
    precio_raw = request.form.get('precio_usd')
    codigo_barras = normalizar_codigo_barras(request.form.get('codigo_barras'))
    imagen_archivo = request.files.get('imagen')

    if not nombre or not nombre.strip():
        return jsonify({'error': 'El nombre es obligatorio.'}), 400

    precio_usd, error_precio = parsear_precio_form(precio_raw)
    if error_precio:
        return jsonify({'error': error_precio}), 400

    try:
        if imagen_archivo and getattr(imagen_archivo, 'filename', ''):
            imagen_url = imagen_url_para_persistir(
                procesar_imagen_para_producto(
                    imagen_archivo,
                    codigo_barras,
                    nombre,
                    descripcion,
                    comercio_id=tienda_id,
                )
            )
            if not imagen_url:
                return jsonify(
                    {
                        'error': (
                            'La imagen no se pudo subir o Supabase no devolvió '
                            'una URL pública válida.'
                        )
                    }
                ), 400
        else:
            imagen_url = resolver_imagen_url_definitiva(
                None,
                codigo_barras,
            )
    except SupabaseUploadError as error:
        return jsonify({'error': str(error)}), 503

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                '''
                INSERT INTO productos (comercio_id, nombre, descripcion, precio_usd, codigo_barras, imagen_url)
                VALUES (?, ?, ?, ?, ?, ?)
                RETURNING id
                ''',
                (tienda_id, nombre, descripcion, precio_usd, codigo_barras, imagen_url),
            )
            fila = cursor.fetchone()
            producto_id = fila[0] if fila else None
            conexion.commit()

        return jsonify(
            {
                'ok': True,
                'mensaje': 'Producto agregado con éxito.',
                'producto_id': producto_id,
            }
        ), 201
    except Exception as error:
        return jsonify({'error': f'Error al agregar producto: {error}'}), 500


@app.route('/api/pagos/cotizacion')
@login_requerido_api
def api_cotizacion_pago():
    comercio = _comercio_sesion_validado()
    plan_tipo = (request.args.get('plan') or 'basica').lower()
    if comercio:
        cotizacion = calcular_cotizacion_cambio_plan(comercio, plan_tipo)
        if not cotizacion:
            return jsonify({'error': 'Plan no válido.'}), 400
        datos = obtener_datos_pago_movil(plan_tipo)
        return jsonify({'ok': True, **datos, **cotizacion}), 200

    montos = calcular_monto_pago_plan(plan_tipo)
    if not montos:
        return jsonify({'error': 'Plan no válido.'}), 400
    datos = obtener_datos_pago_movil(plan_tipo)
    return jsonify({'ok': True, **datos, **montos}), 200


@app.route('/api/pagos/programar-cambio', methods=['POST'])
@login_requerido_api
def api_programar_cambio_plan():
    comercio = _comercio_sesion_validado()
    if not comercio:
        return jsonify({'error': 'Selecciona un comercio válido en tu cuenta.'}), 403

    plan_tipo = (request.form.get('plan_tipo') or (request.get_json(silent=True) or {}).get('plan_tipo') or '').lower()
    cotizacion = calcular_cotizacion_cambio_plan(comercio, plan_tipo)
    if not cotizacion:
        return jsonify({'error': 'Plan no válido.'}), 400
    if cotizacion['tipo_cambio'] != 'downgrade':
        return jsonify({'error': 'Este cambio requiere pago. Usa el flujo de comprobante.'}), 400

    exito, mensaje, datos = programar_downgrade_plan(comercio['id'], plan_tipo)
    if not exito:
        return jsonify({'error': mensaje}), 400

    return jsonify({'ok': True, 'mensaje': mensaje, **(datos or {})}), 200


@app.route('/api/pagos/verificar', methods=['POST'])
@login_requerido_api
def api_verificar_pago():
    comercio = _comercio_sesion_validado()
    if not comercio:
        return jsonify({'error': 'Selecciona un comercio válido en tu cuenta.'}), 403

    plan_tipo = (request.form.get('plan_tipo') or 'basica').lower()
    cotizacion = calcular_cotizacion_cambio_plan(comercio, plan_tipo)
    if not cotizacion:
        return jsonify({'error': 'Plan no válido para pago.'}), 400

    if cotizacion['tipo_cambio'] == 'downgrade':
        exito, mensaje, datos = programar_downgrade_plan(comercio['id'], plan_tipo)
        if not exito:
            return jsonify({'error': mensaje}), 400
        return jsonify({'ok': True, 'mensaje': mensaje, **(datos or {})}), 200

    archivo = request.files.get('comprobante')

    if not archivo or not archivo.filename:
        return jsonify({'error': 'Debes adjuntar la captura del comprobante de pago.'}), 400

    from backend.images import comprimir_bytes_a_bytes, leer_bytes_limitados

    data_bytes, error_lectura = leer_bytes_limitados(archivo)
    if error_lectura:
        return jsonify({'error': error_lectura}), 400

    montos = calcular_monto_pago_plan(plan_tipo, comercio)
    if not montos:
        return jsonify({'error': 'Plan no válido para pago.'}), 400

    ocr = validar_comprobante_pago_movil(data_bytes, montos['monto_bs'])

    if not ocr.get('ok'):
        return jsonify(
            {
                'error': ' '.join(ocr.get('errores') or ['Comprobante no válido.']),
                'ocr_ms': round(ocr.get('ms', 0), 1),
            }
        ), 400

    referencia = ocr['referencia']
    comprobante_url = None

    try:
        comprimido = comprimir_bytes_a_bytes(
            data_bytes,
            prefijo=f'pago_{comercio["id"]}',
            max_dimension=1920,
        )
        if not comprimido:
            raise SupabaseUploadError('No se pudo procesar la imagen del comprobante.')

        payload, content_type, filename = comprimido
        comprobante_url = subir_bytes_a_supabase(
            payload,
            filename=filename,
            content_type=content_type,
            carpeta='pagos',
        )
    except SupabaseUploadError as error:
        return jsonify({'error': str(error)}), 503

    exito, mensaje, datos = activar_suscripcion_por_comprobante(
        comercio['id'],
        plan_tipo,
        referencia,
        comprobante_url=comprobante_url,
        monto_ocr_bs=ocr.get('monto_bs'),
    )

    if not exito:
        return jsonify({'error': mensaje, 'ocr_ms': round(ocr.get('ms', 0), 1)}), 400

    respuesta = {
        'ok': True,
        'mensaje': mensaje,
        'referencia': referencia,
        'ocr_ms': round(ocr.get('ms', 0), 1),
        'estado': datos.get('estado', 'activo'),
        'fecha_vencimiento': datos.get('fecha_vencimiento'),
        'plan_tipo': datos.get('plan_tipo', plan_tipo),
        'monto_usd': datos.get('monto_usd'),
        'monto_bs': datos.get('monto_bs'),
        'tasa': datos.get('tasa'),
        'comprobante_url': comprobante_url,
    }
    return jsonify(respuesta), 200


# ==========================================
# RUTAS DE ADMINISTRACIÓN
# ==========================================


@app.route('/admin')
@admin_requerido
def panel_admin():
    estado_filtro = request.args.get('estado_ticket', None)
    busqueda = request.args.get('q', '').strip()
    tickets = obtener_bandeja_tecnica(estado_filtro=estado_filtro)
    tasa_actual = obtener_tasa_dolar()
    comercios = obtener_todos_comercios_admin(busqueda=busqueda or None)
    banner_url = url_banner_principal(
        obtener_banner_principal(),
        default=DEFAULT_BANNER,
    )
    whatsapp = obtener_config('whatsapp_soporte', WHATSAPP_SOPORTE)

    return render_template(
        'admin.html',
        tasa=tasa_actual,
        comercios=comercios,
        tickets=tickets,
        banner_url=banner_url,
        whatsapp=whatsapp,
        whatsapp_url=WHATSAPP_SOPORTE_URL,
        planes=PLANES,
        q=busqueda,
    )


@app.route('/admin/tasa', methods=['POST'])
@admin_requerido
def actualizar_tasa():
    nueva_tasa = request.form.get('tasa_dolar')
    exito, mensaje = actualizar_tasa_dolar(session.get('usuario_id'), nueva_tasa)
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('panel_admin'))


@app.route('/admin/banner', methods=['POST'])
@admin_requerido
def admin_banner():
    banner_archivo = request.files.get('banner')
    admin_id = session.get('usuario_id')

    if banner_archivo and banner_archivo.filename:
        try:
            banner_url = procesar_imagen_subida(
                banner_archivo,
                prefijo='banner_app',
                carpeta='banners',
                max_dimension=1920,
            )
            exito, mensaje = actualizar_banner_principal(admin_id, banner_url)
        except SupabaseUploadError as error:
            exito, mensaje = False, str(error)
    else:
        exito, mensaje = False, 'Debes seleccionar una imagen.'

    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('panel_admin'))


@app.route('/admin/comercio/estado/<int:comercio_id>', methods=['POST'])
@admin_requerido
def cambiar_estado_comercio(comercio_id):
    visible = parsear_visible_form(
        request.form.get('nuevo_estado') or request.form.get('visible'),
        default=1,
    )
    estado_pago = request.form.get(
        'estado_pago', 'activo' if visible == 1 else 'suspendido'
    )
    exito, mensaje = cambiar_visibilidad_comercio(
        session.get('usuario_id'), comercio_id, visible, estado_pago
    )
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('panel_admin'))


@app.route('/admin/comercio/suspender/<int:comercio_id>', methods=['POST'])
@admin_requerido
def admin_suspender_comercio(comercio_id):
    exito, mensaje = suspender_comercio_temporal(
        session.get('usuario_id'), comercio_id
    )
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('panel_admin'))


@app.route('/admin/comercio/reactivar/<int:comercio_id>', methods=['POST'])
@admin_requerido
def admin_reactivar_comercio(comercio_id):
    exito, mensaje = reactivar_comercio(session.get('usuario_id'), comercio_id)
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('panel_admin'))


@app.route('/admin/comercio/eliminar/<int:comercio_id>', methods=['POST'])
@admin_requerido
def admin_eliminar_comercio(comercio_id):
    exito, mensaje = eliminar_comercio_definitivo(
        session.get('usuario_id'), comercio_id
    )
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('panel_admin'))


@app.route('/admin/comercio/plan/<int:comercio_id>', methods=['POST'])
@admin_requerido
def admin_cambiar_plan(comercio_id):
    plan_tipo = request.form.get('plan_tipo', 'basica')
    estado_pago = request.form.get('estado_pago')
    exito, mensaje = cambiar_plan_comercio(
        session.get('usuario_id'), comercio_id, plan_tipo, estado_pago
    )
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('panel_admin'))


@app.route('/admin/ticket/resolver/<int:ticket_id>', methods=['POST'])
@admin_requerido
def admin_resolver_ticket(ticket_id):
    exito, mensaje = resolver_ticket_soporte(ticket_id)
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('panel_admin'))


@app.route('/api/suscripcion/confirmar-pago', methods=['POST'])
@admin_requerido_api
def api_confirmar_pago():
    """Confirmación manual de pago — solo administradores."""
    data = request.get_json(silent=True) or {}
    comercio_id = data.get('comercio_id')
    plan_tipo = data.get('plan_tipo', 'basica')
    meses, error_meses = parsear_entero_form(data.get('meses', 1), default=1, minimo=1)
    if error_meses:
        return jsonify({'ok': False, 'mensaje': error_meses}), 400

    if not comercio_id:
        return jsonify({'ok': False, 'mensaje': 'comercio_id requerido'}), 400

    exito, mensaje = confirmar_pago_suscripcion(comercio_id, plan_tipo, meses)
    return jsonify({'ok': exito, 'mensaje': mensaje}), 200 if exito else 400


port = int(os.environ.get('PORT', 5000))

if __name__ == '__main__':
    inicializar_base_de_datos()
    app.run(host='0.0.0.0', port=port)

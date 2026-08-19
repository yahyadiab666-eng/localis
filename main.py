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

from backend.supabase_client import SUPABASE_BUCKET_IMAGENES, supabase
from backend.supabase_storage import (
    SupabaseUploadError,
    subir_bytes_a_supabase,
    subir_imagen_a_supabase,
)
if supabase:
    print('Supabase Storage + API configurados correctamente.')
else:
    print('Aviso: SUPABASE_URL o SUPABASE_KEY no configurados. Las subidas de imágenes fallarán.')

import sqlite3

from authlib.integrations.flask_client import OAuth
from config import MAX_UPLOAD_BYTES, WHATSAPP_SOPORTE, WHATSAPP_SOPORTE_URL
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
from backend.image_lookup import aplicar_respaldo_imagenes, resolver_imagen_producto
from backend.db import get_db_connection
from database import init_db, normalize_database_url
from backend.plans import PLANES, MENSAJE_LIMITE_PRODUCTOS, es_limite_ilimitado, obtener_beneficios_plan
from backend.payment_ocr import validar_comprobante_pago_movil
from backend.subscriptions import (
    activar_suscripcion_por_comprobante,
    calcular_monto_pago_plan,
    comercio_puede_gestionar_inventario,
    contar_productos_comercio,
    marcar_bienvenida_vista,
    obtener_avisos_suscripcion,
    obtener_datos_pago_movil,
    obtener_limite_productos_comercio,
    puede_agregar_producto,
    rechazar_renovacion_vencida,
    verificar_vencimiento_comercio,
    verificar_vencimientos_comercios,
)
from backend.utils import (
    formatear_fecha,
    normalizar_codigo_barras,
    normalizar_telefono_whatsapp,
    url_imagen_usable,
    url_maps_comercio,
    url_whatsapp_comercio,
)
from backend.stores import (
    actualizar_datos_comercio,
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

app.secret_key = os.environ.get(
    'LOCALIS_SECRET_KEY', 'clave_secreta_localis_desarrollo'
)

app.config['SUPABASE_CLIENT'] = supabase
app.config['SUPABASE_BUCKET_IMAGENES'] = SUPABASE_BUCKET_IMAGENES
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_BYTES
os.makedirs(os.path.join(BASE_DIR, 'static', 'images', 'productos'), exist_ok=True)

csrf = CSRFProtect(app)


@app.template_filter('fecha_corta')
def _filtro_fecha_corta(valor):
    return formatear_fecha(valor) or '—'

DEFAULT_BANNER = (
    'https://images.pexels.com/photos/18618233/pexels-photo-18618233.jpeg'
    '?auto=compress&cs=tinysrgb&w=1920'
)


def _inicializar_aplicacion():
    """Migraciones y verificación de vencimientos al cargar la app."""
    try:
        if db_url:
            print('Base de datos: PostgreSQL (DATABASE_URL / Supabase SQL).')
        else:
            print('Aviso: DATABASE_URL no configurada.')
        init_db()
        verificar_vencimientos_comercios()
    except Exception as error:
        print(f'Error al inicializar la aplicación: {error}')


_inicializar_aplicacion()

_ultima_verificacion_vencimientos_global = 0.0
_INTERVALO_VENCIMIENTOS_SEG = 300


@app.before_request
def sincronizar_vencimientos_suscripcion():
    """Revisa vencimientos globalmente y por comercio en rutas autenticadas."""
    global _ultima_verificacion_vencimientos_global

    try:
        ahora = time.time()
        if ahora - _ultima_verificacion_vencimientos_global >= _INTERVALO_VENCIMIENTOS_SEG:
            verificar_vencimientos_comercios()
            _ultima_verificacion_vencimientos_global = ahora

        if 'usuario_id' not in session:
            return

        rutas_comercio = (
            '/comercio',
            '/api/productos',
            '/api/pagos',
        )
        if not any(request.path.startswith(ruta) for ruta in rutas_comercio):
            return

        comercio = obtener_comercio_por_usuario(session.get('usuario_id'))
        if comercio:
            verificar_vencimiento_comercio(comercio['id'])
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


def _normalizar_imagenes_comercio(comercio):
    """Normaliza logo, banner y fechas para plantillas (PostgreSQL datetime)."""
    if not comercio:
        return comercio
    comercio = dict(comercio)

    logo = comercio.get('logo_url')
    if logo and str(logo).startswith(('http://', 'https://', '/')):
        comercio['logo_completo'] = logo
    else:
        comercio['logo_completo'] = None

    banner = comercio.get('banner_url') or comercio.get('imagen_portada')
    if banner and str(banner).startswith(('http://', 'https://', '/')):
        comercio['banner_completo'] = banner
    else:
        comercio['banner_completo'] = None

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
    """Sube imagen exclusivamente a Supabase Storage. Lanza SupabaseUploadError si falla."""
    if not file_storage or not getattr(file_storage, 'filename', ''):
        return None

    from backend.images import validar_archivo_subida

    error_validacion = validar_archivo_subida(file_storage)
    if error_validacion:
        raise SupabaseUploadError(error_validacion)

    if not supabase:
        raise SupabaseUploadError(
            'Supabase Storage no está configurado. '
            'Define SUPABASE_URL y SUPABASE_KEY en el entorno.'
        )

    return subir_imagen_a_supabase(
        file_storage,
        supabase,
        prefijo=prefijo,
        carpeta=carpeta,
        max_dimension=max_dimension,
    )


def procesar_logo_comercio(file_storage, prefijo):
    """Logo de comercio → bucket imágenes en Supabase."""
    return procesar_imagen_subida(
        file_storage,
        prefijo=prefijo,
        carpeta='comercios',
    )


def procesar_imagen_para_producto(
    file_storage, codigo_barras, nombre, descripcion, comercio_id
):
    """Prioridad: archivo subido > catálogo por código de barras > búsqueda web."""
    if file_storage and getattr(file_storage, 'filename', ''):
        url = procesar_imagen_subida(
            file_storage,
            prefijo=f'manual_{comercio_id}',
            carpeta='productos',
        )
        if url:
            return url

    try:
        return resolver_imagen_producto(
            imagen_url=None,
            codigo_barras=codigo_barras,
            nombre=nombre,
            descripcion=descripcion,
            buscar_web=True,
        )
    except Exception as e:
        print(f'Error al buscar imagen automática: {e}')
        return None


def login_requerido(f):

    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Debes iniciar sesión para acceder a esta sección.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorada


def login_requerido_api(f):

    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario_id' not in session:
            return jsonify({'error': 'Debes iniciar sesión.'}), 401
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
        return f(*args, **kwargs)

    return decorada


def admin_requerido_api(f):

    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario_id' not in session or not session.get('es_admin'):
            return jsonify({'ok': False, 'mensaje': 'Acceso denegado.'}), 403
        return f(*args, **kwargs)

    return decorada


def _bloquear_gestion_inventario(comercio, redirect_url='panel_comercio'):
    ok, mensaje = comercio_puede_gestionar_inventario(comercio['id'])
    if ok:
        return None
    flash(mensaje, 'error')
    return redirect(url_for(redirect_url, abrir_pago='pro'))


@app.errorhandler(404)
def pagina_no_encontrada(e):
    return redirect(url_for('index'))


@app.errorhandler(500)
def error_interno_servidor(e):
    return redirect(url_for('index'))


@app.errorhandler(CSRFError)
def error_csrf(e):
    flash(
        'Tu sesión de formulario expiró o la petición no es segura. Inténtalo de nuevo.',
        'error',
    )
    return redirect(request.referrer or url_for('index'))


@app.errorhandler(RequestEntityTooLarge)
def error_archivo_demasiado_grande(e):
    max_mb = MAX_UPLOAD_BYTES // (1024 * 1024)
    mensaje = f'El archivo supera el tamaño máximo permitido ({max_mb} MB).'

    if request.path.startswith('/api/'):
        return jsonify({'error': mensaje}), 413

    flash(mensaje, 'error')
    return redirect(request.referrer or url_for('index'))


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

    tasa_actual = obtener_tasa_dolar() or 1.0
    banner_url = obtener_banner_principal() or DEFAULT_BANNER
    whatsapp = obtener_config('whatsapp_soporte', WHATSAPP_SOPORTE)

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
    """Respaldo por código de barras/SKU/nombre cuando la foto principal falla."""
    codigo = normalizar_codigo_barras(request.args.get('codigo'))
    nombre = (request.args.get('nombre') or '').strip() or None
    excluir = (request.args.get('excluir') or '').strip() or None
    buscar_web = request.args.get('web') == '1'

    url = resolver_imagen_producto(
        imagen_url=None,
        codigo_barras=codigo,
        nombre=nombre,
        buscar_web=buscar_web,
        excluir_url=excluir,
        persistir=True,
    )
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
    return render_template('login.html')


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
            return redirect(url_for('panel_comercio'))

        return redirect(url_for('index'))
    except Exception as error:
        flash(f'Error en inicio de sesión con Google: {error}', 'error')
        return redirect(url_for('login'))


@app.route('/logout')
def logout():
    session.clear()
    flash('Sesión cerrada correctamente.', 'info')
    return redirect(url_for('index'))


# ==========================================
# RUTAS DE COMERCIO
# ==========================================


@app.route('/comercio')
@login_requerido
def panel_comercio():
    usuario_id = session.get('usuario_id')
    try:
        tasa_actual = obtener_tasa_dolar() or 1.0
        whatsapp = obtener_config('whatsapp_soporte', WHATSAPP_SOPORTE)

        with get_db_connection(row_factory=sqlite3.Row) as conn:
            cursor = conn.cursor()

            cursor.execute('SELECT id FROM usuarios WHERE id = ?', (usuario_id,))
            if not cursor.fetchone():
                session.clear()
                flash(
                    'La sesión ya no existe en la base de datos. Por favor inicia sesión nuevamente.',
                    'error',
                )
                return redirect(url_for('login'))

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
                return render_template(
                    'registro_comercio.html', categorias=categorias
                )

            cursor.execute(
                '''
                SELECT id, nombre, descripcion, precio_usd, codigo_barras, imagen_url
                FROM productos WHERE comercio_id = ?
                ''',
                (comercio_db['id'],),
            )
            productos_db = cursor.fetchall()

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
        aplicar_respaldo_imagenes(productos)

        comercio = _normalizar_imagenes_comercio(dict(comercio_db))

        plan_info = PLANES.get(comercio.get('plan_tipo', 'gratis'), PLANES['gratis'])
        avisos = obtener_avisos_suscripcion(comercio)
        pago_movil = obtener_datos_pago_movil()
        planes_beneficios = {}
        for codigo in ('basica', 'pro', 'business'):
            info = obtener_beneficios_plan(codigo)
            montos = calcular_monto_pago_plan(codigo)
            if montos:
                info['monto_bs'] = montos['monto_bs']
                info['tasa'] = montos['tasa']
            planes_beneficios[codigo] = info

        return render_template(
            'comercio.html',
            comercio=comercio,
            productos=productos,
            tasa=tasa_actual,
            whatsapp=whatsapp,
            whatsapp_url=WHATSAPP_SOPORTE_URL,
            plan_info=plan_info,
            avisos=avisos,
            pago_movil=pago_movil,
            planes=PLANES,
            planes_beneficios=planes_beneficios,
            abrir_pago=request.args.get('abrir_pago'),
        )
    except Exception as error:
        print(f'Error al cargar panel de comercio: {error}')
        flash(
            'No se pudo cargar el panel del comercio. Intenta iniciar sesión de nuevo.',
            'error',
        )
        return redirect(url_for('index'))


@app.route('/comercio/editar', methods=['GET', 'POST'])
@login_requerido
def editar_comercio():
    usuario_id = session.get('usuario_id')
    comercio = obtener_comercio_por_usuario(usuario_id)

    if not comercio:
        flash('Debes registrar un comercio primero.', 'error')
        return redirect(url_for('panel_comercio'))

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        telefono = request.form.get('telefono', '').strip()
        direccion = request.form.get('direccion', '').strip()
        descripcion = request.form.get('descripcion', '').strip()
        ciudad = request.form.get('ciudad', '').strip()
        zona = request.form.get('zona', '').strip()
        maps_url = request.form.get('maps_url', '').strip()
        logo_archivo = request.files.get('logo')
        banner_archivo = request.files.get('banner')

        logo_url = None
        if logo_archivo and logo_archivo.filename:
            try:
                logo_url = procesar_logo_comercio(
                    logo_archivo, prefijo=f'logo_{comercio["id"]}'
                )
            except SupabaseUploadError as error:
                flash(str(error), 'error')
                return redirect(url_for('editar_comercio'))

        banner_url = None
        if banner_archivo and banner_archivo.filename:
            try:
                banner_url = procesar_imagen_subida(
                    banner_archivo,
                    prefijo=f'banner_{comercio["id"]}',
                    carpeta='banners',
                    max_dimension=1920,
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
            banner_url=banner_url,
        )
        flash(mensaje, 'exito' if exito else 'error')
        return redirect(url_for('panel_comercio'))

    return render_template(
        'editar_comercio.html',
        comercio=_normalizar_imagenes_comercio(comercio),
    )


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
        categoria_id = (
            int(categoria_raw) if categoria_raw and categoria_raw.isdigit() else 1
        )
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
    except Exception as error:
        print(f'Error al registrar comercio: {error}')
        flash(f'No se pudo registrar el comercio: {error}', 'error')
        return redirect(url_for('panel_comercio'))


@app.route('/comercio/producto/nuevo', methods=['GET', 'POST'])
@login_requerido
def nuevo_producto():
    usuario_id = session.get('usuario_id')
    comercio = obtener_comercio_por_usuario(usuario_id)

    if not comercio:
        flash('Debes registrar un comercio primero.', 'error')
        return redirect(url_for('panel_comercio'))

    if request.method == 'POST':
        nombre = request.form.get('nombre')
        descripcion = request.form.get('descripcion')
        precio_usd = request.form.get('precio_usd')
        codigo_barras = normalizar_codigo_barras(request.form.get('codigo_barras'))
        imagen_archivo = request.files.get('imagen')

        if not nombre or not precio_usd:
            flash('El nombre y el precio son obligatorios.', 'error')
            return redirect(url_for('nuevo_producto'))

        ok, msg_limite = puede_agregar_producto(comercio['id'])
        if not ok:
            flash(msg_limite, 'error')
            destino = url_for('panel_comercio')
            if 'vencido' in msg_limite.lower() or 'límite' in msg_limite.lower():
                destino = url_for('panel_comercio', abrir_pago='pro')
            return redirect(destino)

        try:
            imagen_url = procesar_imagen_para_producto(
                imagen_archivo,
                codigo_barras,
                nombre,
                descripcion,
                comercio_id=comercio['id'],
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
    usuario_id = session.get('usuario_id')
    comercio = obtener_comercio_por_usuario(usuario_id)

    if not comercio:
        flash('No se encontró un comercio asociado a esta cuenta.', 'error')
        return redirect(url_for('login'))

    comercio_id = comercio['id']

    bloqueo = _bloquear_gestion_inventario(comercio)
    if bloqueo:
        return bloqueo

    if request.method == 'POST':
        nombre = request.form.get('nombre')
        precio_usd = request.form.get('precio_usd')
        descripcion = request.form.get('descripcion')
        codigo_barras = normalizar_codigo_barras(request.form.get('codigo_barras'))
        imagen_archivo = request.files.get('imagen')

        try:
            with get_db_connection(row_factory=sqlite3.Row) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT imagen_url FROM productos WHERE id = ? AND comercio_id = ?',
                    (producto_id, comercio_id),
                )
                prod_previo = cursor.fetchone()
                imagen_url = prod_previo['imagen_url'] if prod_previo else None

                if imagen_archivo and getattr(imagen_archivo, 'filename', ''):
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
                elif not url_imagen_usable(imagen_url):
                    imagen_url = procesar_imagen_para_producto(
                        None,
                        codigo_barras,
                        nombre,
                        descripcion,
                        comercio_id=comercio_id,
                    )

                cursor.execute(
                    '''
                    UPDATE productos
                    SET nombre = ?, precio_usd = ?, descripcion = ?, codigo_barras = ?, imagen_url = ?
                    WHERE id = ? AND comercio_id = ?
                    ''',
                    (
                        nombre,
                        precio_usd,
                        descripcion,
                        codigo_barras,
                        imagen_url,
                        producto_id,
                        comercio_id,
                    ),
                )
            flash('Producto actualizado con éxito.', 'exito')
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
    comercio = obtener_comercio_por_usuario(session.get('usuario_id'))
    if not comercio:
        flash('Comercio no encontrado.', 'error')
        return redirect(url_for('panel_comercio'))

    bloqueo = _bloquear_gestion_inventario(comercio)
    if bloqueo:
        return bloqueo

    exito, mensaje = eliminar_producto(producto_id, comercio['id'])
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('panel_comercio'))


@app.route('/comercio/productos/cargar-csv', methods=['POST'])
@login_requerido
def cargar_csv():
    comercio = obtener_comercio_por_usuario(session.get('usuario_id'))
    if not comercio:
        flash('Comercio no encontrado.', 'error')
        return redirect(url_for('panel_comercio'))

    bloqueo = _bloquear_gestion_inventario(comercio)
    if bloqueo:
        return bloqueo

    archivo = request.files.get('archivo_csv')
    exito, mensaje, meta = procesar_csv_productos(comercio['id'], archivo)

    if not exito and meta and meta.get('plan_sugerido'):
        flash(mensaje, 'limite_plan')
        return redirect(
            url_for(
                'panel_comercio',
                abrir_pago=meta['plan_sugerido'],
            )
        )

    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('panel_comercio'))


@app.route('/comercio/suscripcion/marcar-bienvenida', methods=['POST'])
@login_requerido
def suscripcion_marcar_bienvenida():
    comercio = obtener_comercio_por_usuario(session.get('usuario_id'))
    if comercio:
        marcar_bienvenida_vista(comercio['id'])
    return redirect(url_for('panel_comercio'))


@app.route('/comercio/suscripcion/rechazar-vencido', methods=['POST'])
@login_requerido
def suscripcion_rechazar_vencido():
    comercio = obtener_comercio_por_usuario(session.get('usuario_id'))
    if not comercio:
        flash('Comercio no encontrado.', 'error')
        return redirect(url_for('panel_comercio'))

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
    comercio = obtener_comercio_por_usuario(session.get('usuario_id'))
    if not comercio:
        flash('Comercio no encontrado.', 'error')
        return redirect(url_for('panel_comercio'))

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
    return redirect(url_for('panel_comercio', abrir_pago=plan_tipo))


# ==========================================
# API REST (JSON)
# ==========================================


@app.route('/api/productos/crear', methods=['POST'])
@login_requerido_api
def api_crear_producto():
    comercio = obtener_comercio_por_usuario(session.get('usuario_id'))
    if not comercio:
        return jsonify({'error': 'Comercio no encontrado.'}), 404

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

    if not nombre or not precio_raw:
        return jsonify({'error': 'El nombre y el precio son obligatorios.'}), 400

    try:
        precio_usd = float(precio_raw)
    except (TypeError, ValueError):
        return jsonify({'error': 'El precio debe ser un número válido.'}), 400

    try:
        imagen_url = procesar_imagen_para_producto(
            imagen_archivo,
            codigo_barras,
            nombre,
            descripcion,
            comercio_id=tienda_id,
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
    plan_tipo = (request.args.get('plan') or 'basica').lower()
    montos = calcular_monto_pago_plan(plan_tipo)
    if not montos:
        return jsonify({'error': 'Plan no válido.'}), 400
    datos = obtener_datos_pago_movil(plan_tipo)
    return jsonify({'ok': True, **datos, **montos}), 200


@app.route('/api/pagos/verificar', methods=['POST'])
@login_requerido_api
def api_verificar_pago():
    comercio = obtener_comercio_por_usuario(session.get('usuario_id'))
    if not comercio:
        return jsonify({'error': 'Comercio no encontrado.'}), 404

    plan_tipo = (request.form.get('plan_tipo') or 'basica').lower()
    archivo = request.files.get('comprobante')

    if not archivo or not archivo.filename:
        return jsonify({'error': 'Debes adjuntar la captura del comprobante de pago.'}), 400

    from backend.images import comprimir_bytes_a_bytes, leer_bytes_limitados

    data_bytes, error_lectura = leer_bytes_limitados(archivo)
    if error_lectura:
        return jsonify({'error': error_lectura}), 400

    montos = calcular_monto_pago_plan(plan_tipo)
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
        if not supabase:
            raise SupabaseUploadError(
                'Supabase Storage no está configurado para almacenar comprobantes.'
            )

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
            supabase,
            filename,
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
    banner_url = obtener_banner_principal()
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
    visible = request.form.get('nuevo_estado') or request.form.get('visible')
    estado_pago = request.form.get(
        'estado_pago', 'activo' if visible == '1' else 'suspendido'
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
    meses = data.get('meses', 1)

    if not comercio_id:
        return jsonify({'ok': False, 'mensaje': 'comercio_id requerido'}), 400

    exito, mensaje = confirmar_pago_suscripcion(comercio_id, plan_tipo, meses)
    return jsonify({'ok': exito, 'mensaje': mensaje}), 200 if exito else 400


port = int(os.environ.get('PORT', 5000))

if __name__ == '__main__':
    inicializar_base_de_datos()
    app.run(host='0.0.0.0', port=port)

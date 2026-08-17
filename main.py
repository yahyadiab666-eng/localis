from functools import wraps
import os
import sqlite3

from authlib.integrations.flask_client import OAuth
from config import DATABASE_FILE, UPLOAD_FOLDER, WHATSAPP_SOPORTE, WHATSAPP_SOPORTE_URL
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

from backend.admin import (
    actualizar_banner_principal,
    actualizar_tasa_dolar,
    cambiar_plan_comercio,
    cambiar_visibilidad_comercio,
    confirmar_pago_suscripcion,
    obtener_banner_principal,
    obtener_bandeja_tecnica,
    obtener_todos_comercios_admin,
    resolver_ticket_soporte,
)
from backend.auth import obtener_o_crear_usuario_google
from backend.images import comprimir_y_guardar
from backend.image_search import obtener_url_imagen_automatica
from backend.db import get_db_connection
from database import init_db
from backend.plans import PLANES, obtener_beneficios_plan
from backend.subscriptions import (
    marcar_bienvenida_vista,
    obtener_avisos_suscripcion,
    obtener_datos_pago_movil,
    puede_agregar_producto,
    rechazar_renovacion_vencida,
    registrar_pago_movil_plan,
    verificar_vencimientos_comercios,
)
from backend.utils import normalizar_telefono_whatsapp, url_maps_comercio, url_whatsapp_comercio
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

app.secret_key = os.environ.get(
    'LOCALIS_SECRET_KEY', 'clave_secreta_localis_desarrollo'
)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'static', 'images'), exist_ok=True)

csrf = CSRFProtect(app)

DEFAULT_BANNER = (
    'https://images.pexels.com/photos/18618233/pexels-photo-18618233.jpeg'
    '?auto=compress&cs=tinysrgb&w=1920'
)


def _inicializar_aplicacion():
    """Migraciones y verificación de vencimientos al cargar la app."""
    init_db()
    verificar_vencimientos_comercios()


_inicializar_aplicacion()


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
google = oauth.register(
    name='google',
    client_id='615657603931-6pk4ufd4hre532skvq3g5t7et3ds4k77.apps.googleusercontent.com',
    client_secret='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',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'},
)


def procesar_imagen_para_producto(
    file_storage, codigo_barras, nombre, descripcion, comercio_id
):
    """Prioridad: imagen manual comprimida > búsqueda por código/nombre > default."""
    if file_storage and getattr(file_storage, 'filename', ''):
        url = comprimir_y_guardar(
            file_storage,
            app.config['UPLOAD_FOLDER'],
            prefijo=f'manual_{comercio_id}',
        )
        if url:
            return url

    try:
        url_externa = obtener_url_imagen_automatica(
            codigo_barras, nombre, descripcion
        )
        if url_externa:
            return url_externa
    except Exception as e:
        print(f'Error al buscar imagen automática: {e}')

    return '/static/images/default-product.webp'


def login_requerido(f):

    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Debes iniciar sesión para acceder a esta sección.', 'error')
            return redirect(url_for('login'))
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
    delivery = request.args.get('delivery', '').strip()

    hay_filtros = bool(palabra_clave or categoria or delivery)

    productos = buscar_y_filtrar_productos(
        palabra_clave=palabra_clave,
        categoria_nombre=categoria,
        delivery=delivery,
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

    if comercio.get('logo_url') and not comercio['logo_url'].startswith('/'):
        comercio['logo_completo'] = f"/static/uploads/{comercio['logo_url']}"
    else:
        comercio['logo_completo'] = comercio.get('logo_url')

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
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)


@app.route('/login/google/callback')
def google_callback():
    token = google.authorize_access_token()
    google_info = token.get('userinfo')

    exito, usuario_o_error = obtener_o_crear_usuario_google(google_info)

    if exito:
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

    flash(f'Error en inicio de sesión con Google: {usuario_o_error}', 'error')
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
    tasa_actual = obtener_tasa_dolar() or 1.0
    whatsapp = obtener_config('whatsapp_soporte', WHATSAPP_SOPORTE)

    conn = get_db_connection(row_factory=sqlite3.Row)
    cursor = conn.cursor()

    cursor.execute('SELECT id FROM usuarios WHERE id = ?', (usuario_id,))
    if not cursor.fetchone():
        conn.close()
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
        conn.close()
        return render_template('registro_comercio.html', categorias=categorias)

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
        productos.append({
            'id': p['id'],
            'nombre': p['nombre'],
            'descripcion': p['descripcion'] or 'Sin descripción',
            'precio_usd': p['precio_usd'],
            'precio_bs': round(p['precio_usd'] * tasa_actual, 2),
            'codigo_barras': p['codigo_barras'] or 'Sin código',
            'imagen_url': (
                p['imagen_url']
                if p['imagen_url'] and p['imagen_url'] != '__PENDING__'
                else '/static/images/default-product.webp'
            ),
        })

    comercio = dict(comercio_db)
    if comercio.get('logo_url') and not str(comercio['logo_url']).startswith('/'):
        comercio['logo_completo'] = f"/static/uploads/{comercio['logo_url']}"
    else:
        comercio['logo_completo'] = comercio.get('logo_url')

    plan_info = PLANES.get(comercio.get('plan_tipo', 'gratis'), PLANES['gratis'])
    avisos = obtener_avisos_suscripcion(comercio)
    pago_movil = obtener_datos_pago_movil()
    planes_beneficios = {
        codigo: obtener_beneficios_plan(codigo)
        for codigo in ('basica', 'pro', 'business')
    }
    conn.close()

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
    )


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

        logo_url = None
        if logo_archivo and logo_archivo.filename:
            logo_url = comprimir_y_guardar(
                logo_archivo,
                app.config['UPLOAD_FOLDER'],
                prefijo=f'logo_{comercio["id"]}',
            )
            if logo_url:
                logo_url = logo_url.replace('/static/uploads/', '')

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

    return render_template('editar_comercio.html', comercio=comercio)


@app.route('/comercio/crear', methods=['GET', 'POST'])
@login_requerido
def crear_comercio():
    if request.method == 'GET':
        conn = get_db_connection(row_factory=sqlite3.Row)
        cursor = conn.cursor()
        cursor.execute('SELECT id, nombre FROM categorias')
        categorias = [dict(c) for c in cursor.fetchall()]
        conn.close()
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
    categoria_id = int(categoria_raw) if categoria_raw and categoria_raw.isdigit() else 1
    logo_archivo = request.files.get('logo')

    logo_url = None
    if logo_archivo and logo_archivo.filename:
        logo_url = comprimir_y_guardar(
            logo_archivo,
            app.config['UPLOAD_FOLDER'],
            prefijo=f'logo_nuevo_{usuario_id}',
        )
        if logo_url:
            logo_url = logo_url.replace('/static/uploads/', '')

    exito, resultado = registrar_comercio_completo(
        usuario_id,
        nombre,
        descripcion,
        telefono,
        direccion,
        categoria_id,
        0,
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
        codigo_barras = request.form.get('codigo_barras')
        imagen_archivo = request.files.get('imagen')

        if not nombre or not precio_usd:
            flash('El nombre y el precio son obligatorios.', 'error')
            return redirect(url_for('nuevo_producto'))

        ok, msg_limite = puede_agregar_producto(comercio['id'])
        if not ok:
            flash(msg_limite, 'error')
            return redirect(url_for('panel_comercio'))

        imagen_url = procesar_imagen_para_producto(
            imagen_archivo,
            codigo_barras,
            nombre,
            descripcion,
            comercio_id=comercio['id'],
        )

        try:
            conn = get_db_connection()
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
            conn.commit()
            conn.close()
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

    if request.method == 'POST':
        nombre = request.form.get('nombre')
        precio_usd = request.form.get('precio_usd')
        descripcion = request.form.get('descripcion')
        codigo_barras = request.form.get('codigo_barras')
        imagen_archivo = request.files.get('imagen')

        conn = get_db_connection(row_factory=sqlite3.Row)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT imagen_url FROM productos WHERE id = ? AND comercio_id = ?',
            (producto_id, comercio_id),
        )
        prod_previo = cursor.fetchone()
        imagen_url = prod_previo['imagen_url'] if prod_previo else None

        if imagen_archivo and getattr(imagen_archivo, 'filename', ''):
            imagen_url = procesar_imagen_para_producto(
                imagen_archivo,
                codigo_barras,
                nombre,
                descripcion,
                comercio_id=comercio_id,
            )
        elif not imagen_url or imagen_url == '/static/images/default-product.webp':
            imagen_url = procesar_imagen_para_producto(
                None, codigo_barras, nombre, descripcion, comercio_id=comercio_id
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
        conn.commit()
        conn.close()

        flash('Producto actualizado con éxito.', 'exito')
        return redirect(url_for('panel_comercio'))

    conn = get_db_connection(row_factory=sqlite3.Row)
    cursor = conn.cursor()
    cursor.execute(
        'SELECT * FROM productos WHERE id = ? AND comercio_id = ?',
        (producto_id, comercio_id),
    )
    producto_row = cursor.fetchone()
    conn.close()

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

    archivo = request.files.get('archivo_csv')
    exito, mensaje = procesar_csv_productos(
        comercio['id'], archivo, upload_folder=app.config['UPLOAD_FOLDER']
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


@app.route('/comercio/suscripcion/solicitar-pago', methods=['POST'])
@login_requerido
def suscripcion_solicitar_pago():
    comercio = obtener_comercio_por_usuario(session.get('usuario_id'))
    if not comercio:
        flash('Comercio no encontrado.', 'error')
        return redirect(url_for('panel_comercio'))

    plan_tipo = request.form.get('plan_tipo', 'basica')
    referencia = request.form.get('referencia', '').strip()
    fecha_transferencia = request.form.get('fecha_transferencia', '').strip()

    exito, mensaje = registrar_pago_movil_plan(
        comercio['id'], plan_tipo, referencia, fecha_transferencia
    )
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('panel_comercio'))


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
        banner_url = comprimir_y_guardar(
            banner_archivo,
            app.config['UPLOAD_FOLDER'],
            prefijo='banner_app',
            max_dimension=1920,
        )
        if banner_url:
            exito, mensaje = actualizar_banner_principal(admin_id, banner_url)
        else:
            exito, mensaje = False, 'No se pudo procesar la imagen del banner.'
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
def api_confirmar_pago():
    """Endpoint preparado para integración con pasarela de pago."""
    data = request.get_json(silent=True) or {}
    comercio_id = data.get('comercio_id')
    plan_tipo = data.get('plan_tipo', 'basica')
    meses = data.get('meses', 1)

    if not comercio_id:
        return jsonify({'ok': False, 'mensaje': 'comercio_id requerido'}), 400

    exito, mensaje = confirmar_pago_suscripcion(comercio_id, plan_tipo, meses)
    return jsonify({'ok': exito, 'mensaje': mensaje}), 200 if exito else 400


if __name__ == '__main__':
    inicializar_base_de_datos()
    app.run(debug=True, port=5000)

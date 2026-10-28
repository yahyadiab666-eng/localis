"""Blueprint del panel del comerciante (/comercio/*).

Bloque 4c: panel, catalogo (alta/edicion/eliminacion), importacion CSV/Excel
asincrona, suscripcion y premios. Los endpoints pasan a llamarse
``comercio.<nombre>``; las referencias ``url_for`` se actualizan en el mismo
cambio. Los helpers de vista viven en ``backend/vistas_comercio.py``.
"""

from __future__ import annotations
import logging
logger = logging.getLogger('localis.blueprints.comercio')

import sqlite3
import traceback

import psycopg2
from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from config import WHATSAPP_SOPORTE, WHATSAPP_SOPORTE_URL, pagos_habilitados
from backend.analytics import resumen_interacciones
from backend.apariencia import normalizar_color_banner
from backend.boosts import estado_boosts_productos, opciones_boost
from backend.db import get_db_connection
from backend.image_lookup import persistir_imagen_producto_hibrida
from backend.import_queue import (
    ColaImportacionLlena,
    encolar_importacion,
    obtener_estado_job,
)
from backend.inventory_import import cargar_archivo_inventario
from backend.plans import PLANES
from backend.rate_limit import limite
from backend.referidos import PUNTOS_POR_DESTACADO, canjear_destacado, resumen_premios
from backend.stores import (
    actualizar_datos_comercio,
    actualizar_producto,
    asegurar_slug_producto,
    eliminar_producto,
    obtener_config,
    obtener_productos_comercio,
    registrar_comercio_completo,
)
from backend.subscriptions import (
    comercio_puede_gestionar_inventario,
    marcar_bienvenida_vista,
    obtener_avisos_suscripcion,
    obtener_datos_pago_movil,
    puede_agregar_producto,
    rechazar_renovacion_vencida,
)
from backend.ubicaciones import (
    normalizar_ubicaciones,
    ubicaciones_desde_form,
)
from backend.utils import (
    imagen_url_para_persistir,
    normalizar_codigo_barras,
    parsear_precio_form,
)
from backend.vistas_comercio import (
    bloquear_gestion_inventario,
    cargar_datos_comercio_usuario,
    planes_beneficios_para_comercio,
    procesar_logo_comercio,
    requiere_comercio,
)
from backend.vistas_imagenes import (
    debug_imagenes_antes_de_render,
    normalizar_imagenes_comercio,
    quitar_imagen_manual,
    registrar_imagen_manual,
    sincronizar_foto_local_si_aplica,
)
from backend.web_seguridad import (
    comercio_sesion_validado,
    login_requerido,
    peticion_acepta_json,
)

bp = Blueprint('comercio', __name__, url_prefix='/comercio')


@bp.route('')
@login_requerido
def panel_comercio():
    usuario_id = session.get('usuario_id')
    abrir_pago = request.args.get('abrir_pago')
    # --- RESTAURAR_PAGOS_INICIO ---
    # Con los pagos ocultos se ignora ``abrir_pago`` para no redirigir a Planes.
    if abrir_pago and pagos_habilitados():
        return redirect(url_for('comercio.comercio_planes', abrir_pago=abrir_pago))
    # --- RESTAURAR_PAGOS_FIN ---

    comercio = None
    productos = []
    tasa_actual = 1.0
    categorias = None
    try:
        comercio, productos, tasa_actual, categorias = cargar_datos_comercio_usuario(
            usuario_id
        )
    except Exception as error:
        # Trazabilidad total: nunca ocultar el error real.
        logger.error(f'[Localis] panel_comercio carga fallo: {type(error).__name__}: {error}')
        traceback.print_exc()
        comercio, productos, tasa_actual, categorias = None, [], 1.0, None

    if categorias is not None:
        return render_template('registro_comercio.html', categorias=categorias)

    # Cada dato secundario con valor por defecto: nunca dispara el banner rojo.
    comercio = comercio or {}
    try:
        plan_info = PLANES.get(comercio.get('plan_tipo') or 'gratis', PLANES['gratis'])
    except Exception:
        plan_info = PLANES.get('gratis', {})
    try:
        avisos = obtener_avisos_suscripcion(comercio) or {}
    except Exception as error:
        logger.error(f'[Localis] panel_comercio avisos fallo: {type(error).__name__}: {error}')
        traceback.print_exc()
        avisos = {}
    try:
        metricas = resumen_interacciones(comercio.get('id'), dias=30)
    except Exception as error:
        logger.error(f'[Localis] panel_comercio metricas fallo: {type(error).__name__}: {error}')
        traceback.print_exc()
        metricas = {'total': 0, 'detalle': [], 'productos_top': [], 'disponible': False, 'dias': 30}
    try:
        whatsapp = obtener_config('whatsapp_soporte', WHATSAPP_SOPORTE)
    except Exception:
        whatsapp = WHATSAPP_SOPORTE

    try:
        boosts = (
            estado_boosts_productos(comercio.get('id')) if comercio.get('id') else {}
        )
    except Exception as error:
        logger.error(f'[Localis] panel_comercio boosts fallo: {type(error).__name__}: {error}')
        boosts = {'productos': {}}

    # Datos de pago movil (misma fuente que el flujo de planes) para el modal.
    try:
        pago_movil = obtener_datos_pago_movil()
    except Exception as error:
        logger.error(f'[Localis] panel_comercio pago movil fallo: {type(error).__name__}: {error}')
        pago_movil = {}

    # Banners B2B de la portada (alquiler con pago móvil o Binance Pay).
    maximo_banners = 5
    try:
        from backend.banners_b2b import (
            MAX_BANNERS_ACTIVOS,
            cupos_banners,
            estado_banners_comercio,
            opciones_banner,
        )

        maximo_banners = MAX_BANNERS_ACTIVOS
        banners_b2b = (
            estado_banners_comercio(comercio.get('id')) if comercio.get('id') else []
        )
        banner_opciones = opciones_banner()
        banner_cupos = cupos_banners()
    except Exception as error:
        logger.error(
            f'[Localis] panel_comercio banners B2B fallo: {type(error).__name__}: {error}'
        )
        banners_b2b, banner_opciones = [], {}
        banner_cupos = {
            'maximo': maximo_banners,
            'ocupados': 0,
            'disponibles': maximo_banners,
        }

    # Premios y puntos (canje de destacados gratuitos).
    try:
        premios = resumen_premios(comercio.get('id')) if comercio.get('id') else {}
    except Exception as error:
        logger.error(f'[Localis] panel_comercio premios fallo: {type(error).__name__}: {error}')
        premios = {'puntos': 0, 'puntos_por_destacado': PUNTOS_POR_DESTACADO}

    productos = productos or []
    debug_imagenes_antes_de_render(productos, 'panel_comercio')
    try:
        return render_template(
            'comercio.html',
            comercio=comercio,
            productos=productos,
            tasa=tasa_actual or 1.0,
            whatsapp=whatsapp,
            whatsapp_url=WHATSAPP_SOPORTE_URL,
            plan_info=plan_info,
            avisos=avisos,
            metricas=metricas,
            boosts=boosts,
            opciones_boost=opciones_boost(),
            banners_b2b=banners_b2b,
            banner_opciones=banner_opciones,
            banner_cupos=banner_cupos,
            banners_portada_total=banner_cupos.get('ocupados', 0),
            pago_movil=pago_movil,
            premios=premios,
            nav_activo='panel',
        )
    except Exception as error:
        # Sin pantallas artificiales: se registra el error real y se propaga al
        # manejador global (que tambien deja el traceback en consola).
        logger.error(f'[Localis] panel_comercio render fallo: {type(error).__name__}: {error}')
        traceback.print_exc()
        raise


@bp.route('/planes')
@login_requerido
def comercio_planes():
    usuario_id = session.get('usuario_id')
    # --- RESTAURAR_PAGOS_INICIO ---
    # Vista de Planes/Suscripcion/Pagos oculta: se redirige limpiamente al panel
    # sin romper la navegacion del comerciante.
    if not pagos_habilitados():
        return redirect(url_for('comercio.panel_comercio'))
    # --- RESTAURAR_PAGOS_FIN ---
    try:
        comercio, productos, tasa_actual, categorias = cargar_datos_comercio_usuario(
            usuario_id
        )
        if categorias is not None:
            return redirect(url_for('comercio.panel_comercio'))

        plan_info = PLANES.get(comercio.get('plan_tipo', 'gratis'), PLANES['gratis'])
        avisos = obtener_avisos_suscripcion(comercio)
        pago_movil = obtener_datos_pago_movil()
        planes_beneficios = planes_beneficios_para_comercio(comercio, tasa_actual)

        # Congelamiento temporal: en fase de lanzamiento no se abre el modal de
        # pago aunque el enlace traiga ``abrir_pago``. El codigo de pago sigue
        # intacto y se reactiva con LOCALIS_PAGOS_HABILITADOS=1.
        abrir_pago = request.args.get('abrir_pago')
        if not pagos_habilitados():
            abrir_pago = None

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
            abrir_pago=abrir_pago,
            nav_activo='planes',
        )
    except psycopg2.Error:
        raise
    except Exception as error:
        logger.error(f'Error al cargar planes del comercio: {error}')
        flash('No se pudo cargar la página de planes.', 'error')
        return redirect(url_for('comercio.panel_comercio'))


@bp.route('/panel')
@login_requerido
def panel_comercio_legacy():
    """Compatibilidad con enlaces antiguos a /comercio/panel."""
    return redirect(url_for('comercio.panel_comercio'))


@bp.route('/seleccionar', methods=['GET', 'POST'])
@login_requerido
def comercio_seleccionar_legacy():
    """Hub de seleccion eliminado: redirige al panel directo."""
    return redirect(url_for('comercio.panel_comercio'))


@bp.route('/registrar', methods=['GET', 'POST'])
@login_requerido
def registrar_comercio_legacy():
    """Compatibilidad con la ruta /comercio/registrar."""
    return redirect(url_for('comercio.crear_comercio'))


@bp.route('/crear', methods=['GET', 'POST'])
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
        documento_identidad = request.form.get('documento_identidad', '').strip()
        banner_color = normalizar_color_banner(request.form.get('banner_color'))
        referido_por = request.form.get('referido_por', '').strip()

        # Ubicaciones: lista de sedes (nombre + enlace de Google Maps). Ya no se
        # capturan dirección/ciudad/zona a mano; se derivan del enlace de Maps.
        ubicaciones = ubicaciones_desde_form(request.form)
        ok_ubicaciones, lista_ubicaciones = normalizar_ubicaciones(ubicaciones)
        if not ok_ubicaciones:
            flash(lista_ubicaciones, 'error')
            return redirect(url_for('comercio.crear_comercio'))
        if not lista_ubicaciones:
            flash(
                'Agrega al menos una ubicación con su nombre y enlace de Google Maps.',
                'error',
            )
            return redirect(url_for('comercio.crear_comercio'))

        categoria_raw = request.form.get('categoria_id')
        if not categoria_raw or not str(categoria_raw).strip().isdigit():
            flash('Debes seleccionar una categoría válida.', 'error')
            return redirect(url_for('comercio.crear_comercio'))
        categoria_id = int(categoria_raw)
        logo_archivo = request.files.get('logo')

        logo_url = None
        if logo_archivo and logo_archivo.filename:
            logo_url, aviso_logo = procesar_logo_comercio(
                logo_archivo, prefijo=f'logo_nuevo_{usuario_id}'
            )
            if aviso_logo:
                flash(aviso_logo, 'info')

        exito, resultado = registrar_comercio_completo(
            usuario_id,
            nombre,
            descripcion,
            telefono,
            '',
            categoria_id,
            logo_url=logo_url,
            documento_identidad=documento_identidad or None,
            banner_color=banner_color,
            referido_por=referido_por or None,
            ubicaciones=lista_ubicaciones,
        )

        if exito:
            flash(
                'Comercio registrado con éxito. Tienes 30 días de prueba gratuita.',
                'exito',
            )
        else:
            flash(resultado, 'error')
        return redirect(url_for('comercio.panel_comercio'))
    except psycopg2.Error:
        raise
    except Exception as error:
        logger.error(f'Error al registrar comercio: {error}')
        flash('No se pudo registrar el comercio. Intenta de nuevo.', 'error')
        return redirect(url_for('comercio.panel_comercio'))


@bp.route('/editar', methods=['GET', 'POST'])
@login_requerido
def editar_comercio():
    comercio, redireccion = requiere_comercio()
    if redireccion:
        return redireccion

    if request.method == 'POST':
        nombre = request.form.get('nombre', '').strip()
        telefono = request.form.get('telefono', '').strip()
        descripcion = request.form.get('descripcion', '').strip()
        banner_color = normalizar_color_banner(request.form.get('banner_color'))
        logo_archivo = request.files.get('logo')

        # Ubicaciones (repeater): nombre + enlace de Google Maps por sede.
        ubicaciones = ubicaciones_desde_form(request.form)
        ok_ubicaciones, lista_ubicaciones = normalizar_ubicaciones(ubicaciones)
        if not ok_ubicaciones:
            flash(lista_ubicaciones, 'error')
            return redirect(url_for('comercio.editar_comercio'))
        if not lista_ubicaciones:
            flash(
                'Agrega al menos una ubicación con su nombre y enlace de Google Maps.',
                'error',
            )
            return redirect(url_for('comercio.editar_comercio'))

        logo_url = None
        if logo_archivo and logo_archivo.filename:
            logo_url, aviso_logo = procesar_logo_comercio(
                logo_archivo, prefijo=f'logo_{comercio["id"]}'
            )
            if aviso_logo:
                flash(aviso_logo, 'info')

        if not nombre:
            flash('El nombre del comercio es obligatorio.', 'error')
            return redirect(url_for('comercio.editar_comercio'))

        exito, mensaje = actualizar_datos_comercio(
            comercio['id'],
            nombre,
            telefono,
            '',
            descripcion=descripcion,
            logo_url=logo_url,
            banner_color=banner_color,
            ubicaciones=lista_ubicaciones,
        )
        flash(mensaje, 'exito' if exito else 'error')
        return redirect(url_for('comercio.panel_comercio'))

    return render_template(
        'editar_comercio.html',
        comercio=normalizar_imagenes_comercio(comercio),
        banner_color_actual=normalizar_color_banner(comercio.get('banner_color')),
        nav_activo='editar',
    )


@bp.route('/producto/nuevo', methods=['GET', 'POST'])
@login_requerido
def nuevo_producto():
    comercio, redireccion = requiere_comercio()
    if redireccion:
        return redireccion

    bloqueo = bloquear_gestion_inventario(comercio)
    if bloqueo:
        return bloqueo

    if request.method == 'POST':
        nombre = request.form.get('nombre')
        descripcion = request.form.get('descripcion')
        precio_raw = request.form.get('precio_usd')
        codigo_barras = normalizar_codigo_barras(request.form.get('codigo_barras'))
        imagen_archivo = request.files.get('imagen')
        imagen_url_form = request.form.get('imagen_url')

        precio_usd, error_precio = parsear_precio_form(precio_raw)
        if not nombre or not nombre.strip():
            flash('El nombre es obligatorio.', 'error')
            return redirect(url_for('comercio.nuevo_producto'))
        if error_precio:
            flash(error_precio, 'error')
            return redirect(url_for('comercio.nuevo_producto'))

        # --- RESTAURAR_PAGOS_INICIO ---
        # En la fase gratuita no se aplican limites de plan al crear productos.
        if pagos_habilitados():
            ok, msg_limite = puede_agregar_producto(comercio['id'])
            if not ok:
                flash(msg_limite, 'error')
                destino = url_for('comercio.panel_comercio')
                if 'vencido' in msg_limite.lower() or 'límite' in msg_limite.lower():
                    destino = url_for('comercio.comercio_planes', abrir_pago='pro')
                return redirect(destino)
        # --- RESTAURAR_PAGOS_FIN ---

        try:
            imagen_url, aviso_img = persistir_imagen_producto_hibrida(
                file_storage=imagen_archivo,
                codigo_barras=codigo_barras,
                nombre=nombre,
                descripcion=descripcion,
                comercio_id=comercio['id'],
                imagen_url_form=imagen_url_form,
            )
            if aviso_img:
                flash(aviso_img, 'info')
        except Exception as error:
            logger.error(f'[Localis Imagen] hibrido alta producto: {error}')
            traceback.print_exc()
            imagen_url = imagen_url_para_persistir(imagen_url_form)

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    '''
                    INSERT INTO productos (comercio_id, nombre, descripcion, precio_usd, codigo_barras, imagen_url)
                    VALUES (?, ?, ?, ?, ?, ?)
                    RETURNING id
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
                fila = cursor.fetchone()
                producto_id = fila[0] if fila else None
                if producto_id:
                    try:
                        from backend.codigos_publicos import asignar_codigo_producto

                        asignar_codigo_producto(cursor, producto_id)
                    except Exception as error:
                        print(
                            f'[Localis Codigos] producto {producto_id}: '
                            f'{type(error).__name__}: {error}',
                            flush=True,
                        )
            sincronizar_foto_local_si_aplica(producto_id, imagen_url)
            registrar_imagen_manual(producto_id, imagen_url)
            asegurar_slug_producto(producto_id, nombre)
            flash('Producto agregado con éxito.', 'exito')
            return redirect(url_for('comercio.panel_comercio'))
        except Exception as e:
            flash(f'Error al agregar producto: {str(e)}', 'error')

    return render_template('nuevo_producto.html')


@bp.route('/producto/editar/<int:producto_id>', methods=['GET', 'POST'])
@login_requerido
def editar_producto(producto_id):
    comercio, redireccion = requiere_comercio()
    if redireccion:
        return redireccion

    comercio_id = comercio['id']

    bloqueo = bloquear_gestion_inventario(comercio)
    if bloqueo:
        return bloqueo

    if request.method == 'POST':
        nombre = request.form.get('nombre')
        precio_raw = request.form.get('precio_usd')
        descripcion = request.form.get('descripcion')
        codigo_barras = normalizar_codigo_barras(request.form.get('codigo_barras'))
        imagen_archivo = request.files.get('imagen')
        imagen_url_form = request.form.get('imagen_url')
        quitar_imagen = str(request.form.get('quitar_imagen') or '').strip().lower() in (
            '1', 'on', 'true', 'si', 'sí', 'yes',
        )

        precio_usd, error_precio = parsear_precio_form(precio_raw)
        if not nombre or not nombre.strip():
            flash('El nombre es obligatorio.', 'error')
            return redirect(url_for('comercio.editar_producto', producto_id=producto_id))
        if error_precio:
            flash(error_precio, 'error')
            return redirect(url_for('comercio.editar_producto', producto_id=producto_id))

        try:
            imagen_url = None
            hay_archivo = bool(
                imagen_archivo and getattr(imagen_archivo, 'filename', '')
            )
            incluir_imagen = False
            if hay_archivo:
                imagen_url, aviso_img = persistir_imagen_producto_hibrida(
                    file_storage=imagen_archivo,
                    codigo_barras=codigo_barras,
                    nombre=nombre,
                    descripcion=descripcion,
                    comercio_id=comercio_id,
                    imagen_url_form=imagen_url_form,
                )
                if aviso_img:
                    flash(aviso_img, 'info')
                incluir_imagen = bool(imagen_url)
            elif imagen_url_form:
                imagen_url = imagen_url_para_persistir(imagen_url_form)
                incluir_imagen = bool(imagen_url)

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
            if exito and incluir_imagen:
                sincronizar_foto_local_si_aplica(producto_id, imagen_url)
                registrar_imagen_manual(producto_id, imagen_url)
            elif exito and quitar_imagen:
                # Descarta la manual y revierte a la automatica (si existe).
                quitar_imagen_manual(producto_id)
            flash(mensaje, 'exito' if exito else 'error')
            return redirect(url_for('comercio.panel_comercio'))
        except Exception as error:
            flash(f'Error al actualizar producto: {error}', 'error')
            return redirect(url_for('comercio.editar_producto', producto_id=producto_id))

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
        return redirect(url_for('comercio.panel_comercio'))

    if not producto_row:
        flash('El producto no existe o no tienes permiso para modificarlo.', 'error')
        return redirect(url_for('comercio.panel_comercio'))

    producto = dict(producto_row)
    debug_imagenes_antes_de_render([producto], 'editar_producto')
    return render_template('nuevo_producto.html', producto=producto)


@bp.route('/producto/eliminar/<int:producto_id>', methods=['POST'])
@login_requerido
def eliminar_producto_ruta(producto_id):
    comercio, redireccion = requiere_comercio()
    if redireccion:
        return redireccion

    bloqueo = bloquear_gestion_inventario(comercio)
    if bloqueo:
        return bloqueo

    try:
        from backend.imagenes_producto import obtener_manual, purgar_manual

        manual = obtener_manual(producto_id)
    except Exception:
        manual = None

    exito, mensaje = eliminar_producto(producto_id, comercio['id'])
    if exito and manual:
        # Ciclo de vida manual: al eliminar el producto se borra su manual.
        try:
            purgar_manual(manual)
        except Exception as error:
            logger.error(f'[Localis Imagen] purga manual al eliminar producto: {error}')
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('comercio.panel_comercio'))


@bp.route('/productos/cargar-csv', methods=['POST'])
@limite('10 per minute')
@login_requerido
def cargar_csv():
    """Encola la importacion y responde 202 sin bloquear la peticion HTTP.

    - Valida comercio, plan y archivo de forma sincrona (rapido).
    - Encola el trabajo con ``queue`` + hilos daemon (contexto de Flask).
    - Devuelve ``HTTP 202 Accepted`` con un ``job_id``; el cliente hace polling
      en ``/comercio/productos/importacion/<job_id>``.
    """
    comercio, redireccion = requiere_comercio()
    if redireccion:
        return redireccion

    respuesta_json = peticion_acepta_json()

    # --- RESTAURAR_PAGOS_INICIO ---
    # En la fase gratuita la importacion nunca se bloquea por plan/suscripcion.
    if pagos_habilitados():
        ok_gestion, mensaje_gestion = comercio_puede_gestionar_inventario(comercio['id'])
        if not ok_gestion:
            if respuesta_json:
                return (
                    jsonify({'ok': False, 'error': mensaje_gestion, 'plan_sugerido': 'pro'}),
                    403,
                )
            flash(mensaje_gestion, 'error')
            return redirect(url_for('comercio.comercio_planes', abrir_pago='pro'))
    # --- RESTAURAR_PAGOS_FIN ---

    archivo = request.files.get('archivo_csv')
    print(
        f'[Localis CSV] POST comercio={comercio["id"]} '
        f'archivo={getattr(archivo, "filename", None)!r} modo=asincrono'
    )

    data, _extension, error_lectura = cargar_archivo_inventario(archivo)
    if error_lectura:
        if respuesta_json:
            return jsonify({'ok': False, 'error': error_lectura}), 400
        flash(error_lectura, 'error')
        return redirect(url_for('comercio.panel_comercio'))

    try:
        job = encolar_importacion(
            comercio['id'],
            getattr(archivo, 'filename', 'inventario.csv'),
            data,
            usuario_id=session.get('usuario_id'),
        )
    except ColaImportacionLlena as error:
        if respuesta_json:
            return jsonify({'ok': False, 'error': str(error)}), 503
        flash(str(error), 'error')
        return redirect(url_for('comercio.panel_comercio'))
    except ValueError as error:
        if respuesta_json:
            return jsonify({'ok': False, 'error': str(error)}), 400
        flash(str(error), 'error')
        return redirect(url_for('comercio.panel_comercio'))

    mensaje = 'Tu catálogo se está procesando en segundo plano.'
    if respuesta_json:
        return (
            jsonify(
                {
                    'ok': True,
                    'job_id': job['job_id'],
                    'estado': job['estado'],
                    'mensaje': mensaje,
                    'estado_url': url_for(
                        'comercio.estado_importacion', job_id=job['job_id']
                    ),
                }
            ),
            202,
        )

    flash(mensaje, 'info')
    return redirect(url_for('comercio.panel_comercio'))


@bp.route('/productos/importacion/<job_id>')
@login_requerido
def estado_importacion(job_id):
    """Estado del trabajo de importacion para el polling del panel."""
    comercio, redireccion = requiere_comercio()
    if redireccion:
        return redireccion

    estado = obtener_estado_job(job_id, comercio_id=comercio['id'])
    if not estado:
        return jsonify({'ok': False, 'error': 'Trabajo no encontrado o expirado.'}), 404
    return jsonify({'ok': True, **estado}), 200


@bp.route('/productos/cargar-csv', methods=['GET'])
@login_requerido
def cargar_csv_get():
    """Evita 405 si el usuario recarga la URL del POST de importacion."""
    flash('Selecciona un archivo CSV o Excel desde el panel de comercio.', 'info')
    return redirect(url_for('comercio.panel_comercio'))


@bp.route('/suscripcion/marcar-bienvenida', methods=['POST'])
@login_requerido
def suscripcion_marcar_bienvenida():
    # --- RESTAURAR_PAGOS_INICIO ---
    if not pagos_habilitados():
        return redirect(url_for('comercio.panel_comercio'))
    # --- RESTAURAR_PAGOS_FIN ---
    comercio = comercio_sesion_validado()
    if comercio:
        marcar_bienvenida_vista(comercio['id'])
    return redirect(url_for('comercio.panel_comercio'))


@bp.route('/suscripcion/rechazar-vencido', methods=['POST'])
@login_requerido
def suscripcion_rechazar_vencido():
    # --- RESTAURAR_PAGOS_INICIO ---
    if not pagos_habilitados():
        return redirect(url_for('comercio.panel_comercio'))
    # --- RESTAURAR_PAGOS_FIN ---
    comercio, redireccion = requiere_comercio()
    if redireccion:
        return redireccion

    exito, mensaje = rechazar_renovacion_vencida(comercio['id'])
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('comercio.panel_comercio'))


@bp.route('/suscripcion/solicitar-pago', methods=['GET', 'POST'])
@login_requerido
def suscripcion_solicitar_pago():
    """
    Ruta legacy conservada por compatibilidad.
    Redirige al panel de comercio y abre el modal de pago OCR automatico.
    """
    # --- RESTAURAR_PAGOS_INICIO ---
    if not pagos_habilitados():
        return redirect(url_for('comercio.panel_comercio'))
    # --- RESTAURAR_PAGOS_FIN ---
    comercio, redireccion = requiere_comercio()
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
    return redirect(url_for('comercio.comercio_planes', abrir_pago=plan_tipo))


@bp.route('/boost/canjear', methods=['POST'])
@login_requerido
def boost_canjear_puntos():
    """Canjea puntos por un destacado gratis de un producto (sin pago movil)."""
    comercio, redireccion = requiere_comercio()
    if redireccion:
        return redireccion

    producto_id = request.form.get('producto_id')
    # El modal usa "dias_puntos" (para no chocar con el selector de pago movil);
    # el formulario de la seccion Premios usa "dias".
    dias = request.form.get('dias_puntos') or request.form.get('dias')
    exito, mensaje = canjear_destacado(comercio['id'], producto_id, dias)
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('comercio.panel_comercio'))


@bp.route('/premios')
@login_requerido
def comercio_premios():
    """Seccion 'Premios y Puntos': saldo, referidos y catalogo de canjes."""
    comercio, redireccion = requiere_comercio()
    if redireccion:
        return redireccion

    try:
        premios = resumen_premios(comercio['id'])
    except Exception as error:
        logger.error(f'[Localis] premios fallo: {type(error).__name__}: {error}')
        premios = {
            'puntos': 0,
            'referidos_total': 0,
            'referidos': [],
            'canjes_total': 0,
            'puntos_por_destacado': PUNTOS_POR_DESTACADO,
            'dias_destacado': 7,
        }
    try:
        productos = obtener_productos_comercio(comercio['id'])
    except Exception:
        productos = []

    return render_template(
        'comercio_premios.html',
        comercio=normalizar_imagenes_comercio(comercio),
        productos=productos,
        premios=premios,
        nav_activo='premios',
    )

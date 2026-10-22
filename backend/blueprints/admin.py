"""Blueprint del panel de administracion (/admin/*).

Lote 1 del Bloque 4c: panel principal y listado de usuarios. Los endpoints
pasan a llamarse ``admin.panel_admin`` / ``admin.admin_usuarios``; las
referencias ``url_for`` se actualizan en el mismo cambio.
"""

from __future__ import annotations
import logging
logger = logging.getLogger('localis.blueprints.admin')

import traceback

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

from config import DEFAULT_BANNER_URL, WHATSAPP_SOPORTE, WHATSAPP_SOPORTE_URL
from backend.admin import (
    actualizar_banner_principal,
    actualizar_comercio_admin,
    actualizar_producto_admin,
    actualizar_tasa_dolar,
    alternar_destacado_producto,
    cambiar_plan_comercio,
    cambiar_visibilidad_comercio,
    crear_producto_admin,
    destacar_producto_por_dias,
    eliminar_comercio_definitivo,
    eliminar_producto_admin,
    obtener_bandeja_tecnica,
    contar_revisiones_pago_pendientes,
    obtener_banner_principal,
    obtener_categorias_admin,
    obtener_comercio_admin,
    obtener_producto_admin,
    obtener_productos_admin,
    obtener_todos_comercios_admin,
    obtener_usuarios_admin,
    reactivar_comercio,
    resolver_ticket_soporte,
    suspender_comercio_temporal,
)
from backend.apariencia import normalizar_color_banner
from backend.image_lookup import persistir_imagen_producto_hibrida
from backend.import_queue import (
    ColaImportacionLlena,
    encolar_importacion,
    obtener_estado_job,
)
from backend.inventory_import import cargar_archivo_inventario
from backend.plans import PLANES
from backend.pre_cuentas import (
    crear_pre_cuenta_admin,
    reasignar_propietario_comercio,
    transferir_propiedad_comercio,
)
from backend.stores import asegurar_slug_producto, obtener_config, obtener_tasa_dolar
from backend.utils import (
    imagen_url_para_persistir,
    normalizar_codigo_barras,
    parsear_precio_form,
    parsear_visible_form,
    url_maps_comercio,
    url_whatsapp_comercio,
)
from backend.vistas_imagenes import (
    normalizar_imagenes_comercio,
    procesar_imagen_subida,
    quitar_imagen_manual,
    registrar_imagen_manual,
    sincronizar_foto_local_si_aplica,
)
from backend.web_seguridad import admin_requerido, peticion_acepta_json
from utils.images import url_hero_inicio

bp = Blueprint('admin', __name__, url_prefix='/admin')


@bp.route('')
@admin_requerido
def panel_admin():
    estado_filtro = request.args.get('estado_ticket', None)
    tipo_filtro = request.args.get('tipo_ticket', None)
    busqueda = request.args.get('q', '').strip()
    tickets = obtener_bandeja_tecnica(
        estado_filtro=estado_filtro, tipo_filtro=tipo_filtro
    )
    tasa_actual = obtener_tasa_dolar()
    comercios = obtener_todos_comercios_admin(busqueda=busqueda or None)
    banner_url = url_hero_inicio(obtener_banner_principal()) or DEFAULT_BANNER_URL
    whatsapp = obtener_config('whatsapp_soporte', WHATSAPP_SOPORTE)

    return render_template(
        'admin.html',
        tasa=tasa_actual,
        comercios=comercios,
        tickets=tickets,
        revisiones_pendientes=contar_revisiones_pago_pendientes(),
        estado_ticket=estado_filtro or '',
        tipo_ticket=tipo_filtro or '',
        banner_url=banner_url,
        whatsapp=whatsapp,
        whatsapp_url=WHATSAPP_SOPORTE_URL,
        planes=PLANES,
        categorias=obtener_categorias_admin(),
        q=busqueda,
    )


@bp.route('/usuarios')
@admin_requerido
def admin_usuarios():
    """Listado de todos los usuarios registrados (auditoria de perfiles)."""
    busqueda = request.args.get('q', '').strip()
    usuarios = obtener_usuarios_admin(busqueda=busqueda or None)
    return render_template(
        'admin_usuarios.html',
        usuarios=usuarios,
        q=busqueda,
    )


@bp.route('/tasa', methods=['POST'])
@admin_requerido
def actualizar_tasa():
    nueva_tasa = request.form.get('tasa_dolar')
    exito, mensaje = actualizar_tasa_dolar(session.get('usuario_id'), nueva_tasa)
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('admin.panel_admin'))


@bp.route('/banner', methods=['POST'])
@admin_requerido
def admin_banner():
    banner_archivo = request.files.get('banner')
    admin_id = session.get('usuario_id')

    if banner_archivo and banner_archivo.filename:
        banner_url, aviso_banner = procesar_imagen_subida(
            banner_archivo,
            prefijo='banner_app',
            carpeta='banners',
            max_dimension=1920,
        )
        if aviso_banner:
            flash(aviso_banner, 'info')
            exito, mensaje = False, aviso_banner
        elif banner_url:
            exito, mensaje = actualizar_banner_principal(admin_id, banner_url)
        else:
            exito, mensaje = False, 'No se pudo procesar el banner.'
    else:
        exito, mensaje = False, 'Debes seleccionar una imagen.'

    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('admin.panel_admin'))


@bp.route('/comercio/estado/<int:comercio_id>', methods=['POST'])
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
    return redirect(url_for('admin.panel_admin'))


@bp.route('/comercio/suspender/<int:comercio_id>', methods=['POST'])
@admin_requerido
def admin_suspender_comercio(comercio_id):
    exito, mensaje = suspender_comercio_temporal(
        session.get('usuario_id'), comercio_id
    )
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('admin.panel_admin'))


@bp.route('/comercio/reactivar/<int:comercio_id>', methods=['POST'])
@admin_requerido
def admin_reactivar_comercio(comercio_id):
    exito, mensaje = reactivar_comercio(session.get('usuario_id'), comercio_id)
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('admin.panel_admin'))


@bp.route('/pre-cuenta/nueva', methods=['POST'])
@admin_requerido
def admin_crear_pre_cuenta():
    """Pre-crea un comercio (estado ``pre_cuenta``) sin exigir registro previo."""
    admin_id = session.get('usuario_id')
    categoria_raw = request.form.get('categoria_id')
    categoria_id = None
    if categoria_raw and str(categoria_raw).strip().isdigit():
        categoria_id = int(categoria_raw)
    datos = {
        'nombre': request.form.get('nombre', ''),
        'descripcion': request.form.get('descripcion', ''),
        'telefono': request.form.get('telefono', ''),
        'direccion': request.form.get('direccion', ''),
        'ciudad': request.form.get('ciudad', ''),
        'zona': request.form.get('zona', ''),
        'categoria_id': categoria_id,
    }
    exito, mensaje, comercio_id = crear_pre_cuenta_admin(admin_id, datos)
    if peticion_acepta_json():
        return (
            jsonify({'ok': bool(exito), 'mensaje': mensaje, 'comercio_id': comercio_id}),
            200 if exito else 400,
        )
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('admin.panel_admin'))


@bp.route('/comercio/<int:comercio_id>/transferir', methods=['POST'])
@admin_requerido
def admin_transferir_comercio(comercio_id):
    """Consolida/transfiere la propiedad de una tienda a un correo (Gmail)."""
    admin_id = session.get('usuario_id')
    email = request.form.get('owner_email', '')
    exito, mensaje, vinculado = transferir_propiedad_comercio(
        admin_id, comercio_id, email
    )
    if peticion_acepta_json():
        return (
            jsonify({'ok': bool(exito), 'mensaje': mensaje, 'vinculado': bool(vinculado)}),
            200 if exito else 400,
        )
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('admin.panel_admin'))


@bp.route('/comercio/<int:comercio_id>/reasignar', methods=['POST'])
@admin_requerido
def admin_reasignar_comercio(comercio_id):
    """Cambia el dueño de una tienda ya normal (sin reiniciar su plan)."""
    admin_id = session.get('usuario_id')
    email = request.form.get('owner_email', '')
    exito, mensaje, vinculado = reasignar_propietario_comercio(
        admin_id, comercio_id, email
    )
    if peticion_acepta_json():
        return (
            jsonify({'ok': bool(exito), 'mensaje': mensaje, 'vinculado': bool(vinculado)}),
            200 if exito else 400,
        )
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('admin.panel_admin'))


@bp.route('/comercio/eliminar/<int:comercio_id>', methods=['POST'])
@admin_requerido
def admin_eliminar_comercio(comercio_id):
    exito, mensaje = eliminar_comercio_definitivo(
        session.get('usuario_id'), comercio_id
    )
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('admin.panel_admin'))


@bp.route('/comercio/plan/<int:comercio_id>', methods=['POST'])
@admin_requerido
def admin_cambiar_plan(comercio_id):
    plan_tipo = request.form.get('plan_tipo', 'basica')
    estado_pago = request.form.get('estado_pago')
    exito, mensaje = cambiar_plan_comercio(
        session.get('usuario_id'), comercio_id, plan_tipo, estado_pago
    )
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('admin.panel_admin'))


@bp.route('/ticket/resolver/<int:ticket_id>', methods=['POST'])
@admin_requerido
def admin_resolver_ticket(ticket_id):
    exito, mensaje = resolver_ticket_soporte(ticket_id)
    flash(mensaje, 'exito' if exito else 'error')
    return redirect(url_for('admin.panel_admin'))


@bp.route('/comercio/<int:comercio_id>')
@admin_requerido
def admin_comercio_detalle(comercio_id):
    """Ficha de acceso total: tienda + perfil del dueño + catálogo.

    Permite al administrador visualizar y auditar cualquier comercio registrado
    y gestionar su estado, plan y productos sin depender del comerciante.
    """
    comercio = obtener_comercio_admin(comercio_id)
    if not comercio:
        flash('Comercio no encontrado.', 'error')
        return redirect(url_for('admin.panel_admin'))

    busqueda = request.args.get('q', '').strip()
    productos = obtener_productos_admin(comercio_id, busqueda=busqueda or None)
    comercio = normalizar_imagenes_comercio(comercio)
    try:
        comercio['maps_link'] = url_maps_comercio(comercio)
    except Exception:
        comercio['maps_link'] = None
    try:
        comercio['telefono_whatsapp_url'] = url_whatsapp_comercio(
            comercio.get('telefono'), 'Hola, te escribimos desde Localis.'
        )
    except Exception:
        comercio['telefono_whatsapp_url'] = None

    tasa_actual = obtener_tasa_dolar()
    return render_template(
        'admin_comercio.html',
        comercio=comercio,
        productos=productos,
        tasa=tasa_actual,
        planes=PLANES,
        q=busqueda,
    )


@bp.route('/comercio/<int:comercio_id>/editar', methods=['GET', 'POST'])
@admin_requerido
def admin_editar_comercio(comercio_id):
    """Onboarding asistido: el admin edita el perfil de cualquier tienda.

    Permite ajustar nombre, descripción, banner, logo, teléfono, categoría y
    visibilidad como si fuera el dueño, sin depender del comerciante.
    """
    comercio = obtener_comercio_admin(comercio_id)
    if not comercio:
        flash('Comercio no encontrado.', 'error')
        return redirect(url_for('admin.panel_admin'))

    categorias = obtener_categorias_admin()

    if request.method == 'POST':
        nombre = (request.form.get('nombre') or '').strip()
        if not nombre:
            flash('El nombre del comercio es obligatorio.', 'error')
            return redirect(url_for('admin.admin_editar_comercio', comercio_id=comercio_id))

        logo_url = None
        banner_url = None
        logo_archivo = request.files.get('logo')
        banner_archivo = request.files.get('banner')

        if logo_archivo and logo_archivo.filename:
            logo_url, aviso_logo = procesar_imagen_subida(
                logo_archivo,
                prefijo=f'logo_admin_{comercio_id}',
                carpeta='comercios',
            )
            if aviso_logo:
                flash(aviso_logo, 'info')

        if banner_archivo and banner_archivo.filename:
            banner_url, aviso_banner = procesar_imagen_subida(
                banner_archivo,
                prefijo=f'banner_admin_{comercio_id}',
                carpeta='comercios',
                max_dimension=1920,
            )
            if aviso_banner:
                flash(aviso_banner, 'info')

        categoria_raw = request.form.get('categoria_id')
        categoria_id = (
            int(categoria_raw)
            if categoria_raw and str(categoria_raw).strip().isdigit()
            else None
        )

        exito, mensaje = actualizar_comercio_admin(
            session.get('usuario_id'),
            comercio_id,
            {
                'nombre': nombre,
                'descripcion': request.form.get('descripcion', '').strip(),
                'telefono': request.form.get('telefono', '').strip(),
                'documento_identidad': request.form.get('documento_identidad', '').strip(),
                'direccion': request.form.get('direccion', '').strip(),
                'ciudad': request.form.get('ciudad', '').strip(),
                'zona': request.form.get('zona', '').strip(),
                'maps_url': request.form.get('maps_url', '').strip(),
                'categoria_id': categoria_id,
                'visible': request.form.get('visible'),
                'banner_color': request.form.get('banner_color'),
            },
            logo_url=logo_url,
            banner_url=banner_url,
        )
        flash(mensaje, 'exito' if exito else 'error')
        return redirect(url_for('admin.admin_comercio_detalle', comercio_id=comercio_id))

    comercio = normalizar_imagenes_comercio(comercio)
    return render_template(
        'admin_editar_comercio.html',
        comercio=comercio,
        categorias=categorias,
        banner_color_actual=normalizar_color_banner(comercio.get('banner_color')),
        nav_activo='admin',
    )


@bp.route('/comercio/<int:comercio_id>/producto/nuevo', methods=['GET', 'POST'])
@admin_requerido
def admin_nuevo_producto(comercio_id):
    """Onboarding asistido: el admin carga productos en cualquier comercio."""
    comercio = obtener_comercio_admin(comercio_id)
    if not comercio:
        flash('Comercio no encontrado.', 'error')
        return redirect(url_for('admin.panel_admin'))

    if request.method == 'POST':
        nombre = (request.form.get('nombre') or '').strip()
        descripcion = (request.form.get('descripcion') or '').strip()
        precio_raw = request.form.get('precio_usd')
        codigo_barras = normalizar_codigo_barras(request.form.get('codigo_barras'))
        imagen_archivo = request.files.get('imagen')
        imagen_url_form = request.form.get('imagen_url')

        if not nombre:
            flash('El nombre es obligatorio.', 'error')
            return redirect(url_for('admin.admin_nuevo_producto', comercio_id=comercio_id))

        precio_usd, error_precio = parsear_precio_form(precio_raw)
        if error_precio:
            flash(error_precio, 'error')
            return redirect(url_for('admin.admin_nuevo_producto', comercio_id=comercio_id))

        imagen_url = None
        try:
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
        except Exception as error:
            logger.error(f'[Localis Admin Imagen] alta producto: {error}')
            traceback.print_exc()
            imagen_url = imagen_url_para_persistir(imagen_url_form)

        exito, mensaje, producto_id = crear_producto_admin(
            session.get('usuario_id'),
            comercio_id,
            nombre,
            descripcion,
            precio_usd,
            codigo_barras=codigo_barras,
            imagen_url=imagen_url,
        )
        if exito:
            sincronizar_foto_local_si_aplica(producto_id, imagen_url)
            registrar_imagen_manual(producto_id, imagen_url)
            asegurar_slug_producto(producto_id, nombre)
        flash(mensaje, 'exito' if exito else 'error')
        return redirect(url_for('admin.admin_comercio_detalle', comercio_id=comercio_id))

    return render_template(
        'admin_producto.html',
        comercio=comercio,
        producto=None,
        nav_activo='admin',
    )


@bp.route(
    '/comercio/<int:comercio_id>/producto/<int:producto_id>/editar',
    methods=['GET', 'POST'],
)
@admin_requerido
def admin_editar_producto(comercio_id, producto_id):
    """Onboarding asistido: el admin edita productos de cualquier comercio."""
    comercio = obtener_comercio_admin(comercio_id)
    if not comercio:
        flash('Comercio no encontrado.', 'error')
        return redirect(url_for('admin.panel_admin'))

    producto = obtener_producto_admin(producto_id, comercio_id=comercio_id)
    if not producto:
        flash('El producto no existe para este comercio.', 'error')
        return redirect(url_for('admin.admin_comercio_detalle', comercio_id=comercio_id))

    if request.method == 'POST':
        nombre = (request.form.get('nombre') or '').strip()
        descripcion = (request.form.get('descripcion') or '').strip()
        precio_raw = request.form.get('precio_usd')
        codigo_barras = normalizar_codigo_barras(request.form.get('codigo_barras'))
        imagen_archivo = request.files.get('imagen')
        imagen_url_form = request.form.get('imagen_url')
        quitar_imagen = str(request.form.get('quitar_imagen') or '').strip().lower() in (
            '1', 'on', 'true', 'si', 'sí', 'yes',
        )

        if not nombre:
            flash('El nombre es obligatorio.', 'error')
            return redirect(
                url_for(
                    'admin.admin_editar_producto',
                    comercio_id=comercio_id,
                    producto_id=producto_id,
                )
            )

        precio_usd, error_precio = parsear_precio_form(precio_raw)
        if error_precio:
            flash(error_precio, 'error')
            return redirect(
                url_for(
                    'admin.admin_editar_producto',
                    comercio_id=comercio_id,
                    producto_id=producto_id,
                )
            )

        imagen_url = None
        incluir_imagen = False
        try:
            hay_archivo = bool(
                imagen_archivo and getattr(imagen_archivo, 'filename', '')
            )
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

            exito, mensaje = actualizar_producto_admin(
                session.get('usuario_id'),
                producto_id,
                comercio_id,
                nombre,
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
                quitar_imagen_manual(producto_id)
            flash(mensaje, 'exito' if exito else 'error')
        except Exception as error:
            flash(f'Error al actualizar producto: {error}', 'error')
        return redirect(url_for('admin.admin_comercio_detalle', comercio_id=comercio_id))

    return render_template(
        'admin_producto.html',
        comercio=comercio,
        producto=producto,
        nav_activo='admin',
    )


@bp.route('/comercio/<int:comercio_id>/productos/cargar-csv', methods=['POST'])
@admin_requerido
def admin_cargar_csv(comercio_id):
    """Onboarding asistido: importación masiva CSV/Excel en cualquier comercio.

    Reutiliza exactamente el mismo pipeline asíncrono del comerciante
    (``cargar_archivo_inventario`` + ``encolar_importacion``) sin tocar su lógica.
    """
    comercio = obtener_comercio_admin(comercio_id)
    if not comercio:
        if peticion_acepta_json():
            return jsonify({'ok': False, 'error': 'Comercio no encontrado.'}), 404
        flash('Comercio no encontrado.', 'error')
        return redirect(url_for('admin.panel_admin'))

    respuesta_json = peticion_acepta_json()
    archivo = request.files.get('archivo_csv')

    data, _extension, error_lectura = cargar_archivo_inventario(archivo)
    if error_lectura:
        if respuesta_json:
            return jsonify({'ok': False, 'error': error_lectura}), 400
        flash(error_lectura, 'error')
        return redirect(url_for('admin.admin_comercio_detalle', comercio_id=comercio_id))

    try:
        job = encolar_importacion(
            comercio_id,
            getattr(archivo, 'filename', 'inventario.csv'),
            data,
            usuario_id=session.get('usuario_id'),
        )
    except ColaImportacionLlena as error:
        if respuesta_json:
            return jsonify({'ok': False, 'error': str(error)}), 503
        flash(str(error), 'error')
        return redirect(url_for('admin.admin_comercio_detalle', comercio_id=comercio_id))
    except ValueError as error:
        if respuesta_json:
            return jsonify({'ok': False, 'error': str(error)}), 400
        flash(str(error), 'error')
        return redirect(url_for('admin.admin_comercio_detalle', comercio_id=comercio_id))

    mensaje = 'El catálogo de esta tienda se está procesando en segundo plano.'
    if respuesta_json:
        return (
            jsonify(
                {
                    'ok': True,
                    'job_id': job['job_id'],
                    'estado': job['estado'],
                    'mensaje': mensaje,
                    'estado_url': url_for(
                        'admin.admin_estado_importacion',
                        comercio_id=comercio_id,
                        job_id=job['job_id'],
                    ),
                }
            ),
            202,
        )

    flash(mensaje, 'info')
    return redirect(url_for('admin.admin_comercio_detalle', comercio_id=comercio_id))


@bp.route('/comercio/<int:comercio_id>/productos/importacion/<job_id>')
@admin_requerido
def admin_estado_importacion(comercio_id, job_id):
    """Estado de un job de importación lanzado desde el panel admin."""
    estado = obtener_estado_job(job_id, comercio_id=comercio_id)
    if not estado:
        return jsonify({'ok': False, 'error': 'Trabajo no encontrado o expirado.'}), 404
    return jsonify({'ok': True, **estado}), 200


@bp.route('/producto/eliminar/<int:producto_id>', methods=['POST'])
@admin_requerido
def admin_eliminar_producto_ruta(producto_id):
    """Elimina un producto de cualquier comercio desde el panel admin."""
    comercio_id = request.form.get('comercio_id', type=int)
    exito, mensaje = eliminar_producto_admin(
        producto_id, session.get('usuario_id'), comercio_id=comercio_id
    )
    flash(mensaje, 'exito' if exito else 'error')
    if comercio_id:
        return redirect(
            url_for('admin.admin_comercio_detalle', comercio_id=comercio_id)
        )
    return redirect(url_for('admin.panel_admin'))


@bp.route('/producto/destacar/<int:producto_id>', methods=['POST'])
@admin_requerido
def admin_destacar_producto(producto_id):
    """Destacado de un producto: por tiempo (días) o toggle manual.

    - Si llega ``dias`` (p. ej. 3/5/7/30) se asigna duración y se fija
      ``boost_fin = ahora + dias``; ``dias <= 0`` revoca el destacado.
    - Si no llega ``dias``, se mantiene el interruptor manual de un clic.
    """
    dias = request.form.get('dias', None)
    if dias is None and request.is_json:
        datos = request.get_json(silent=True) or {}
        dias = datos.get('dias')

    if dias is not None and str(dias).strip() != '':
        exito, mensaje, info = destacar_producto_por_dias(
            session.get('usuario_id'), producto_id, dias
        )
        if peticion_acepta_json():
            return (
                jsonify({'ok': exito, 'mensaje': mensaje, 'info': info}),
                200 if exito else 400,
            )
        flash(mensaje, 'exito' if exito else 'error')
        comercio_id = request.form.get('comercio_id', type=int)
        if comercio_id:
            return redirect(
                url_for('admin.admin_comercio_detalle', comercio_id=comercio_id)
            )
        return redirect(url_for('admin.panel_admin'))

    valor = request.form.get('destacado', request.form.get('nuevo_estado'))
    if valor is None and request.is_json:
        datos = request.get_json(silent=True) or {}
        valor = datos.get('destacado', datos.get('nuevo_estado'))
    if valor is None:
        producto = obtener_producto_admin(producto_id)
        if not producto:
            if peticion_acepta_json():
                return jsonify({'ok': False, 'error': 'Producto no encontrado.'}), 404
            flash('Producto no encontrado.', 'error')
            return redirect(url_for('admin.panel_admin'))
        valor = 0 if str(producto.get('destacado') or 0) in ('1', 'true', 'True') else 1

    exito, mensaje, nuevo = alternar_destacado_producto(
        session.get('usuario_id'), producto_id, valor
    )
    if peticion_acepta_json():
        return (
            jsonify({'ok': exito, 'mensaje': mensaje, 'destacado': bool(nuevo)}),
            200 if exito else 400,
        )
    flash(mensaje, 'exito' if exito else 'error')
    comercio_id = request.form.get('comercio_id', type=int)
    if comercio_id:
        return redirect(url_for('admin.admin_comercio_detalle', comercio_id=comercio_id))
    return redirect(url_for('admin.panel_admin'))

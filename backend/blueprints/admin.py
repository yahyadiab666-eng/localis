"""Blueprint del panel de administracion (/admin/*).

Lote 1 del Bloque 4c: panel principal y listado de usuarios. Los endpoints
pasan a llamarse ``admin.panel_admin`` / ``admin.admin_usuarios``; las
referencias ``url_for`` se actualizan en el mismo cambio.
"""

from __future__ import annotations

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
    actualizar_tasa_dolar,
    cambiar_plan_comercio,
    cambiar_visibilidad_comercio,
    eliminar_comercio_definitivo,
    obtener_bandeja_tecnica,
    obtener_banner_principal,
    obtener_categorias_admin,
    obtener_comercio_admin,
    obtener_productos_admin,
    obtener_todos_comercios_admin,
    obtener_usuarios_admin,
    reactivar_comercio,
    resolver_ticket_soporte,
    suspender_comercio_temporal,
)
from backend.plans import PLANES
from backend.pre_cuentas import (
    crear_pre_cuenta_admin,
    reasignar_propietario_comercio,
    transferir_propiedad_comercio,
)
from backend.stores import obtener_config, obtener_tasa_dolar
from backend.utils import (
    parsear_visible_form,
    url_maps_comercio,
    url_whatsapp_comercio,
)
from backend.vistas_imagenes import (
    normalizar_imagenes_comercio,
    procesar_imagen_subida,
)
from backend.web_seguridad import admin_requerido, peticion_acepta_json
from utils.images import url_hero_inicio

bp = Blueprint('admin', __name__, url_prefix='/admin')


@bp.route('')
@admin_requerido
def panel_admin():
    estado_filtro = request.args.get('estado_ticket', None)
    busqueda = request.args.get('q', '').strip()
    tickets = obtener_bandeja_tecnica(estado_filtro=estado_filtro)
    tasa_actual = obtener_tasa_dolar()
    comercios = obtener_todos_comercios_admin(busqueda=busqueda or None)
    banner_url = url_hero_inicio(obtener_banner_principal()) or DEFAULT_BANNER_URL
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

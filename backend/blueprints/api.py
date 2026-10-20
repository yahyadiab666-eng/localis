"""Blueprint de la API JSON (rutas /api/*).

Extraido de ``main.py`` (Bloque 4). Sus endpoints se consumen por URL literal
desde JS (no por ``url_for``), asi que moverlos no rompe plantillas ni enlaces.

Se importa y registra al FINAL de main.py, despues de que exista el objeto CSRF
(``registrar_csrf``), para que ``exento_csrf`` funcione.
"""

from __future__ import annotations

import traceback

from flask import Blueprint, jsonify, session

from backend.auth import obtener_usuario_por_id
from backend.session_comercio import es_usuario_comerciante
from backend.stores import obtener_comercio_por_usuario, obtener_producto_publico
from backend.tasa_bcv import estado_tasa_bcv, obtener_tasa_bcv
from backend.utils import url_whatsapp_comercio
from backend.web_seguridad import exento_csrf, login_requerido_api
from utils.images import PLACEHOLDER_PRODUCTO, url_imagen_producto_segura

bp = Blueprint('api', __name__, url_prefix='/api')


@bp.route('/tasa-bcv')
def api_tasa_bcv_actual():
    """Tasa vigente en caché (sin forzar red) + metadatos de la caché."""
    return jsonify({'tasa_bcv': obtener_tasa_bcv(), 'cache': estado_tasa_bcv()}), 200


@bp.route('/actualizar-tasa', methods=['POST'])
@exento_csrf
def api_actualizar_tasa():
    """Fuerza la sincronizacion manual de la tasa oficial del BCV (solo POST).

    Requiere sesion iniciada. Antes aceptaba GET sin autenticacion, lo que
    permitia dispararlo desde un `<img>`/enlace (abuso de recursos). Devuelve
    200 si la tasa es fresca; 503 si se sirvio desde un respaldo. Nunca
    propaga excepciones a Flask.
    """
    if 'usuario_id' not in session:
        return jsonify({'ok': False, 'error': 'No autorizado.'}), 401
    try:
        resultado = obtener_tasa_bcv(forzar_actualizacion=True)
        return jsonify(resultado), (200 if resultado.get('ok') else 503)
    except Exception as error:
        return jsonify({'ok': False, 'error': f'{type(error).__name__}: {error}'}), 500


@bp.route('/producto/<int:producto_id>')
def api_producto(producto_id):
    producto = obtener_producto_publico(producto_id)
    if not producto:
        return jsonify({'error': 'Producto no encontrado'}), 404
    try:
        producto['imagen_url'] = url_imagen_producto_segura(producto)
    except Exception:
        traceback.print_exc()
        producto['imagen_url'] = PLACEHOLDER_PRODUCTO
    # Contacto directo al WhatsApp del comercio con el producto en el mensaje.
    try:
        nombre_producto = (producto.get('nombre') or 'un producto').strip()
        producto['comercio_whatsapp_url'] = url_whatsapp_comercio(
            producto.get('comercio_telefono'),
            f'¡Hola! Vi "{nombre_producto}" en Localis y me interesa.',
        )
    except Exception:
        producto['comercio_whatsapp_url'] = None
    return jsonify(producto)


@bp.route('/me')
@login_requerido_api
def api_me():
    """Perfil JSON del usuario autenticado (rol actual y ``has_store``).

    Endpoint aditivo: no reemplaza ``/perfil`` ni altera respuestas existentes.
    Permite a los clientes saber si deben mostrar interfaces de administración.
    """
    usuario_id = session.get('usuario_id')
    es_admin = bool(session.get('es_admin'))

    usuario = None
    try:
        usuario = obtener_usuario_por_id(usuario_id)
    except Exception as error:
        print(f'[Localis API] /api/me usuario fallo: {type(error).__name__}: {error}')
        usuario = None

    has_store = False
    comercio_id = None
    try:
        has_store = bool(es_usuario_comerciante(usuario_id))
        if has_store:
            comercio = obtener_comercio_por_usuario(usuario_id)
            comercio_id = comercio.get('id') if comercio else None
    except Exception as error:
        print(f'[Localis API] /api/me comercio fallo: {type(error).__name__}: {error}')
        has_store = False

    if es_admin:
        rol = 'admin'
    elif has_store:
        rol = 'comerciante'
    else:
        rol = (usuario or {}).get('rol') or session.get('rol') or 'user'

    return jsonify(
        {
            'id': usuario_id,
            'nombre': (usuario or {}).get('nombre') or session.get('username'),
            'correo': (usuario or {}).get('correo') or session.get('correo'),
            'foto_url': (usuario or {}).get('foto_url') or session.get('foto_url'),
            'rol': rol,
            'es_admin': es_admin,
            'has_store': bool(has_store),
            'comercio_id': comercio_id,
        }
    )

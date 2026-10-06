"""Contexto de sesión Flask para comerciantes (panel, inventario, pagos)."""

from flask import session

from backend.stores import obtener_comercio_por_usuario

CLAVE_COMERCIO_ID = 'comercio_id'
CLAVE_PANEL_ACTIVO = 'panel_comercio_activo'


def vincular_comercio_en_sesion(comercio_id):
    """Persiste el comercio activo en la sesión del usuario."""
    if comercio_id:
        session[CLAVE_COMERCIO_ID] = int(comercio_id)
        session[CLAVE_PANEL_ACTIVO] = True
        # Fase 2: un usuario con comercio cuenta como "comerciante" para las
        # redirecciones. Al registrar/renovar su tienda pasa a ser comerciante.
        session['es_comerciante'] = True
        session.modified = True


def limpiar_contexto_comercio():
    session.pop(CLAVE_COMERCIO_ID, None)
    session.pop(CLAVE_PANEL_ACTIVO, None)
    session.pop('es_comerciante', None)
    session.modified = True


def es_usuario_comerciante(usuario_id=None):
    """True si el usuario autenticado posee un comercio (capacidad de comerciante).

    Fase 2: separa comerciantes de clientes. Es retrocompatible: si la sesión es
    antigua y no trae ``es_comerciante``, se deriva del ``comercio_id`` en sesión
    o de la base de datos (sin romper nada).
    """
    if not usuario_id:
        usuario_id = session.get('usuario_id')
    if not usuario_id:
        return False
    if 'es_comerciante' in session:
        return bool(session.get('es_comerciante'))
    if session.get(CLAVE_COMERCIO_ID):
        session['es_comerciante'] = True
        session.modified = True
        return True
    try:
        comercio = obtener_comercio_por_usuario(usuario_id)
    except Exception:
        return False
    if comercio:
        vincular_comercio_en_sesion(comercio['id'])
        return True
    session['es_comerciante'] = False
    session.modified = True
    return False


def asegurar_contexto_comercio(usuario_id):
    """
    Garantiza comercio_id en sesión para comerciantes autenticados.
    Retorna el id del comercio o None.
    """
    if not usuario_id or session.get('es_admin'):
        return None
    if session.get(CLAVE_COMERCIO_ID):
        return session[CLAVE_COMERCIO_ID]
    comercio = obtener_comercio_por_usuario(usuario_id)
    if comercio:
        vincular_comercio_en_sesion(comercio['id'])
        return comercio['id']
    return None


def destino_panel_usuario():
    """Destino seguro según capacidad (Fase 2).

    - Admin -> panel de administración.
    - Comerciante con negocio -> panel de comercio.
    - Cliente (sin negocio) -> catálogo público (``index``).
    """
    if session.get('es_admin'):
        return 'panel_admin'
    if session.get('usuario_id'):
        asegurar_contexto_comercio(session.get('usuario_id'))
        if es_usuario_comerciante():
            return 'panel_comercio'
        return 'index'
    return 'index'


def es_ruta_comercio(path):
    return path.startswith('/comercio') or path.startswith('/api/pagos')

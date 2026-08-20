"""Contexto de sesión Flask para comerciantes (panel, inventario, pagos)."""

from flask import session

from backend.stores import listar_comercios_por_usuario, usuario_posee_comercio

CLAVE_COMERCIO_ID = 'comercio_id'
CLAVE_PANEL_ACTIVO = 'panel_comercio_activo'


def vincular_comercio_en_sesion(comercio_id):
    if comercio_id:
        session[CLAVE_COMERCIO_ID] = int(comercio_id)
        session[CLAVE_PANEL_ACTIVO] = True
        session.modified = True


def limpiar_contexto_comercio():
    session.pop(CLAVE_COMERCIO_ID, None)
    session.pop(CLAVE_PANEL_ACTIVO, None)
    session.modified = True


def asegurar_contexto_comercio(usuario_id):
    """
    Valida que comercio_id en sesión pertenezca al usuario autenticado.
    No asigna comercio automáticamente.
    """
    if not usuario_id or session.get('es_admin'):
        return None

    comercio_id = session.get(CLAVE_COMERCIO_ID)
    if not comercio_id:
        return None

    if usuario_posee_comercio(usuario_id, comercio_id):
        return int(comercio_id)

    limpiar_contexto_comercio()
    return None


def destino_panel_usuario():
    if session.get('es_admin'):
        return 'panel_admin'
    if session.get('usuario_id'):
        if asegurar_contexto_comercio(session.get('usuario_id')):
            return 'panel_comercio'
        return 'comercio_inicio'
    return 'index'


def es_ruta_comercio(path):
    return path.startswith('/comercio') or path.startswith('/api/pagos')

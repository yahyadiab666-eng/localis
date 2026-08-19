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
        session.modified = True


def limpiar_contexto_comercio():
    session.pop(CLAVE_COMERCIO_ID, None)
    session.pop(CLAVE_PANEL_ACTIVO, None)
    session.modified = True


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
    """Redirección segura según rol/contexto; evita mandar al index público por error."""
    if session.get('es_admin'):
        return 'panel_admin'
    if session.get('usuario_id'):
        asegurar_contexto_comercio(session.get('usuario_id'))
        return 'panel_comercio'
    return 'index'


def es_ruta_comercio(path):
    return path.startswith('/comercio') or path.startswith('/api/pagos')

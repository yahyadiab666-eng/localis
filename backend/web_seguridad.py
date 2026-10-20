"""Decoradores y utilidades web compartidas (sesion, CSRF, admin).

Se extraen de ``main.py`` para que tambien los usen los blueprints sin caer en
un import circular. ``main`` registra el objeto CSRF con ``registrar_csrf``
justo despues de ``CSRFProtect(app)``, de modo que ``exento_csrf`` funciona.
"""

from __future__ import annotations

from functools import wraps

import psycopg2
from flask import flash, jsonify, redirect, request, session, url_for

from backend.db import get_db_connection
from backend.session_comercio import asegurar_contexto_comercio

_csrf = None


def registrar_csrf(csrf):
    """main llama esto tras ``CSRFProtect(app)`` para habilitar exento_csrf()."""
    global _csrf
    _csrf = csrf


def sesion_usuario_activa():
    """Verifica que el usuario de la sesion siga existiendo en PostgreSQL."""
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
            return redirect(url_for('auth.login'))
        if not sesion_usuario_activa():
            session.clear()
            flash(
                'Tu sesión expiró o ya no es válida. Inicia sesión nuevamente.',
                'error',
            )
            return redirect(url_for('auth.login'))
        asegurar_contexto_comercio(session.get('usuario_id'))
        return f(*args, **kwargs)

    return decorada


def login_requerido_api(f):
    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario_id' not in session:
            return jsonify({'error': 'Debes iniciar sesión.'}), 401
        if not sesion_usuario_activa():
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
            return redirect(url_for('auth.login'))
        if not sesion_usuario_activa():
            session.clear()
            flash('Tu sesión expiró. Inicia sesión nuevamente.', 'error')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)

    return decorada


def admin_requerido_api(f):
    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario_id' not in session or not session.get('es_admin'):
            return jsonify({'ok': False, 'mensaje': 'Acceso denegado.'}), 403
        if not sesion_usuario_activa():
            session.clear()
            return jsonify({'ok': False, 'mensaje': 'Sesión inválida o expirada.'}), 401
        return f(*args, **kwargs)

    return decorada


def peticion_acepta_json():
    """True si la peticion viene de fetch()/AJAX y espera JSON (no HTML)."""
    if request.headers.get('X-Requested-With') == 'fetch':
        return True
    acepta = (request.headers.get('Accept') or '').lower()
    return 'application/json' in acepta and 'text/html' not in acepta


def exento_csrf(funcion):
    """Marca una ruta publica como exenta de CSRF (con fallback si csrf es None)."""
    try:
        return _csrf.exempt(funcion) if _csrf is not None else funcion
    except Exception:
        return funcion


def comercio_sesion_validado():
    """Comercio activo validado contra PostgreSQL (HTML y API)."""
    from backend.stores import obtener_comercio_por_usuario

    usuario_id = session.get('usuario_id')
    asegurar_contexto_comercio(usuario_id)
    return obtener_comercio_por_usuario(usuario_id)

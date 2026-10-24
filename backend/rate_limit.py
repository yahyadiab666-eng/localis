"""Rate limiting (Flask-Limiter) para endpoints sensibles.

- Clave: usuario de sesion si hay, si no la IP.
- Almacenamiento en memoria (coherente con WEB_CONCURRENCY=1).
- Desactivable con ``LOCALIS_RATE_LIMIT=0``.
- ``limite(regla)`` devuelve un decorador no-op si el limiter no esta activo,
  de modo que la app funciona aunque falte la libreria.

Solo se limitan las rutas decoradas (``default_limits=[]``): paginas, estaticos
y health checks no se ven afectados.
"""

from __future__ import annotations

import logging
import os

limiter = None
_log = logging.getLogger('localis.ratelimit')


def _deshabilitado():
    return (os.environ.get('LOCALIS_RATE_LIMIT') or '1').strip().lower() in (
        '0',
        'false',
        'no',
        'off',
    )


def _clave():
    try:
        from flask import current_app, request, session

        # Sin cookie de sesion no se toca ``session``: una simple lectura marca
        # ``Vary: Cookie`` y eso impide cachear las paginas publicas anonimas.
        # OJO: el nombre sale de la config; ``current_app.session_cookie_name``
        # no existe en esta version de Flask.
        nombre = current_app.config.get('SESSION_COOKIE_NAME', 'session')
        if request.cookies.get(nombre):
            uid = session.get('usuario_id')
            if uid:
                return f'usuario:{uid}'
    except Exception:
        pass
    try:
        from flask_limiter.util import get_remote_address

        return get_remote_address()
    except Exception:
        return 'anon'


def configurar_rate_limit(app):
    """Crea el Limiter y lo asocia a la app. Retorna el limiter o None."""
    global limiter
    if _deshabilitado():
        _log.info('rate limiting deshabilitado (LOCALIS_RATE_LIMIT=0)')
        return None
    try:
        from flask_limiter import Limiter
    except Exception as error:
        _log.warning('Flask-Limiter no disponible: %s', error)
        return None
    limiter = Limiter(
        key_func=_clave,
        app=app,
        default_limits=[],
        storage_uri='memory://',
        headers_enabled=True,
    )
    _log.info('rate limiting activo (memoria, 1 worker)')
    return limiter


def limite(regla):
    """Decorador de limite; no-op si el limiter no esta activo."""

    def decorador(funcion):
        if limiter is None:
            return funcion
        return limiter.limit(regla)(funcion)

    return decorador

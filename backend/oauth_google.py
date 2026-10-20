"""Cliente OAuth de Google (login con Google).

Extraido de ``main.py`` (Bloque 4) para que el blueprint de autenticacion pueda
usar el mismo cliente que inicializa la app. El cliente se crea una sola vez
(``crear_oauth``) y las rutas lo obtienen con ``obtener_google``.
"""

from __future__ import annotations

import os

from authlib.integrations.flask_client import OAuth

_oauth = None
_google = None


def crear_oauth(app):
    """Inicializa OAuth y registra el proveedor Google. Devuelve el cliente."""
    global _oauth, _google
    _oauth = OAuth(app)
    client_id = os.getenv('GOOGLE_CLIENT_ID', '').strip()
    client_secret = os.getenv('GOOGLE_CLIENT_SECRET', '').strip()
    if client_id and client_secret:
        _google = _oauth.register(
            name='google',
            client_id=client_id,
            client_secret=client_secret,
            server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
            client_kwargs={'scope': 'openid email profile'},
        )
    return _google


def obtener_google():
    """Devuelve el cliente OAuth de Google (o None si no esta configurado)."""
    return _google


def google_configurado():
    """True si hay credenciales de Google en el entorno."""
    return bool(
        os.getenv('GOOGLE_CLIENT_ID', '').strip()
        and os.getenv('GOOGLE_CLIENT_SECRET', '').strip()
    )

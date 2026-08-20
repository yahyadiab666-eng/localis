import os
from datetime import timedelta

# Ruta absoluta a la raíz del proyecto (donde está este config.py)
RUTA_RAIZ = os.path.dirname(os.path.abspath(__file__))

# WhatsApp de soporte técnico (sin + ni espacios)
WHATSAPP_SOPORTE = os.environ.get("LOCALIS_WHATSAPP", "584125970507")
WHATSAPP_SOPORTE_URL = "https://wa.me/584125970507"

PAGO_MOVIL_DEFAULT = {
    "banco": "Banco Caribe",
    "cedula_rif": "30209716",
    "telefono": "04127957989",
}

# Tamaño máximo por petición multipart (imágenes, comprobantes, CSV/Excel).
MAX_UPLOAD_BYTES = int(os.environ.get('MAX_UPLOAD_BYTES', str(8 * 1024 * 1024)))

# Diagnóstico y alertas de errores críticos
ERROR_REPORT_EMAIL = os.environ.get('ERROR_REPORT_EMAIL', 'ydiab.t@gmail.com')
ENABLE_ERROR_EMAILS = os.environ.get('ENABLE_ERROR_EMAILS', 'true').lower() in (
    '1',
    'true',
    'yes',
)

# Sesión Flask (producción en nube)
SESSION_COOKIE_NAME = os.environ.get('SESSION_COOKIE_NAME', 'localis_session')
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
SESSION_LIFETIME_DAYS = int(os.environ.get('SESSION_LIFETIME_DAYS', '14'))


def es_entorno_produccion():
    """Detecta Render u otros despliegues productivos."""
    if os.environ.get('FLASK_ENV', '').lower() == 'production':
        return True
    if os.environ.get('RENDER', '').lower() == 'true':
        return True
    if os.environ.get('RENDER_EXTERNAL_URL'):
        return True
    return False


APP_ENV = 'production' if es_entorno_produccion() else os.environ.get(
    'FLASK_ENV', 'development'
)

SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER', '').strip()
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '').strip()
SMTP_FROM = os.environ.get('SMTP_FROM', SMTP_USER or 'localis@noreply.local')
SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', 'true').lower() in ('1', 'true', 'yes')
ERROR_EMAIL_COOLDOWN_SEC = int(os.environ.get('ERROR_EMAIL_COOLDOWN_SEC', '120'))

_DEV_SECRET_KEY = 'clave_secreta_localis_desarrollo'


def obtener_secret_key():
    """SECRET_KEY de sesión: obligatoria en producción."""
    clave = (os.environ.get('LOCALIS_SECRET_KEY') or '').strip()
    if clave:
        return clave
    if es_entorno_produccion():
        raise RuntimeError(
            'LOCALIS_SECRET_KEY no está configurada. '
            'Define una clave aleatoria segura en el entorno de producción.'
        )
    return _DEV_SECRET_KEY


def validar_config_arranque():
    """
    Valida variables críticas al iniciar la app.
    Retorna lista de advertencias (no bloqueantes).
    Lanza RuntimeError si falta configuración imprescindible.
    """
    errores = []
    advertencias = []

    if not (os.environ.get('DATABASE_URL') or '').strip():
        errores.append('DATABASE_URL no configurada (PostgreSQL es obligatorio).')
    if not (os.environ.get('DATABASE_KEY') or '').strip():
        errores.append('DATABASE_KEY no configurada (PostgreSQL es obligatorio).')

    if es_entorno_produccion():
        obtener_secret_key()
        if not (os.environ.get('GOOGLE_CLIENT_ID') or '').strip():
            advertencias.append(
                'GOOGLE_CLIENT_ID no configurado: el inicio de sesión con Google no funcionará.'
            )
    else:
        if not (os.environ.get('LOCALIS_SECRET_KEY') or '').strip():
            advertencias.append(
                'LOCALIS_SECRET_KEY no definida: usando clave de desarrollo (no usar en producción).'
            )

    if errores:
        raise RuntimeError('Configuración inválida: ' + ' | '.join(errores))

    return advertencias


def aplicar_config_sesion_flask(app):
    """Cookies de sesión seguras para despliegue en la nube."""
    app.config.update(
        SESSION_COOKIE_NAME=SESSION_COOKIE_NAME,
        SESSION_COOKIE_HTTPONLY=SESSION_COOKIE_HTTPONLY,
        SESSION_COOKIE_SAMESITE=SESSION_COOKIE_SAMESITE,
        SESSION_COOKIE_SECURE=es_entorno_produccion(),
        PERMANENT_SESSION_LIFETIME=timedelta(days=SESSION_LIFETIME_DAYS),
    )

import os

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
APP_ENV = os.environ.get('FLASK_ENV', os.environ.get('RENDER', 'production'))

SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '587'))
SMTP_USER = os.environ.get('SMTP_USER', '').strip()
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD', '').strip()
SMTP_FROM = os.environ.get('SMTP_FROM', SMTP_USER or 'localis@noreply.local')
SMTP_USE_TLS = os.environ.get('SMTP_USE_TLS', 'true').lower() in ('1', 'true', 'yes')
ERROR_EMAIL_COOLDOWN_SEC = int(os.environ.get('ERROR_EMAIL_COOLDOWN_SEC', '120'))

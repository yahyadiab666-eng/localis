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

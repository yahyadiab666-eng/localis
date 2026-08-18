import os

# Ruta absoluta a la raíz del proyecto (donde está este config.py)
RUTA_RAIZ = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(RUTA_RAIZ, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# WhatsApp de soporte técnico (sin + ni espacios)
WHATSAPP_SOPORTE = os.environ.get("LOCALIS_WHATSAPP", "584125970507")
WHATSAPP_SOPORTE_URL = "https://wa.me/584125970507"

PAGO_MOVIL_DEFAULT = {
    "banco": "Banesco",
    "cedula_rif": "J-501234567",
    "telefono": "04125970507",
}

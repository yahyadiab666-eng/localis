import os

# Ruta absoluta a la raíz del proyecto (donde está este config.py)
RUTA_RAIZ = os.path.dirname(os.path.abspath(__file__))

# Rutas globales centralizadas
DATABASE_FILE = os.path.join(RUTA_RAIZ, "database", "localis.db")
RUTA_SCHEMA = os.path.join(RUTA_RAIZ, "schema.sql")

# Crear la carpeta 'database' automáticamente si no existe
os.makedirs(os.path.dirname(DATABASE_FILE), exist_ok=True)

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
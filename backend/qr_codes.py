"""Generador de códigos QR para enlaces de tienda (Localis).

Ligero y autocontenido: usa la librería ``qrcode`` (con Pillow, ya dependencia
del proyecto) para producir un PNG en memoria. No requiere almacenamiento ni
procesamiento pesado: el QR se genera "al vuelo" en cada petición.
"""

import io

import qrcode
from qrcode.constants import ERROR_CORRECT_M


def generar_qr_png(url, box_size=10, border=2):
    """Devuelve los bytes PNG de un QR que codifica ``url``.

    :param url: enlace público de la tienda.
    :param box_size: tamaño de cada módulo en píxeles.
    :param border: grosor del borde silencioso (en módulos).
    """
    url = (url or '').strip()
    if not url:
        raise ValueError('URL vacía para el código QR.')

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)
    imagen = qr.make_image(fill_color='#111111', back_color='#ffffff')

    buffer = io.BytesIO()
    imagen.save(buffer, format='PNG')
    return buffer.getvalue()

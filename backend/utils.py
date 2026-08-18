"""Utilidades compartidas para URLs, contacto y fechas."""

import re
from datetime import date, datetime
from urllib.parse import quote


def formatear_fecha(valor):
    """Convierte datetime/date/str de PostgreSQL o SQLite a YYYY-MM-DD."""
    if valor is None or valor == '':
        return None
    if isinstance(valor, datetime):
        return valor.strftime('%Y-%m-%d')
    if isinstance(valor, date):
        return valor.strftime('%Y-%m-%d')
    texto = str(valor).strip()
    return texto[:10] if texto else None


def normalizar_telefono_whatsapp(telefono):
    """Convierte teléfono local VE a formato wa.me (58412...)."""
    if not telefono:
        return None
    digits = re.sub(r'\D', '', telefono)
    if not digits:
        return None
    if digits.startswith('0'):
        digits = '58' + digits[1:]
    elif len(digits) == 10 and digits.startswith('4'):
        digits = '58' + digits
    elif not digits.startswith('58'):
        digits = '58' + digits
    return digits


def url_whatsapp_comercio(telefono, texto=None):
    numero = normalizar_telefono_whatsapp(telefono)
    if not numero:
        return None
    msg = texto or 'Hola, vi tu tienda en Localis'
    return f'https://wa.me/{numero}?text={quote(msg)}'


def url_maps_comercio(comercio):
    """Resuelve URL de Google Maps desde enlace guardado o dirección textual."""
    maps_url = (comercio.get('maps_url') or '').strip()
    if maps_url:
        return maps_url

    partes = [
        comercio.get('direccion'),
        comercio.get('zona'),
        comercio.get('ciudad'),
    ]
    query = ', '.join(p.strip() for p in partes if p and str(p).strip())
    if query:
        return f'https://maps.google.com/?q={quote(query)}'
    return None

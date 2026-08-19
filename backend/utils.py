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


DEFAULT_IMAGEN_PRODUCTO = '/static/images/default-product.webp'
_VALORES_IMAGEN_VACIOS = frozenset({
    '',
    'none',
    'null',
    'nan',
    'n/a',
    '-',
    '__pending__',
})
_HYPERLINK_RE = re.compile(r'HYPERLINK\(\s*["\']([^"\']+)["\']', re.IGNORECASE)


def _bytes_a_texto(valor):
    if isinstance(valor, memoryview):
        valor = bytes(valor)
    if isinstance(valor, (bytes, bytearray)):
        try:
            return valor.decode('utf-8').strip()
        except UnicodeDecodeError:
            return None
    return None


def texto_campo_imagen(valor, default=None):
    """Convierte cualquier valor de celda/BD a texto de URL o ruta para PostgreSQL."""
    if valor is None:
        return default
    texto_bytes = _bytes_a_texto(valor)
    if texto_bytes is not None:
        valor = texto_bytes
    texto = str(valor).strip().strip('"').strip("'")
    if not texto or texto.lower() in _VALORES_IMAGEN_VACIOS:
        return default
    enlace = _HYPERLINK_RE.search(texto)
    if enlace:
        texto = enlace.group(1).strip()
    return texto[:2048] if texto else default


def normalizar_url_imagen(valor, default=DEFAULT_IMAGEN_PRODUCTO):
    """URL usable en <img src>. Conserva http(s) y rutas /static; el resto usa default."""
    texto = texto_campo_imagen(valor, default=None)
    if not texto:
        return default
    if texto.startswith(('http://', 'https://', '/')):
        return texto
    return default
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

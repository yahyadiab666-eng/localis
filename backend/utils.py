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
_VALORES_CODIGO_VACIOS = frozenset({
    '',
    'none',
    'null',
    'nan',
    'n/a',
    '-',
    'sin codigo',
    'sin código',
    's/c',
})
_HYPERLINK_RE = re.compile(r'HYPERLINK\(\s*["\']([^"\']+)["\']', re.IGNORECASE)
_MARCADORES_IMAGEN_GENERICA = (
    'default-product',
    'placeholder',
    'no-image',
    'sin-imagen',
)


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


def es_imagen_generica(valor):
    """True si el valor es vacío o un placeholder genérico (no usar como foto real)."""
    texto = texto_campo_imagen(valor, default=None)
    if not texto:
        return True
    lower = texto.lower()
    return any(marca in lower for marca in _MARCADORES_IMAGEN_GENERICA)


def url_imagen_usable(valor):
    """True si hay una URL/ruta real (http o absoluta), no un default genérico."""
    texto = texto_campo_imagen(valor, default=None)
    if not texto or es_imagen_generica(texto):
        return False
    return texto.startswith(('http://', 'https://', '/'))


def imagen_url_almacenada(valor):
    """URL persistible en BD, o None si está vacía o es placeholder genérico."""
    texto = texto_campo_imagen(valor, default=None)
    if not texto or es_imagen_generica(texto):
        return None
    return texto


def imagen_url_para_persistir(valor):
    """Normaliza un valor nuevo antes de INSERT; None si no hay imagen real."""
    return imagen_url_almacenada(valor)


def imagen_url_para_actualizacion(nueva, existente):
    """
    Conserva la imagen existente si la nueva entrada está vacía o es inválida.
    Nunca devuelve cadenas vacías: solo URL persistible o None.
    """
    persistida = imagen_url_para_persistir(nueva)
    if persistida:
        return persistida
    return imagen_url_almacenada(existente)


def normalizar_url_imagen(valor, default=None):
    """URL usable en <img src>. Conserva http(s) y rutas /; no inyecta default genérico."""
    texto = texto_campo_imagen(valor, default=None)
    if not texto or es_imagen_generica(texto):
        return default
    if texto.startswith(('http://', 'https://', '/')):
        return texto
    return default


def normalizar_codigo_barras(valor):
    """Normaliza EAN/SKU desde CSV/Excel/PostgreSQL (espacios, .0, notación científica)."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return str(valor) if valor else None
    if isinstance(valor, float):
        if valor != valor:  # NaN
            return None
        try:
            if valor == int(valor):
                return str(int(valor))
        except (OverflowError, ValueError):
            pass
        texto = str(valor).strip()
    else:
        texto_bytes = _bytes_a_texto(valor)
        texto = (texto_bytes if texto_bytes is not None else str(valor)).strip()

    if not texto:
        return None
    if texto.lower() in _VALORES_CODIGO_VACIOS:
        return None

    if re.fullmatch(r'[+-]?\d+(\.\d+)?[eE][+-]?\d+', texto):
        try:
            numero = float(texto)
            if numero == int(numero):
                return str(int(numero))
        except (OverflowError, ValueError):
            pass

    limpio = re.sub(r'\s+', '', texto)
    if re.fullmatch(r'\d+\.0+', limpio):
        limpio = limpio.split('.', 1)[0]
    return limpio or None


def normalizar_nombre_producto(valor):
    """Nombre comparable (minúsculas, espacios colapsados) para respaldo por título."""
    if valor is None:
        return None
    texto = str(valor).strip().lower()
    if not texto or texto in _VALORES_CODIGO_VACIOS:
        return None
    return ' '.join(texto.split()) or None


def parsear_precio_form(valor):
    """
    Convierte precio de formulario/POST a float para PostgreSQL DOUBLE PRECISION.
    Retorna (precio, None) o (None, mensaje_error).
    """
    if valor is None:
        return None, 'El precio es obligatorio.'
    texto = str(valor).strip()
    if not texto:
        return None, 'El precio es obligatorio.'
    limpio = texto.replace(',', '.')
    try:
        precio = float(limpio)
    except (TypeError, ValueError):
        return None, 'El precio debe ser un número válido (usa punto decimal).'
    if precio < 0:
        return None, 'El precio no puede ser negativo.'
    return round(precio, 2), None


def parsear_entero_form(valor, default=None, minimo=None):
    """Entero seguro desde formulario. Retorna (entero, error)."""
    if valor is None or str(valor).strip() == '':
        if default is not None:
            return default, None
        return None, 'Valor entero requerido.'
    try:
        numero = int(str(valor).strip())
    except (TypeError, ValueError):
        return None, 'Valor entero inválido.'
    if minimo is not None and numero < minimo:
        return None, f'El valor debe ser al menos {minimo}.'
    return numero, None


def parsear_visible_form(valor, default=1):
    """Normaliza checkbox/select de visibilidad a 0 o 1."""
    if valor is None or str(valor).strip() == '':
        return default
    texto = str(valor).strip().lower()
    if texto in ('1', 'true', 'on', 'si', 'sí', 'activo'):
        return 1
    if texto in ('0', 'false', 'off', 'no', 'inactivo'):
        return 0
    try:
        return 1 if int(texto) else 0
    except (TypeError, ValueError):
        return default


def normalizar_telefono_whatsapp(telefono):
    """Convierte teléfono local VE a formato wa.me (58412...)."""
    if not telefono:
        return None
    digits = re.sub(r'\D', '', str(telefono))
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

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
_MARCADORES_URL_ARTIFICIAL = (
    'pexels.com',
    '/static/images/',
    '/static/img/placeholder',
)
_HOSTS_CATALOGO_OFICIAL = (
    'images.openfoodfacts.org',
    'static.openfoodfacts.org',
    'world.openfoodfacts.org',
    'openfoodfacts.org',
    'openproductsfacts.org',
    'openbeautyfacts.org',
    'openpetfoodfacts.org',
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


def es_url_imagen_artificial(valor):
    """True si la URL es de prueba (OFF, wsrv, Pexels) o ruta local /static/."""
    texto = (texto_campo_imagen(valor, default=None) or '').lower()
    if not texto:
        return False
    return any(marca in texto for marca in _MARCADORES_URL_ARTIFICIAL)


def url_imagen_externa_valida(valor):
    """URL https genérica. Rechaza Pexels de prueba y /static/."""
    texto = texto_campo_imagen(valor, default=None)
    if not texto or es_imagen_generica(texto) or es_url_imagen_artificial(texto):
        return None
    if texto.startswith('https://'):
        return texto
    return None


def url_imagen_catalogo_valida(valor):
    """
    URL mostrable/persistible del catálogo: Storage público o foto oficial OFF.
    Nunca placeholder, Pexels genérico ni /static/.
    """
    storage = url_imagen_subida_storage_valida(valor)
    if storage:
        return storage
    texto = texto_campo_imagen(valor, default=None)
    if not texto or es_imagen_generica(texto):
        return None
    if not texto.lower().startswith('https://'):
        return None
    lower = texto.lower()
    if any(marca in lower for marca in ('placeholder', 'no-image', 'default-product', '.svg')):
        return None
    if any(host in lower for host in _HOSTS_CATALOGO_OFICIAL):
        if any(bad in lower for bad in ('/user/', 'avatar', 'no-image', 'placeholder')):
            return None
        if '_small' in lower:
            return None
        return texto
    if 'wsrv.nl' in lower and any(
        host in lower
        for host in (
            'openfoodfacts',
            'openproductsfacts',
            'openbeautyfacts',
            'openpetfoodfacts',
        )
    ):
        return texto
    return None


# Alias histórico
url_imagen_manual_valida = url_imagen_externa_valida


def url_imagen_subida_storage_valida(valor):
    """URL publica del bucket Supabase tras una subida manual de archivo."""
    from backend.supabase_client import corregir_typo_ruta_storage

    texto = texto_campo_imagen(valor, default=None)
    if not texto or es_imagen_generica(texto):
        return None
    texto = corregir_typo_ruta_storage(texto)
    texto_lower = texto.lower()
    if not texto_lower.startswith('https://'):
        return None
    if '/storage/v1/object/public/' not in texto_lower:
        return None
    return texto


def url_imagen_local_valida(valor):
    """Foto que el comerciante subió a mano, servida desde static/uploads/."""
    from backend.uploads_locales import url_upload_local_valida

    return url_upload_local_valida(valor)


# Alias histórico
url_imagen_supabase_valida = url_imagen_subida_storage_valida


def url_imagen_usable(valor):
    """True si hay URL pública de Supabase Storage."""
    return bool(url_imagen_subida_storage_valida(valor))


def imagen_url_almacenada(valor):
    """Valor persistible: Storage, catálogo oficial u upload local del comerciante."""
    return url_imagen_catalogo_valida(valor) or url_imagen_local_valida(valor)


def url_imagen_para_vista(valor):
    """URL para <img src>. Siempre str: catálogo usable o cadena vacía (nunca None)."""
    return url_imagen_catalogo_valida(valor) or ''


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


def es_url_subida_storage(valor):
    """True si el valor es enlace público de Supabase Storage."""
    return bool(url_imagen_subida_storage_valida(valor))


def es_url_externa_texto(valor):
    """True si el valor es URL https externa (catálogo/CSV), no subida a Storage."""
    return bool(url_imagen_externa_valida(valor) and not es_url_subida_storage(valor))


def normalizar_url_imagen(valor, default=None):
    """URL usable en <img src>. Conserva http(s) y rutas /; no inyecta default genérico."""
    texto = texto_campo_imagen(valor, default=None)
    if not texto or es_imagen_generica(texto):
        return default
    if texto.startswith(('http://', 'https://', '/')):
        return texto
    return default


def url_banner_principal(valor, default=None):
    """
    URL del banner promocional (hero). Conserva el Pexels de compras aprobado.
    """
    from config import DEFAULT_BANNER_URL

    fallback = default if default is not None else DEFAULT_BANNER_URL
    texto = texto_campo_imagen(valor, default=None)
    if not texto:
        return fallback

    lower = texto.lower()
    if 'pexels-photo-18618233' in lower:
        return texto
    if texto.startswith('/static/img/hero-compras.svg'):
        return fallback
    if texto.startswith('/static/') or (
        'pexels.com' in lower and '18618233' not in lower
    ):
        return fallback

    almacenada = imagen_url_almacenada(texto)
    if almacenada:
        return almacenada
    return fallback


def url_estatica_existe(ruta_relativa):
    """True si el archivo existe bajo la carpeta static/ del proyecto."""
    if not ruta_relativa or not str(ruta_relativa).startswith('/static/'):
        return False
    from config import RUTA_RAIZ
    import os

    relativa = str(ruta_relativa).replace('\\', '/').lstrip('/')
    if relativa.startswith('static/'):
        relativa = relativa[len('static/') :]
    destino = os.path.join(RUTA_RAIZ, 'static', relativa)
    return os.path.isfile(destino)


# Misma normalización SQL que en lecturas de inventario (Excel / CSV / PostgreSQL).
EXPR_CODIGO_BARRAS = (
    "regexp_replace("
    "regexp_replace(TRIM(BOTH FROM CAST(codigo_barras AS TEXT)), '\\s+', '', 'g'), "
    "'\\.0+$', '', 'g')"
)


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


def normalizar_clave_imagen_catalogo(nombre, descripcion=None):
    """
    Clave de archivo en bucket productos/ a partir de nombre + descripción.
    Ej.: 'Harina PAN' + '1kg blanca' → 'harina-pan-1kg-blanca'
    """
    partes = []
    for valor in (nombre, descripcion):
        norm = normalizar_nombre_producto(valor)
        if norm and norm not in partes:
            partes.append(norm)
    if not partes:
        return None
    slug = re.sub(r'[^a-z0-9]+', '-', ' '.join(partes))
    slug = re.sub(r'-+', '-', slug).strip('-')
    return slug[:180] if slug else None


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
    maps_url = (comercio.get('maps_url') or comercio.get('ubicacion_maps_url') or '').strip()
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


def validar_ubicacion_comercio(direccion, ciudad=None, zona=None, maps_url=None):
    """
    Valida y normaliza la ubicación física del comercio.
    Retorna (True, dict_datos) o (False, mensaje_error).
    """
    direccion = (direccion or '').strip()
    if len(direccion) < 5:
        return False, 'La dirección física del local es obligatoria (mínimo 5 caracteres).'

    ciudad_norm = (ciudad or '').strip() or None
    zona_norm = (zona or '').strip() or None
    maps_norm = (maps_url or '').strip() or None

    return True, {
        'direccion': direccion,
        'ciudad': ciudad_norm,
        'zona': zona_norm,
        'maps_url': maps_norm,
        'ubicacion_maps_url': maps_norm,
    }

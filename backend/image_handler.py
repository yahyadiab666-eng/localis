"""
Resolución de imágenes para vistas Flask.

Principio fail-safe: nunca llama a red, catálogo maestro, PostgREST ni Storage.
Si la URL es None, inválida o no es de Supabase Storage, devuelve un placeholder
local de inmediato. Nunca lanza excepciones hacia las rutas.
"""

PLACEHOLDER_PRODUCTO = '/static/img/placeholder-producto.svg'
PLACEHOLDER_BANNER = '/static/img/placeholder-banner.svg'
PLACEHOLDER_LOGO = '/static/img/placeholder-logo.svg'

_PREFIJO_STORAGE = '/storage/v1/object/public/'
_VALORES_VACIOS = frozenset({
    '',
    'none',
    'null',
    'nan',
    'n/a',
    '-',
    '__pending__',
})


def _texto_url(valor):
    if valor is None:
        return ''
    try:
        texto = str(valor).strip().strip('"').strip("'")
    except Exception:
        return ''
    if not texto or texto.lower() in _VALORES_VACIOS:
        return ''
    return texto[:2048]


def es_url_storage_publica(valor):
    """True si el valor ya es una URL pública de Supabase Storage (sin I/O)."""
    texto = _texto_url(valor)
    if not texto:
        return False
    lower = texto.lower().replace('/subase/', '/storage/')
    return lower.startswith('https://') and _PREFIJO_STORAGE in lower


def es_placeholder_local(valor):
    texto = _texto_url(valor).lower()
    return '/static/img/placeholder-' in texto


def url_storage_o_vacio(valor):
    """URL de Storage lista para <img>, o ''. No consulta red ni maestro."""
    try:
        texto = _texto_url(valor)
        if not texto:
            return ''
        texto = texto.replace('/subase/', '/storage/').replace('/Subase/', '/storage/')
        if es_url_storage_publica(texto):
            return texto
        return ''
    except Exception:
        return ''


def url_imagen_segura(valor, placeholder=PLACEHOLDER_PRODUCTO):
    """
    URL para mostrar: Storage persistida o placeholder local.
    Fail-safe: cualquier error o valor vacío retorna el placeholder.
    """
    try:
        url = url_storage_o_vacio(valor)
        if url:
            return url
    except Exception:
        pass
    return placeholder or PLACEHOLDER_PRODUCTO


def url_imagen_producto_segura(producto=None, imagen_url=None, codigo_barras=None):
    """
    Resuelve la foto de un producto para plantillas.
    codigo_barras se ignora a propósito: no hay lookup de catálogo en el render.
    """
    del codigo_barras
    try:
        if imagen_url is None and producto is not None:
            if hasattr(producto, 'get'):
                imagen_url = producto.get('imagen_url')
            else:
                imagen_url = getattr(producto, 'imagen_url', None)
        return url_imagen_segura(imagen_url, PLACEHOLDER_PRODUCTO)
    except Exception:
        return PLACEHOLDER_PRODUCTO


def url_banner_segura(valor, default=None):
    """Banner de la home: Storage ya persistida o placeholder local. Sin red."""
    try:
        url = url_storage_o_vacio(valor)
        if url:
            return url
        if default is not None:
            url = url_storage_o_vacio(default)
            if url:
                return url
            if es_placeholder_local(default):
                return default
        return PLACEHOLDER_BANNER
    except Exception:
        return PLACEHOLDER_BANNER


def url_logo_opcional(valor):
    """Logo de comercio: Storage o vacío (la plantilla usa icono). Nunca lanza."""
    return url_storage_o_vacio(valor) or None


def url_banner_comercio_opcional(valor):
    """Banner de tienda: Storage o vacío (la plantilla usa degradado). Nunca lanza."""
    return url_storage_o_vacio(valor) or None


def enriquecer_comercio_imagenes(comercio):
    """
    Añade logo_completo / banner_completo / tiene_banner sin I/O.
    No pisa el resto de campos. Si falla, el comercio se devuelve igual.
    """
    if not comercio:
        return comercio
    try:
        fila = dict(comercio)
        fila['logo_completo'] = url_logo_opcional(fila.get('logo_url'))
        banner = fila.get('banner_url') or fila.get('imagen_portada')
        fila['banner_completo'] = url_banner_comercio_opcional(banner)
        fila['tiene_banner'] = bool(fila.get('banner_completo'))
        return fila
    except Exception:
        try:
            comercio['logo_completo'] = None
            comercio['banner_completo'] = None
            comercio['tiene_banner'] = False
        except Exception:
            pass
        return comercio

"""
Helper centralizado de imágenes (fail-safe).

No consulta red, catálogo maestro ni Storage. Si el campo es None, vacío
o falla la lectura, retorna un placeholder local. Las rutas Flask deben
usar este módulo; los listados de Postgres no dependen de él.
"""

from backend.comercio_schema import (
    CANDIDATOS_BANNER,
    CANDIDATOS_LOGO,
    CANDIDATOS_PORTADA,
    valor_campo,
)

PLACEHOLDER_PRODUCTO = '/static/img/placeholder-producto.svg'
PLACEHOLDER_BANNER = '/static/img/placeholder-banner.svg'
PLACEHOLDER_LOGO = '/static/img/placeholder-logo.svg'
HERO_LOCAL = '/static/img/hero-compras.svg'

COL_PRODUCTO = 'imagen_url'

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
    texto = _texto_url(valor)
    if not texto:
        return False
    lower = texto.lower().replace('/subase/', '/storage/')
    return lower.startswith('https://') and _PREFIJO_STORAGE in lower


def es_asset_estatico_local(valor):
    texto = _texto_url(valor)
    return texto.startswith('/static/img/')


def es_placeholder_local(valor):
    return '/static/img/placeholder-' in _texto_url(valor).lower()


def url_storage_o_vacio(valor):
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


def url_mostrable(valor, permitir_estatico=False):
    """URL lista para <img src>, o ''. Nunca lanza."""
    try:
        texto = _texto_url(valor)
        if not texto:
            return ''
        texto = texto.replace('/subase/', '/storage/').replace('/Subase/', '/storage/')
        if es_url_storage_publica(texto):
            return texto
        if permitir_estatico and es_asset_estatico_local(texto):
            return texto
        return ''
    except Exception:
        return ''


def url_imagen_segura(valor, placeholder=PLACEHOLDER_PRODUCTO, permitir_estatico=False):
    """Storage (o estático permitido) o placeholder. Fail-safe absoluto."""
    try:
        url = url_mostrable(valor, permitir_estatico=permitir_estatico)
        if url:
            return url
    except Exception:
        pass
    return placeholder or PLACEHOLDER_PRODUCTO


def url_imagen_producto(producto=None, imagen_url=None, codigo_barras=None):
    """productos.imagen_url estrictamente. codigo_barras no dispara red."""
    del codigo_barras
    try:
        if imagen_url is None and producto is not None:
            imagen_url = valor_campo(producto, COL_PRODUCTO)
        return url_imagen_segura(imagen_url, PLACEHOLDER_PRODUCTO)
    except Exception:
        return PLACEHOLDER_PRODUCTO


# Alias usados por plantillas / image_handler.
url_imagen_producto_segura = url_imagen_producto


def url_banner_segura(valor, default=None):
    """Banner de home: Storage, estático local o hero de compras. Nunca None."""
    try:
        url = url_mostrable(valor, permitir_estatico=True)
        if url:
            return url
        if default is not None:
            url = url_mostrable(default, permitir_estatico=True)
            if url:
                return url
        return HERO_LOCAL
    except Exception:
        return HERO_LOCAL


def url_hero_inicio(banner_config=None, banner_comercio=None):
    """Hero del index: config → banner del comercio → ilustración local."""
    try:
        for candidato in (banner_config, banner_comercio, HERO_LOCAL):
            url = url_mostrable(candidato, permitir_estatico=True)
            if url:
                return url
        return HERO_LOCAL
    except Exception:
        return HERO_LOCAL


def url_logo_opcional(valor):
    return url_mostrable(valor) or None


def url_banner_comercio_opcional(valor):
    return url_mostrable(valor) or None


def enriquecer_comercio_imagenes(comercio):
    """logo_completo / banner_completo desde columnas oficiales o alias. Sin I/O."""
    if not comercio:
        return comercio
    try:
        fila = dict(comercio)
        logo = valor_campo(fila, *CANDIDATOS_LOGO)
        banner = valor_campo(fila, *CANDIDATOS_BANNER)
        fila['logo_url'] = logo
        fila['banner_url'] = banner
        fila['imagen_portada'] = valor_campo(fila, *CANDIDATOS_PORTADA)
        fila['logo_completo'] = url_logo_opcional(logo)
        fila['banner_completo'] = url_banner_comercio_opcional(banner)
        fila['tiene_banner'] = bool(fila.get('banner_completo'))
        return fila
    except Exception:
        try:
            comercio = dict(comercio)
            comercio.setdefault('logo_completo', None)
            comercio.setdefault('banner_completo', None)
            comercio.setdefault('tiene_banner', False)
        except Exception:
            pass
        return comercio

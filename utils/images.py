"""
Helper centralizado de imágenes (fail-safe absoluto).

Intercepta URLs antes de Jinja2. Si el valor es None, vacío o inválido,
inyecta un placeholder de inmediato. Nunca lanza hacia las rutas Flask.
"""

from __future__ import annotations

import functools
import logging

from backend.comercio_schema import (
    CANDIDATOS_BANNER,
    CANDIDATOS_LOGO,
    CANDIDATOS_PORTADA,
    valor_campo,
)
from config import DEFAULT_BANNER_URL

logger = logging.getLogger('localis.images')

PLACEHOLDER_PRODUCTO = '/static/img/placeholder-producto.svg'
PLACEHOLDER_BANNER = DEFAULT_BANNER_URL
PLACEHOLDER_LOGO = '/static/img/placeholder-logo.svg'
HERO_LOCAL = DEFAULT_BANNER_URL
HERO_ONERROR = '/static/img/hero-compras.svg'

COL_PRODUCTO = 'imagen_url'
CANDIDATOS_IMAGEN_PRODUCTO = (
    'imagen_url',
    'url_imagen',
    'URL de la imagen',
)
_PREFIJO_STORAGE = '/storage/v1/object/public/'
_MARCA_HERO_APROBADO = 'pexels-photo-18618233'
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
    except Exception as error:
        logger.error('No se pudo leer URL de imagen: %s', error, exc_info=True)
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


def es_hero_aprobado(valor):
    """True si es el banner de compras aprobado (Pexels 18618233)."""
    texto = _texto_url(valor).lower()
    return _MARCA_HERO_APROBADO in texto


def es_asset_estatico_local(valor):
    texto = _texto_url(valor)
    return texto.startswith('/static/img/')


def es_placeholder_local(valor):
    texto = _texto_url(valor).lower()
    return '/static/img/placeholder-' in texto or _MARCA_HERO_APROBADO in texto


def url_publica_producto_desde_bd(valor):
    """
    Convierte el valor persistido en URL para <img src>.
    Acepta Storage público, ruta del bucket o foto oficial del catálogo.
    Vacío si no hay imagen usable.
    """
    try:
        texto = _texto_url(valor)
        if not texto:
            return ''
        texto = texto.replace('/subase/', '/storage/').replace('/Subase/', '/storage/')
        if es_url_storage_publica(texto):
            return texto
        from backend.utils import url_imagen_catalogo_valida

        catalogo = url_imagen_catalogo_valida(texto)
        if catalogo:
            return catalogo
        if '://' in texto or texto.startswith('/'):
            return ''
        from backend.supabase_client import construir_url_publica_storage

        canonica = construir_url_publica_storage(texto.lstrip('/'))
        if es_url_storage_publica(canonica):
            return canonica
        return ''
    except Exception as error:
        logger.error(
            'url_publica_producto_desde_bd fallo valor=%r: %s',
            valor,
            error,
            exc_info=True,
        )
        return ''


def url_storage_o_vacio(valor):
    try:
        texto = _texto_url(valor)
        if not texto:
            return ''
        texto = texto.replace('/subase/', '/storage/').replace('/Subase/', '/storage/')
        if es_url_storage_publica(texto):
            return texto
        return ''
    except Exception as error:
        logger.error('url_storage_o_vacio fallo: %s', error, exc_info=True)
        return ''


def url_mostrable(valor, permitir_estatico=False, permitir_hero=False):
    """URL lista para <img src>, o ''. Nunca lanza."""
    try:
        texto = _texto_url(valor)
        if not texto:
            return ''
        texto = texto.replace('/subase/', '/storage/').replace('/Subase/', '/storage/')
        if es_url_storage_publica(texto):
            return texto
        from backend.utils import url_imagen_catalogo_valida

        catalogo = url_imagen_catalogo_valida(texto)
        if catalogo:
            return catalogo
        if permitir_hero and es_hero_aprobado(texto):
            return texto
        if permitir_estatico and es_asset_estatico_local(texto):
            return texto
        return ''
    except Exception as error:
        logger.error('url_mostrable fallo valor=%r: %s', valor, error, exc_info=True)
        return ''


def url_imagen_segura(valor, placeholder=PLACEHOLDER_PRODUCTO, permitir_estatico=False):
    try:
        url = url_mostrable(valor, permitir_estatico=permitir_estatico)
        if url:
            return url
        if not _texto_url(valor):
            logger.warning('Imagen vacia o None; se usa placeholder %s', placeholder)
        else:
            logger.error('Imagen no usable (%r); se usa placeholder %s', valor, placeholder)
    except Exception as error:
        logger.error('url_imagen_segura fallo: %s', error, exc_info=True)
    return placeholder or PLACEHOLDER_PRODUCTO


def imagen_fail_safe(placeholder=PLACEHOLDER_PRODUCTO, permitir_estatico=False, permitir_hero=False):
    """Decorador: nunca deja pasar None/vacío a Jinja2."""

    def _decorador(funcion):
        @functools.wraps(funcion)
        def _envoltura(*args, **kwargs):
            try:
                resultado = funcion(*args, **kwargs)
                url = url_mostrable(
                    resultado,
                    permitir_estatico=permitir_estatico,
                    permitir_hero=permitir_hero,
                )
                if url:
                    return url
                if resultado in (None, ''):
                    logger.warning(
                        'Resolver %s sin URL; placeholder=%s',
                        getattr(funcion, '__name__', funcion),
                        placeholder,
                    )
                else:
                    logger.error(
                        'Resolver %s devolvio URL inutilizable (%r); placeholder=%s',
                        getattr(funcion, '__name__', funcion),
                        resultado,
                        placeholder,
                    )
            except Exception as error:
                logger.error(
                    'Resolver %s lanzo %s; placeholder=%s',
                    getattr(funcion, '__name__', funcion),
                    error,
                    placeholder,
                    exc_info=True,
                )
            return placeholder

        return _envoltura

    return _decorador


@imagen_fail_safe(placeholder=PLACEHOLDER_PRODUCTO)
def url_imagen_producto(producto=None, imagen_url=None, codigo_barras=None):
    """URL pública de Storage ya resuelta en el SELECT. No dispara red."""
    del codigo_barras
    if imagen_url is None and producto is not None:
        imagen_url = valor_campo(producto, *CANDIDATOS_IMAGEN_PRODUCTO)
    return url_publica_producto_desde_bd(imagen_url)


url_imagen_producto_segura = url_imagen_producto


@imagen_fail_safe(placeholder=DEFAULT_BANNER_URL, permitir_estatico=True, permitir_hero=True)
def url_banner_segura(valor, default=None):
    if valor:
        return valor
    return default or DEFAULT_BANNER_URL


@imagen_fail_safe(placeholder=DEFAULT_BANNER_URL, permitir_estatico=True, permitir_hero=True)
def url_hero_inicio(banner_config=None, banner_comercio=None):
    """Hero del index: config Storage, si no el banner de compras aprobado."""
    for candidato in (banner_config, banner_comercio, DEFAULT_BANNER_URL):
        url = url_mostrable(
            candidato, permitir_estatico=True, permitir_hero=True
        )
        if url:
            return url
    return DEFAULT_BANNER_URL


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
        if not fila['logo_completo'] and logo:
            logger.error('Logo de comercio no usable: %r', logo)
        if not fila['banner_completo'] and banner:
            logger.error('Banner de comercio no usable: %r', banner)
        return fila
    except Exception as error:
        logger.error('enriquecer_comercio_imagenes fallo: %s', error, exc_info=True)
        try:
            comercio = dict(comercio)
            comercio.setdefault('logo_completo', None)
            comercio.setdefault('banner_completo', None)
            comercio.setdefault('tiene_banner', False)
        except Exception:
            logger.error('No se pudo degradar fila de comercio', exc_info=True)
        return comercio

"""
Capa de compatibilidad del pipeline de imágenes LEGADO.

Las APIs globales de códigos de barras (Barcode Spider, UPCitemdb y Barcode
Lookup) fueron ELIMINADAS: no funcionan para el mercado venezolano y añadían
dependencia de pago. ``buscar_por_ean`` y ``buscar_por_nombre`` devuelven
``None`` a propósito.

El pipeline oficial y gratuito es ``services.professional_image_pipeline``:
EAN/UPC → búsqueda web ``[nombre]+[marca]+[presentación]+"venezuela"`` →
validación de fuentes/calidad → rembg (fondo blanco) → Supabase Storage.

Este módulo se conserva solo por compatibilidad con scripts y pruebas:
helpers de validación de URLs y la cascada ``resolver_imagen_automatica``
(que ahora resuelve a placeholder sin salir a red).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from backend.utils import normalizar_codigo_barras, texto_campo_imagen

_LOG = '[Localis SmartImage]'
_TIMEOUT = float(os.getenv('LOCALIS_BARCODE_TIMEOUT_SEC', '5'))
_REINTENTOS = 2
_USER_AGENT = 'LocalisApp/1.0 (imagenes-catalogo@localis.app)'

PLACEHOLDER_PRODUCTO = '/static/img/placeholder-producto.svg'
PLACEHOLDER_POR_CATEGORIA = {
    'alimentos': PLACEHOLDER_PRODUCTO,
    'tecnologia': PLACEHOLDER_PRODUCTO,
    'tecnología': PLACEHOLDER_PRODUCTO,
    'electrodomesticos': PLACEHOLDER_PRODUCTO,
    'electrodomésticos': PLACEHOLDER_PRODUCTO,
    'hogar': PLACEHOLDER_PRODUCTO,
    'ropa': PLACEHOLDER_PRODUCTO,
    'belleza': PLACEHOLDER_PRODUCTO,
    'otros': PLACEHOLDER_PRODUCTO,
}

_CLAVES_IMAGEN = (
    'image',
    'image_url',
    'imageUrl',
    'thumbnail',
    'photo',
    'picture',
    'large_image',
    'largeImage',
)
_HOSTS_BLOQUEADOS = (
    'images.google',
    'google.com/imgres',
    'bing.com',
    'wsrv.nl',
    'wikimedia',
    'unsplash.com',
    'pexels.com',
    'placeholder',
    'example.com',
    'openfoodfacts',
)

_STOP = frozenset({
    'de', 'la', 'el', 'los', 'las', 'del', 'y', 'en', 'con', 'un', 'una',
    'kg', 'g', 'l', 'ml', 'und',
})
_GENERICOS = frozenset({
    'martillo', 'destornillador', 'taladro', 'camisa', 'polo', 'pantalon',
    'zapato', 'ropa', 'leche', 'agua', 'arroz', 'producto', 'articulo',
})

_aviso_sin_clave_emitido = False


@dataclass
class ResultadoImagen:
    url: str
    fuente: str
    es_placeholder: bool
    ean: str | None = None


def _log(msg):
    print(f'{_LOG} {msg}')


def placeholder_categoria(categoria=None):
    clave = str(categoria or '').strip().lower()
    return PLACEHOLDER_POR_CATEGORIA.get(clave, PLACEHOLDER_PRODUCTO)


def url_catalogo_api_valida(valor):
    """HTTPS de catálogo de API. No descarga el archivo."""
    texto = texto_campo_imagen(valor, default=None)
    if not texto or not texto.lower().startswith('https://'):
        return None
    lower = texto.lower()
    if any(marca in lower for marca in _HOSTS_BLOQUEADOS):
        return None
    if any(marca in lower for marca in ('no-image', 'default-product', '.svg')):
        return None
    parsed = urlparse(texto)
    if not parsed.hostname:
        return None
    return texto


def _ean_normalizado(valor):
    codigo = normalizar_codigo_barras(valor)
    if not codigo or not codigo.isdigit():
        return None
    if len(codigo) < 8 or len(codigo) > 14:
        return None
    return codigo


def _consulta_nombre_util(nombre, descripcion=None, marca=None):
    partes = ' '.join(
        filter(None, (str(nombre or ''), str(marca or ''), str(descripcion or '')[:80]))
    ).lower()
    partes = re.sub(r'[^a-z0-9áéíóúüñ\s]+', ' ', partes)
    tokens = [
        t for t in partes.split()
        if t and t not in _STOP and len(t) >= 3
    ]
    if not tokens:
        return None
    if all(t in _GENERICOS for t in tokens) and len(tokens) <= 2:
        return None
    return ' '.join(tokens[:6])


def _extraer_url_de_nodo(nodo):
    if isinstance(nodo, str):
        return url_catalogo_api_valida(nodo)
    if isinstance(nodo, (list, tuple)):
        for item in nodo:
            hallada = _extraer_url_de_nodo(item)
            if hallada:
                return hallada
        return None
    if not isinstance(nodo, dict):
        return None
    for clave in _CLAVES_IMAGEN:
        if clave in nodo:
            hallada = _extraer_url_de_nodo(nodo.get(clave))
            if hallada:
                return hallada
    for clave in ('images', 'item_attributes', 'item', 'product', 'products', 'items', 'Data', 'data', 'result', 'results'):
        if clave in nodo:
            hallada = _extraer_url_de_nodo(nodo.get(clave))
            if hallada:
                return hallada
    return None


def _log_url_segura(url, params=None):
    """URL para consola sin exponer API keys."""
    if params and 'key' in params:
        params = {**params, 'key': '***'}
    if params:
        from urllib.parse import urlencode
        return f'{url}?{urlencode(params)}'
    return url


def _get_json(url, *, headers=None, params=None):
    url_log = _log_url_segura(url, params)
    ultimo = None
    for intento in range(_REINTENTOS):
        try:
            _log(f'GET {url_log} intento={intento + 1}/{_REINTENTOS}')
            resp = requests.get(
                url,
                headers={'User-Agent': _USER_AGENT, **(headers or {})},
                params=params,
                timeout=_TIMEOUT,
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                ultimo = f'HTTP {resp.status_code}'
                _log(f'respuesta {ultimo} cuerpo={resp.text[:200]!r}')
                time.sleep(0.4 * (2 ** intento))
                continue
            if resp.status_code in (401, 403):
                _log(f'auth proveedor HTTP {resp.status_code} cuerpo={resp.text[:200]!r}')
                return None
            if resp.status_code != 200:
                _log(f'HTTP {resp.status_code} cuerpo={resp.text[:300]!r}')
                return None
            datos = resp.json()
            _log(f'HTTP 200 claves={list(datos.keys()) if isinstance(datos, dict) else type(datos).__name__}')
            return datos if isinstance(datos, (dict, list)) else None
        except Exception as error:
            ultimo = type(error).__name__
            _log(f'excepción red {ultimo}: {error}')
            time.sleep(0.25 * (2 ** intento))
    if ultimo:
        _log(f'red agotada: {ultimo}')
    return None


def hay_proveedor_pagado():
    """Compatibilidad: indica si el pipeline de imágenes está operativo.

    Ya NO existen APIs de códigos de barras de pago (Barcode Spider, UPCitemdb
    y Barcode Lookup fueron eliminadas). El pipeline oficial y gratuito es
    ``services.professional_image_pipeline``.
    """
    try:
        from services.professional_image_pipeline import pipeline_habilitado

        return pipeline_habilitado()
    except Exception:
        return False


def buscar_por_ean(ean):
    """DESACTIVADO a propósito.

    Antes consultaba Barcode Spider / UPCitemdb / Barcode Lookup, inefectivas
    para el mercado venezolano. La resolución real (EAN → búsqueda web gratuita
    → rembg → Supabase Storage) vive en ``services.professional_image_pipeline``.
    Se conserva la firma por compatibilidad con scripts y pruebas.
    """
    del ean
    return None


def buscar_por_nombre(nombre, *, descripcion=None, marca=None, categoria=None):
    """DESACTIVADO a propósito. Ver ``buscar_por_ean``."""
    del nombre, descripcion, marca, categoria
    return None


def resolver_imagen_automatica(
    *,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
    categoria=None,
    marca=None,
):
    """
    Cascada EAN → nombre. Nunca lanza.
    url es API HTTPS o placeholder local. No hay Storage ni Pillow.
    """
    global _aviso_sin_clave_emitido
    try:
        ean_norm = _ean_normalizado(codigo_barras)
        _log(
            f'entrada codigo_barras={codigo_barras!r} ean_norm={ean_norm!r} '
            f'nombre={nombre!r} categoria={categoria!r} '
            f'busqueda_imagen_activa={hay_proveedor_pagado()}'
        )
        if not hay_proveedor_pagado():
            if not _aviso_sin_clave_emitido:
                _log(
                    'pipeline de imágenes desactivado; se usa placeholder. '
                    'Activa LOCALIS_IMG_PIPELINE=1 para el pipeline profesional.'
                )
                _aviso_sin_clave_emitido = True
            return ResultadoImagen(
                url=placeholder_categoria(categoria),
                fuente='placeholder',
                es_placeholder=True,
                ean=_ean_normalizado(codigo_barras),
            )

        ean = _ean_normalizado(codigo_barras)
        if ean:
            url = buscar_por_ean(ean)
            if url:
                return ResultadoImagen(
                    url=url, fuente='barcode_api', es_placeholder=False, ean=ean
                )

        url_nom = buscar_por_nombre(
            nombre, descripcion=descripcion, marca=marca, categoria=categoria
        )
        if url_nom:
            return ResultadoImagen(
                url=url_nom, fuente='nombre_api', es_placeholder=False, ean=ean
            )

        return ResultadoImagen(
            url=placeholder_categoria(categoria),
            fuente='placeholder',
            es_placeholder=True,
            ean=ean,
        )
    except Exception as error:
        _log(f'fallo silencioso: {type(error).__name__}: {error}')
        return ResultadoImagen(
            url=placeholder_categoria(categoria),
            fuente='placeholder',
            es_placeholder=True,
            ean=_ean_normalizado(codigo_barras),
        )
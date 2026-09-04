"""
Pipeline automático de imágenes (pago por consumo).

Solo se usa cuando el producto NO tiene foto manual.
No descarga binarios ni usa Pillow: guarda la URL HTTPS que devuelve la API.

Prioridad:
1. Lookup por EAN/UPC (Barcode Spider, opcionalmente UPCitemdb / Barcode Lookup)
2. Búsqueda por nombre + marca
3. Placeholder de categoría

Credenciales (ninguna mensualidad fija en este código; cada proveedor cobra créditos):
  BARCODE_SPIDER_API_KEY
  UPCITEMDB_API_KEY          (opcional)
  BARCODE_LOOKUP_API_KEY     (opcional)
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


def _get_json(url, *, headers=None, params=None):
    ultimo = None
    for intento in range(_REINTENTOS):
        try:
            resp = requests.get(
                url,
                headers={'User-Agent': _USER_AGENT, **(headers or {})},
                params=params,
                timeout=_TIMEOUT,
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                ultimo = f'HTTP {resp.status_code}'
                time.sleep(0.4 * (2 ** intento))
                continue
            if resp.status_code in (401, 403):
                _log(f'auth proveedor HTTP {resp.status_code}')
                return None
            if resp.status_code != 200:
                return None
            datos = resp.json()
            return datos if isinstance(datos, (dict, list)) else None
        except Exception as error:
            ultimo = type(error).__name__
            time.sleep(0.25 * (2 ** intento))
    if ultimo:
        _log(f'red {ultimo}')
    return None


def _clave_spider():
    return (os.getenv('BARCODE_SPIDER_API_KEY') or '').strip()


def _clave_upcitemdb():
    return (os.getenv('UPCITEMDB_API_KEY') or '').strip()


def _clave_barcodelookup():
    return (os.getenv('BARCODE_LOOKUP_API_KEY') or '').strip()


def hay_proveedor_pagado():
    return bool(_clave_spider() or _clave_upcitemdb() or _clave_barcodelookup())


def _buscar_barcode_spider_ean(ean):
    token = _clave_spider()
    if not token:
        return None
    datos = _get_json(
        f'https://api.barcodespider.com/v2/products/{ean}',
        params={'key': token},
    )
    return _extraer_url_de_nodo(datos)


def _buscar_barcode_spider_nombre(consulta):
    token = _clave_spider()
    if not token:
        return None
    datos = _get_json(
        'https://api.barcodespider.com/v2/products',
        params={'key': token, 'query': consulta},
    )
    return _extraer_url_de_nodo(datos)


def _buscar_upcitemdb_ean(ean):
    key = _clave_upcitemdb()
    if not key:
        return None
    datos = _get_json(
        'https://api.upcitemdb.com/prod/v1/lookup',
        headers={'user_key': key, 'Accept': 'application/json'},
        params={'upc': ean},
    )
    return _extraer_url_de_nodo(datos)


def _buscar_upcitemdb_nombre(consulta):
    key = _clave_upcitemdb()
    if not key:
        return None
    datos = _get_json(
        'https://api.upcitemdb.com/prod/v1/search',
        headers={'user_key': key, 'Accept': 'application/json'},
        params={'s': consulta},
    )
    return _extraer_url_de_nodo(datos)


def _buscar_barcodelookup_ean(ean):
    key = _clave_barcodelookup()
    if not key:
        return None
    datos = _get_json(
        'https://api.barcodelookup.com/v3/products',
        params={'barcode': ean, 'key': key},
    )
    return _extraer_url_de_nodo(datos)


def _buscar_barcodelookup_nombre(consulta):
    key = _clave_barcodelookup()
    if not key:
        return None
    datos = _get_json(
        'https://api.barcodelookup.com/v3/products',
        params={'search': consulta, 'key': key},
    )
    return _extraer_url_de_nodo(datos)


def _cache_maestro(ean):
    if not ean:
        return None
    try:
        from backend.catalogo_maestro import imagen_maestro_por_codigo

        return url_catalogo_api_valida(imagen_maestro_por_codigo(ean))
    except Exception:
        return None


def _guardar_maestro(ean, url):
    if not ean or not url:
        return
    try:
        from backend.catalogo_maestro import guardar_imagen_maestro

        guardar_imagen_maestro(ean, url)
    except Exception as error:
        _log(f'cache maestro: {type(error).__name__}')


def buscar_por_ean(ean):
    codigo = _ean_normalizado(ean)
    if not codigo:
        return None
    cache = _cache_maestro(codigo)
    if cache:
        return cache
    for fn, nombre in (
        (_buscar_barcode_spider_ean, 'barcodespider'),
        (_buscar_upcitemdb_ean, 'upcitemdb'),
        (_buscar_barcodelookup_ean, 'barcodelookup'),
    ):
        url = fn(codigo)
        if url:
            _log(f'ean={codigo} fuente={nombre}')
            _guardar_maestro(codigo, url)
            return url
    return None


def buscar_por_nombre(nombre, *, descripcion=None, marca=None, categoria=None):
    del categoria
    consulta = _consulta_nombre_util(nombre, descripcion=descripcion, marca=marca)
    if not consulta:
        return None
    for fn, nombre_fn in (
        (_buscar_barcode_spider_nombre, 'barcodespider-search'),
        (_buscar_upcitemdb_nombre, 'upcitemdb-search'),
        (_buscar_barcodelookup_nombre, 'barcodelookup-search'),
    ):
        url = fn(consulta)
        if url:
            _log(f'nombre={consulta!r} fuente={nombre_fn}')
            return url
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
        if not hay_proveedor_pagado():
            if not _aviso_sin_clave_emitido:
                _log(
                    'sin API key (BARCODE_SPIDER_API_KEY / UPCITEMDB_API_KEY / '
                    'BARCODE_LOOKUP_API_KEY); se usa placeholder'
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
            if ean:
                _guardar_maestro(ean, url_nom)
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

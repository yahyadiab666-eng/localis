"""
Fuentes oficiales de fotos de producto, ordenadas por familia.

Alimentos → Open Food Facts
Tecnología / hogar / ropa → Open Products Facts
Belleza → Open Beauty Facts
Si el código no aparece en la fuente preferida, se prueba la siguiente.
Wikimedia es el último recurso para marcas de electrónica (no para comida).
"""

from __future__ import annotations

import unicodedata
from urllib.parse import quote

import requests

_USER_AGENT = 'LocalisApp/1.0 (Localis; contacto@localis.app)'
_TIMEOUT = 4
_LOG = '[Localis Fuentes]'

FAMILIA_ALIMENTOS = 'alimentos'
FAMILIA_TECNOLOGIA = 'tecnologia'
FAMILIA_HOGAR = 'hogar'
FAMILIA_ROPA = 'ropa'
FAMILIA_BELLEZA = 'belleza'
FAMILIA_OTRO = 'otro'

_FUENTES = (
    {
        'id': 'openfoodfacts',
        'api': 'https://world.openfoodfacts.org/api/v2/product/{codigo}.json',
        'search': 'https://search.openfoodfacts.org/search',
        'search_tipo': 'off_es',
    },
    {
        'id': 'openproductsfacts',
        'api': 'https://world.openproductsfacts.org/api/v2/product/{codigo}.json',
        'search': 'https://world.openproductsfacts.org/cgi/search.pl',
        'search_tipo': 'cgi',
    },
    {
        'id': 'openbeautyfacts',
        'api': 'https://world.openbeautyfacts.org/api/v2/product/{codigo}.json',
        'search': 'https://world.openbeautyfacts.org/cgi/search.pl',
        'search_tipo': 'cgi',
    },
    {
        'id': 'openpetfoodfacts',
        'api': 'https://world.openpetfoodfacts.org/api/v2/product/{codigo}.json',
        'search': 'https://world.openpetfoodfacts.org/cgi/search.pl',
        'search_tipo': 'cgi',
    },
)
_FUENTE_POR_ID = {f['id']: f for f in _FUENTES}

_ORDEN_FAMILIA = {
    FAMILIA_ALIMENTOS: (
        'openfoodfacts',
        'openpetfoodfacts',
        'openproductsfacts',
    ),
    FAMILIA_TECNOLOGIA: (
        'openproductsfacts',
        'openfoodfacts',
    ),
    FAMILIA_HOGAR: (
        'openproductsfacts',
        'openbeautyfacts',
        'openfoodfacts',
    ),
    FAMILIA_ROPA: (
        'openproductsfacts',
        'openbeautyfacts',
    ),
    FAMILIA_BELLEZA: (
        'openbeautyfacts',
        'openproductsfacts',
    ),
    FAMILIA_OTRO: (
        'openfoodfacts',
        'openproductsfacts',
        'openbeautyfacts',
    ),
}

_TOKENS_ALIMENTOS = frozenset({
    'harina', 'arroz', 'aceite', 'cafe', 'pasta', 'spaghetti', 'leche',
    'atun', 'mantequilla', 'panela', 'refresco', 'pan', 'pepsi', 'coca',
    'azucar', 'sal', 'mayonesa', 'ketchup', 'salsa', 'jugo', 'galleta',
    'cereal', 'yogurt', 'queso', 'jamon', 'pollo', 'carne', 'frijol',
    'caraota', 'lenteja', 'maiz', 'arepa', 'empanada', 'chocolate',
    'gaseosa', 'malta', 'agua', 'vino', 'cerveza', 'whisky',
})
_TOKENS_TECNOLOGIA = frozenset({
    'iphone', 'samsung', 'xiaomi', 'huawei', 'laptop', 'notebook',
    'televisor', 'monitor', 'mouse', 'teclado', 'auricular', 'audifono',
    'tablet', 'router', 'playstation', 'xbox', 'nintendo', 'cargador',
    'bluetooth', 'smartphone', 'celular', 'android', 'macbook',
    'airpods', 'impresora', 'procesador', 'intel', 'nvidia', 'amd',
    'camara', 'drone', 'consola', 'ssd', 'hdd', 'usb', 'hdmi',
    'galaxy', 'pixel', 'lenovo', 'hp', 'dell', 'asus', 'acer',
    'sony', 'lg', 'tcl', 'hisense', 'logitech', 'jbl', 'bose',
})
_TOKENS_HOGAR = frozenset({
    'licuadora', 'nevera', 'refrigerador', 'olla', 'sarten', 'plancha',
    'aspiradora', 'microondas', 'lavadora', 'ventilador', 'lampara',
    'colchon', 'almohada', 'toalla', 'sabanas', 'detergente',
})
_TOKENS_ROPA = frozenset({
    'camisa', 'pantalon', 'zapato', 'zapatos', 'vestido', 'falda',
    'jean', 'tennis', 'gorra', 'sombrero', 'chaqueta', 'abrigo',
    'ropa', 'blusa', 'short', 'medias',
})
_TOKENS_BELLEZA = frozenset({
    'shampoo', 'champu', 'jabon', 'crema', 'perfume', 'desodorante',
    'maquillaje', 'labial', 'bloqueador', 'protector', 'serum',
})
_CATEGORIA_A_FAMILIA = {
    'alimentos': FAMILIA_ALIMENTOS,
    'alimento': FAMILIA_ALIMENTOS,
    'viveres': FAMILIA_ALIMENTOS,
    'víveres': FAMILIA_ALIMENTOS,
    'comida': FAMILIA_ALIMENTOS,
    'tecnologia': FAMILIA_TECNOLOGIA,
    'tecnología': FAMILIA_TECNOLOGIA,
    'electronica': FAMILIA_TECNOLOGIA,
    'electrónica': FAMILIA_TECNOLOGIA,
    'hogar': FAMILIA_HOGAR,
    'casa': FAMILIA_HOGAR,
    'ropa': FAMILIA_ROPA,
    'moda': FAMILIA_ROPA,
    'belleza': FAMILIA_BELLEZA,
    'cuidado': FAMILIA_BELLEZA,
}


def _norm(texto):
    plano = ''.join(
        ch for ch in unicodedata.normalize('NFKD', str(texto or '').lower())
        if not unicodedata.combining(ch)
    )
    return plano.strip()


def clasificar_familia(nombre=None, categoria=None):
    """Familia de búsqueda a partir de categoría del comercio o del nombre."""
    cat = _norm(categoria).replace('í', 'i').replace('ó', 'o')
    for clave, familia in _CATEGORIA_A_FAMILIA.items():
        if clave in cat:
            return familia
    tokens = set(_norm(nombre).replace('/', ' ').split())
    if tokens & _TOKENS_TECNOLOGIA:
        return FAMILIA_TECNOLOGIA
    if tokens & _TOKENS_HOGAR:
        return FAMILIA_HOGAR
    if tokens & _TOKENS_ROPA:
        return FAMILIA_ROPA
    if tokens & _TOKENS_BELLEZA:
        return FAMILIA_BELLEZA
    if tokens & _TOKENS_ALIMENTOS:
        return FAMILIA_ALIMENTOS
    return FAMILIA_OTRO


def fuentes_para_familia(familia):
    ids = _ORDEN_FAMILIA.get(familia) or _ORDEN_FAMILIA[FAMILIA_OTRO]
    return [_FUENTE_POR_ID[i] for i in ids if i in _FUENTE_POR_ID]


def fuentes_para_codigo(familia=None):
    """
    El código de barras es único: primero las fuentes de la familia,
    después el resto de catálogos oficiales.
    """
    preferidas = fuentes_para_familia(familia or FAMILIA_OTRO)
    vistos = {fuente['id'] for fuente in preferidas}
    resto = [fuente for fuente in _FUENTES if fuente['id'] not in vistos]
    return list(preferidas) + resto


def usa_wikimedia(familia):
    return familia in {FAMILIA_TECNOLOGIA, FAMILIA_HOGAR, FAMILIA_OTRO}


def _http_json(url, params=None):
    try:
        respuesta = requests.get(
            url,
            params=params,
            headers={'User-Agent': _USER_AGENT},
            timeout=_TIMEOUT,
        )
    except Exception as error:
        print(f'{_LOG} red {url}: {type(error).__name__}: {error}')
        return None
    if respuesta.status_code != 200:
        return None
    try:
        return respuesta.json()
    except Exception:
        return None


def producto_por_codigo(fuente, codigo):
    url = fuente['api'].format(codigo=codigo)
    datos = _http_json(url)
    if not datos or datos.get('status') not in (1, '1'):
        return None
    producto = datos.get('product') or {}
    if not producto.get('code') and not producto.get('id'):
        return None
    return producto


def hits_por_nombre(fuente, consulta, page_size=8):
    if not consulta:
        return []
    if fuente['search_tipo'] == 'off_es':
        datos = _http_json(
            fuente['search'],
            {'q': consulta, 'page_size': page_size},
        )
        if not datos:
            return []
        return list(datos.get('hits') or [])
    datos = _http_json(
        fuente['search'],
        {
            'search_terms': consulta,
            'search_simple': 1,
            'action': 'process',
            'json': 1,
            'page_size': page_size,
        },
    )
    if not datos:
        return []
    return list(datos.get('products') or [])


def buscar_wikimedia(nombre):
    """Miniatura oficial de Wikipedia (producto/marca). None si no hay foto usable."""
    titulo = ' '.join(str(nombre or '').split()[:6]).strip()
    if len(titulo) < 3:
        return None
    for lang in ('es', 'en'):
        url = (
            f'https://{lang}.wikipedia.org/api/rest_v1/page/summary/'
            f'{quote(titulo, safe="")}'
        )
        datos = _http_json(url)
        if not datos or datos.get('type') == 'disambiguation':
            continue
        orig = datos.get('originalimage') or {}
        thumb = datos.get('thumbnail') or {}
        candidatos = []
        try:
            ancho_orig = int(orig.get('width') or 0)
        except (TypeError, ValueError):
            ancho_orig = 0
        if orig.get('source') and (ancho_orig == 0 or ancho_orig <= 1600):
            candidatos.append(orig)
        elif thumb.get('source'):
            candidatos.append(thumb)
        elif orig.get('source'):
            candidatos.append(orig)
        for meta in candidatos:
            src = meta.get('source')
            if not src or not str(src).startswith('https://'):
                continue
            return {
                'url': src,
                'ancho': meta.get('width'),
                'alto': meta.get('height'),
                'fuente': 'wikimedia',
            }
    return None

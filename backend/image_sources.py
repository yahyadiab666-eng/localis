"""
Fuentes oficiales de fotos de producto, ordenadas por familia.

Alimentos → Open Food Facts
Tecnología / hogar / ferretería / ropa → Open Products Facts
Belleza → Open Beauty Facts
Si el código no aparece o no hay EAN: nombre + categoría en todos los
catálogos abiertos, Wikimedia Commons y Openverse (imágenes libres).
"""

from __future__ import annotations

import time
import unicodedata
from urllib.parse import quote

import requests

_USER_AGENT = 'LocalisApp/1.0 (Localis; contacto@localis.app)'
_TIMEOUT = 5
_REINTENTOS = 2
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
    ),
    FAMILIA_HOGAR: (
        'openproductsfacts',
        'openbeautyfacts',
    ),
    FAMILIA_ROPA: (
        'openproductsfacts',
        'openbeautyfacts',
    ),
    FAMILIA_BELLEZA: (
        'openbeautyfacts',
        'openproductsfacts',
        'openfoodfacts',
    ),
    FAMILIA_OTRO: (
        'openproductsfacts',
        'openfoodfacts',
        'openbeautyfacts',
        'openpetfoodfacts',
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
    'martillo', 'destornillador', 'taladro', 'llave', 'tornillo',
    'clavo', 'pintura', 'cemento', 'brocha', 'alicate', 'serrucho',
    'cinta', 'tuerca', 'cable', 'tuberia', 'valvula', 'bombillo',
    'foco', 'extension', 'silicona', 'pegamento', 'lija',
})
_TOKENS_ROPA = frozenset({
    'camisa', 'pantalon', 'zapato', 'zapatos', 'vestido', 'falda',
    'jean', 'jeans', 'tennis', 'gorra', 'sombrero', 'chaqueta', 'abrigo',
    'ropa', 'blusa', 'short', 'medias', 'polo', 'camiseta', 'sudadera',
    'zapatilla', 'bota', 'botas', 'cinturon', 'cartera',
})
_TOKENS_BELLEZA = frozenset({
    'shampoo', 'champu', 'jabon', 'crema', 'perfume', 'desodorante',
    'maquillaje', 'labial', 'bloqueador', 'protector', 'serum',
    'colonia', 'gel', 'acondicionador', 'pasta', 'dental',
})
_CATEGORIA_A_FAMILIA = {
    'alimentos': FAMILIA_ALIMENTOS,
    'alimento': FAMILIA_ALIMENTOS,
    'viveres': FAMILIA_ALIMENTOS,
    'víveres': FAMILIA_ALIMENTOS,
    'abarrotes': FAMILIA_ALIMENTOS,
    'comida': FAMILIA_ALIMENTOS,
    'tecnologia': FAMILIA_TECNOLOGIA,
    'tecnología': FAMILIA_TECNOLOGIA,
    'electronica': FAMILIA_TECNOLOGIA,
    'electrónica': FAMILIA_TECNOLOGIA,
    'hogar': FAMILIA_HOGAR,
    'casa': FAMILIA_HOGAR,
    'ferreteria': FAMILIA_HOGAR,
    'ferretería': FAMILIA_HOGAR,
    'herramientas': FAMILIA_HOGAR,
    'construccion': FAMILIA_HOGAR,
    'construcción': FAMILIA_HOGAR,
    'ropa': FAMILIA_ROPA,
    'moda': FAMILIA_ROPA,
    'calzado': FAMILIA_ROPA,
    'belleza': FAMILIA_BELLEZA,
    'cuidado': FAMILIA_BELLEZA,
    'miscelanea': FAMILIA_OTRO,
    'miscelánea': FAMILIA_OTRO,
    'bazar': FAMILIA_OTRO,
    'varios': FAMILIA_OTRO,
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
    """Commons/Wikipedia como último recurso para cualquier catálogo general."""
    del familia
    return True


def _http_json(url, params=None):
    """GET JSON con un reintento ante timeout/5xx. Nunca lanza."""
    ultimo = None
    for intento in range(_REINTENTOS):
        try:
            respuesta = requests.get(
                url,
                params=params,
                headers={'User-Agent': _USER_AGENT},
                timeout=_TIMEOUT,
            )
        except Exception as error:
            ultimo = error
            if intento + 1 < _REINTENTOS:
                time.sleep(0.35 * (intento + 1))
                continue
            print(f'{_LOG} red {url}: {type(error).__name__}: {error}')
            return None
        if respuesta.status_code in (429, 500, 502, 503, 504):
            if intento + 1 < _REINTENTOS:
                time.sleep(0.4 * (intento + 1))
                continue
            return None
        if respuesta.status_code != 200:
            return None
        try:
            return respuesta.json()
        except Exception:
            return None
    if ultimo:
        print(f'{_LOG} red {url}: {type(ultimo).__name__}: {ultimo}')
    return None


def _lista_hits(datos):
    """Acepta hits / products / results aunque la API cambie la clave."""
    if not isinstance(datos, dict):
        return []
    for clave in ('hits', 'products', 'results', 'items'):
        items = datos.get(clave)
        if isinstance(items, list) and items:
            return items
    return []


def producto_por_codigo(fuente, codigo):
    url = fuente['api'].format(codigo=codigo)
    datos = _http_json(url)
    if not datos:
        return None
    producto = datos.get('product')
    if not isinstance(producto, dict):
        producto = {}
    status = datos.get('status')
    if status not in (1, '1', True) and not (
        producto.get('images') or producto.get('image_url') or producto.get('image_front_url')
    ):
        return None
    if not producto:
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
        return _lista_hits(datos)
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
    return _lista_hits(datos)


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
                'titulo': datos.get('title') or titulo,
            }
    return None


def buscar_commons(consulta):
    """Wikimedia Commons: fotos libres por nombre de producto/herramienta."""
    q = ' '.join(str(consulta or '').split()[:6]).strip()
    if len(q) < 3:
        return None
    datos = _http_json(
        'https://commons.wikimedia.org/w/api.php',
        {
            'action': 'query',
            'generator': 'search',
            'gsrsearch': q,
            'gsrnamespace': 6,
            'gsrlimit': 8,
            'prop': 'imageinfo',
            'iiprop': 'url|size|mime',
            'iiurlwidth': 800,
            'format': 'json',
        },
    )
    paginas = ((datos or {}).get('query') or {}).get('pages') or {}
    if not isinstance(paginas, dict):
        return None
    for pagina in paginas.values():
        infos = pagina.get('imageinfo') or []
        if not infos:
            continue
        info = infos[0] if isinstance(infos[0], dict) else {}
        mime = str(info.get('mime') or '').lower()
        if mime and not mime.startswith('image/'):
            continue
        src = info.get('url') or info.get('thumburl')
        if not src or not str(src).startswith('https://'):
            continue
        if 'upload.wikimedia.org' not in str(src).lower():
            continue
        return {
            'url': src,
            'ancho': info.get('thumbwidth') or info.get('width'),
            'alto': info.get('thumbheight') or info.get('height'),
            'fuente': 'commons',
            'titulo': pagina.get('title') or q,
        }
    return None


_HOSTS_IMAGEN_LIBRE = (
    'upload.wikimedia.org',
    'wikipedia.org',
    'wikimedia.org',
    'staticflickr.com',
    'flickr.com',
    'openverse.org',
    'unsplash.com',
    'images.unsplash.com',
    'pxhere.com',
    'rawpixel.com',
    'nappy.co',
    'stocksnap.io',
)


def _url_libre_usable(src):
    if not src or not str(src).startswith('https://'):
        return False
    lower = str(src).lower()
    if 'placeholder' in lower or '.svg' in lower:
        return False
    return any(host in lower for host in _HOSTS_IMAGEN_LIBRE)


def buscar_openverse(consulta):
    """Catálogo abierto de imágenes libres (comercio general, CC)."""
    q = ' '.join(str(consulta or '').split()[:6]).strip()
    if len(q) < 3:
        return None
    datos = _http_json(
        'https://api.openverse.org/v1/images/',
        {
            'q': q,
            'page_size': 8,
            'mature': 'false',
        },
    )
    resultados = _lista_hits(datos)
    preferidas = []
    respaldo = []
    for item in resultados:
        if not isinstance(item, dict):
            continue
        src = item.get('url') or item.get('thumbnail')
        if not src or not str(src).startswith('https://'):
            continue
        if 'placeholder' in str(src).lower() or str(src).lower().endswith('.svg'):
            continue
        try:
            ancho = int(item.get('width') or 0)
            alto = int(item.get('height') or 0)
        except (TypeError, ValueError):
            ancho, alto = 0, 0
        meta = {
            'url': src,
            'ancho': ancho or None,
            'alto': alto or None,
            'fuente': 'openverse',
            'titulo': item.get('title') or q,
            'tags': item.get('tags') or [],
        }
        if _url_libre_usable(src) and 'thumbnail' not in str(src).lower():
            preferidas.append(meta)
        else:
            respaldo.append(meta)
    if preferidas:
        return preferidas[0]
    if respaldo:
        return respaldo[0]
    return None

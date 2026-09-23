"""Logo **oficial** de marca (nunca se inventa arte).

Cuando el motor agota las fuentes y no encuentra la foto real del producto,
solo se acepta el **logo oficial** de la marca si existe arte verificable:

  1. Logo oficial por Simple Icons (SVG).
  2. Logo oficial por Wikidata/Wikimedia Commons (P154).
  3. Favicon oficial del dominio de la marca (PNG).

Si no hay arte oficial, se devuelve ``(None, None)`` y la ficha queda en estado
neutro (imagen vacía). Las utilidades de monograma/tarjeta que existían antes
se conservan solo por compatibilidad y **no** se usan en producción.
"""

from __future__ import annotations

import io
import os
import re
import unicodedata

from backend.runtime_cache import get_or_load
from backend import http_client as _http
from urllib.parse import quote as _quote


def _get_json(url, params=None):
    try:
        respuesta = _http.get(url, params=params, timeout=12.0, tipo='json')
        if respuesta is not None and respuesta.status_code == 200:
            return respuesta.json()
    except Exception:
        return None
    return None

_LOG = '[Localis Marca]'
_LADO = 600
_BLANCO = (255, 255, 255)
_TTL_SEG = max(300, int(os.getenv('LOCALIS_MARCA_LOGO_TTL_SEC', '86400')))

# marca normalizada -> dominio para el favicon oficial.
MARCAS_DOMINIO = {
    # Tecnología / electro
    'samsung': 'samsung.com', 'lg': 'lg.com', 'apple': 'apple.com',
    'iphone': 'apple.com', 'motorola': 'motorola.com', 'xiaomi': 'mi.com',
    'huawei': 'huawei.com', 'lenovo': 'lenovo.com', 'asus': 'asus.com',
    'acer': 'acer.com', 'sony': 'sony.com', 'panasonic': 'panasonic.com',
    'toshiba': 'toshiba.com', 'philips': 'philips.com', 'bosch': 'bosch.com',
    'siemens': 'siemens.com', 'whirlpool': 'whirlpool.com',
    'electrolux': 'electrolux.com', 'canon': 'canon.com', 'nikon': 'nikon.com',
    'kalley': 'kalley.com.co', 'haier': 'haier.com', 'mabe': 'mabe.com',
    'oster': 'oster.com', 'daewoo': 'daewoo.com', 'jbl': 'jbl.com',
    'logitech': 'logitech.com', 'kingston': 'kingston.com',
    'sandisk': 'sandisk.com', 'tp link': 'tp-link.com', 'tp-link': 'tp-link.com',
    'nokia': 'nokia.com', 'zte': 'zte.com.cn', 'tintatek': 'tintatek.com',
    # Ropa / calzado
    'nike': 'nike.com', 'adidas': 'adidas.com', 'puma': 'puma.com',
    'reebok': 'reebok.com', 'skechers': 'skechers.com',
    'new balance': 'newbalance.com', 'bata': 'bata.com', 'converse': 'converse.com',
    'vans': 'vans.com',
    # Cuidado personal / salud
    'colgate': 'colgate.com', 'dove': 'dove.com', 'nivea': 'nivea.com',
    'gillette': 'gillette.com', 'loreal': 'loreal.com', 'pantene': 'pantene.com',
    'sedal': 'sedal.com', 'rexona': 'rexona.com', 'ponds': 'ponds.com',
    'bayer': 'bayer.com', 'genfar': 'genfar.com', 'calox': 'calox.com',
    'la sante': 'lasante.com', 'medifarma': 'medifarma.com',
    # Alimentos / bebidas
    'pepsi': 'pepsi.com', 'coca cola': 'coca-cola.com', 'nestle': 'nestle.com',
    'unilever': 'unilever.com', 'heineken': 'heineken.com', 'redbull': 'redbull.com',
    'ferrero': 'ferrero.com', 'quaker': 'quakeroats.com', 'heinz': 'heinz.com',
    'bimbo': 'bimbo.com', 'mavesa': 'mavesa.com', 'polar': 'polar.com.ve',
    'altunsa': 'altunsa.com', 'danone': 'danone.com', 'kelloggs': 'kelloggs.com',
    'mondelez': 'mondelez.com', 'oreo': 'oreo.com',
    # Ferretería / automotriz
    '3m': '3m.com', 'makita': 'makita.com', 'dewalt': 'dewalt.com',
    'stanley': 'stanleytools.com', 'black decker': 'blackanddecker.com',
    'black+decker': 'blackanddecker.com', 'truper': 'truper.com',
    'pretul': 'pretul.com', 'skil': 'skil.com', 'sika': 'sika.com',
    'pintuco': 'pintuco.com', 'renner': 'renner.com.co', 'chevron': 'chevron.com',
    'mobil': 'mobil.com', 'valvoline': 'valvoline.com', 'bridgestone': 'bridgestone.com',
    'goodyear': 'goodyear.com', 'pirelli': 'pirelli.com', 'michelin': 'michelin.com',
    # Hogar
    'tefal': 'tefal.com', 'kenwood': 'kenwoodworld.com', 'totto': 'totto.com',
}

# marca normalizada -> slug de Simple Icons.
MARCAS_SLUG = {
    'samsung': 'samsung', 'lg': 'lg', 'apple': 'apple', 'motorola': 'motorola',
    'xiaomi': 'xiaomi', 'huawei': 'huawei', 'lenovo': 'lenovo', 'asus': 'asus',
    'acer': 'acer', 'sony': 'sony', 'panasonic': 'panasonic', 'toshiba': 'toshiba',
    'bosch': 'bosch', 'siemens': 'siemens', 'nikon': 'nikon', 'nike': 'nike',
    'adidas': 'adidas', 'puma': 'puma', 'reebok': 'reebok', 'unilever': 'unilever',
    'redbull': 'redbull', '3m': '3m',
}

_FUENTES_FAVICON = (
    'https://icons.duckduckgo.com/ip3/{dominio}.ico',
    'https://www.google.com/s2/favicons?domain={dominio}&sz=128',
)

_PLANTILLA_CACHE = 'marca_logo_v2'


def _normalizar(marca):
    texto = unicodedata.normalize('NFKD', str(marca or ''))
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', texto.lower()).strip()


def slug_marca(marca):
    base = re.sub(r'[^a-z0-9]+', '-', _normalizar(marca)).strip('-')
    return base[:60] or 'marca'


def _descargar(url, timeout=10.0):
    import requests

    try:
        respuesta = _http.get(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (Localis; logo-marca)'},
            timeout=timeout,
        )
        if respuesta.status_code != 200 or not respuesta.content:
            return None
        return respuesta.content
    except Exception:
        return None


def _procesar_logo_png(data):
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        return None
    if img.mode not in ('RGBA', 'LA'):
        img = img.convert('RGB')
    lado = _LADO
    lienzo = Image.new('RGB', (lado, lado), _BLANCO)
    copia = img.copy()
    copia.thumbnail((int(lado * 0.7), int(lado * 0.7)))
    x = (lado - copia.width) // 2
    y = (lado - copia.height) // 2
    if copia.mode in ('RGBA', 'LA'):
        lienzo.paste(copia, (x, y), copia)
    else:
        lienzo.paste(copia, (x, y))
    buffer = io.BytesIO()
    lienzo.save(buffer, 'WEBP', quality=92, method=4)
    return buffer.getvalue()


def _subir(data, nombre, content_type='image/png'):
    """Sube a Storage; para PNG también admite respaldo local."""
    if not data:
        return None
    try:
        from backend.supabase_storage import subir_bytes_con_respaldo

        url = subir_bytes_con_respaldo(
            data, filename=nombre, content_type=content_type, carpeta='marcas'
        )
        if url:
            return url
    except Exception:
        pass
    if nombre.lower().endswith('.png'):
        try:
            from backend.uploads_locales import guardar_bytes_upload

            return guardar_bytes_upload(data, nombre, carpeta='marcas')
        except Exception:
            return None
    return None


def logo_favicon(marca):
    dominio = MARCAS_DOMINIO.get(_normalizar(marca))
    if not dominio:
        return None
    for plantilla in _FUENTES_FAVICON:
        data = _descargar(plantilla.format(dominio=dominio))
        if not data:
            continue
        procesado = _procesar_logo_png(data)
        if not procesado:
            continue
        url = _subir(procesado, f'marca_{slug_marca(marca)}.webp', 'image/webp')
        if url:
            return url
    return None


def logo_simpleicons(marca):
    slug = MARCAS_SLUG.get(_normalizar(marca))
    if not slug:
        return None
    data = _descargar(f'https://cdn.simpleicons.org/{slug}')
    if not data:
        return None
    return _subir(data, f'marca_{slug}.svg', 'image/svg+xml')


def _wikidata_claims(qid, propiedad):
    datos = _get_json(
        'https://www.wikidata.org/w/api.php',
        params={
            'action': 'wbgetclaims', 'entity': qid, 'property': propiedad,
            'format': 'json',
        },
    )
    if not datos:
        return []
    return (datos.get('claims') or {}).get(propiedad) or []


def _wikidata_buscar(marca):
    datos = _get_json(
        'https://www.wikidata.org/w/api.php',
        params={
            'action': 'wbsearchentities', 'search': marca, 'language': 'es',
            'uselang': 'es', 'type': 'item', 'limit': 5, 'format': 'json',
        },
    )
    if not datos:
        datos = _get_json(
            'https://www.wikidata.org/w/api.php',
            params={
                'action': 'wbsearchentities', 'search': marca, 'language': 'en',
                'type': 'item', 'limit': 5, 'format': 'json',
            },
        )
    return (datos or {}).get('search') or []


_PALABRAS_MARCA = (
    'empresa', 'compañía', 'compania', 'marca', 'brand', 'company',
    'corporation', 'fabricante', 'manufacturer', 'alimento', 'bebida',
    'farmac', 'bodeg', 'supermercado', 'retail', 'tienda',
)


def _wikidata_es_marca(entidad, marca):
    label = str(entidad.get('label') or '').strip().lower()
    descripcion = str(entidad.get('description') or '').strip().lower()
    marca_norm = str(marca or '').strip().lower()
    if label and marca_norm and label != marca_norm:
        # Se acepta igualdad exacta o que el label contenga la marca.
        if marca_norm not in label and label not in marca_norm:
            return False
    return any(p in descripcion for p in _PALABRAS_MARCA)


def logo_wikidata(marca):
    """Logo **oficial vectorial** desde Wikidata/Wikimedia Commons (P154)."""
    marca = str(marca or '').strip()
    if not marca:
        return None
    for entidad in _wikidata_buscar(marca):
        qid = entidad.get('id')
        if not qid:
            continue
        if not _wikidata_es_marca(entidad, marca):
            continue
        claims = _wikidata_claims(qid, 'P154')
        if not claims:
            continue
        try:
            archivo = claims[0]['mainsnak']['datavalue']['value']
        except Exception:
            continue
        if not archivo:
            continue
        url = 'https://commons.wikimedia.org/wiki/Special:FilePath/' + _quote(archivo)
        data = _descargar(url)
        if not data:
            continue
        extension = str(archivo).rsplit('.', 1)[-1].lower()
        if extension == 'svg':
            content_type = 'image/svg+xml'
        elif extension in ('png', 'webp', 'gif'):
            data = _procesar_logo_png(data) or data
            content_type = 'image/webp'
        else:
            data = _procesar_logo_png(data)
            if not data:
                continue
            extension = 'webp'
            content_type = 'image/webp'
        subido = _subir(data, f'marca_{slug_marca(marca)}.{extension}', content_type)
        if subido:
            return subido
    return None


def _resolver(marca, permitir_red=True):
    """Logo **oficial** de la marca, o ``(None, None)``.

    Nunca inventa un monograma ni un placeholder: si no hay arte oficial
    verificable, el llamador debe dejar la imagen vacía (estado neutro).
    """
    marca = str(marca or '').strip()
    if not marca:
        return None, None
    if permitir_red:
        for generador, nombre in (
            (logo_simpleicons, 'logo_simpleicons'),
            (logo_wikidata, 'logo_wikidata'),
            (logo_favicon, 'logo_favicon'),
        ):
            try:
                url = generador(marca)
            except Exception:
                url = None
            if url:
                return url, nombre
    return None, None


def resolver_logo_marca(marca, permitir_red=True):
    """(url, fuente) del mejor logo/monograma disponible para la marca."""
    clave = f'{_PLANTILLA_CACHE}:{_normalizar(marca)}:{int(bool(permitir_red))}'
    try:
        return get_or_load(
            clave,
            lambda: _resolver(marca, permitir_red=permitir_red),
            ttl_seconds=_TTL_SEG,
        )
    except Exception:
        return _resolver(marca, permitir_red=permitir_red)


def logo_instantaneo(marca):
    """Obsoleto: ya **no** se inventan monogramas.

    Se conserva por compatibilidad de imports. Devuelve siempre ``None`` para
    que ningún flujo de importación fabrique un logo de texto.
    """
    return None

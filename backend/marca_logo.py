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
from pathlib import Path

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
_AMARILLO = (245, 158, 11)
_GRIS_TEXTO = (87, 83, 78)
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


def iniciales_marca(marca):
    palabras = [
        p for p in re.split(r'\s+', str(marca or '').strip())
        if p and p.lower() not in {'de', 'del', 'la', 'el', 'los', 'las', 'y'}
    ]
    if not palabras:
        return '?'
    if len(palabras) >= 2:
        return (palabras[0][0] + palabras[1][0]).upper()
    palabra = palabras[0]
    return (palabra[:2] if len(palabra) >= 2 else palabra).upper()


def _fuente(tamano):
    from PIL import ImageFont

    for intento in (
        lambda: ImageFont.truetype('DejaVuSans-Bold.ttf', tamano),
        lambda: ImageFont.truetype('Arial Bold.ttf', tamano),
        lambda: ImageFont.truetype('arial.ttf', tamano),
        lambda: ImageFont.load_default(size=tamano),
    ):
        try:
            return intento()
        except Exception:
            continue
    return ImageFont.load_default()


def monograma_png(marca):
    """PNG limpio (fondo blanco) con las iniciales de la marca."""
    from PIL import Image, ImageDraw

    marca = str(marca or '').strip() or 'Producto'
    lado = _LADO
    img = Image.new('RGB', (lado, lado), _BLANCO)
    dibujo = ImageDraw.Draw(img)
    dibujo.rectangle([0, 0, lado - 1, lado - 1], outline=(241, 240, 237), width=3)

    radio = int(lado * 0.28)
    centro = (lado // 2, int(lado * 0.42))
    dibujo.ellipse(
        [centro[0] - radio, centro[1] - radio, centro[0] + radio, centro[1] + radio],
        fill=_AMARILLO,
    )

    iniciales = iniciales_marca(marca)
    fuente_ini = _fuente(int(radio * 1.05))
    caja = dibujo.textbbox((0, 0), iniciales, font=fuente_ini)
    dibujo.text(
        (centro[0] - (caja[2] - caja[0]) / 2 - caja[0],
         centro[1] - (caja[3] - caja[1]) / 2 - caja[1]),
        iniciales,
        font=fuente_ini,
        fill=_BLANCO,
    )

    nombre = marca if len(marca) <= 18 else marca[:17].rstrip() + '…'
    fuente_nom = _fuente(34)
    caja_nom = dibujo.textbbox((0, 0), nombre, font=fuente_nom)
    dibujo.text(
        (lado / 2 - (caja_nom[2] - caja_nom[0]) / 2 - caja_nom[0], int(lado * 0.78)),
        nombre,
        font=fuente_nom,
        fill=_GRIS_TEXTO,
    )

    buffer = io.BytesIO()
    img.save(buffer, 'PNG', optimize=True)
    return buffer.getvalue()


def _carpeta_local():
    from config import RUTA_RAIZ

    destino = Path(RUTA_RAIZ) / 'static' / 'uploads' / 'marcas'
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def archivo_monograma_local(marca):
    """Genera (una vez) y devuelve la URL local del monograma de la marca."""
    nombre = f'{slug_marca(marca)}.png'
    ruta = _carpeta_local() / nombre
    if not ruta.is_file():
        try:
            ruta.write_bytes(monograma_png(marca))
        except Exception as error:
            print(f'{_LOG} no se pudo generar monograma {marca!r}: {type(error).__name__}: {error}')
            return None
    return f'/static/uploads/marcas/{nombre}'


_MONO_CACHE = 'marca_monograma_v1'


def logo_monograma_durable(marca):
    """Monograma **durable**: se sube a Storage (o local) y se cachea por marca.

    Evita que las tarjetas queden rotas tras un redespliegue (disco efímero).
    """
    def _generar():
        try:
            data = monograma_png(marca)
        except Exception:
            return archivo_monograma_local(marca)
        subido = _subir(data, f'marca_{slug_marca(marca)}.png', 'image/png')
        return subido or archivo_monograma_local(marca)

    try:
        return get_or_load(
            f'{_MONO_CACHE}:{_normalizar(marca)}', _generar, ttl_seconds=_TTL_SEG
        )
    except Exception:
        return _generar()


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


def tarjeta_producto_png(nombre, categoria=None):
    """Tarjeta limpia (fondo blanco) con el nombre del producto.

    Placeholder profesional y **distinto por producto** para el último recurso
    (evita el 'sin imagen' genérico y da identidad visual a toda la tarjeta).
    """
    from PIL import Image, ImageDraw

    nombre = str(nombre or 'Producto').strip() or 'Producto'
    categoria_txt = str(categoria or '').strip()
    lado = _LADO
    img = Image.new('RGB', (lado, lado), _BLANCO)
    dibujo = ImageDraw.Draw(img)
    dibujo.rectangle([0, 0, lado - 1, lado - 1], outline=(241, 240, 237), width=3)
    dibujo.rectangle([0, 0, lado, 14], fill=_AMARILLO)

    # Icono de caja (marca visual limpia).
    dibujo.rounded_rectangle(
        [lado * 0.34, lado * 0.20, lado * 0.66, lado * 0.46],
        radius=18, outline=(245, 158, 11), width=8,
    )
    dibujo.line([lado * 0.34, lado * 0.30, lado * 0.66, lado * 0.30], fill=(245, 158, 11), width=8)

    if categoria_txt:
        fuente_cat = _fuente(26)
        caja = dibujo.textbbox((0, 0), categoria_txt[:24], font=fuente_cat)
        dibujo.text(
            (lado / 2 - (caja[2] - caja[0]) / 2 - caja[0], lado * 0.50),
            categoria_txt[:24], font=fuente_cat, fill=(168, 162, 158),
        )

    # Nombre con salto de línea simple (hasta 3 líneas).
    fuente = _fuente(40)
    palabras = nombre.split()
    lineas = []
    actual = ''
    for palabra in palabras:
        prueba = f'{actual} {palabra}'.strip()
        caja = dibujo.textbbox((0, 0), prueba, font=fuente)
        if caja[2] - caja[0] > lado * 0.78 and actual:
            lineas.append(actual)
            actual = palabra
        else:
            actual = prueba
    if actual:
        lineas.append(actual)
    lineas = lineas[:3]
    y = lado * 0.60
    for linea in lineas:
        caja = dibujo.textbbox((0, 0), linea, font=fuente)
        dibujo.text(
            (lado / 2 - (caja[2] - caja[0]) / 2 - caja[0], y),
            linea, font=fuente, fill=(68, 64, 60),
        )
        y += (caja[3] - caja[1]) + 14

    buffer = io.BytesIO()
    img.save(buffer, 'PNG', optimize=True)
    return buffer.getvalue()


def archivo_tarjeta_producto(nombre, categoria=None):
    """Genera (una vez, cacheada) y devuelve la URL de la tarjeta del producto."""
    import hashlib

    clave = hashlib.sha1(
        f'{_normalizar(nombre)}|{_normalizar(categoria)}'.encode('utf-8', 'ignore')
    ).hexdigest()[:14]
    filename = f'producto_{clave}.png'

    def _generar():
        try:
            data = tarjeta_producto_png(nombre, categoria)
        except Exception:
            return None
        subido = _subir(data, filename, 'image/png')
        if subido:
            return subido
        try:
            from backend.uploads_locales import guardar_bytes_upload

            return guardar_bytes_upload(data, filename, carpeta='genericos')
        except Exception:
            return None

    try:
        return get_or_load(
            f'tarjeta_producto_v1:{clave}', _generar, ttl_seconds=_TTL_SEG
        )
    except Exception:
        return _generar()

"""Respaldo visual por marca (logo oficial o monograma limpio).

Cuando el motor agota todas las fuentes y no encuentra la foto real del
producto, **nunca** deja la tarjeta vacía ni con un placeholder genérico:
asigna la identidad visual de la marca.

Cadena de respaldo:
  1. Logo oficial por dominio de la marca (favicon de alta resolución, PNG).
  2. Logo oficial por Simple Icons (SVG, si el slug existe).
  3. **Monograma de marca** generado localmente (PNG, fondo blanco) — universal,
     instantáneo y sin red, válido para cualquier marca (Altunsa, Mavesa, etc.).

Todo se guarda en Supabase Storage (o `/static/uploads/marcas/` como respaldo)
y se cachea por marca para no repetir trabajo.
"""

from __future__ import annotations

import io
import os
import re
import unicodedata
from pathlib import Path

from backend.runtime_cache import get_or_load

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

_PLANTILLA_CACHE = 'marca_logo_v1'


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


def _descargar(url, timeout=10.0):
    import requests

    try:
        respuesta = requests.get(
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


def _resolver(marca, permitir_red=True):
    marca = str(marca or '').strip()
    if not marca:
        return None, None
    if permitir_red:
        url = logo_favicon(marca)
        if url:
            return url, 'logo_favicon'
        url = logo_simpleicons(marca)
        if url:
            return url, 'logo_simpleicons'
    local = archivo_monograma_local(marca)
    if local:
        return local, 'logo_monograma'
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
    """Monograma local sin red (para asignación inmediata en importaciones)."""
    try:
        local = archivo_monograma_local(marca)
        return local
    except Exception:
        return None

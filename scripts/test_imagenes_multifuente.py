#!/usr/bin/env python3
"""
Valida cascada oficial, filtro de calidad y subida manual.

Casos:
  - Comida (Harina PAN / Pepsi) → URL oficial Open Facts o Storage
  - Electrónica (iPhone / Logitech) → ficha oficial o None (nunca wiki/calle)
  - Ferretería / ropa genérica → None (placeholder)
  - Pillow rechaza miniatura y foto de escena
  - Subida manual sigue devolviendo URL persistible
  - CSS de tarjetas usa object-fit: contain
"""

from __future__ import annotations

import io
import re
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _cargar_entorno():
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=True)


def _ok(condicion, mensaje, errores):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    errores.append(mensaje)
    return False


def _jpeg_nitido():
    from PIL import Image, ImageDraw

    img = Image.new('RGB', (480, 480), (255, 255, 255))
    dibujo = ImageDraw.Draw(img)
    for i in range(0, 480, 24):
        dibujo.line((i, 0, i, 479), fill=(30, 30, 30), width=2)
        dibujo.line((0, i, 479, i), fill=(30, 30, 30), width=2)
    dibujo.rectangle((40, 40, 440, 440), outline=(20, 80, 40), width=10)
    dibujo.ellipse((140, 140, 340, 340), fill=(245, 158, 11))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=90)
    return buf.getvalue()


def _jpeg_miniatura():
    from PIL import Image

    img = Image.new('RGB', (48, 48), (90, 90, 90))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=40)
    return buf.getvalue()


def _jpeg_calle():
    from PIL import Image, ImageDraw

    img = Image.new('RGB', (480, 480), (42, 48, 38))
    dibujo = ImageDraw.Draw(img)
    dibujo.rectangle((0, 300, 479, 479), fill=(70, 72, 68))
    dibujo.ellipse((80, 80, 220, 200), fill=(180, 160, 90))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=80)
    return buf.getvalue()


def _url_oficial_o_almacenada(url):
    from backend.utils import imagen_url_almacenada

    return bool(imagen_url_almacenada(url))


def _host_prohibido(url):
    texto = str(url or '').lower()
    return any(
        marca in texto
        for marca in (
            'wikimedia',
            'wikipedia',
            'openverse',
            'flickr',
            'unsplash',
            'instagram',
            'facebook',
        )
    )


def main() -> int:
    _cargar_entorno()
    errores = []

    from backend.image_manager import (
        _consulta_nombre_permitida,
        _tokens_nombre,
        descubrir_imagen_catalogo,
    )
    from backend.image_quality import evaluar_imagen_bytes
    from backend.image_sources import (
        FAMILIA_ALIMENTOS,
        FAMILIA_HOGAR,
        FAMILIA_ROPA,
        FAMILIA_TECNOLOGIA,
        clasificar_familia,
    )
    from backend.utils import imagen_url_almacenada, url_imagen_catalogo_valida

    print('=== Clasificacion de familia ===')
    _ok(
        clasificar_familia(nombre='Harina PAN 1kg', categoria='Alimentos')
        == FAMILIA_ALIMENTOS,
        'Harina PAN / Alimentos = familia alimentos',
        errores,
    )
    _ok(
        clasificar_familia(nombre='iPhone 15 Pro', categoria='Tecnología')
        == FAMILIA_TECNOLOGIA,
        'iPhone / Tecnologia = familia tecnologia',
        errores,
    )
    _ok(
        clasificar_familia(nombre='Mouse Logitech inalambrico') == FAMILIA_TECNOLOGIA,
        'Logitech por nombre = tecnologia',
        errores,
    )
    _ok(
        clasificar_familia(nombre='Martillo de uña', categoria='Ferretería')
        == FAMILIA_HOGAR,
        'Martillo / Ferreteria = familia hogar',
        errores,
    )
    _ok(
        clasificar_familia(nombre='Camisa polo', categoria='Ropa') == FAMILIA_ROPA,
        'Camisa / Ropa = familia ropa',
        errores,
    )

    print('\n=== Consulta por nombre (solo precisas) ===')
    _ok(
        _consulta_nombre_permitida(_tokens_nombre('Harina PAN'), FAMILIA_ALIMENTOS),
        'Harina PAN permite respaldo por nombre',
        errores,
    )
    _ok(
        _consulta_nombre_permitida(_tokens_nombre('Pepsi'), FAMILIA_ALIMENTOS),
        'Pepsi (marca) permite respaldo por nombre',
        errores,
    )
    _ok(
        _consulta_nombre_permitida(_tokens_nombre('iPhone'), FAMILIA_TECNOLOGIA),
        'iPhone (marca) permite respaldo por nombre',
        errores,
    )
    _ok(
        not _consulta_nombre_permitida(_tokens_nombre('Martillo'), FAMILIA_HOGAR),
        'Martillo generico NO consulta APIs',
        errores,
    )
    _ok(
        not _consulta_nombre_permitida(_tokens_nombre('Camisa polo'), FAMILIA_ROPA),
        'Camisa polo generica NO consulta APIs',
        errores,
    )
    _ok(
        not _consulta_nombre_permitida(_tokens_nombre('Leche'), FAMILIA_ALIMENTOS),
        'Leche generica NO consulta APIs',
        errores,
    )

    print('\n=== Filtro de calidad Pillow ===')
    baja = evaluar_imagen_bytes(_jpeg_miniatura())
    _ok(not baja.get('ok'), f'miniatura rechazada ({baja.get("motivo")})', errores)
    nitida = evaluar_imagen_bytes(_jpeg_nitido())
    _ok(
        nitida.get('ok') and nitida.get('ancho') == 480,
        f'foto nitida aceptada {nitida.get("ancho")}x{nitida.get("alto")} nitidez={nitida.get("nitidez")}',
        errores,
    )
    calle = evaluar_imagen_bytes(_jpeg_calle(), exigir_fondo_ficha=True)
    _ok(
        not calle.get('ok'),
        f'foto de escena rechazada ({calle.get("motivo")})',
        errores,
    )
    wiki = (
        'https://upload.wikimedia.org/wikipedia/commons/8/89/HD_transparent_picture.png'
    )
    _ok(
        url_imagen_catalogo_valida(wiki) is None,
        'Wikimedia/Commons queda fuera del catalogo oficial',
        errores,
    )

    print('=== Cascada comida ===')
    t0 = time.perf_counter()
    url_comida = descubrir_imagen_catalogo(
        nombre='Harina PAN', categoria='Alimentos'
    ) or descubrir_imagen_catalogo(nombre='Pepsi', categoria='Alimentos')
    ms_comida = (time.perf_counter() - t0) * 1000
    print(f'  url_comida={url_comida!r} ({ms_comida:.0f} ms)')
    persistible = _url_oficial_o_almacenada(url_comida)
    _ok(bool(persistible), 'comida devolvio URL persistible de catalogo', errores)
    _ok(not _host_prohibido(url_comida), 'comida no usa fuente abierta/social', errores)
    _ok(
        bool(url_comida)
        and (
            str(url_comida).startswith('https://')
            or str(url_comida).startswith('/static/uploads/')
        ),
        'comida es URL operativa',
        errores,
    )

    print('\n=== Cascada electronica ===')
    t0 = time.perf_counter()
    url_tech = descubrir_imagen_catalogo(
        nombre='iPhone', categoria='Tecnología'
    ) or descubrir_imagen_catalogo(
        nombre='Logitech', categoria='Tecnología'
    )
    ms_tech = (time.perf_counter() - t0) * 1000
    print(f'  url_tech={url_tech!r} ({ms_tech:.0f} ms)')
    persistible_tech = _url_oficial_o_almacenada(url_tech)
    _ok(not _host_prohibido(url_tech), 'electronica no usa wiki/redes/calle', errores)
    if url_tech:
        _ok(persistible_tech, 'si hay foto de electronica, es oficial o Storage', errores)
        _ok(
            str(url_tech).startswith('https://')
            or str(url_tech).startswith('/static/uploads/'),
            'electronica es URL operativa',
            errores,
        )
        if url_comida:
            _ok(
                url_tech != url_comida,
                'comida y electronica no reutilizan la misma foto',
                errores,
            )
    else:
        _ok(True, 'electronica sin ficha oficial: placeholder (correcto)', errores)

    print('\n=== Cascada general sin EAN ===')
    t0 = time.perf_counter()
    url_ferre = descubrir_imagen_catalogo(
        nombre='Martillo', categoria='Ferretería'
    )
    ms_ferre = (time.perf_counter() - t0) * 1000
    print(f'  url_ferre={url_ferre!r} ({ms_ferre:.0f} ms)')
    _ok(url_ferre is None, 'ferreteria generica no inventa foto de calle', errores)
    _ok(ms_ferre < 2500, f'ferreteria aborta rapido ({ms_ferre:.0f} ms)', errores)

    t0 = time.perf_counter()
    url_ropa = descubrir_imagen_catalogo(nombre='Camisa polo', categoria='Ropa')
    ms_ropa = (time.perf_counter() - t0) * 1000
    print(f'  url_ropa={url_ropa!r} ({ms_ropa:.0f} ms)')
    _ok(url_ropa is None, 'ropa generica no inventa foto de catalogo', errores)
    _ok(ms_ropa < 2500, f'ropa aborta rapido ({ms_ropa:.0f} ms)', errores)

    print('\n=== Subida manual hibrida ===')
    from werkzeug.datastructures import FileStorage

    from backend.image_lookup import persistir_imagen_producto_hibrida

    archivo = FileStorage(
        stream=io.BytesIO(_jpeg_nitido()),
        filename='foto-manual-catalogo.jpg',
        content_type='image/jpeg',
    )
    t0 = time.perf_counter()
    url_manual, aviso = persistir_imagen_producto_hibrida(
        file_storage=archivo,
        nombre='Producto manual de prueba',
        comercio_id='test',
    )
    ms_manual = (time.perf_counter() - t0) * 1000
    print(f'  url_manual={url_manual!r} aviso={aviso!r} ({ms_manual:.0f} ms)')
    _ok(bool(imagen_url_almacenada(url_manual)), 'subida manual con URL persistible', errores)
    _ok(
        'openfoodfacts' not in str(url_manual or '').lower(),
        'archivo manual no se sustituyo por cascada automatica',
        errores,
    )
    _ok(ms_manual < 8000, f'subida manual rapida ({ms_manual:.0f} ms)', errores)

    print('\n=== Presentacion catalogo ===')
    css = (RAIZ / 'static' / 'css' / 'responsive.css').read_text(encoding='utf-8')
    _ok('object-fit: contain' in css, 'tarjetas usan object-fit: contain', errores)
    _ok('height: 180px' in css, 'contenedor de foto con altura fija 180px', errores)
    _ok('#fffefb' in css, 'fondo limpio en contenedor de foto', errores)
    wraps = re.findall(r'\.localis-img-producto-wrap[^{]*\{([^}]+)\}', css, flags=re.S)
    alturas_fijas = [
        match.group(0)
        for cuerpo in wraps
        for match in re.finditer(r'(?<![a-z-])height:\s*\d+px', cuerpo)
    ]
    _ok(bool(alturas_fijas), 'wraps con height fijo en px', errores)

    print('\n=== RESULTADO ===')
    if errores:
        print('FALLO imagenes multifuente:')
        for item in errores:
            print(f'  - {item}')
        return 1
    print(
        'OK cascada oficial: comida limpia, genericos con placeholder, '
        'calidad estricta, subida manual visible, tarjetas contain.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

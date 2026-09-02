#!/usr/bin/env python3
"""
Valida presentacion uniforme del catalogo y subida manual hibrida.

Comprueba:
  1. CSS: altura fija 180px, object-fit contain, fondo limpio.
  2. Plantillas: tarjetas con wrap, modal con <img> (no bg-cover).
  3. Compresion: JPEG apaisado -> WebP cuadrado sin estirar.
  4. Subida manual: URL persistible inmediata (local o Storage).
  5. Alta sin archivo no bloquea con OpenFoodFacts.
  6. Listado SQL no une catalogo_maestro.
  7. El filtro de plantilla muestra /static/uploads/ y no placeholder.
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

PLACEHOLDER = '/static/img/placeholder-producto.svg'


def _cargar_entorno() -> None:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=True)


def _ok(condicion, mensaje, errores):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    errores.append(mensaje)
    return False


def _jpeg_apaisado(ancho=1200, alto=800) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new('RGB', (ancho, alto), (18, 92, 64))
    dibujo = ImageDraw.Draw(img)
    dibujo.rectangle((60, 60, ancho - 60, alto - 60), fill=(245, 158, 11))
    dibujo.ellipse((ancho // 3, alto // 4, ancho // 3 + 280, alto // 4 + 280), fill=(254, 251, 246))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=90)
    return buf.getvalue()


def _file_storage(data: bytes, nombre='foto-catalogo.jpg'):
    from werkzeug.datastructures import FileStorage

    return FileStorage(
        stream=io.BytesIO(data),
        filename=nombre,
        content_type='image/jpeg',
    )


def _alturas_fijas_wrap(css: str):
    bloques = re.findall(
        r'\.localis-img-producto-wrap[^{]*\{([^}]+)\}',
        css,
        flags=re.S,
    )
    malas = []
    for cuerpo in bloques:
        for match in re.finditer(r'(?<![a-z-])height:\s*(\d+)px', cuerpo):
            malas.append(match.group(0).strip())
    return malas


def _probar_css(errores):
    print('\n=== CSS tarjetas ===')
    css = (RAIZ / 'static' / 'css' / 'responsive.css').read_text(encoding='utf-8')
    _ok('object-fit: contain' in css, 'object-fit: contain', errores)
    _ok('height: 180px' in css, 'altura fija 180px en el wrap', errores)
    _ok('#fffefb' in css, 'fondo #fffefb', errores)
    malas = _alturas_fijas_wrap(css)
    _ok(bool(malas), f'wraps con height px {malas or ""}'.strip(), errores)
    _ok(
        'bg-cover' not in (RAIZ / 'templates' / 'cliente.html').read_text(encoding='utf-8'),
        'modal del catalogo ya no usa bg-cover',
        errores,
    )


def _probar_plantillas(errores):
    print('\n=== Plantillas ===')
    cliente = (RAIZ / 'templates' / 'cliente.html').read_text(encoding='utf-8')
    tienda = (RAIZ / 'templates' / 'tienda_publica.html').read_text(encoding='utf-8')
    comercio = (RAIZ / 'templates' / 'comercio.html').read_text(encoding='utf-8')
    _ok('localis-img-producto-wrap' in cliente, 'catalogo home usa wrap 1:1', errores)
    _ok('localis-img-producto-wrap' in tienda, 'tienda publica usa wrap 1:1', errores)
    _ok('id="modal-imagen"' in cliente and '<img id="modal-imagen"' in cliente, 'modal usa <img>', errores)
    _ok('backgroundImage' not in cliente, 'JS del modal no asigna backgroundImage', errores)
    _ok('localis-img-producto-thumb-wrap' in comercio, 'panel comercio usa thumbs cuadrados', errores)


def _probar_lienzo(errores):
    print('\n=== Lienzo cuadrado (backend) ===')
    from PIL import Image

    from backend.images import comprimir_bytes_a_bytes

    jpeg = _jpeg_apaisado()
    data, content_type, filename = comprimir_bytes_a_bytes(
        jpeg,
        prefijo='lienzo_test',
        max_dimension=720,
        lienzo_cuadrado=True,
    )
    _ok(content_type == 'image/webp', f'content_type WebP ({content_type})', errores)
    _ok(filename.endswith('.webp'), f'filename WebP ({filename})', errores)
    _ok(len(data) < len(jpeg), f'WebP mas liviano ({len(data)} < {len(jpeg)})', errores)
    with Image.open(io.BytesIO(data)) as img:
        _ok(img.size[0] == img.size[1], f'lienzo cuadrado {img.size}', errores)
        _ok(img.size[0] <= 720, f'lado <= 720 ({img.size[0]})', errores)


def _probar_subida_y_filtro(errores):
    print('\n=== Subida manual hibrida ===')
    from backend.image_lookup import persistir_imagen_producto_hibrida
    from backend.uploads_locales import archivo_upload_existe
    from backend.utils import imagen_url_almacenada, url_imagen_local_valida
    from utils.images import url_imagen_producto_segura, url_publica_producto_desde_bd

    jpeg = _jpeg_apaisado()
    t0 = time.perf_counter()
    url, aviso = persistir_imagen_producto_hibrida(
        file_storage=_file_storage(jpeg),
        nombre='Producto presentacion test',
        comercio_id='presentacion',
    )
    ms = (time.perf_counter() - t0) * 1000
    print(f'  url={url!r} aviso={aviso!r} ({ms:.0f} ms)')
    _ok(bool(imagen_url_almacenada(url)), 'URL persistible inmediata', errores)
    _ok(ms < 4000, f'subida en menos de 4s ({ms:.0f} ms)', errores)
    _ok('openfoodfacts' not in str(url or '').lower(), 'no se sustituyo por OpenFoodFacts', errores)
    mostrable = url_publica_producto_desde_bd(url)
    _ok(bool(mostrable) and mostrable != PLACEHOLDER, 'url_publica usable', errores)
    segura = url_imagen_producto_segura({'imagen_url': url})
    _ok(segura and segura != PLACEHOLDER, f'filtro plantilla ({segura})', errores)
    local = url_imagen_local_valida(url)
    if local:
        _ok(archivo_upload_existe(url), 'archivo existe en static/uploads', errores)
    return url


def _probar_alta_sin_archivo(errores):
    print('\n=== Alta sin archivo (sin bloquear) ===')
    from backend.image_lookup import persistir_imagen_producto_hibrida

    t0 = time.perf_counter()
    url, aviso = persistir_imagen_producto_hibrida(
        file_storage=None,
        nombre='Producto sin foto test',
        comercio_id='presentacion',
        codigo_barras=None,
    )
    ms = (time.perf_counter() - t0) * 1000
    print(f'  url={url!r} aviso={aviso!r} ({ms:.0f} ms)')
    _ok(ms < 1500, f'sin archivo en menos de 1.5s ({ms:.0f} ms)', errores)
    _ok(aviso is None, 'sin aviso de error al crear sin foto', errores)


def _probar_sql_listado(errores):
    print('\n=== SQL listado ===')
    stores = (RAIZ / 'backend' / 'stores.py').read_text(encoding='utf-8')
    _ok(
        'catalogo_maestro_imagenes' not in stores.split('def _base_query_productos_publicos')[1].split('def ')[0],
        'SELECT publico sin JOIN a catalogo_maestro',
        errores,
    )
    _ok('p.imagen_url AS imagen_url' in stores, 'listado lee p.imagen_url', errores)


def _probar_catalogo_http(errores):
    print('\n=== HTML catalogo (test client) ===')
    from main import _hilo_init, app

    if _hilo_init.is_alive():
        print('  esperando inicializacion de arranque...')
        _hilo_init.join(timeout=90)

    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    t0 = time.perf_counter()
    with app.test_client() as cliente:
        respuesta = cliente.get('/')
        ms = (time.perf_counter() - t0) * 1000
        html = respuesta.get_data(as_text=True)
        print(f'  GET / -> {respuesta.status_code} ({ms:.0f} ms) bytes={len(html)}')
        _ok(respuesta.status_code == 200, 'home HTTP 200', errores)
        _ok(ms < 12000, f'home en menos de 12s ({ms:.0f} ms)', errores)
        if 'localis-img-producto-wrap' in html:
            _ok(True, 'HTML del catalogo incluye wrap de foto', errores)
        else:
            _ok(
                'No se encontraron productos' in html or 'grid-productos-mobile' in html,
                'home renderiza seccion de catalogo (vacio o con productos)',
                errores,
            )
        css = cliente.get('/static/css/responsive.css')
        _ok(css.status_code == 200, 'CSS responsive servido', errores)
        cuerpo_css = css.get_data(as_text=True)
        _ok('object-fit: contain' in cuerpo_css, 'CSS servido tiene contain', errores)


def _limpiar_url(url):
    from backend.uploads_locales import url_upload_local_valida
    from backend.utils import url_imagen_local_valida

    local = url_imagen_local_valida(url) or url_upload_local_valida(url)
    if not local:
        return
    relativo = local[len('/static/') :].replace('\\', '/')
    ruta = RAIZ / 'static' / Path(*relativo.split('/'))
    nombre = ruta.name.lower()
    if not ruta.is_file():
        return
    if 'presentacion' not in nombre and not nombre.startswith('manual_presentacion'):
        return
    try:
        ruta.unlink()
    except OSError as error:
        print(f'  aviso limpieza: {error}')


def main():
    _cargar_entorno()
    errores = []
    url = None
    try:
        _probar_css(errores)
        _probar_plantillas(errores)
        _probar_lienzo(errores)
        url = _probar_subida_y_filtro(errores)
        _probar_alta_sin_archivo(errores)
        _probar_sql_listado(errores)
        _probar_catalogo_http(errores)
    finally:
        _limpiar_url(url)

    print('\n=== RESULTADO ===')
    if errores:
        print('FALLO presentacion catalogo:')
        for item in errores:
            print(f'  - {item}')
        return 1
    print(
        'OK presentacion: tarjetas 1:1 contain, lienzo cuadrado, '
        'subida manual visible, listado sin maestro, home rapida.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

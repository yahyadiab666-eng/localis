#!/usr/bin/env python3
"""
Protocolo QA de sesión de usuario: catálogo Localis.

Simula:
  1. Alta de producto con EAN (Nutella).
  2. Alta de producto sin EAN genérico (Martillo) → placeholder.
  3. Alta de producto sin EAN con marca (Harina PAN) → foto oficial o placeholder.
  4. Importación masiva CSV (EAN + genérico + URL externa basura).
  5. Fallo de red / sin coincidencia oficial → placeholder, sin basura.
  6. Render del catálogo: solo Storage, /static/uploads/ o placeholder oficial.
"""

from __future__ import annotations

import csv
import io
import os
import re
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

PREFIJO = '__localis_qa_sesion__'
PLACEHOLDER = '/static/img/placeholder-producto.svg'
EAN_NUTELLA = '3017620422003'
HOSTS_PROHIBIDOS = (
    'wikimedia',
    'wikipedia',
    'openverse',
    'flickr',
    'unsplash',
    'instagram',
    'facebook',
    'pexels.com',
    'ejemplo.com',
    'cdn.example',
    'images.google',
)
HOSTS_OFF_CRUDOS = (
    'openfoodfacts.org',
    'openproductsfacts.org',
    'openbeautyfacts.org',
    'openpetfoodfacts.org',
    'wsrv.nl',
)
_RE_IMG_TAG = re.compile(r'<img\b[^>]*>', re.I)
_RE_SRC = re.compile(r'\bsrc="([^"]*)"', re.I)
_RE_WRAP = re.compile(r'localis-img-producto-wrap')


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


def _es_url_limpia(url):
    from backend.utils import imagen_url_almacenada, url_imagen_api_oficial_valida

    texto = str(url or '').strip()
    if not texto:
        return False
    if texto == PLACEHOLDER or texto.endswith('placeholder-producto.svg'):
        return True
    if imagen_url_almacenada(texto) or url_imagen_api_oficial_valida(texto):
        return True
    return False


def _tiene_basura(url):
    texto = str(url or '').lower()
    if any(h in texto for h in HOSTS_PROHIBIDOS):
        return True
    if any(h in texto for h in HOSTS_OFF_CRUDOS):
        return True
    return False


def _limpiar(usuario_id=None, comercio_id=None):
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        if comercio_id:
            cursor.execute(
                'DELETE FROM productos WHERE comercio_id = ?',
                (int(comercio_id),),
            )
            try:
                cursor.execute(
                    'DELETE FROM comercios WHERE id = ?',
                    (int(comercio_id),),
                )
            except Exception:
                conexion.rollback()
        cursor.execute(
            'DELETE FROM productos WHERE nombre LIKE ?',
            (PREFIJO + '%',),
        )
        cursor.execute(
            'DELETE FROM comercios WHERE nombre LIKE ?',
            (PREFIJO + '%',),
        )
        cursor.execute(
            'DELETE FROM usuarios WHERE correo LIKE ?',
            (PREFIJO + '%',),
        )
        if usuario_id:
            try:
                cursor.execute(
                    'DELETE FROM usuarios WHERE id = ?',
                    (int(usuario_id),),
                )
            except Exception:
                conexion.rollback()
        conexion.commit()


def _crear_sandbox():
    from backend.db import get_db_connection

    token = uuid.uuid4().hex[:8]
    correo = f'{PREFIJO}{token}@localis.test'
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            INSERT INTO usuarios (nombre, correo, rol)
            VALUES (?, ?, 'comerciante')
            RETURNING id
            """,
            ('QA Sesion Catalogo', correo),
        )
        fila = cursor.fetchone()
        usuario_id = int(fila['id'] if isinstance(fila, dict) else fila[0])
        cursor.execute('SELECT id FROM categorias ORDER BY id LIMIT 1')
        cat = cursor.fetchone()
        categoria_id = int(
            cat['id'] if isinstance(cat, dict) else (cat[0] if cat else 1)
        )
        cursor.execute(
            """
            INSERT INTO comercios (
                usuario_id, nombre, descripcion, telefono, direccion, ciudad, zona,
                categoria_id, plan_id, plan_tipo, limite_productos, estado_pago, visible
            )
            VALUES (?, ?, 'QA sesion catalogo', '04141234567', 'Av. Prueba',
                    'Porlamar', 'Centro', ?, NULL, 'gratis', 80, 'activo', 1)
            RETURNING id
            """,
            (usuario_id, f'{PREFIJO} tienda {token}', categoria_id),
        )
        fila_c = cursor.fetchone()
        conexion.commit()
        comercio_id = int(fila_c['id'] if isinstance(fila_c, dict) else fila_c[0])
    return usuario_id, comercio_id, correo


def _sesion(cliente, usuario_id, comercio_id, correo):
    with cliente.session_transaction() as sess:
        sess['usuario_id'] = int(usuario_id)
        sess['username'] = 'QA Sesion'
        sess['correo'] = correo
        sess['rol'] = 'comerciante'
        sess['es_admin'] = False
        sess['comercio_id'] = int(comercio_id)
        sess['panel_comercio_activo'] = True


def _url_bd(producto_id):
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            'SELECT imagen_url FROM productos WHERE id = ?',
            (int(producto_id),),
        )
        fila = cursor.fetchone()
    if not fila:
        return None
    return fila['imagen_url'] if isinstance(fila, dict) else fila[0]


def _esperar_url(producto_id, timeout=90):
    from backend.utils import imagen_url_almacenada

    t0 = time.monotonic()
    ultima = _url_bd(producto_id)
    while time.monotonic() - t0 < timeout:
        ultima = _url_bd(producto_id)
        if imagen_url_almacenada(ultima):
            return ultima
        time.sleep(1.2)
    return ultima


def _esperar_sin_url(producto_id, timeout=10):
    t0 = time.monotonic()
    ultima = _url_bd(producto_id)
    while time.monotonic() - t0 < timeout:
        ultima = _url_bd(producto_id)
        if ultima:
            return ultima
        time.sleep(0.5)
    return ultima


def _app_cliente():
    from main import _hilo_init, app

    if _hilo_init.is_alive():
        print('  esperando init...')
        _hilo_init.join(timeout=90)
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


def _csv_inventario():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['nombre', 'precio', 'codigo_barras', 'descripcion', 'imagen_url'])
    writer.writerow(
        [
            f'{PREFIJO} Pepsi 1.5L',
            '1.80',
            '4060800105406',
            'refresco cola',
            '',
        ]
    )
    writer.writerow(
        [
            f'{PREFIJO} Martillo CSV',
            '4.50',
            '',
            'herramienta de acero',
            'https://unsplash.com/photos/calle-deforme.jpg',
        ]
    )
    writer.writerow(
        [
            f'{PREFIJO} Basura externa',
            '0.99',
            '',
            'sin ficha',
            'https://ejemplo.com/foto-de-la-calle.jpg',
        ]
    )
    writer.writerow(
        [
            f'{PREFIJO} Nutella CSV',
            '3.20',
            EAN_NUTELLA,
            'crema de avellanas',
            'https://images.openfoodfacts.org/images/products/raw.jpg',
        ]
    )
    return buf.getvalue().encode('utf-8')


def _probar_sanitizacion_unitaria(errores):
    print('\n=== Blindaje de URLs (unidad) ===')
    from utils.images import (
        PLACEHOLDER_PRODUCTO,
        url_imagen_producto,
        url_publica_producto_desde_bd,
    )
    from backend.utils import imagen_url_para_persistir

    basura = [
        'https://ejemplo.com/a.jpg',
        'https://cdn.example.com/foto.webp',
        'https://images.openfoodfacts.org/images/products/x.jpg',
        'https://upload.wikimedia.org/wikipedia/commons/x.png',
        'https://unsplash.com/photo.jpg',
        'http://openfoodfacts.org/x.jpg',
    ]
    for url in basura:
        persistida = imagen_url_para_persistir(url)
        publica = url_publica_producto_desde_bd(url)
        vista = url_imagen_producto({'imagen_url': url, 'codigo_barras': None})
        _ok(persistida is None, f'no se persiste {url[:48]}', errores)
        _ok(not publica, f'no se muestra cruda {url[:48]}', errores)
        _ok(
            vista == PLACEHOLDER_PRODUCTO,
            f'vista placeholder ante {url[:48]}',
            errores,
        )

    vacia = url_imagen_producto({'imagen_url': None, 'codigo_barras': None})
    _ok(vacia == PLACEHOLDER_PRODUCTO, 'sin coincidencia: placeholder oficial', errores)


def _probar_red_caida(errores):
    print('\n=== Fallback con red caída ===')
    from services.smart_image_pipeline import resolver_imagen_automatica
    from utils.images import PLACEHOLDER_PRODUCTO, url_imagen_producto

    with patch(
        'services.smart_image_pipeline.hay_proveedor_pagado', return_value=True
    ), patch(
        'services.smart_image_pipeline._get_json', return_value=None
    ):
        resultado = resolver_imagen_automatica(
            codigo_barras='0000000000000',
            nombre='Producto sin red QA',
            categoria='Alimentos',
        )
    print(f'  url_red_caida={resultado.url!r}')
    _ok(resultado.es_placeholder, 'red caída no inserta basura', errores)
    vista = url_imagen_producto({'imagen_url': None})
    _ok(vista == PLACEHOLDER_PRODUCTO, 'frontend usa placeholder si falla la red', errores)


def _srcs_producto(html):
    srcs = []
    for tag in _RE_IMG_TAG.findall(html):
        if 'localis-img-producto' not in tag:
            continue
        if 'id="modal-imagen"' in tag:
            continue
        found = _RE_SRC.search(tag)
        if found:
            srcs.append(found.group(1))
    return srcs


def _assert_html_catalogo(html, errores, etiqueta):
    srcs = _srcs_producto(html)
    wraps = _RE_WRAP.findall(html)
    print(f'  {etiqueta}: imgs={len(srcs)} wraps={len(wraps)}')
    if 'No se encontraron' in html or 'aún no tiene productos' in html:
        _ok(True, f'{etiqueta} catalogo vacio o con mensaje', errores)
        return
    _ok(len(wraps) >= 1 or 'grid-productos-mobile' in html, f'{etiqueta} grilla presente', errores)
    for src in srcs:
        _ok(_es_url_limpia(src), f'{etiqueta} src limpio {src[:80]}', errores)
        _ok(not _tiene_basura(src), f'{etiqueta} sin host prohibido {src[:80]}', errores)
    for host in HOSTS_PROHIBIDOS + HOSTS_OFF_CRUDOS:
        malos = [src for src in srcs if host in src.lower()]
        _ok(not malos, f'{etiqueta} sin {host} en <img>', errores)


def _probar_sesion_http(errores):
    print('\n=== Sesión de usuario (HTTP) ===')
    from backend.image_lookup import programar_descubrimiento_producto
    from backend.utils import imagen_url_almacenada
    from utils.images import PLACEHOLDER_PRODUCTO, url_imagen_producto

    usuario_id = None
    comercio_id = None
    app = _app_cliente()
    try:
        usuario_id, comercio_id, correo = _crear_sandbox()
        with app.test_client() as cliente:
            _sesion(cliente, usuario_id, comercio_id, correo)

            # 1) Alta con EAN
            alta_ean = cliente.post(
                '/api/productos/crear',
                data={
                    'nombre': f'{PREFIJO} Nutella 350g',
                    'descripcion': 'crema de avellanas',
                    'precio_usd': '3.50',
                    'codigo_barras': EAN_NUTELLA,
                },
            )
            cuerpo_ean = alta_ean.get_json(silent=True) or {}
            print(f'  alta EAN -> {alta_ean.status_code} {cuerpo_ean}')
            _ok(alta_ean.status_code == 201, 'alta con EAN HTTP 201', errores)
            id_ean = cuerpo_ean.get('producto_id')
            _ok(bool(id_ean), 'producto EAN creado', errores)
            if id_ean and not imagen_url_almacenada(cuerpo_ean.get('imagen_url')):
                programar_descubrimiento_producto(id_ean, categoria='Alimentos')
                url_ean = _esperar_url(id_ean, timeout=120)
            else:
                url_ean = cuerpo_ean.get('imagen_url') or _url_bd(id_ean)
            print(f'  url_ean={url_ean!r}')
            if url_ean and not str(url_ean).endswith('placeholder-producto.svg'):
                from backend.utils import url_imagen_api_oficial_valida

                _ok(
                    bool(imagen_url_almacenada(url_ean) or url_imagen_api_oficial_valida(url_ean)),
                    'EAN persistió Storage o URL de API',
                    errores,
                )
                _ok(not _tiene_basura(url_ean), 'EAN sin host de calle/OFF crudo', errores)
            else:
                vista = url_imagen_producto(
                    {'imagen_url': url_ean, 'codigo_barras': EAN_NUTELLA}
                )
                _ok(
                    vista == PLACEHOLDER_PRODUCTO or _es_url_limpia(vista),
                    'EAN sin match: placeholder limpio',
                    errores,
                )

            # 2) Alta sin EAN genérico
            alta_gen = cliente.post(
                '/api/productos/crear',
                data={
                    'nombre': f'{PREFIJO} Martillo de uña',
                    'descripcion': 'herramienta de acero',
                    'precio_usd': '5.00',
                    'codigo_barras': '',
                },
            )
            cuerpo_gen = alta_gen.get_json(silent=True) or {}
            print(f'  alta genérico -> {alta_gen.status_code} {cuerpo_gen}')
            _ok(alta_gen.status_code == 201, 'alta genérico HTTP 201', errores)
            id_gen = cuerpo_gen.get('producto_id')
            if id_gen:
                programar_descubrimiento_producto(id_gen, categoria='Ferretería')
                url_gen = _esperar_sin_url(id_gen, timeout=12)
            else:
                url_gen = cuerpo_gen.get('imagen_url')
            print(f'  url_generico={url_gen!r}')
            _ok(
                (not url_gen) or str(url_gen).endswith('placeholder-producto.svg'),
                'genérico no persiste foto inventada',
                errores,
            )
            vista_gen = url_imagen_producto(
                {'id': id_gen, 'imagen_url': url_gen, 'codigo_barras': None}
            )
            _ok(
                vista_gen == PLACEHOLDER_PRODUCTO,
                'genérico renderiza placeholder oficial',
                errores,
            )
            if id_gen:
                api_gen = cliente.get(f'/api/producto/{int(id_gen)}')
                data_gen = api_gen.get_json(silent=True) or {}
                img_api = data_gen.get('imagen_url')
                print(f'  api genérico imagen={img_api!r}')
                _ok(_es_url_limpia(img_api), 'API genérico ilustra placeholder/limpia', errores)
                _ok(not _tiene_basura(img_api), 'API genérico sin basura', errores)

            tienda_pre = cliente.get(f'/tienda/{comercio_id}')
            _ok(tienda_pre.status_code == 200, 'tienda pre-CSV HTTP 200', errores)
            html_pre = tienda_pre.get_data(as_text=True)
            _assert_html_catalogo(html_pre, errores, 'tienda-pre')
            _ok(
                'localis-img-producto-wrap--vacio' in html_pre
                or PLACEHOLDER in html_pre,
                'genérico pinta placeholder simétrico en la grilla',
                errores,
            )

            # 3) Alta sin EAN con marca
            alta_marca = cliente.post(
                '/api/productos/crear',
                data={
                    'nombre': f'{PREFIJO} Harina PAN 1kg',
                    'descripcion': 'harina de maiz precocida',
                    'precio_usd': '1.20',
                    'codigo_barras': '',
                },
            )
            cuerpo_marca = alta_marca.get_json(silent=True) or {}
            print(f'  alta marca -> {alta_marca.status_code} {cuerpo_marca}')
            _ok(alta_marca.status_code == 201, 'alta con marca HTTP 201', errores)
            id_marca = cuerpo_marca.get('producto_id')
            if id_marca and not imagen_url_almacenada(cuerpo_marca.get('imagen_url')):
                programar_descubrimiento_producto(id_marca, categoria='Alimentos')
                url_marca = _esperar_url(id_marca, timeout=90)
            else:
                url_marca = cuerpo_marca.get('imagen_url') or (
                    _url_bd(id_marca) if id_marca else None
                )
            print(f'  url_marca={url_marca!r}')
            if url_marca and not str(url_marca).endswith('placeholder-producto.svg'):
                from backend.utils import url_imagen_api_oficial_valida

                _ok(
                    bool(imagen_url_almacenada(url_marca) or url_imagen_api_oficial_valida(url_marca)),
                    'marca persistió Storage o URL de API',
                    errores,
                )
                _ok(not _tiene_basura(url_marca), 'marca sin URL cruda', errores)
            else:
                vista_m = url_imagen_producto({'imagen_url': url_marca})
                _ok(
                    vista_m == PLACEHOLDER_PRODUCTO or _es_url_limpia(vista_m),
                    'marca sin match: placeholder',
                    errores,
                )

            # 4) CSV masivo
            csv_bytes = _csv_inventario()
            alta_csv = cliente.post(
                '/comercio/productos/cargar-csv',
                data={
                    'archivo_csv': (io.BytesIO(csv_bytes), 'inventario-qa.csv')
                },
                content_type='multipart/form-data',
            )
            print(f'  CSV -> {alta_csv.status_code} loc={alta_csv.headers.get("Location")}')
            _ok(
                alta_csv.status_code in (200, 302),
                f'importación CSV HTTP {alta_csv.status_code}',
                errores,
            )

            from backend.db import get_db_connection

            with get_db_connection() as conexion:
                cursor = conexion.cursor()
                cursor.execute(
                    """
                    SELECT nombre, codigo_barras, imagen_url
                    FROM productos WHERE comercio_id = ?
                    """,
                    (int(comercio_id),),
                )
                filas = []
                for f in cursor.fetchall():
                    if isinstance(f, dict):
                        filas.append(f)
                    else:
                        filas.append(
                            {
                                'nombre': f[0],
                                'codigo_barras': f[1],
                                'imagen_url': f[2],
                            }
                        )

            print(f'  productos post-CSV={len(filas)}')
            _ok(len(filas) >= 3, f'CSV dejó productos ({len(filas)})', errores)
            for fila in filas:
                url = fila.get('imagen_url')
                nombre = fila.get('nombre')
                _ok(
                    not _tiene_basura(url),
                    f'CSV {nombre!r} sin URL basura ({url!r})',
                    errores,
                )
                if url:
                    _ok(
                        bool(imagen_url_almacenada(url)),
                        f'CSV {nombre!r} URL persistible',
                        errores,
                    )

            from backend.image_lookup import asociar_imagenes_inventario

            try:
                n = asociar_imagenes_inventario(comercio_id)
                print(f'  worker CSV actualizados={n}')
            except Exception as error:
                print(f'  aviso worker CSV: {error}')

            # 5) Render catálogo público
            home = cliente.get('/')
            _ok(home.status_code == 200, 'home HTTP 200', errores)
            html_home = home.get_data(as_text=True)
            _assert_html_catalogo(html_home, errores, 'home')

            tienda = cliente.get(f'/tienda/{comercio_id}')
            _ok(tienda.status_code == 200, 'tienda sandbox HTTP 200', errores)
            html_tienda = tienda.get_data(as_text=True)
            _assert_html_catalogo(html_tienda, errores, 'tienda')
            _ok(
                'height: 180px' in cliente.get('/static/css/responsive.css').get_data(as_text=True),
                'CSS altura fija 180px',
                errores,
            )
            css_body = cliente.get('/static/css/responsive.css').get_data(as_text=True)
            _ok(
                'placeholder-producto' in css_body
                and 'localis-img-producto-wrap--vacio' in css_body,
                'CSS placeholder de simetría presente',
                errores,
            )
            _ok(
                html_tienda.count('localis-img-producto-wrap') >= 1,
                'cada tarjeta tiene wrap simétrico',
                errores,
            )
            _ok(
                'localis-img-producto-wrap--vacio' in html_tienda
                or PLACEHOLDER in html_tienda,
                'tienda CSV muestra placeholder oficial en genéricos',
                errores,
            )
            ph = cliente.get(PLACEHOLDER)
            _ok(ph.status_code == 200, 'placeholder SVG servido', errores)
    finally:
        _limpiar(usuario_id=usuario_id, comercio_id=comercio_id)


def _probar_css_simetria(errores):
    print('\n=== Simetría de tarjetas ===')
    css = (RAIZ / 'static' / 'css' / 'responsive.css').read_text(encoding='utf-8')
    _ok('object-fit: contain' in css, 'object-fit contain', errores)
    _ok('height: 180px' in css, 'altura wrap 180px', errores)
    _ok('#fffefb' in css, 'fondo estudio #fffefb', errores)
    _ok(
        '.localis-img-producto-wrap--vacio' in css,
        'estado vacío con placeholder de fondo',
        errores,
    )
    svg = (RAIZ / 'static' / 'img' / 'placeholder-producto.svg').read_text(
        encoding='utf-8'
    )
    _ok('viewBox="0 0 400 400"' in svg, 'placeholder cuadrado 400x400', errores)


def main() -> int:
    os.environ.setdefault('LOCALIS_DEBUG_IMAGENES', '0')
    _cargar_entorno()
    errores = []
    _probar_sanitizacion_unitaria(errores)
    _probar_red_caida(errores)
    _probar_css_simetria(errores)
    _probar_sesion_http(errores)

    print('\n=== RESULTADO SESION USUARIO ===')
    if errores:
        print(f'FALLO ({len(errores)}):')
        for item in errores:
            print(f'  - {item}')
        return 1
    print(
        'OK sesion: EAN/marca Storage o placeholder; genericos y CSV basura '
        'rechazados; catalogo sin URLs crudas; grilla simetrica.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

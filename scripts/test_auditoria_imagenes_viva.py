#!/usr/bin/env python3
"""
Auditoría viva de imágenes: alta de productos de varios rubros.
Fichas oficiales se procesan en segundo plano; sin match limpio, placeholder.
"""

from __future__ import annotations

import io
import sys
import time
import uuid
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

PLACEHOLDER = '/static/img/placeholder-producto.svg'
MAX_PROGRAMAR_MS = 300
MAX_ESPERA_SEG = 240
MARCA_QA = '__localis_qa_img_viva__'

CASOS = (
    {
        'nombre': 'Nutella 350g',
        'codigo_barras': '3017620422003',
        'categoria': 'Alimentos',
        'descripcion': 'crema de avellanas',
        'rubro': 'alimentos',
        'espera': 'oficial',
    },
    {
        'nombre': 'Harina PAN 1kg',
        'codigo_barras': None,
        'categoria': 'Alimentos',
        'descripcion': 'harina de maiz precocida',
        'rubro': 'alimentos',
        'espera': 'oficial',
    },
    {
        'nombre': 'Martillo de uña',
        'codigo_barras': None,
        'categoria': 'Ferretería',
        'descripcion': 'herramienta de acero',
        'rubro': 'ferreteria',
        'espera': 'placeholder',
    },
    {
        'nombre': 'Camisa polo',
        'codigo_barras': None,
        'categoria': 'Ropa',
        'descripcion': 'polo de algodon',
        'rubro': 'ropa',
        'espera': 'placeholder',
    },
    {
        'nombre': 'iPhone',
        'codigo_barras': None,
        'categoria': 'Tecnología',
        'descripcion': 'smartphone',
        'rubro': 'tecnologia',
        'espera': 'oficial_o_placeholder',
    },
)


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


def _limpiar(usuario_id=None, comercio_id=None):
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        if comercio_id:
            cursor.execute(
                'DELETE FROM productos WHERE comercio_id = ?',
                (int(comercio_id),),
            )
            cursor.execute(
                'DELETE FROM comercios WHERE id = ?',
                (int(comercio_id),),
            )
        if usuario_id:
            cursor.execute(
                'DELETE FROM usuarios WHERE id = ?',
                (int(usuario_id),),
            )
        cursor.execute(
            'DELETE FROM productos WHERE nombre LIKE ?',
            (MARCA_QA + '%',),
        )
        cursor.execute(
            'DELETE FROM comercios WHERE nombre LIKE ?',
            (MARCA_QA + '%',),
        )
        cursor.execute(
            'DELETE FROM usuarios WHERE correo LIKE ?',
            (MARCA_QA + '%',),
        )
        conexion.commit()


def _crear_sandbox():
    from backend.db import get_db_connection

    token = uuid.uuid4().hex[:8]
    correo = f'{MARCA_QA}{token}@localis.test'
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            INSERT INTO usuarios (nombre, correo, rol)
            VALUES (?, ?, 'comerciante')
            RETURNING id
            """,
            ('QA Imagen Viva', correo),
        )
        fila = cursor.fetchone()
        usuario_id = int(fila['id'] if isinstance(fila, dict) else fila[0])
        cursor.execute('SELECT id FROM categorias ORDER BY id LIMIT 1')
        cat = cursor.fetchone()
        categoria_id = int(cat['id'] if isinstance(cat, dict) else (cat[0] if cat else 1))
        cursor.execute(
            """
            INSERT INTO comercios (
                usuario_id, nombre, descripcion, telefono, direccion, ciudad, zona,
                categoria_id, plan_id, plan_tipo, limite_productos, estado_pago, visible
            )
            VALUES (?, ?, 'QA imagen viva', '04141234567', 'Av. Prueba', 'Porlamar',
                    'Centro', ?, NULL, 'gratis', 80, 'activo', 1)
            RETURNING id
            """,
            (usuario_id, f'{MARCA_QA} tienda {token}', categoria_id),
        )
        fila_c = cursor.fetchone()
        conexion.commit()
        comercio_id = int(fila_c['id'] if isinstance(fila_c, dict) else fila_c[0])
    return usuario_id, comercio_id


def _insertar_producto(comercio_id, caso):
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            INSERT INTO productos (
                comercio_id, nombre, precio_usd, descripcion, codigo_barras, imagen_url
            )
            VALUES (?, ?, 2.5, ?, ?, NULL)
            RETURNING id
            """,
            (
                int(comercio_id),
                caso['nombre'],
                caso['descripcion'],
                caso['codigo_barras'],
            ),
        )
        fila = cursor.fetchone()
        conexion.commit()
    return int(fila['id'] if isinstance(fila, dict) else fila[0])


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


def _esperar_url(producto_id, timeout=MAX_ESPERA_SEG):
    from backend.utils import imagen_url_almacenada

    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        url = _url_bd(producto_id)
        if imagen_url_almacenada(url):
            return url
        time.sleep(1.2)
    return _url_bd(producto_id)


def _es_placeholder(url):
    texto = str(url or '').lower()
    return (not texto) or PLACEHOLDER in texto or 'placeholder' in texto


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


def _esperar_sin_url(producto_id, timeout=12):
    t0 = time.monotonic()
    ultima = _url_bd(producto_id)
    while time.monotonic() - t0 < timeout:
        ultima = _url_bd(producto_id)
        if ultima:
            return ultima
        time.sleep(0.6)
    return ultima


def _descargar_bytes(url):
    if str(url).startswith('/static/'):
        ruta = RAIZ / str(url).lstrip('/').replace('/', '\\')
        if not ruta.exists():
            ruta = RAIZ / str(url).lstrip('/')
        return ruta.read_bytes() if ruta.exists() else None
    import requests

    try:
        resp = requests.get(url, timeout=20)
        if resp.status_code == 200 and resp.content:
            return resp.content
    except Exception:
        return None
    return None


def _lienzo_estudio(data):
    from PIL import Image

    from backend.images import FONDO_LIENZO

    img = Image.open(io.BytesIO(data))
    img.load()
    if img.size[0] != img.size[1]:
        return False, f'no cuadrado {img.size}'
    if img.size[0] < 200:
        return False, f'muy pequeno {img.size}'
    pixel = img.convert('RGB').getpixel((2, 2))
    cerca = all(abs(pixel[i] - FONDO_LIENZO[i]) <= 24 for i in range(3))
    if not cerca:
        return False, f'esquina {pixel} no es fondo estudio'
    return True, f'lienzo {img.size} fondo={pixel}'


def _probar_fail_safe(errores):
    print('\n=== Fail-safe IA ===')
    from unittest.mock import patch

    from backend.image_ai import aislar_producto_webp, procesar_descarga_oficial

    _ok(aislar_producto_webp(b'') is None, 'bytes vacios -> None', errores)
    _ok(aislar_producto_webp(b'no-es-imagen') is None, 'basura -> None', errores)
    with patch('backend.image_ai._aislar_bytes_sync', side_effect=RuntimeError('boom')):
        _ok(aislar_producto_webp(b'\x89PNG') is None, 'excepcion rembg -> None', errores)
    with patch('backend.image_ai.ia_estudio_habilitada', return_value=False):
        _ok(
            procesar_descarga_oficial(b'\x89PNG', 'qa') is None,
            'IA desactivada -> None',
            errores,
        )


def _probar_http_no_bloquea(errores, producto_id, categoria):
    from backend.image_lookup import programar_descubrimiento_producto

    t0 = time.perf_counter()
    programar_descubrimiento_producto(producto_id, categoria=categoria)
    ms = (time.perf_counter() - t0) * 1000
    _ok(
        ms < MAX_PROGRAMAR_MS,
        f'programar_descubrimiento no bloquea HTTP ({ms:.1f} ms)',
        errores,
    )


def _probar_vista(errores, producto_id, url, espera_placeholder=False):
    from utils.images import PLACEHOLDER_PRODUCTO, url_imagen_producto

    vista = url_imagen_producto(
        {'id': producto_id, 'imagen_url': url, 'codigo_barras': None}
    )
    _ok(bool(vista), f'filtro vista tiene URL ({vista})', errores)
    if espera_placeholder:
        _ok(
            vista == PLACEHOLDER_PRODUCTO or PLACEHOLDER in str(vista),
            'sin ficha oficial usa placeholder Localis',
            errores,
        )
        return
    _ok(
        vista != PLACEHOLDER_PRODUCTO and PLACEHOLDER not in str(vista),
        'HTML no usa placeholder gris',
        errores,
    )


def main() -> int:
    _cargar_entorno()
    errores = []
    usuario_id = None
    comercio_id = None
    try:
        _limpiar()
        _probar_fail_safe(errores)
        usuario_id, comercio_id = _crear_sandbox()
        print('\n=== Alta + worker + estudio ===')
        from backend.image_ai import ia_estudio_habilitada
        from backend.image_lookup import programar_descubrimiento_producto
        from backend.utils import imagen_url_almacenada

        ia_ok = ia_estudio_habilitada()
        print(f'  rembg instalado={ia_ok}')

        for caso in CASOS:
            print(f'\n--- {caso["rubro"]}: {caso["nombre"]} ---')
            producto_id = _insertar_producto(comercio_id, caso)
            _ok(_url_bd(producto_id) in (None, ''), 'nace sin imagen_url', errores)
            _probar_http_no_bloquea(errores, producto_id, caso['categoria'])
            espera = caso['espera']
            if espera == 'placeholder':
                url = _esperar_sin_url(producto_id)
                print(f'  url={url!r}')
                _ok(not url, f'{caso["rubro"]} no persiste foto inventada', errores)
                _ok(not _host_prohibido(url), f'{caso["rubro"]} sin host abierto', errores)
                _probar_vista(errores, producto_id, url, espera_placeholder=True)
                continue

            url = _esperar_url(producto_id)
            if not imagen_url_almacenada(url) and espera == 'oficial':
                print('  reintento worker...')
                programar_descubrimiento_producto(
                    producto_id, categoria=caso['categoria']
                )
                url = _esperar_url(producto_id, timeout=90)
            print(f'  url={url!r}')
            _ok(not _host_prohibido(url), f'{caso["rubro"]} sin wiki/redes/calle', errores)
            if not url:
                _ok(
                    espera == 'oficial_o_placeholder',
                    f'{caso["rubro"]} sin ficha oficial: placeholder',
                    errores,
                )
                _probar_vista(errores, producto_id, url, espera_placeholder=True)
                continue
            _ok(bool(imagen_url_almacenada(url)), f'{caso["rubro"]} URL persistible', errores)
            _ok(not _es_placeholder(url), f'{caso["rubro"]} no es placeholder', errores)
            _probar_vista(errores, producto_id, url)
            data = _descargar_bytes(url) if url else None
            _ok(bool(data) and len(data) > 80, f'{caso["rubro"]} bytes descargables', errores)
            if data:
                from PIL import Image

                img = Image.open(io.BytesIO(data))
                _ok(
                    img.size[0] >= 200 and img.size[1] >= 200,
                    f'{caso["rubro"]} resolucion {img.size}',
                    errores,
                )
                estudio_ok, detalle = _lienzo_estudio(data)
                _ok(estudio_ok, f'{caso["rubro"]} estudio IA ({detalle})', errores)
    finally:
        _limpiar(usuario_id=usuario_id, comercio_id=comercio_id)
        if usuario_id:
            print('  sandbox QA limpiado')

    print('\n=== RESULTADO AUDITORIA IMAGENES ===')
    if errores:
        print(f'FALLO ({len(errores)}):')
        for item in errores:
            print(f'  - {item}')
        return 1
    print(
        'OK auditoria viva: fichas oficiales persistidas, genericos con '
        'placeholder Localis, worker no bloquea HTTP.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

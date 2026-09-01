#!/usr/bin/env python3
"""
Simula a un comerciante autenticado creando un producto con foto local.

Comprueba:
  1. Compresion WebP del archivo adjunto.
  2. Persistencia de una URL usable (Storage o /static/uploads/).
  3. POST autenticado a /api/productos/crear (mismo flujo que el formulario).
  4. Que la foto se sirve y el filtro de plantilla no cae al placeholder.
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

PREFIJO_NOMBRE = '__localis_test_manual__'
PLACEHOLDER = '/static/img/placeholder-producto.svg'


def _cargar_entorno() -> None:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=True)


def _jpeg_comerciante(ancho=1200, alto=800) -> bytes:
    from PIL import Image, ImageDraw

    img = Image.new('RGB', (ancho, alto), (18, 92, 64))
    dibujo = ImageDraw.Draw(img)
    dibujo.rectangle((80, 80, ancho - 80, alto - 80), fill=(245, 158, 11))
    dibujo.ellipse((ancho // 3, alto // 3, ancho // 3 + 220, alto // 3 + 220), fill=(254, 251, 246))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=88)
    return buf.getvalue()


def _file_storage(data: bytes, nombre='foto-manual.jpg'):
    from werkzeug.datastructures import FileStorage

    return FileStorage(
        stream=io.BytesIO(data),
        filename=nombre,
        content_type='image/jpeg',
    )


def _ok(condicion, mensaje, errores):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    errores.append(mensaje)
    return False


def _limpiar_producto(producto_id, url=None):
    from backend.db import get_db_connection
    from backend.uploads_locales import url_upload_local_valida
    from backend.utils import url_imagen_local_valida

    if producto_id:
        try:
            with get_db_connection() as conexion:
                cursor = conexion.cursor()
                cursor.execute(
                    'DELETE FROM productos WHERE id = ? OR nombre LIKE ?',
                    (int(producto_id), PREFIJO_NOMBRE + '%'),
                )
        except Exception as error:
            print(f'  aviso limpieza producto: {error}')
    local = url_imagen_local_valida(url) or url_upload_local_valida(url)
    if local:
        relativo = local[len('/static/') :].replace('\\', '/')
        ruta = RAIZ / 'static' / Path(*relativo.split('/'))
        try:
            if ruta.is_file():
                ruta.unlink()
        except OSError as error:
            print(f'  aviso limpieza archivo: {error}')


def _elegir_comercio():
    from backend.db import get_db_connection
    from backend.subscriptions import puede_agregar_producto

    with get_db_connection(row_factory=True) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT c.id AS comercio_id, c.usuario_id, c.estado_pago, c.visible,
                   u.nombre, u.correo, u.rol
            FROM comercios c
            JOIN usuarios u ON u.id = c.usuario_id
            WHERE COALESCE(LOWER(CAST(c.estado_pago AS TEXT)), 'activo')
                  NOT IN ('vencido', 'suspendido')
              AND COALESCE(LOWER(CAST(u.rol AS TEXT)), 'comerciante') <> 'admin'
            ORDER BY c.id
            """
        )
        filas = cursor.fetchall() or []

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            'DELETE FROM productos WHERE nombre LIKE ?',
            (PREFIJO_NOMBRE + '%',),
        )

    for fila in filas:
        datos = dict(fila) if not isinstance(fila, dict) else fila
        comercio_id = datos.get('comercio_id')
        ok, _msg = puede_agregar_producto(comercio_id)
        if ok:
            return datos
    return dict(filas[0]) if filas else None


def _probar_pipeline(jpeg_bytes, errores):
    from backend.image_lookup import persistir_imagen_producto_hibrida
    from backend.uploads_locales import archivo_upload_existe
    from backend.utils import (
        imagen_url_almacenada,
        url_imagen_local_valida,
        url_imagen_subida_storage_valida,
    )
    from utils.images import url_imagen_producto_segura, url_publica_producto_desde_bd

    print('\n=== Pipeline: FileStorage -> comprimir -> URL ===')
    t0 = time.perf_counter()
    url, aviso = persistir_imagen_producto_hibrida(
        file_storage=_file_storage(jpeg_bytes),
        codigo_barras=None,
        nombre='Pepsi Cola Test Manual Upload',
        comercio_id='pipeline',
    )
    ms = (time.perf_counter() - t0) * 1000
    print(f'  tiempo_pipeline={ms:.0f} ms aviso={aviso!r} url={url!r}')

    persistible = imagen_url_almacenada(url)
    _ok(bool(persistible), 'pipeline devolvio URL persistible', errores)
    _ok(
        not (url or '').lower().startswith('/static/img/placeholder'),
        'pipeline no uso placeholder',
        errores,
    )
    _ok(
        'openfoodfacts' not in (url or '').lower(),
        'archivo manual no se sustituyo por foto de OpenFoodFacts',
        errores,
    )
    _ok(ms < 8000, f'pipeline en menos de 8s ({ms:.0f} ms)', errores)

    mostrable = url_publica_producto_desde_bd(url)
    _ok(bool(mostrable) and mostrable != PLACEHOLDER, 'url_publica_producto_desde_bd usable', errores)
    segura = url_imagen_producto_segura({'imagen_url': url})
    _ok(segura and segura != PLACEHOLDER, f'filtro plantilla usable ({segura})', errores)

    if url_imagen_local_valida(url):
        _ok(archivo_upload_existe(url), 'archivo WebP existe en static/uploads', errores)
        from PIL import Image

        relativo = url[len('/static/') :]
        ruta = RAIZ / 'static' / Path(*relativo.split('/'))
        with Image.open(ruta) as img:
            _ok(img.size[0] > 0 and img.size[1] > 0, f'WebP abre {img.size} {img.format}', errores)
            _ok(ruta.stat().st_size < len(jpeg_bytes), 'WebP comprimido es mas liviano que el JPEG', errores)
    elif url_imagen_subida_storage_valida(url):
        print('  Storage acepto la subida; se omite chequeo de disco local.')
    return url, ms


def _probar_e2e(jpeg_bytes, errores):
    print('\n=== E2E: comerciante autenticado POST /api/productos/crear ===')
    comercio = _elegir_comercio()
    if not comercio:
        errores.append('No hay comercio activo para simular al comerciante')
        print('  FALLO  no hay comercio/usuario en PostgreSQL')
        return None, None, None

    print(
        f"  comercio_id={comercio.get('comercio_id')} "
        f"usuario_id={comercio.get('usuario_id')} "
        f"estado={comercio.get('estado_pago')!r}"
    )

    from main import _hilo_init, app

    if _hilo_init.is_alive():
        print('  esperando inicializacion de arranque (init_db)...')
        _hilo_init.join(timeout=90)

    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False

    nombre = f'{PREFIJO_NOMBRE} {uuid.uuid4().hex[:8]} Pepsi'
    producto_id = None
    imagen_url = None
    t0 = time.perf_counter()
    respuesta = None
    cuerpo = {}
    with app.test_client() as cliente:
        with cliente.session_transaction() as sess:
            sess['usuario_id'] = comercio['usuario_id']
            sess['username'] = comercio.get('nombre') or 'Comerciante'
            sess['correo'] = comercio.get('correo') or ''
            sess['rol'] = 'comerciante'
            sess['es_admin'] = False
            sess['comercio_id'] = int(comercio['comercio_id'])
            sess['panel_comercio_activo'] = True

        for intento in range(4):
            respuesta = cliente.post(
                '/api/productos/crear',
                data={
                    'nombre': nombre,
                    'descripcion': 'Producto de prueba de subida manual',
                    'precio_usd': '1.25',
                    'codigo_barras': '',
                    'imagen': (io.BytesIO(jpeg_bytes), 'foto-manual.jpg'),
                },
                content_type='multipart/form-data',
            )
            if respuesta.status_code != 503:
                break
            print(f'  aviso HTTP 503 (intento {intento + 1}), reintento...')
            time.sleep(0.8 * (intento + 1))
        ms = (time.perf_counter() - t0) * 1000
        cuerpo = respuesta.get_json(silent=True) or {}
        print(f'  http={respuesta.status_code} tiempo={ms:.0f} ms cuerpo={cuerpo}')

        _ok(respuesta.status_code == 201, 'API creo el producto (HTTP 201)', errores)
        _ok(cuerpo.get('ok') is True, 'JSON ok=true', errores)
        producto_id = cuerpo.get('producto_id')
        imagen_url = cuerpo.get('imagen_url')
        _ok(bool(producto_id), 'API devolvio producto_id', errores)
        _ok(bool(imagen_url), 'API devolvio imagen_url (no vacia)', errores)
        _ok(
            'openfoodfacts' not in str(imagen_url or '').lower(),
            'imagen_url no es OpenFoodFacts (se conservo la foto subida)',
            errores,
        )
        _ok(ms < 12000, f'alta manual en menos de 12s ({ms:.0f} ms)', errores)

        from backend.db import get_db_connection
        from backend.utils import imagen_url_almacenada
        from utils.images import url_imagen_producto_segura

        if producto_id:
            with get_db_connection(row_factory=True) as conexion:
                cursor = conexion.cursor()
                cursor.execute(
                    'SELECT imagen_url, nombre FROM productos WHERE id = ?',
                    (int(producto_id),),
                )
                fila = cursor.fetchone()
            fila = dict(fila) if fila and not isinstance(fila, dict) else (fila or {})
            url_bd = fila.get('imagen_url')
            print(f'  url_bd={url_bd!r}')
            _ok(bool(imagen_url_almacenada(url_bd)), 'PostgreSQL guardo URL persistible', errores)
            imagen_url = imagen_url or url_bd
            segura = url_imagen_producto_segura({'id': producto_id, 'imagen_url': url_bd})
            _ok(segura != PLACEHOLDER, f'filtro no cae a placeholder ({segura})', errores)

            if str(imagen_url or '').startswith('/static/uploads/'):
                estatico = cliente.get(imagen_url)
                _ok(
                    estatico.status_code == 200 and len(estatico.data) > 200,
                    f'GET {imagen_url} sirve bytes ({len(estatico.data)} B)',
                    errores,
                )
                _ok(
                    (estatico.headers.get('Content-Type') or '').startswith('image/')
                    or estatico.data[:4] == b'RIFF',
                    f'imagen servida (Content-Type={estatico.headers.get("Content-Type")!r})',
                    errores,
                )

            publico = cliente.get(f'/api/producto/{int(producto_id)}')
            if publico.status_code == 200:
                datos = publico.get_json(silent=True) or {}
                _ok(
                    datos.get('imagen_url') not in (None, '', PLACEHOLDER, 'None'),
                    f'API publica ilustra el producto ({datos.get("imagen_url")})',
                    errores,
                )
            else:
                print(
                    f'  aviso GET /api/producto/{producto_id} -> {publico.status_code} '
                    '(comercio no visible/gratis; se omite chequeo publico)'
                )

    return producto_id, imagen_url, ms


def main() -> int:
    _cargar_entorno()
    errores = []
    jpeg_bytes = _jpeg_comerciante()
    print(f'JPEG de prueba: {len(jpeg_bytes)} bytes')

    url_pipeline, _ms_pipe = _probar_pipeline(jpeg_bytes, errores)
    producto_id = None
    url_e2e = None
    try:
        producto_id, url_e2e, _ms_e2e = _probar_e2e(jpeg_bytes, errores)
    finally:
        _limpiar_producto(producto_id, url_e2e)
        _limpiar_producto(None, url_pipeline)

    print('\n=== RESULTADO ===')
    if errores:
        print('FALLO subida manual:')
        for item in errores:
            print(f'  - {item}')
        return 1
    print(
        'OK subida manual: el comerciante publica con foto visible, '
        'comprimida y enlazada (Storage o respaldo local).'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

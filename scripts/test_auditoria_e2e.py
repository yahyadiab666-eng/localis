#!/usr/bin/env python3
"""
Auditoria E2E de Localis: visitante, comerciante, CSV, planes y OCR.

No reemplaza el inventario de comercios reales. Crea un sandbox
(__localis_qa_e2e__) y lo elimina al terminar.
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

PREFIJO = '__localis_qa_e2e__'
CORREO_QA = f'{PREFIJO}@localis.test'
NOMBRE_COMERCIO = f'{PREFIJO} bodega'
MAX_PUBLICO_MS = 8000
MAX_API_MS = 5000


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


def _jpeg(ancho=640, alto=480, color=(30, 110, 70)):
    from PIL import Image, ImageDraw

    img = Image.new('RGB', (ancho, alto), color)
    dibujo = ImageDraw.Draw(img)
    dibujo.rectangle((40, 40, ancho - 40, alto - 40), fill=(245, 158, 11))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=85)
    return buf.getvalue()


def _esperar_init(app):
    from main import _hilo_init

    if _hilo_init.is_alive():
        print('  esperando init_db...')
        _hilo_init.join(timeout=90)
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    return app


def _hit(cliente, metodo, ruta, errores, *, ok_codes, max_ms, etiqueta=None, **kwargs):
    etiqueta = etiqueta or f'{metodo} {ruta}'
    t0 = time.perf_counter()
    respuesta = getattr(cliente, metodo.lower())(ruta, **kwargs)
    ms = (time.perf_counter() - t0) * 1000
    codigo = respuesta.status_code
    print(f'  {etiqueta} -> {codigo} ({ms:.0f} ms)')
    _ok(codigo in ok_codes, f'{etiqueta} HTTP {codigo} (esperado {ok_codes})', errores)
    _ok(ms < max_ms, f'{etiqueta} en menos de {max_ms} ms ({ms:.0f} ms)', errores)
    return respuesta, ms


def _sesion_usuario(cliente, usuario_id, *, es_admin=False, comercio_id=None, correo=None):
    with cliente.session_transaction() as sess:
        sess['usuario_id'] = int(usuario_id)
        sess['username'] = 'QA E2E'
        sess['correo'] = correo or CORREO_QA
        sess['rol'] = 'admin' if es_admin else 'comerciante'
        sess['es_admin'] = bool(es_admin)
        if comercio_id:
            sess['comercio_id'] = int(comercio_id)
            sess['panel_comercio_activo'] = True


def _limpiar_sandbox(usuario_id=None, comercio_id=None):
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        if comercio_id:
            cursor.execute('DELETE FROM productos WHERE comercio_id = ?', (int(comercio_id),))
            for sql in (
                'DELETE FROM pagos WHERE tienda_id = ?',
                'DELETE FROM pagos WHERE comercio_id = ?',
            ):
                try:
                    cursor.execute(sql, (int(comercio_id),))
                except Exception:
                    conexion.rollback()
            try:
                cursor.execute('DELETE FROM comercios WHERE id = ?', (int(comercio_id),))
            except Exception as error:
                print(f'  aviso limpieza comercio: {error}')
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
                cursor.execute('DELETE FROM usuarios WHERE id = ?', (int(usuario_id),))
            except Exception:
                conexion.rollback()


def _crear_usuario_sandbox():
    from backend.db import get_db_connection

    _limpiar_sandbox()
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            INSERT INTO usuarios (nombre, correo, rol)
            VALUES (?, ?, 'comerciante')
            RETURNING id
            """,
            ('QA E2E Localis', CORREO_QA),
        )
        fila = cursor.fetchone()
        usuario_id = fila['id'] if isinstance(fila, dict) else fila[0]
        cursor.execute('SELECT id FROM categorias ORDER BY id LIMIT 1')
        cat = cursor.fetchone()
        categoria_id = cat['id'] if isinstance(cat, dict) else (cat[0] if cat else 1)
    return int(usuario_id), int(categoria_id)


def _comercio_de_usuario(usuario_id):
    from backend.db import get_db_connection

    with get_db_connection(row_factory=True) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            'SELECT id FROM comercios WHERE usuario_id = ? ORDER BY id DESC LIMIT 1',
            (int(usuario_id),),
        )
        fila = cursor.fetchone()
    if not fila:
        return None
    return int(fila['id'] if isinstance(fila, dict) else fila[0])


def _elegir_comercio_publico():
    from backend.db import get_db_connection

    with get_db_connection(row_factory=True) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT c.id
            FROM comercios c
            WHERE COALESCE(c.visible, 1) = 1
              AND LOWER(TRIM(CAST(c.estado_pago AS TEXT))) IN ('activo', 'gratis')
            ORDER BY c.id
            LIMIT 1
            """
        )
        fila = cursor.fetchone()
    if not fila:
        return None
    return int(fila['id'] if isinstance(fila, dict) else fila[0])


def _probar_visitante(cliente, errores):
    print('\n=== Visitante / rutas publicas ===')
    home, _ = _hit(cliente, 'GET', '/', errores, ok_codes={200}, max_ms=MAX_PUBLICO_MS)
    html = home.get_data(as_text=True)
    _ok('localis' in html.lower(), 'home menciona Localis', errores)
    _ok('Productos del Catálogo' in html or 'No se encontraron' in html, 'home pinta catalogo', errores)
    _ok('localis-img-producto-wrap' in html or 'No se encontraron' in html, 'tarjetas con wrap 1:1', errores)

    _hit(cliente, 'GET', '/como-funciona', errores, ok_codes={200}, max_ms=MAX_PUBLICO_MS)
    _hit(cliente, 'GET', '/ayuda', errores, ok_codes={200}, max_ms=MAX_PUBLICO_MS)
    _hit(cliente, 'GET', '/login', errores, ok_codes={200}, max_ms=MAX_PUBLICO_MS)
    reg, _ = _hit(
        cliente, 'GET', '/registro', errores, ok_codes={302}, max_ms=3000
    )
    _ok('/login' in (reg.headers.get('Location') or ''), 'registro legacy va a login', errores)

    cafe, _ = _hit(
        cliente,
        'GET',
        '/?q=Caf%C3%A9',
        errores,
        ok_codes={200},
        max_ms=MAX_PUBLICO_MS,
        etiqueta='GET /?q=Café',
    )
    _ok('Caf' in cafe.get_data(as_text=True) or 'resultados' in cafe.get_data(as_text=True).lower()
        or 'No se encontraron' in cafe.get_data(as_text=True),
        'busqueda con tilde no rompe el HTML', errores)

    health, _ = _hit(cliente, 'GET', '/health', errores, ok_codes={200, 503}, max_ms=12000)
    cuerpo = health.get_json(silent=True) or {}
    _ok('ok' in cuerpo and 'database' in cuerpo, 'health expone estado de BD', errores)
    if health.status_code == 503:
        print(f'  aviso health 503: {cuerpo.get("database")}')

    _hit(
        cliente,
        'GET',
        '/ruta-que-no-existe-localis',
        errores,
        ok_codes={404},
        max_ms=3000,
        etiqueta='GET 404',
    )
    _hit(
        cliente,
        'GET',
        '/tienda/999999',
        errores,
        ok_codes={302},
        max_ms=4000,
        etiqueta='GET tienda inexistente',
    )
    prod_404, _ = _hit(
        cliente,
        'GET',
        '/api/producto/999999',
        errores,
        ok_codes={404},
        max_ms=MAX_API_MS,
    )
    _ok((prod_404.get_json(silent=True) or {}).get('error'), 'API producto 404 con JSON', errores)

    comercio_id = _elegir_comercio_publico()
    if not comercio_id:
        errores.append('No hay comercio publico para auditar /tienda')
        return None
    tienda, _ = _hit(
        cliente,
        'GET',
        f'/tienda/{comercio_id}',
        errores,
        ok_codes={200},
        max_ms=MAX_PUBLICO_MS,
    )
    html_tienda = tienda.get_data(as_text=True)
    _ok('Catálogo' in html_tienda or 'catalogo' in html_tienda.lower(), 'tienda publica renderiza catalogo', errores)
    _ok('localis-img-producto-wrap' in html_tienda or 'No hay' in html_tienda, 'tienda usa wraps de foto', errores)
    return comercio_id


def _probar_auth_y_admin(cliente, errores):
    print('\n=== Auth y aislamiento admin ===')
    panel, _ = _hit(
        cliente, 'GET', '/comercio', errores, ok_codes={302}, max_ms=3000,
        etiqueta='GET /comercio anonimo',
    )
    _ok('/login' in (panel.headers.get('Location') or ''), 'panel exige login', errores)
    planes, _ = _hit(
        cliente, 'GET', '/comercio/planes', errores, ok_codes={302}, max_ms=3000,
        etiqueta='GET /comercio/planes anonimo',
    )
    _ok('/login' in (planes.headers.get('Location') or ''), 'planes exige login', errores)
    admin, _ = _hit(
        cliente, 'GET', '/admin', errores, ok_codes={302}, max_ms=3000,
        etiqueta='GET /admin anonimo',
    )
    _ok('/login' in (admin.headers.get('Location') or ''), 'admin exige login', errores)
    api, _ = _hit(
        cliente,
        'POST',
        '/api/productos/crear',
        errores,
        ok_codes={401},
        max_ms=3000,
        etiqueta='POST /api/productos/crear anonimo',
        data={'nombre': 'x', 'precio_usd': '1'},
    )
    _ok((api.get_json(silent=True) or {}).get('error'), 'API crear exige sesion', errores)


def _probar_sandbox_comercio(cliente, errores):
    print('\n=== Flujo comerciante sandbox ===')
    usuario_id, categoria_id = _crear_usuario_sandbox()
    comercio_id = None
    producto_id = None
    try:
        _sesion_usuario(cliente, usuario_id)
        crear_get, _ = _hit(
            cliente, 'GET', '/comercio/crear', errores, ok_codes={200}, max_ms=MAX_PUBLICO_MS
        )
        _ok('categoria' in crear_get.get_data(as_text=True).lower(), 'formulario de alta de comercio', errores)

        panel_sin, _ = _hit(
            cliente, 'GET', '/comercio', errores, ok_codes={200}, max_ms=MAX_PUBLICO_MS,
            etiqueta='GET /comercio sin tienda',
        )
        _ok(
            'categoria' in panel_sin.get_data(as_text=True).lower()
            or 'Registrar' in panel_sin.get_data(as_text=True),
            'usuario sin comercio ve el alta',
            errores,
        )

        logo = _jpeg(200, 200, (20, 80, 160))
        alta, _ = _hit(
            cliente,
            'POST',
            '/comercio/crear',
            errores,
            ok_codes={302},
            max_ms=12000,
            data={
                'nombre': NOMBRE_COMERCIO,
                'descripcion': 'Bodega de prueba QA Café ñandú',
                'telefono': '04141234567',
                'direccion': 'Av. Santiago Mariño, Porlamar',
                'ciudad': 'Porlamar',
                'zona': 'Centro',
                'categoria_id': str(categoria_id),
                'documento_identidad': 'J-12345678-9',
                'logo': (io.BytesIO(logo), 'logo-qa.jpg'),
            },
            content_type='multipart/form-data',
        )
        _ok('/comercio' in (alta.headers.get('Location') or ''), 'alta redirige al panel', errores)

        comercio_id = _comercio_de_usuario(usuario_id)
        _ok(bool(comercio_id), f'comercio sandbox persistido id={comercio_id}', errores)
        if not comercio_id:
            return None, None, None

        _sesion_usuario(cliente, usuario_id, comercio_id=comercio_id)
        panel, _ = _hit(
            cliente, 'GET', '/comercio', errores, ok_codes={200}, max_ms=MAX_PUBLICO_MS
        )
        html_panel = panel.get_data(as_text=True)
        _ok(PREFIJO in html_panel or 'Tus Artículos' in html_panel, 'panel del comercio carga', errores)
        _ok('aviso-csv-imagenes' in html_panel, 'disclaimer CSV visible en el panel', errores)
        _ok(
            'asociación automática de imágenes' in html_panel.lower()
            or 'asociacion automatica de imagenes' in html_panel.lower(),
            'texto de advertencia CSV presente',
            errores,
        )

        nuevo, _ = _hit(
            cliente, 'GET', '/comercio/producto/nuevo', errores, ok_codes={200}, max_ms=MAX_PUBLICO_MS
        )
        _ok('imagen' in nuevo.get_data(as_text=True).lower(), 'formulario de producto con campo imagen', errores)

        planes, _ = _hit(
            cliente, 'GET', '/comercio/planes', errores, ok_codes={200}, max_ms=MAX_PUBLICO_MS
        )
        html_planes = planes.get_data(as_text=True)
        _ok('plan' in html_planes.lower(), 'pagina de planes renderiza', errores)

        foto = _jpeg(1200, 800)
        alta_prod, _ = _hit(
            cliente,
            'POST',
            '/api/productos/crear',
            errores,
            ok_codes={201},
            max_ms=12000,
            data={
                'nombre': f'{PREFIJO} Café ñandú',
                'descripcion': 'Producto QA con foto manual',
                'precio_usd': '2.50',
                'codigo_barras': '',
                'imagen': (io.BytesIO(foto), 'foto-qa.jpg'),
            },
            content_type='multipart/form-data',
            etiqueta='POST /api/productos/crear con foto',
        )
        cuerpo = alta_prod.get_json(silent=True) or {}
        producto_id = cuerpo.get('producto_id')
        imagen_url = cuerpo.get('imagen_url')
        _ok(cuerpo.get('ok') is True, 'JSON ok=true al crear producto', errores)
        _ok(bool(producto_id), 'producto_id presente', errores)
        _ok(bool(imagen_url), 'producto nace con imagen_url', errores)
        _ok(
            str(imagen_url).endswith('.webp') or 'storage' in str(imagen_url).lower(),
            f'imagen comprimida WebP/Storage ({imagen_url})',
            errores,
        )
        _ok(
            'openfoodfacts' not in str(imagen_url or '').lower(),
            'foto manual no sustituida por cascada',
            errores,
        )

        if imagen_url and str(imagen_url).startswith('/static/'):
            static, _ = _hit(
                cliente,
                'GET',
                imagen_url,
                errores,
                ok_codes={200},
                max_ms=3000,
                etiqueta='GET foto local',
            )
            _ok(
                'image/' in (static.headers.get('Content-Type') or ''),
                'foto servida como image/*',
                errores,
            )
            _ok(len(static.data) > 200, 'foto no esta vacia', errores)

        if producto_id:
            publico, _ = _hit(
                cliente,
                'GET',
                f'/api/producto/{int(producto_id)}',
                errores,
                ok_codes={200},
                max_ms=MAX_API_MS,
            )
            data = publico.get_json(silent=True) or {}
            _ok(data.get('imagen_url'), 'API publica ilustra el producto', errores)
            _ok('ñandú' in str(data.get('nombre') or '') or 'nandu' in str(data.get('nombre') or '').lower()
                or PREFIJO in str(data.get('nombre') or ''),
                'nombre con caracteres especiales se conserva', errores)

        tienda_sandbox, _ = _hit(
            cliente,
            'GET',
            f'/tienda/{comercio_id}',
            errores,
            ok_codes={200},
            max_ms=MAX_PUBLICO_MS,
            etiqueta='GET tienda sandbox',
        )
        html_t = tienda_sandbox.get_data(as_text=True)
        _ok(PREFIJO in html_t, 'tienda publica muestra el producto sandbox', errores)
        _ok('localis-img-producto' in html_t, 'foto del producto aparece en tienda', errores)

        return usuario_id, comercio_id, producto_id
    except Exception:
        _limpiar_sandbox(usuario_id, comercio_id)
        raise


def _probar_csv(cliente, usuario_id, comercio_id, errores):
    print('\n=== Importador CSV ===')
    _sesion_usuario(cliente, usuario_id, comercio_id=comercio_id)

    get_csv, _ = _hit(
        cliente,
        'GET',
        '/comercio/productos/cargar-csv',
        errores,
        ok_codes={302},
        max_ms=3000,
    )
    _ok('/comercio' in (get_csv.headers.get('Location') or ''), 'GET CSV no da 405', errores)

    vacio, _ = _hit(
        cliente,
        'POST',
        '/comercio/productos/cargar-csv',
        errores,
        ok_codes={302},
        max_ms=8000,
        data={'archivo_csv': (io.BytesIO(b''), 'vacio.csv')},
        content_type='multipart/form-data',
        etiqueta='POST CSV vacio',
    )
    _ok('/comercio' in (vacio.headers.get('Location') or ''), 'CSV vacio redirige al panel', errores)

    sin_precio = 'nombre,stock\nPan,4\n'.encode('utf-8')
    malo, _ = _hit(
        cliente,
        'POST',
        '/comercio/productos/cargar-csv',
        errores,
        ok_codes={302},
        max_ms=8000,
        data={'archivo_csv': (io.BytesIO(sin_precio), 'malo.csv')},
        content_type='multipart/form-data',
        etiqueta='POST CSV sin precio',
    )
    _ok('/comercio' in (malo.headers.get('Location') or ''), 'CSV invalido no tumba la ruta', errores)

    from backend.inventory_import import (
        cargar_archivo_inventario,
        detectar_mapeo_columnas,
        leer_encabezados_inventario,
        validar_inventario_previo,
    )

    utf8 = (
        'nombre,precio,descripcion,codigo_barras\n'
        f'{PREFIJO} Arroz,1.15,grano largo,\n'
        f'{PREFIJO} Café,2.80,tostado molido,7702001001234\n'
    ).encode('utf-8')
    from types import SimpleNamespace

    archivo = SimpleNamespace(filename='qa.csv', stream=io.BytesIO(utf8))
    data, ext, err = cargar_archivo_inventario(archivo)
    _ok(err is None and ext == 'csv', 'CSV sandbox se lee', errores)
    encabezados, err = leer_encabezados_inventario(data, ext)
    mapeo, meta, err = detectar_mapeo_columnas(encabezados)
    _ok(err is None, 'mapeo CSV sandbox', errores)
    valido, msg, _meta = validar_inventario_previo(data, ext, encabezados, mapeo, meta)
    _ok(valido and msg is None, 'validacion CSV sandbox', errores)

    bueno, _ = _hit(
        cliente,
        'POST',
        '/comercio/productos/cargar-csv',
        errores,
        ok_codes={302},
        max_ms=20000,
        data={'archivo_csv': (io.BytesIO(utf8), 'qa.csv')},
        content_type='multipart/form-data',
        etiqueta='POST CSV valido sandbox',
    )
    _ok('/comercio' in (bueno.headers.get('Location') or ''), 'CSV valido redirige', errores)

    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            'SELECT COUNT(*) FROM productos WHERE comercio_id = ? AND nombre LIKE ?',
            (int(comercio_id), PREFIJO + '%'),
        )
        fila = cursor.fetchone()
        total = fila['count'] if isinstance(fila, dict) else fila[0]
    _ok(int(total) >= 2, f'CSV dejo al menos 2 productos sandbox (hay {total})', errores)

    panel, _ = _hit(
        cliente, 'GET', '/comercio', errores, ok_codes={200}, max_ms=MAX_PUBLICO_MS,
        etiqueta='GET panel tras CSV',
    )
    _ok('aviso-csv-imagenes' in panel.get_data(as_text=True), 'aviso CSV sigue visible', errores)


def _probar_pagos_ocr(cliente, usuario_id, comercio_id, errores):
    print('\n=== Planes, cotizacion y OCR ===')
    _sesion_usuario(cliente, usuario_id, comercio_id=comercio_id)

    cot, _ = _hit(
        cliente,
        'GET',
        '/api/pagos/cotizacion?plan=basica',
        errores,
        ok_codes={200},
        max_ms=MAX_API_MS,
    )
    data = cot.get_json(silent=True) or {}
    _ok(data.get('ok') is True, 'cotizacion basica ok', errores)
    _ok('monto_bs' in data or 'monto_usd' in data, 'cotizacion trae montos', errores)

    invalido, _ = _hit(
        cliente,
        'GET',
        '/api/pagos/cotizacion?plan=plan_fantasma',
        errores,
        ok_codes={400},
        max_ms=MAX_API_MS,
        etiqueta='GET cotizacion plan invalido',
    )
    _ok((invalido.get_json(silent=True) or {}).get('error'), 'plan invalido explica error', errores)

    sin_file, _ = _hit(
        cliente,
        'POST',
        '/api/pagos/verificar',
        errores,
        ok_codes={400},
        max_ms=MAX_API_MS,
        data={'plan_tipo': 'basica'},
        etiqueta='POST OCR sin comprobante',
    )
    _ok(
        'comprobante' in str((sin_file.get_json(silent=True) or {}).get('error') or '').lower(),
        'OCR exige captura',
        errores,
    )

    jpeg = _jpeg(400, 300, (240, 240, 240))
    ocr, _ = _hit(
        cliente,
        'POST',
        '/api/pagos/verificar',
        errores,
        ok_codes={400, 200},
        max_ms=45000,
        data={
            'plan_tipo': 'basica',
            'comprobante': (io.BytesIO(jpeg), 'comprobante.jpg'),
        },
        content_type='multipart/form-data',
        etiqueta='POST OCR con JPEG falso',
    )
    cuerpo = ocr.get_json(silent=True) or {}
    if ocr.status_code == 200:
        errores.append('OCR acepto un JPEG sin datos bancarios (falso positivo)')
    else:
        _ok(bool(cuerpo.get('error')), 'OCR rechaza comprobante invalido con mensaje', errores)
        _ok(ocr.status_code != 500, 'OCR no explota con 500', errores)

    down, _ = _hit(
        cliente,
        'POST',
        '/api/pagos/programar-cambio',
        errores,
        ok_codes={200, 400},
        max_ms=MAX_API_MS,
        data={'plan_tipo': 'gratis'},
        etiqueta='POST programar downgrade',
    )
    _ok(down.status_code != 500, 'programar-cambio no da 500', errores)


def _probar_cascada_y_manual(errores):
    print('\n=== Cascada y subida manual (unidad) ===')
    from backend.image_manager import _descubrir_y_persistir_oficial
    from backend.image_lookup import persistir_imagen_producto_hibrida
    from backend.utils import imagen_url_almacenada
    from unittest.mock import patch

    llamadas = []

    def fake_codigo(codigo, familia):
        llamadas.append('codigo')
        return 'https://images.openfoodfacts.org/images/products/x.jpg'

    def fake_nombre(*_a, **_k):
        llamadas.append('nombre')
        return 'https://images.openfoodfacts.org/images/products/y.jpg'

    with patch(
        'backend.image_manager._buscar_codigo_en_fuentes', side_effect=fake_codigo
    ), patch(
        'backend.image_manager._buscar_nombre_en_fuentes', side_effect=fake_nombre
    ), patch(
        'backend.image_manager._espejar_en_storage', return_value=None
    ), patch(
        'backend.image_manager.guardar_imagen_maestro', return_value=None
    ):
        url = _descubrir_y_persistir_oficial(
            '3017620422003',
            nombre='Diablitos Underwood',
            descripcion='pate',
        )
    _ok(llamadas == ['codigo'], f'cascada EAN-first llamadas={llamadas}', errores)
    _ok('products/x.jpg' in str(url), 'usa URL del codigo', errores)

    archivo_url, aviso = persistir_imagen_producto_hibrida(
        file_storage=__import__('werkzeug.datastructures', fromlist=['FileStorage']).FileStorage(
            stream=io.BytesIO(_jpeg()),
            filename='qa-manual.jpg',
            content_type='image/jpeg',
        ),
        nombre='QA manual',
        comercio_id='qa',
    )
    _ok(bool(imagen_url_almacenada(archivo_url)), f'manual hibrida {archivo_url}', errores)
    _ok(aviso is None or isinstance(aviso, str), 'aviso hibrido controlado', errores)


def main() -> int:
    _cargar_entorno()
    errores = []
    usuario_id = None
    comercio_id = None

    from main import app

    _esperar_init(app)
    cliente = app.test_client()

    try:
        _probar_visitante(cliente, errores)
        _probar_auth_y_admin(cliente, errores)
        usuario_id, comercio_id, _pid = _probar_sandbox_comercio(cliente, errores)
        if usuario_id and comercio_id:
            _probar_csv(cliente, usuario_id, comercio_id, errores)
            _probar_pagos_ocr(cliente, usuario_id, comercio_id, errores)
        _probar_cascada_y_manual(errores)
    finally:
        _limpiar_sandbox(usuario_id, comercio_id)

    print('\n=== RESULTADO AUDITORIA ===')
    if errores:
        print(f'FALLO auditoria ({len(errores)} hallazgo/s):')
        for item in errores:
            print(f'  - {item}')
        return 1
    print(
        'OK auditoria E2E: visitante, tienda, alta de comercio, producto WebP, '
        'CSV con errores y carga sandbox, planes/OCR y cascada EAN-first.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

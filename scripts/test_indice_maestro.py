#!/usr/bin/env python3
"""Pruebas del catálogo maestro indexado y soporte de formatos (CSV/XLSX/XLS).

No requiere base de datos ni red:
  - Normalización de nombres/marcas.
  - Búsqueda O(1) en el índice en memoria + rendimiento (microsegundos).
  - Asignación instantánea de imágenes en la importación (maestro + placeholder).
  - Lectura real de .xls (si xlwt está disponible), .xlsx y .csv.
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

_ERRORES = []


def _ok(condicion, mensaje):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    _ERRORES.append(mensaje)
    return False


def _probar_normalizacion():
    from backend.catalogo_maestro_index import normalizar_clave_producto

    print('\n=== Normalización de nombres/marcas ===')
    _ok(
        normalizar_clave_producto('Harina de Maíz P.A.N. 1kg', 'PAN') == 'harina maiz pan',
        'Harina de Maíz P.A.N. 1kg + PAN',
    )
    _ok(normalizar_clave_producto('Pepsi Cola 2L') == 'cola pepsi', 'Pepsi Cola 2L')
    _ok(normalizar_clave_producto('MAVESA 1kg', 'Mavesa') == 'mavesa', 'MAVESA + marca')
    _ok(normalizar_clave_producto('Coca-Cola 350ml') == 'coca cola', 'Coca-Cola 350ml')


def _probar_indice():
    from backend.catalogo_maestro_index import IndiceMaestro

    print('\n=== Búsqueda en el índice ===')
    indice = IndiceMaestro(filas=3)
    indice.por_codigo['7590000040110'] = 'https://x.supabase.co/ean.webp'
    indice.por_nombre['harina maiz pan'] = 'https://x.supabase.co/nombre.webp'

    url, origen = indice.buscar(codigo='7590000040110')
    _ok(url == 'https://x.supabase.co/ean.webp' and origen == 'codigo', 'coincide por código de barras')
    url2, origen2 = indice.buscar(nombre='Harina P.A.N. 1kg', marca='PAN')
    _ok(
        url2 == 'https://x.supabase.co/nombre.webp' and origen2 in ('nombre', 'nombre_similar'),
        f'coincide por nombre/marca ({origen2})',
    )
    url3, origen3 = indice.buscar(nombre='Producto inexistente XYZ')
    _ok(url3 is None and origen3 is None, 'no inventa coincidencias')

    print('\n=== Rendimiento (20.000 búsquedas) ===')
    inicio = time.perf_counter()
    for _ in range(20000):
        indice.buscar(codigo='7590000040110')
    duracion = time.perf_counter() - inicio
    por_busqueda = duracion / 20000 * 1_000_000
    _ok(duracion < 1.0, f'20.000 búsquedas en {duracion * 1000:.1f} ms ({por_busqueda:.2f} µs/búsqueda)')


def _probar_placeholders():
    from backend.catalogo_maestro_index import imagen_generica_categoria

    print('\n=== Placeholder por categoría ===')
    _ok(imagen_generica_categoria('Alimentos') == '/static/img/placeholder-alimentos.svg', 'Alimentos')
    _ok(imagen_generica_categoria('Tecnología') == '/static/img/placeholder-tecnologia.svg', 'Tecnología')
    _ok(imagen_generica_categoria('Bebidas y licores') == '/static/img/placeholder-bebidas.svg', 'Bebidas')
    _ok(imagen_generica_categoria(None) == '/static/img/placeholder-otros.svg', 'genérico')


def _probar_asignacion_instantanea():
    print('\n=== Asignación instantánea (import) ===')
    from backend.catalogo_maestro_index import IndiceMaestro
    from backend.inventory_import import asignar_imagenes_instantaneas

    indice = IndiceMaestro(filas=2)
    indice.por_codigo['7590000040110'] = 'https://x.supabase.co/ean.webp'
    indice.por_nombre['harina maiz pan'] = 'https://x.supabase.co/nombre.webp'

    productos = [
        {
            'nombre': 'Coca Cola',
            'codigo_barras': None,
            'marca': '',
            'imagen_url': 'https://x.supabase.co/storage/v1/object/public/imagenes/productos/manual.webp',
        },
        {'nombre': 'Harina PAN', 'codigo_barras': '7590000040110', 'marca': '', 'imagen_url': None},
        {
            'nombre': 'Harina de Maíz P.A.N. 1kg',
            'codigo_barras': None,
            'marca': 'PAN',
            'imagen_url': None,
        },
        {'nombre': 'Articulo Nuevo Desconocido', 'codigo_barras': '0000000000000', 'marca': '', 'imagen_url': None},
    ]

    with patch('backend.catalogo_maestro_index.obtener_indice', return_value=indice):
        nuevos = asignar_imagenes_instantaneas(productos, snapshot_imagenes={}, categoria='Alimentos')

    _ok(productos[0]['imagen_fuente'] == 'archivo', 'respeta la imagen del archivo')
    _ok(productos[1]['imagen_fuente'] == 'maestro_codigo', 'asigna por código de barras')
    _ok(productos[2]['imagen_fuente'] == 'maestro_nombre', 'asigna por nombre/marca')
    _ok(
        productos[3]['imagen_url'] == '/static/img/placeholder-alimentos.svg'
        and productos[3]['imagen_fuente'] == 'placeholder_categoria',
        'producto nuevo recibe placeholder limpio de su categoría',
    )
    _ok(nuevos == 1, 'cuenta solo 1 producto nuevo')


def _probar_xls():
    print('\n=== Lectura de .xls (formato viejo) ===')
    try:
        import xlwt
    except ImportError:
        print('  OMITIDO  xlwt no instalado (solo hace falta para generar .xls en la prueba)')
        return

    from backend.inventory_import import (
        cargar_archivo_inventario,
        detectar_mapeo_columnas,
        iter_filas_inventario,
        leer_encabezados_inventario,
        parsear_fila_inventario,
        validar_inventario_previo,
    )

    libro = xlwt.Workbook()
    hoja = libro.add_sheet('Inventario')
    for col, titulo in enumerate(['nombre', 'precio', 'codigo barras', 'marca']):
        hoja.write(0, col, titulo)
    hoja.write(1, 0, 'Harina P.A.N. 1kg')
    hoja.write(1, 1, 1.75)
    hoja.write(1, 2, '7590000040110')
    hoja.write(1, 3, 'PAN')
    hoja.write(2, 0, 'Coca Cola 2L')
    hoja.write(2, 1, 2.10)
    hoja.write(2, 3, 'Coca-Cola')
    buffer = io.BytesIO()
    libro.save(buffer)
    data = buffer.getvalue()

    archivo = SimpleNamespace(filename='inventario.xls', stream=io.BytesIO(data))
    datos, ext, error = cargar_archivo_inventario(archivo)
    _ok(error is None and ext == 'xls', 'detecta extensión .xls')

    encabezados, error = leer_encabezados_inventario(datos, ext)
    _ok(error is None and encabezados[:2] == ['nombre', 'precio'], f'encabezados .xls {encabezados}')

    mapeo, meta, error = detectar_mapeo_columnas(encabezados)
    _ok(error is None and mapeo.get('codigo_barras') == 'codigo barras', 'mapea código de barras')
    _ok(error is None and mapeo.get('marca') == 'marca', 'mapea marca')

    filas = list(iter_filas_inventario(datos, ext, encabezados))
    _ok(len(filas) == 2, f'lee 2 filas de datos ({len(filas)})')

    parsed = parsear_fila_inventario(filas[0], mapeo, meta)
    _ok(parsed and parsed['nombre'] == 'Harina P.A.N. 1kg', 'parsea nombre')
    _ok(parsed and parsed['marca'] == 'PAN', 'parsea marca')
    _ok(parsed and parsed['codigo_barras'] == '7590000040110', f'barcode .xls {parsed and parsed["codigo_barras"]}')

    valido, msg, meta_val = validar_inventario_previo(datos, ext, encabezados, mapeo, meta)
    _ok(valido and (meta_val or {}).get('filas_validas') == 2, f'valida .xls ({msg})')


def _probar_xlsx():
    print('\n=== Lectura de .xlsx ===')
    import openpyxl

    from backend.inventory_import import (
        cargar_archivo_inventario,
        detectar_mapeo_columnas,
        iter_filas_inventario,
        leer_encabezados_inventario,
        parsear_fila_inventario,
        validar_inventario_previo,
    )

    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.append(['nombre', 'precio', 'codigo barras', 'marca'])
    hoja.append(['Coca Cola 2L', 2.1, '7591234567890', 'Coca-Cola'])
    hoja.append(['Mavesa 1kg', 1.2, None, 'Mavesa'])
    buffer = io.BytesIO()
    libro.save(buffer)

    archivo = SimpleNamespace(filename='inventario.xlsx', stream=io.BytesIO(buffer.getvalue()))
    datos, ext, error = cargar_archivo_inventario(archivo)
    _ok(error is None and ext == 'xlsx', 'detecta extensión .xlsx')

    encabezados, error = leer_encabezados_inventario(datos, ext)
    mapeo, meta, error = detectar_mapeo_columnas(encabezados)
    _ok(error is None and mapeo.get('precio') == 'precio', 'mapea columnas .xlsx')

    filas = list(iter_filas_inventario(datos, ext, encabezados))
    parsed = parsear_fila_inventario(filas[0], mapeo, meta)
    _ok(len(filas) == 2 and parsed['codigo_barras'] == '7591234567890', 'lee filas .xlsx')

    valido, msg, meta_val = validar_inventario_previo(datos, ext, encabezados, mapeo, meta)
    _ok(valido and (meta_val or {}).get('filas_validas') == 2, f'valida .xlsx ({msg})')


def main() -> int:
    _probar_normalizacion()
    _probar_indice()
    _probar_placeholders()
    _probar_asignacion_instantanea()
    _probar_xls()
    _probar_xlsx()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK catálogo maestro indexado, matching instantáneo y soporte XLS')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Reconocimiento de archivos .xlsx al subir inventario (SIN red ni BD).

Cubre los motivos habituales de "el sistema no reconoce mi archivo":

- extensión en mayúsculas o con puntos/espacios en el nombre;
- extensión no soportada y archivo vacío;
- Excel guardado como HTML con extensión .xls;
- binario de Excel subido como .csv;
- .xlsx corrupto o que no es un ZIP;
- Excel con preámbulo tipo ERP y con pestañas de resumen.

No sustituye a ``test_excel_multihoja.py`` / ``test_importacion_formatos.py``
(que cubren hojas y la importación completa); aquí se verifica la **puerta de
entrada**, que es donde se pierde el archivo antes de parsearlo.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend import inventory_import as inv  # noqa: E402

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def _archivo(nombre, data):
    return SimpleNamespace(filename=nombre, stream=io.BytesIO(data))


def _xlsx(filas, hoja='Inventario'):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = hoja
    for fila in filas:
        ws.append(fila)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


CABECERA = ['Codigo', 'Descripcion', 'Precio', 'Stock']


def main() -> int:
    print('=== Nombre de archivo: extensión y casos raros ===')
    data = _xlsx([CABECERA, ['7702000000001', 'Cafe 500g', 12.5, 4]])

    for nombre in ('inventario.xlsx', 'INVENTARIO.XLSX', 'Inventario.Xlsx',
                   'mi inventario v2.final.xlsx', 'inventario.xlsx ', '.xlsx',
                   'inventario.xlsx (1)', 'inventario (copia).xlsx'):
        _, ext, err = inv.cargar_archivo_inventario(_archivo(nombre, data))
        _ok(ext == 'xlsx' and err is None, f'acepta {nombre!r} como .xlsx')

    for nombre in ('inventario.xlsx2', 'inventario.xlsx.exe', 'inventario', 'foto.png'):
        _, ext, err = inv.cargar_archivo_inventario(_archivo(nombre, data))
        _ok(ext is None and bool(err), f'rechaza {nombre!r} (no es una extensión válida)')

    _, _, err = inv.cargar_archivo_inventario(_archivo('datos.csv', data))
    _ok(err and 'válido' in err, 'binario de Excel disfrazado de .csv -> mensaje claro')

    _, _, err = inv.cargar_archivo_inventario(_archivo('foto.png', b'\x89PNG datos'))
    _ok(err and '.csv' in err and '.xlsx' in err, 'extensión no soportada -> indica las válidas')

    _, _, err = inv.cargar_archivo_inventario(_archivo('vacio.xlsx', b''))
    _ok(err and 'vacío' in err, 'archivo vacío -> mensaje claro')

    _, _, err = inv.cargar_archivo_inventario(_archivo('', data))
    _ok(err and 'adjuntó' in err, 'sin archivo -> mensaje claro')

    print('\n=== Tamaño máximo ===')
    original_max = inv.MAX_IMPORT_FILE_BYTES
    inv.MAX_IMPORT_FILE_BYTES = 1024
    try:
        _, _, err = inv.cargar_archivo_inventario(_archivo('grande.xlsx', b'x' * 5000))
        _ok(err and 'tamaño máximo' in err, 'excede el límite -> mensaje claro')
    finally:
        inv.MAX_IMPORT_FILE_BYTES = original_max

    print('\n=== .xls que en realidad es HTML (caso típico de Excel) ===')
    html = b'<html><body><table><tr><td>x</td></tr></table></body></html>'
    datos, ext, err = inv.cargar_archivo_inventario(_archivo('reporte.xls', html))
    _ok(ext == 'xls' and err is None, 'la extensión .xls se detecta')
    _, error_lectura = inv.leer_encabezados_inventario(datos, ext)
    _ok(bool(error_lectura), 'lectura falla con mensaje controlado (no excepción)')

    print('\n=== .xlsx corrupto / que no es ZIP ===')
    for nombre, contenido in (('roto.xlsx', b'esto no es un zip'),
                              ('truncado.xlsx', data[:40])):
        datos, ext, err = inv.cargar_archivo_inventario(_archivo(nombre, contenido))
        _ok(err is None, f'{nombre}: la puerta de entrada lo deja pasar')
        cab, error = inv.leer_encabezados_inventario(datos, ext)
        _ok(cab is None and bool(error), f'{nombre}: error controlado y explicado')

    print('\n=== .xlsx válido: cabecera, mapeo y filas ===')
    datos, ext, err = inv.cargar_archivo_inventario(_archivo('inv.xlsx', data))
    _ok(err is None and ext == 'xlsx', 'se carga')
    cabeceras, error = inv.leer_encabezados_inventario(datos, ext)
    _ok(error is None and cabeceras, f'lee cabeceras ({cabeceras})')
    filas = list(inv.iter_filas_inventario(datos, ext, cabeceras))
    _ok(len(filas) == 1, f'lee las filas de datos ({len(filas)})')
    _ok(str(filas[0].get('Codigo')) == '7702000000001', 'primer código correcto')
    _ok(filas[0].get('Precio') == 12.5, 'precio leído como número')

    print('\n=== .xlsx con preámbulo ERP ===')
    erp = _xlsx([
        ['REPORTE DE INVENTARIO', None, None, None],
        ['Sucursal: Principal', None, None, None],
        [],
        CABECERA,
        ['7702000000002', 'Harina PAN', 8.0, 10],
    ])
    datos, ext, _ = inv.cargar_archivo_inventario(_archivo('erp.xlsx', erp))
    cabeceras, error = inv.leer_encabezados_inventario(datos, ext)
    _ok(error is None and cabeceras, 'detecta la cabecera aunque haya preámbulo')
    analisis = inv.analizar_inventario(datos, ext)
    _ok(analisis and analisis[1], 'analiza el inventario con preámbulo')

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK reconocimiento .xlsx: extensiones, tamaños y errores controlados')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

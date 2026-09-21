#!/usr/bin/env python3
"""Prueba del parser de .xls heredados de ERP (ReporteGeneral).

Reproduce el formato típico: preámbulo de varias filas, cabecera con etiquetas
**desalineadas** de las columnas de datos y filas alternas vacías. Verifica que
se detecte la cabecera y que se mapeen Código/Descripción/Costo/Existencia por
su columna real.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

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


def _erp_xls():
    import xlwt

    libro = xlwt.Workbook()
    hoja = libro.add_sheet('Sheet 1')
    hoja.write(0, 0, 'EMPRESA, C.A.')
    hoja.write(2, 0, 'Reporte General')
    hoja.write(5, 0, 'Clasificación : Productos, Ensamblados')
    # Cabecera con etiquetas desplazadas respecto a los datos (como el ERP real).
    hoja.write(15, 0, 'Código  ')
    hoja.write(15, 2, 'Descripción  ')
    hoja.write(15, 8, ' Costo')
    hoja.write(15, 11, 'Existencia')
    hoja.write(15, 16, ' Valor Inventario')
    filas = [
        (17, 7599450000225, 'ACEITE AMANECER 500 ML', 1.67, 5),
        (19, 6921199103232, 'ACONDICIONADOR DOVEY 400 ML', 2.0, 1),
        (21, 'AFEITADORA', 'AFEITADORA DORCO UNIDAD', 0.23, 24),
        (23, 7591031001959, 'AGUA MINALBA 1.5 LT', 1.34, 8),
    ]
    for numero, codigo, nombre, costo, existencia in filas:
        hoja.write(numero, 0, codigo)
        hoja.write(numero, 2, nombre)
        hoja.write(numero, 10, costo)
        hoja.write(numero, 15, existencia)
    buffer = io.BytesIO()
    libro.save(buffer)
    return buffer.getvalue()


def main() -> int:
    print('=== Parser .xls ERP heredado ===')
    from backend.inventory_import import (
        analizar_inventario,
        cargar_archivo_inventario,
        iter_lotes_productos,
    )

    data = _erp_xls()
    archivo = SimpleNamespace(filename='ReporteGeneral.Xls', stream=io.BytesIO(data))
    contenido, extension, error = cargar_archivo_inventario(archivo)
    _ok(error is None and extension == 'xls', 'reconoce .xls heredado')

    indice, encabezados, mapeo, error = analizar_inventario(contenido, extension)
    _ok(error is None, f'detecta cabecera sin error ({error})')
    _ok(indice == 15, f'encuentra la fila de cabecera (fila {indice})')
    _ok(mapeo.get('codigo_barras') == 0, f'mapea Código -> col {mapeo.get("codigo_barras")}')
    _ok(mapeo.get('nombre') == 2, f'mapea Descripción -> col {mapeo.get("nombre")}')
    _ok(mapeo.get('precio') == 10, f'mapea Costo (desalineado) -> col {mapeo.get("precio")}')
    _ok(mapeo.get('stock') == 15, f'mapea Existencia (desalineado) -> col {mapeo.get("stock")}')

    mapa = {campo: campo for campo in mapeo}
    productos = []
    for lote in iter_lotes_productos(
        contenido, extension, encabezados, mapa, {'precio_en_bs': False},
        columnas=mapeo, fila_inicio=indice,
    ):
        productos.extend(lote)

    _ok(len(productos) == 4, f'lee 4 productos ({len(productos)})')
    _ok(productos[0]['nombre'] == 'ACEITE AMANECER 500 ML', 'nombre correcto')
    _ok(productos[0]['precio_usd'] == 1.67, f'costo correcto ({productos[0]["precio_usd"]})')
    _ok(productos[0]['stock'] == 5, f'existencia correcta ({productos[0]["stock"]})')
    _ok(
        productos[2]['codigo_barras'] == 'AFEITADORA'
        and productos[2]['precio_usd'] == 0.23,
        'código de texto y costo en fila alterna',
    )

    print('\n=== Sin precio (solo existencias) no se rechaza ===')
    from backend.inventory_import import parsear_fila_inventario

    # Fila con nombre y existencia, sin costo.
    fila = {'nombre': 'PRODUCTO SOLO STOCK', 'stock': 12}
    parsed = parsear_fila_inventario(
        fila, {'nombre': 'nombre', 'stock': 'stock'}, {'precio_en_bs': False}
    )
    _ok(parsed is not None, 'fila sin precio es válida')
    _ok(parsed and parsed['precio_usd'] is None, 'precio queda None (se conserva en UPSERT)')
    _ok(parsed and parsed['stock'] == 12, 'existencia leída')

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK parser ERP .xls (cabecera dinámica y columnas desalineadas)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

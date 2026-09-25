#!/usr/bin/env python3
"""Sincronizacion de inventario CSV: altas, bajas y modificaciones (BD real).

Crea un comercio temporal, aplica un UPSERT y verifica:
  - Alta de producto nuevo.
  - Modificacion de precio.
  - Producto identico -> sin cambios.
  - Producto ausente del archivo -> baja (inactivo).
  - Reaparicion -> reactivacion.
Limpia todo al finalizar.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass

_ERRORES = []
CORREO = '__sync_test__@localis.test'
NOMBRE_COMERCIO = '__sync_test__ comercio'
EANS = ('9990000000001', '9990000000002', '9990000000003', '9990000000004')


def _ok(condicion, mensaje):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    _ERRORES.append(mensaje)
    return False


def _producto(nombre, ean, precio, stock=5):
    return {
        'nombre': nombre,
        'descripcion': f'{nombre} desc',
        'precio_usd': precio,
        'stock': stock,
        'codigo_barras': ean,
        'imagen_url': None,
        'marca': None,
        'categoria': 'otros',
    }


def _limpiar(get_db_connection):
    with get_db_connection() as c:
        cur = c.cursor()
        cur.execute("SELECT id FROM comercios WHERE nombre = ?", (NOMBRE_COMERCIO,))
        fila = cur.fetchone()
        if fila:
            cid = fila[0] if not isinstance(fila, dict) else list(fila.values())[0]
            cur.execute('DELETE FROM productos WHERE comercio_id = ?', (cid,))
            cur.execute('DELETE FROM comercios WHERE id = ?', (cid,))
        cur.execute('DELETE FROM usuarios WHERE correo = ?', (CORREO,))
        c.commit()


def main() -> int:
    from backend.db import get_db_connection
    from backend.inventory_import import persistir_importacion_upsert

    _limpiar(get_db_connection)
    with get_db_connection() as c:
        cur = c.cursor()
        cur.execute(
            "INSERT INTO usuarios (nombre, correo, rol) VALUES (?, ?, 'comerciante') RETURNING id",
            ('Sync Test', CORREO),
        )
        fila = cur.fetchone()
        usuario_id = fila[0] if not isinstance(fila, dict) else list(fila.values())[0]
        cur.execute(
            """
            INSERT INTO comercios (usuario_id, nombre, visible, estado_pago)
            VALUES (?, ?, 0, 'gratis') RETURNING id
            """,
            (usuario_id, NOMBRE_COMERCIO),
        )
        fila = cur.fetchone()
        comercio_id = fila[0] if not isinstance(fila, dict) else list(fila.values())[0]
        c.commit()

    try:
        inicial = [
            _producto('Producto A', EANS[0], 1.0),
            _producto('Producto B', EANS[1], 2.0),
            _producto('Producto C', EANS[2], 3.0),
        ]
        ins, act, omi, baj = persistir_importacion_upsert(comercio_id, inicial)
        _ok((ins, act, omi, baj) == (3, 0, 0, 0), f'carga inicial: 3 altas ({ins},{act},{omi},{baj})')

        segunda = [
            _producto('Producto A', EANS[0], 1.5),
            _producto('Producto B', EANS[1], 2.0),
            _producto('Producto D', EANS[3], 4.0),
        ]
        ins, act, omi, baj = persistir_importacion_upsert(comercio_id, segunda)
        _ok(ins == 1, f'detecta 1 alta nueva (D): {ins}')
        _ok(act == 1, f'detecta 1 modificacion (A): {act}')
        _ok(omi == 1, f'detecta 1 sin cambios (B): {omi}')
        _ok(baj == 1, f'detecta 1 baja (C ausente): {baj}')

        with get_db_connection(row_factory=True) as c:
            cur = c.cursor()
            cur.execute(
                'SELECT codigo_barras, precio_usd, COALESCE(activo,1) AS activo '
                'FROM productos WHERE comercio_id = ?',
                (comercio_id,),
            )
            estado = {r['codigo_barras']: r for r in map(dict, cur.fetchall())}
        _ok(abs(float(estado[EANS[0]]['precio_usd']) - 1.5) < 1e-6, 'el precio de A se actualizo')
        _ok(int(estado[EANS[2]]['activo']) == 0, 'C quedo inactivo (baja)')
        _ok(int(estado[EANS[3]]['activo']) == 1, 'D quedo activo (alta)')

        ins, act, omi, baj = persistir_importacion_upsert(comercio_id, inicial)
        _ok(ins == 0, 'C no se duplica al reaparecer')
        with get_db_connection(row_factory=True) as c:
            cur = c.cursor()
            cur.execute(
                'SELECT COUNT(*) AS n, COALESCE(MAX(COALESCE(activo,1)),0) AS activo '
                'FROM productos WHERE comercio_id = ? AND codigo_barras = ?',
                (comercio_id, EANS[2]),
            )
            fila = dict(cur.fetchone())
        _ok(int(fila['n']) == 1 and int(fila['activo']) == 1, 'C se reactivo sin duplicarse')
    finally:
        _limpiar(get_db_connection)

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK sincronizacion de inventario: altas, bajas, modificaciones y reactivacion')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

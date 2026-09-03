#!/usr/bin/env python3
"""
One-shot: NULL en productos.imagen_url y borra del maestro cualquier URL
que no sea Supabase Storage ni /static/uploads/.

Uso:
  python scripts/purgar_imagenes_externas.py
  python scripts/purgar_imagenes_externas.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

_SQL_COUNT_PRODUCTOS = """
SELECT COUNT(*) AS n
FROM productos
WHERE imagen_url IS NOT NULL
  AND CAST(imagen_url AS TEXT) NOT LIKE '%/storage/v1/object/public/%'
  AND CAST(imagen_url AS TEXT) NOT LIKE '/static/uploads/%'
"""

_SQL_COUNT_MAESTRO = """
SELECT COUNT(*) AS n
FROM catalogo_maestro_imagenes
WHERE url_imagen IS NOT NULL
  AND CAST(url_imagen AS TEXT) NOT LIKE '%/storage/v1/object/public/%'
  AND CAST(url_imagen AS TEXT) NOT LIKE '/static/uploads/%'
"""

_SQL_PURGE_PRODUCTOS = """
UPDATE productos
SET imagen_url = NULL
WHERE imagen_url IS NOT NULL
  AND CAST(imagen_url AS TEXT) NOT LIKE '%/storage/v1/object/public/%'
  AND CAST(imagen_url AS TEXT) NOT LIKE '/static/uploads/%'
"""

_SQL_PURGE_MAESTRO = """
DELETE FROM catalogo_maestro_imagenes
WHERE url_imagen IS NOT NULL
  AND CAST(url_imagen AS TEXT) NOT LIKE '%/storage/v1/object/public/%'
  AND CAST(url_imagen AS TEXT) NOT LIKE '/static/uploads/%'
"""


def _count(cursor, sql):
    cursor.execute(sql)
    fila = cursor.fetchone()
    if not fila:
        return 0
    if isinstance(fila, dict):
        return int(fila.get('n') or 0)
    return int(fila[0] or 0)


def main() -> int:
    parser = argparse.ArgumentParser(description='Purga URLs de imagen externas')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Solo cuenta filas afectadas, no modifica',
    )
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=True)
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        n_prod = _count(cursor, _SQL_COUNT_PRODUCTOS)
        n_mae = _count(cursor, _SQL_COUNT_MAESTRO)
        print(f'productos con URL externa: {n_prod}')
        print(f'maestro con URL externa: {n_mae}')
        if args.dry_run:
            print('dry-run: sin cambios')
            return 0
        cursor.execute(_SQL_PURGE_PRODUCTOS)
        purged_prod = cursor.rowcount
        cursor.execute(_SQL_PURGE_MAESTRO)
        purged_mae = cursor.rowcount
        conexion.commit()
        print(f'purgados productos={purged_prod} maestro={purged_mae}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

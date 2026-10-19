"""Inicializa y verifica la base de datos de STAGING sin tocar produccion.

Uso (PowerShell):
    $env:STAGING_DATABASE_URL="postgresql://..."; python scripts/init_staging.py

Nunca usa el DATABASE_URL de produccion: solo lee STAGING_DATABASE_URL y
rechaza el host del proyecto de produccion como salvaguarda.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

HOST_PRODUCCION = 'wesnnnvoavprgqcczzsg'  # proyecto Supabase de produccion


def main() -> int:
    url = (os.environ.get('STAGING_DATABASE_URL') or '').strip()
    if not url:
        print('Define STAGING_DATABASE_URL (connection string del proyecto de staging).')
        return 2
    if 'wesnnnvoavprgqcczzsg' in url:
        print('RECHAZADO: esa URL apunta al proyecto de PRODUCCION.')
        return 2

    # database.py lee DATABASE_URL al importar; la fijamos ANTES de importarlo.
    os.environ['DATABASE_URL'] = url
    import database

    print('Inicializando staging...')
    ok = database.init_db()

    from backend.db import get_db_connection

    try:
        with get_db_connection() as c:
            cur = c.cursor()
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            fila = cur.fetchone()
            total = next(iter(fila.values())) if isinstance(fila, dict) else fila[0]
            print(f'tablas en staging: {total}')
    except Exception as e:
        print(f'No se pudo contar tablas: {type(e).__name__}: {e}')

    print('OK staging inicializado.' if ok else 'FALLO al inicializar staging.')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

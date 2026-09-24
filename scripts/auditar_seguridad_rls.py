#!/usr/bin/env python3
"""
Auditoría y blindaje de RLS (Supabase/PostgreSQL) para Localis.

Supabase expone por defecto todas las tablas del esquema ``public`` vía su API.
Si una tabla no tiene Row Level Security, la llave ``anon`` puede leer/escribir
directamente. Este script:

  - Detecta tablas ``public`` con RLS desactivado.
  - Con ``--apply`` habilita RLS (sin ``FORCE``, para no afectar al rol de la app,
    que es owner/service_role y ya bypassa RLS).

Seguro por diseño: el servidor accede como ``postgres``/``service_role`` (bypassa
RLS), y el frontend NO usa la llave anon. Sin ``--apply`` solo reporta.
"""

from __future__ import annotations

import argparse
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


def _tablas_sin_rls(cursor):
    cursor.execute(
        """
        SELECT c.relname
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
          AND c.relrowsecurity = false
        ORDER BY c.relname
        """
    )
    return [str(next(iter(dict(f).values()), '')) for f in cursor.fetchall()]


def _tablas_rls(cursor):
    cursor.execute(
        """
        SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity = true
        """
    )
    fila = cursor.fetchone()
    return int(next(iter(dict(fila).values()), 0)) if fila else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Habilita RLS (sin esto solo reporta).')
    args = parser.parse_args()

    from backend.db import get_db_connection

    with get_db_connection(row_factory=True) as conexion:
        cursor = conexion.cursor()
        sin_rls = _tablas_sin_rls(cursor)
        con_rls = _tablas_rls(cursor)

        print('=== Auditoría RLS (Supabase) ===')
        print(f'Tablas con RLS habilitado: {con_rls}')
        print(f'Tablas SIN RLS (expuestas a la API): {len(sin_rls)}')
        for tabla in sin_rls:
            print(f'  {tabla}')

        if not args.apply:
            print('\nDry-run: nada se modificó. Repite con --apply.')
            return 0

        habilitadas = 0
        for tabla in sin_rls:
            # Identificador validado contra el catálogo (no hay inyección).
            if not tabla.replace('_', '').isalnum():
                continue
            cursor.execute(f'ALTER TABLE public.{tabla} ENABLE ROW LEVEL SECURITY')
            habilitadas += 1
        conexion.commit()
        print(f'\nRLS habilitado en {habilitadas} tabla(s).')
        print('Nota: el servidor usa postgres/service_role (bypassa RLS); el frontend no usa anon.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Prueba de las migraciones versionadas (requiere BD; se omite sin ella).

Verifica que:
- init_db crea la tabla ``schema_migrations``.
- La version base ``0001`` queda registrada.
- Es idempotente: correr init_db dos veces no duplica registros ni falla.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    return bool(cond)


def _escalar(fila):
    if fila is None:
        return None
    if isinstance(fila, dict):
        return next(iter(fila.values()), None)
    return fila[0]


def main() -> int:
    from database import DATABASE_URL, init_db

    if not DATABASE_URL:
        print('  SKIP  DATABASE_URL no configurada: prueba de migraciones omitida.')
        return 0

    fallos = []

    print('=== init_db aplica migraciones ===')
    fallos.append(not _ok(bool(init_db()), 'init_db() OK'))

    from backend.db import get_db_connection

    with get_db_connection() as c:
        cur = c.cursor()
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = 'schema_migrations'
            )
            """
        )
        fallos.append(not _ok(bool(_escalar(cur.fetchone())), 'tabla schema_migrations existe'))
        cur.execute("SELECT version FROM schema_migrations WHERE version = '0001'")
        fallos.append(not _ok(_escalar(cur.fetchone()) == '0001', 'version base 0001 registrada'))

    print('=== Idempotencia (segunda corrida) ===')
    fallos.append(not _ok(bool(init_db()), 'init_db() segunda vez OK'))
    with get_db_connection() as c:
        cur = c.cursor()
        cur.execute("SELECT COUNT(*) FROM schema_migrations WHERE version = '0001'")
        n = _escalar(cur.fetchone())
        fallos.append(not _ok(n == 1, f'sin duplicados (0001 aparece {n} vez/veces)'))

    print('\n=== RESULTADO ===')
    total = sum(1 for f in fallos if f)
    if total:
        print(f'FALLOS: {total}')
        return 1
    print('OK migraciones versionadas: tabla, version base e idempotencia')
    return 0


if __name__ == '__main__':
    sys.exit(main())

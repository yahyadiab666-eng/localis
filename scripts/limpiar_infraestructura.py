#!/usr/bin/env python3
"""
Limpieza de infraestructura de Localis (PostgreSQL/Supabase).

- Elimina índices **duplicados exactos** (deja el canónico/PK).
- Borra registros sandbox/QA (prefijo ``__`` y correos ``@localis.test``).
- Purga logs antiguos de ``image_pipeline_log`` (retención configurable).

Es seguro por diseño: sin ``--apply`` solo reporta (dry-run). Antes de borrar un
índice verifica que exista otro **idéntico** (mismas columnas/unicidad) que lo
cubra, para no degradar ninguna consulta.

Uso::

    python scripts/limpiar_infraestructura.py
    python scripts/limpiar_infraestructura.py --apply
    python scripts/limpiar_infraestructura.py --apply --retencion-log-dias 30
"""

from __future__ import annotations

import argparse
import re
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

# índice redundante -> índice que lo cubre (se conserva)
INDICES_REDUNDANTES = {
    'idx_usuarios_correo': 'usuarios_correo_key',
    'idx_categorias_nombre': 'categorias_nombre_key',
    'idx_planes_codigo': 'planes_codigo_key',
    'idx_productos_tienda': 'idx_productos_comercio_id',
    'idx_product_image_overrides_ean': 'product_image_overrides_pkey',
}


def _definiciones(cursor):
    cursor.execute(
        """
        SELECT tablename, indexname, indexdef
        FROM pg_indexes WHERE schemaname = 'public'
        """
    )
    return [dict(f) for f in cursor.fetchall()]


def _firma(indexdef):
    """Firma comparable: tipo de acceso + columnas + unicidad."""
    texto = re.sub(r'\s+', ' ', str(indexdef or '')).strip()
    unico = 'UNIQUE' in texto.upper()
    match = re.search(r'USING\s+\w+\s+\((.*)\)\s*$', texto)
    columnas = re.sub(r'\s+', '', match.group(1)) if match else texto
    return (unico, columnas)


def _indices_redundantes_seguros(cursor):
    """Lista de (drop, keeper) solo si son duplicados exactos."""
    definiciones = {f['indexname']: f for f in _definiciones(cursor)}
    seguros = []
    for redundante, keeper in INDICES_REDUNDANTES.items():
        d_red = definiciones.get(redundante)
        d_kep = definiciones.get(keeper)
        if not d_red or not d_kep:
            continue
        if d_red['tablename'] != d_kep['tablename']:
            continue
        if _firma(d_red['indexdef']) == _firma(d_kep['indexdef']):
            seguros.append((redundante, keeper, d_red['tablename']))
    return seguros


def _sandbox_counts(cursor):
    counts = {}
    for nombre, sql in (
        ('productos', "SELECT COUNT(*) FROM productos WHERE LEFT(LOWER(TRIM(nombre)),2)='__'"),
        ('comercios', "SELECT COUNT(*) FROM comercios WHERE LEFT(LOWER(TRIM(nombre)),2)='__'"),
        ('usuarios', "SELECT COUNT(*) FROM usuarios WHERE LOWER(COALESCE(correo,'')) LIKE '%@localis.test'"),
    ):
        cursor.execute(sql)
        fila = cursor.fetchone()
        counts[nombre] = int(next(iter(dict(fila).values()), 0)) if fila else 0
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Aplica los cambios (sin esto solo lista).')
    parser.add_argument('--retencion-log-dias', type=int, default=30, help='Días de logs a conservar.')
    args = parser.parse_args()

    from backend.db import get_db_connection

    with get_db_connection(row_factory=True) as conexion:
        cursor = conexion.cursor()
        redundantes = _indices_redundantes_seguros(cursor)
        counts = _sandbox_counts(cursor)

        print('=== Limpieza de infraestructura (dry-run=%s) ===' % (not args.apply))
        print(f'Índices redundantes seguros: {len(redundantes)}')
        for redundante, keeper, tabla in redundantes:
            print(f'  DROP {redundante}  (cubierto por {keeper} en {tabla})')
        print('Registros sandbox:', counts)
        print(f'Retención de image_pipeline_log: {args.retencion_log_dias} días')

        if not args.apply:
            print('\nDry-run: nada se modificó. Repite con --apply.')
            return 0

        borrados = 0
        for redundante, _keeper, _tabla in redundantes:
            cursor.execute(f'DROP INDEX IF EXISTS {redundante}')
            borrados += 1
        print(f'Índices eliminados: {borrados}')

        cursor.execute("DELETE FROM productos WHERE LEFT(LOWER(TRIM(nombre)),2)='__'")
        p = cursor.rowcount or 0
        cursor.execute("DELETE FROM comercios WHERE LEFT(LOWER(TRIM(nombre)),2)='__'")
        c = cursor.rowcount or 0
        cursor.execute("DELETE FROM usuarios WHERE LOWER(COALESCE(correo,'')) LIKE '%@localis.test'")
        u = cursor.rowcount or 0
        print(f'Sandbox borrados: productos={p} comercios={c} usuarios={u}')

        try:
            cursor.execute(
                """
                DELETE FROM image_pipeline_log
                WHERE timestamp < (CURRENT_TIMESTAMP - (%s * INTERVAL '1 day'))
                """,
                (int(args.retencion_log_dias),),
            )
            print(f'Logs antiguos borrados: {cursor.rowcount or 0}')
        except Exception as error:
            print(f'aviso purga de logs: {type(error).__name__}: {error}')

        conexion.commit()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

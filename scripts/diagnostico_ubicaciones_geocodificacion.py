"""Rellena ciudad/zona/dirección de las sedes desde su enlace de Google Maps.

Los comercios creados antes de rellenar automáticamente la ubicación tienen sus
sedes sin ``ciudad``/``zona``, así que no aparecen en las búsquedas por
ubicación. Este script las completa (resolviendo el enlace corto y geocodificando
al revés con OpenStreetMap) y actualiza también las columnas históricas del
comercio desde su sede principal.

Por defecto es **dry-run**: solo muestra qué haría. Con ``--apply`` lo guarda.

Uso:
    python scripts/diagnostico_ubicaciones_geocodificacion.py            # ver
    python scripts/diagnostico_ubicaciones_geocodificacion.py --apply    # aplicar
    python scripts/diagnostico_ubicaciones_geocodificacion.py --apply --limite 20

Necesita ``LOCALIS_GEOCODIFICACION`` habilitada (por defecto lo está).
"""

from __future__ import annotations

import argparse
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(RAIZ, '.env'), override=False)


def _valor(fila, clave, indice=0):
    return fila.get(clave) if isinstance(fila, dict) else fila[indice]


def _comercios_con_sedes_pendientes(cursor, limite=None):
    """Comercios con al menos una sede a la que le falta ciudad o zona."""
    consulta = """
        SELECT DISTINCT s.comercio_id
        FROM sucursales s
        WHERE COALESCE(TRIM(s.ciudad), '') = '' OR COALESCE(TRIM(s.zona), '') = ''
        ORDER BY s.comercio_id
    """
    if limite:
        consulta += f' LIMIT {int(limite)}'
    cursor.execute(consulta)
    return [int(_valor(f, 'comercio_id', 0)) for f in cursor.fetchall()]


def main():
    parser = argparse.ArgumentParser(
        description='Backfill de ciudad/zona/dirección de sedes desde Google Maps'
    )
    parser.add_argument('--apply', action='store_true', help='guardar los cambios')
    parser.add_argument('--limite', type=int, default=None, help='máximo de comercios')
    args = parser.parse_args()

    from backend.db import get_db_connection
    from backend.geo_ubicacion import (
        enriquecer_ubicaciones_geo,
        geocodificacion_habilitada,
    )
    from backend.ubicaciones import (
        cargar_ubicaciones,
        datos_ubicacion_principal,
        guardar_ubicaciones,
    )

    if not geocodificacion_habilitada():
        print('La geocodificación está apagada (LOCALIS_GEOCODIFICACION=0).')
        print('Actívala para poder rellenar ciudad/zona.')
        return 1

    modo = 'APLICAR' if args.apply else 'DRY-RUN (usa --apply para guardar)'
    print(f'Modo: {modo}')

    actualizadas = 0
    sedes_actualizadas = 0
    comercios_actualizados = 0

    with get_db_connection(row_factory=True) as conexion:
        cursor = conexion.cursor()
        comercios = _comercios_con_sedes_pendientes(cursor, args.limite)
        print(f'Comercios con sedes sin ciudad/zona: {len(comercios)}')

        for comercio_id in comercios:
            sedes = cargar_ubicaciones(cursor, comercio_id)
            enriquecidas = enriquecer_ubicaciones_geo(sedes)
            principal = datos_ubicacion_principal(enriquecidas)

            cambios = [
                s for s, e in zip(sedes, enriquecidas)
                if (s.get('ciudad') != e.get('ciudad')) or (s.get('zona') != e.get('zona'))
            ]
            if not cambios:
                continue

            nombres = ', '.join(
                f"{e.get('nombre')} -> ciudad={e.get('ciudad')!r} zona={e.get('zona')!r}"
                for e in enriquecidas
            )
            print(f'  comercio {comercio_id}: {nombres}')

            if args.apply:
                guardar_ubicaciones(cursor, comercio_id, enriquecidas)
                cursor.execute(
                    """
                    UPDATE comercios
                    SET direccion = ?, ciudad = ?, zona = ?,
                        maps_url = ?, ubicacion_maps_url = ?
                    WHERE id = ?
                    """,
                    (
                        principal['direccion'],
                        principal['ciudad'],
                        principal['zona'],
                        principal['maps_url'],
                        principal['ubicacion_maps_url'],
                        comercio_id,
                    ),
                )
                sedes_actualizadas += len(cambios)
                comercios_actualizados += 1

            actualizadas += 1

        if args.apply:
            conexion.commit()

    if args.apply:
        print(
            f'\nListo: {comercios_actualizados} comercio(s), '
            f'{sedes_actualizadas} sede(s) actualizadas.'
        )
    else:
        print(f'\nSe actualizarían {actualizadas} comercio(s). Repite con --apply.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

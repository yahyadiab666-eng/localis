#!/usr/bin/env python3
"""
Saneamiento del catálogo: elimina comercios/productos/usuarios de prueba.

Los *productos fantasma* que aparecen en la vista de clientes provienen de
sandboxes de QA (prefijo ``__``, p. ej. ``__localis_qa_e2e__``) que quedaron en
la base si una corrida de pruebas falló antes de limpiar. La vista pública ya
los filtra por nombre; este script los borra definitivamente.

Por seguridad, sin ``--apply`` solo muestra un resumen (dry-run).

Uso::

    python scripts/limpiar_datos_prueba.py
    python scripts/limpiar_datos_prueba.py --apply
    python scripts/limpiar_datos_prueba.py --apply --prefijo __localis_qa_e2e__
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

PREFIJO_DEFECTO = '__'


def _patron_like(prefijo):
    """Escapa ``_`` y ``%`` para que el prefijo ``__`` sea literal en SQL."""
    escapado = str(prefijo).replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    return f'{escapado}%'


def _comercios_sandbox(prefijo):
    from backend.db import get_db_connection

    with get_db_connection(row_factory=True) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT id, nombre
            FROM comercios
            WHERE LOWER(TRIM(COALESCE(nombre, ''))) LIKE ? ESCAPE '\\'
            ORDER BY id
            """,
            (_patron_like(prefijo).lower(),),
        )
        return [dict(fila) for fila in cursor.fetchall()]


def _usuarios_sandbox(prefijo):
    from backend.db import get_db_connection

    with get_db_connection(row_factory=True) as conexion:
        cursor = conexion.cursor()
        patron = _patron_like(prefijo).lower()
        cursor.execute(
            """
            SELECT id, nombre, correo
            FROM usuarios
            WHERE LOWER(TRIM(COALESCE(correo, ''))) LIKE ? ESCAPE '\\'
               OR LOWER(TRIM(COALESCE(nombre, ''))) LIKE ? ESCAPE '\\'
            ORDER BY id
            """,
            (patron, patron),
        )
        return [dict(fila) for fila in cursor.fetchall()]


def _contar_productos(ids_comercios):
    if not ids_comercios:
        return 0
    from backend.db import get_db_connection

    placeholders = ', '.join('?' for _ in ids_comercios)
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            f'SELECT COUNT(*) FROM productos WHERE comercio_id IN ({placeholders})',
            tuple(int(i) for i in ids_comercios),
        )
        fila = cursor.fetchone()
    return int(fila[0] if isinstance(fila, (list, tuple)) else list(fila.values())[0])


def _productos_imagen_invalida():
    """Productos cuya imagen no es mostrable (externa no persistida/fabricada).

    Se alinean con la política de presentación: si la URL no es Storage, upload
    local ni API de catálogo válida, queda en estado neutro (``None`` +
    ``pendiente``) para que el motor la reintente.
    """
    from backend.activos_verificados import es_asset_generado
    from backend.db import get_db_connection
    from backend.utils import url_imagen_catalogo_valida
    from utils.images import url_publica_producto_desde_bd

    with get_db_connection(row_factory=True) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT id, imagen_url, imagen_fuente
            FROM productos
            WHERE imagen_url IS NOT NULL AND TRIM(CAST(imagen_url AS TEXT)) <> ''
            ORDER BY id
            """
        )
        filas = [dict(f) for f in cursor.fetchall()]

    invalidas = []
    for fila in filas:
        crudo = fila.get('imagen_url')
        if es_asset_generado(crudo, fila.get('imagen_fuente')):
            continue
        if not (url_publica_producto_desde_bd(crudo) or url_imagen_catalogo_valida(crudo)):
            invalidas.append(fila)
    return invalidas


def _limpiar_imagenes_invalidas(apply=False):
    invalidas = _productos_imagen_invalida()
    print('=== Imágenes no mostrables (externas/fabricadas) ===')
    print(f'Productos afectados: {len(invalidas)}')
    for fila in invalidas[:20]:
        print(f"  id={fila['id']} url={str(fila.get('imagen_url'))[:80]!r}")
    if len(invalidas) > 20:
        print(f'  … {len(invalidas) - 20} más')
    if not apply:
        print('\nDry-run: nada se modificó. Repite con --apply para limpiarlas.')
        return 0
    if not invalidas:
        return 0

    from backend.db import get_db_connection

    ids = [int(f['id']) for f in invalidas]
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        placeholders = ', '.join('?' for _ in ids)
        cursor.execute(
            f"""
            UPDATE productos
            SET imagen_url = NULL, imagen_fuente = NULL, imagen_estado = 'pendiente'
            WHERE id IN ({placeholders})
            """,
            tuple(ids),
        )
        afectados = cursor.rowcount or 0
        conexion.commit()
    print(f'\nImágenes inválidas limpiadas: {afectados}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Borra los registros (sin esto solo lista).')
    parser.add_argument('--prefijo', default=PREFIJO_DEFECTO, help='Prefijo de sandbox (por defecto "__").')
    parser.add_argument(
        '--imagenes-invalidas',
        action='store_true',
        help='En vez de sandboxes, limpia imágenes externas/fabricadas no mostrables.',
    )
    args = parser.parse_args()

    if args.imagenes_invalidas:
        return _limpiar_imagenes_invalidas(apply=args.apply)

    comercios = _comercios_sandbox(args.prefijo)
    usuarios = _usuarios_sandbox(args.prefijo)
    total_productos = _contar_productos([c['id'] for c in comercios])

    print('=== Saneamiento de datos de prueba ===')
    print(f'Prefijo: {args.prefijo!r}')
    print(f'Comercios de prueba : {len(comercios)}')
    print(f'Productos asociados : {total_productos}')
    print(f'Usuarios de prueba  : {len(usuarios)}')
    for comercio in comercios[:20]:
        print(f"  comercio id={comercio['id']} nombre={comercio['nombre']!r}")
    if len(comercios) > 20:
        print(f'  … {len(comercios) - 20} comercio(s) más')

    if not args.apply:
        print('\nDry-run: nada se borró. Repite con --apply para eliminarlos.')
        return 0

    if not comercios and not usuarios:
        print('\nNada que borrar.')
        return 0

    from backend.db import get_db_connection

    ids = [int(c['id']) for c in comercios]
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        borrados_productos = 0
        if ids:
            placeholders = ', '.join('?' for _ in ids)
            cursor.execute(
                f'DELETE FROM productos WHERE comercio_id IN ({placeholders})',
                tuple(ids),
            )
            borrados_productos = cursor.rowcount or 0
        borrados_comercios = 0
        for comercio_id in ids:
            cursor.execute('DELETE FROM comercios WHERE id = ?', (comercio_id,))
            borrados_comercios += cursor.rowcount or 0
        borrados_usuarios = 0
        for usuario in usuarios:
            cursor.execute('DELETE FROM usuarios WHERE id = ?', (int(usuario['id']),))
            borrados_usuarios += cursor.rowcount or 0
        conexion.commit()

    print('\n=== Resultado ===')
    print(f'productos borrados={borrados_productos} '
          f'comercios={borrados_comercios} usuarios={borrados_usuarios}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Repara fotos que quedaron apuntando al disco local de Render.

``static/uploads/`` es disco **efímero**: cada deploy lo borra y no se comparte
entre instancias. Medido en producción: 3 productos devolvían 404.

Para cada URL ``/static/uploads/...`` persistida:

- si el archivo existe en disco, se sube a Supabase Storage y se reescribe la
  URL (la foto se recupera);
- si no existe, la imagen queda nula y en estado ``pendiente`` para que el
  pipeline la vuelva a asignar (catálogo por EAN o fuentes).

Dry-run por defecto; con ``--apply`` aplica los cambios.

Uso::

    python scripts/reparar_imagenes_locales.py
    python scripts/reparar_imagenes_locales.py --apply
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

PREFIJO_LOCAL = '/static/uploads/'
CAMPOS = ('imagen_url', 'imagen_manual_url')


def _recuperar_a_storage(url_local):
    """Sube el archivo local a Storage y devuelve la URL pública, o None."""
    from backend.supabase_storage import intentar_subir_bytes
    from backend.uploads_locales import leer_bytes_upload

    data, filename, content_type = leer_bytes_upload(url_local)
    if not data:
        return None
    url, _aviso = intentar_subir_bytes(
        data, filename, content_type=content_type, carpeta='productos'
    )
    return url


def _filas_con_locales(cursor, maximo):
    cursor.execute(
        """
        SELECT id, nombre, imagen_url, imagen_manual_url
        FROM productos
        WHERE POSITION(? IN COALESCE(imagen_url, '')) = 1
           OR POSITION(? IN COALESCE(imagen_manual_url, '')) = 1
        ORDER BY id
        LIMIT ?
        """,
        (PREFIJO_LOCAL, PREFIJO_LOCAL, int(maximo)),
    )
    return [dict(fila) for fila in cursor.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--max', type=int, default=500)
    args = parser.parse_args()

    from backend.db import get_db_connection
    from backend.uploads_locales import archivo_upload_existe

    with get_db_connection(row_factory=True) as conexion:
        filas = _filas_con_locales(conexion.cursor(), args.max)

    if not filas:
        print('No hay fotos apuntando al disco local. Nada que reparar.')
        return 0

    recuperables = []
    perdidas = []
    for fila in filas:
        for campo in CAMPOS:
            valor = str(fila.get(campo) or '')
            if not valor.startswith(PREFIJO_LOCAL):
                continue
            if archivo_upload_existe(valor):
                recuperables.append((int(fila['id']), campo, valor))
            else:
                perdidas.append((int(fila['id']), campo, valor))

    total = len(recuperables) + len(perdidas)
    print(f'Fotos en disco local: {total} ({len(recuperables)} recuperables, '
          f'{len(perdidas)} perdidas)')
    for _pid, _campo, valor in recuperables[:20]:
        print(f'  recuperable: {valor}')
    for pid, _campo, valor in perdidas[:20]:
        print(f'  perdida: id={pid} {valor}')

    if not args.apply:
        print('\nDry-run: nada se modificó. Repite con --apply.')
        return 0

    ids_pendientes = {pid for pid, _c, _v in perdidas}
    subidas = {}
    fallos = 0
    for pid, campo, valor in recuperables:
        url = _recuperar_a_storage(valor)
        if url:
            subidas.setdefault(pid, {})[campo] = url
            print(f'  recuperada id={pid} -> {url}')
        else:
            fallos += 1
            ids_pendientes.add(pid)
            print(f'  no se pudo subir id={pid} {valor}')

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        for pid, campos in subidas.items():
            for campo, url in campos.items():
                cursor.execute(
                    f'UPDATE productos SET {campo} = ? WHERE id = ?', (url, pid)
                )
        if ids_pendientes:
            lista = sorted(ids_pendientes)
            placeholders = ', '.join('?' for _ in lista)
            cursor.execute(
                f"""
                UPDATE productos
                SET imagen_url = NULL, imagen_fuente = NULL, imagen_estado = 'pendiente'
                WHERE id IN ({placeholders})
                """,
                tuple(lista),
            )
        conexion.commit()

    print(f'\nRecuperadas a Storage: {len(subidas)} ({fallos} fallo(s))')
    print(f'Marcadas pendientes (archivo perdido): {len(ids_pendientes)}')
    print('El pipeline reasignará las pendientes (catálogo por EAN o fuentes).')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
#!/usr/bin/env python3
"""
Repara imágenes **rotas** (URLs persistidas cuyo archivo ya no existe).

Detecta URLs de Supabase Storage que responden 400/404 y las deja en estado
``pendiente`` (imagen nula) para que el pipeline las vuelva a descargar/subir.
Nunca toca URLs que responden correctamente (200/2xx) ni hosts que bloquean
verificaciones automáticas (403/405).

Dry-run por defecto; con ``--apply`` aplica los cambios.

Uso::

    python scripts/reparar_imagenes_rotas.py
    python scripts/reparar_imagenes_rotas.py --apply
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass

_BLOQUEOS_BOT = (401, 403, 405, 429)
# Errores de red que indican host/objeto inalcanzable (no un bloqueo de bots).
_ERRORES_RED_ROTA = ('ConnectError', 'ConnectTimeout', 'ReadError', 'RemoteProtocolError', 'InvalidURL')


def _verificar(url):
    import httpx

    try:
        with httpx.Client(timeout=httpx.Timeout(8.0, connect=4.0), follow_redirects=True) as http:
            resp = http.head(url)
            if resp.status_code >= 400:
                resp = http.get(url, headers={'Range': 'bytes=0-64'})
            return url, resp.status_code
    except Exception as error:
        return url, type(error).__name__


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--comercio', type=int, default=None)
    parser.add_argument('--max', type=int, default=400)
    parser.add_argument(
        '--incluir-maestro',
        action='store_true',
        help='También elimina entradas rotas del catálogo maestro global.',
    )
    args = parser.parse_args()

    from backend.db import get_db_connection

    with get_db_connection(row_factory=True) as conexion:
        cursor = conexion.cursor()
        query = """
            SELECT id, imagen_url FROM productos
            WHERE POSITION('http' IN imagen_url) = 1
        """
        params = []
        if args.comercio:
            query += ' AND comercio_id = ?'
            params.append(int(args.comercio))
        query += ' ORDER BY id DESC LIMIT ?'
        params.append(int(args.max))
        cursor.execute(query, tuple(params))
        filas = [dict(f) for f in cursor.fetchall()]

    if not filas:
        print('No hay imágenes de Storage para verificar.')
        return 0

    print(f'Verificando {len(filas)} imagen(es) de Storage...')
    with ThreadPoolExecutor(max_workers=8) as pool:
        resultados = list(pool.map(_verificar, [f['imagen_url'] for f in filas]))

    rotas = []
    for fila, (_url, status) in zip(filas, resultados):
        if isinstance(status, int) and status >= 400 and status not in _BLOQUEOS_BOT:
            rotas.append(fila)
        elif isinstance(status, str) and status in _ERRORES_RED_ROTA:
            rotas.append(fila)

    print(f'Rotos (400/404): {len(rotas)}')
    for fila in rotas[:20]:
        print(f"  id={fila['id']} {str(fila['imagen_url'])[:90]}")

    if not args.apply:
        print('\nDry-run: nada se modificó. Repite con --apply.')
        return 0
    if not rotas:
        print('Nada que reparar.')
        return 0

    ids = [int(f['id']) for f in rotas]
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
    print(f'\nImágenes rotas marcadas pendientes: {afectados}')

    if args.incluir_maestro:
        _limpiar_maestro_roto(get_db_connection, args.apply)
    print('El pipeline las reasignará (catálogo global por EAN o fuentes).')
    return 0


def _limpiar_maestro_roto(get_db_connection, apply):
    """Elimina de los caches globales las URLs HTTP que no responden."""
    from concurrent.futures import ThreadPoolExecutor as _Pool

    with get_db_connection(row_factory=True) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT codigo_barras, url_imagen FROM catalogo_maestro_imagenes
            WHERE POSITION('http' IN url_imagen) = 1
            """
        )
        filas = [dict(f) for f in cursor.fetchall()]
        cursor.execute(
            """
            SELECT clave, url_imagen FROM imagenes_automaticas
            WHERE POSITION('http' IN url_imagen) = 1
            """
        )
        filas_cache = [dict(f) for f in cursor.fetchall()]

    def _roto(status):
        return (isinstance(status, int) and status >= 400 and status not in _BLOQUEOS_BOT) or (
            isinstance(status, str) and status in _ERRORES_RED_ROTA
        )

    rotos_maestro = []
    if filas:
        with _Pool(max_workers=8) as pool:
            resultados = list(pool.map(_verificar, [f['url_imagen'] for f in filas]))
        rotos_maestro = [f['codigo_barras'] for f, (_u, st) in zip(filas, resultados) if _roto(st)]
        print(f'Catálogo maestro: {len(rotos_maestro)} entrada(s) rota(s) de {len(filas)}')

    rotos_cache = []
    if filas_cache:
        with _Pool(max_workers=8) as pool:
            resultados = list(pool.map(_verificar, [f['url_imagen'] for f in filas_cache]))
        rotos_cache = [f['clave'] for f, (_u, st) in zip(filas_cache, resultados) if _roto(st)]
        print(f'imagenes_automaticas: {len(rotos_cache)} URL(s) rota(s) de {len(filas_cache)}')

    if not apply or (not rotos_maestro and not rotos_cache):
        return
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        if rotos_maestro:
            for inicio in range(0, len(rotos_maestro), 200):
                lote = rotos_maestro[inicio : inicio + 200]
                ph = ', '.join('?' for _ in lote)
                cursor.execute(
                    f'DELETE FROM catalogo_maestro_imagenes WHERE codigo_barras IN ({ph})',
                    tuple(lote),
                )
            print(f'Catálogo maestro: {len(rotos_maestro)} entrada(s) rota(s) eliminada(s).')
        if rotos_cache:
            for inicio in range(0, len(rotos_cache), 200):
                lote = rotos_cache[inicio : inicio + 200]
                ph = ', '.join('?' for _ in lote)
                cursor.execute(
                    f'UPDATE imagenes_automaticas SET url_imagen = NULL, encontrada = 0 '
                    f'WHERE clave IN ({ph})',
                    tuple(lote),
                )
            print(f'imagenes_automaticas: {len(rotos_cache)} URL(s) rota(s) limpiada(s).')
        conexion.commit()


if __name__ == '__main__':
    raise SystemExit(main())

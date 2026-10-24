#!/usr/bin/env python3
"""
Genera las miniaturas de catálogo (``thumb_<nombre>``) de las fotos ya subidas.

La tarjeta de producto muestra la foto en una franja de 180 px de alto con
``object-fit: contain``, así que una miniatura de 400 px se ve igual y pesa unas
4x menos (medido: 32 KB -> 8 KB). El código ya genera la miniatura **al subir**
una foto nueva; este script es para las que ya estaban en el bucket.

Idempotente: salta las fotos que ya tienen miniatura, comprobada por el endpoint
``/object/info`` (la fuente autoritativa: ``HEAD`` miente y el CDN va con retraso).

Dry-run por defecto; con ``--apply`` aplica los cambios.

Uso::

    python scripts/generar_miniaturas.py
    python scripts/generar_miniaturas.py --apply
    python scripts/generar_miniaturas.py --apply --max 50
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import unquote, urlparse

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass

MARCA_STORAGE = '/storage/v1/object/public/'
CONSULTAS = (
    ('productos', 'imagen_url'),
    ('productos', 'imagen_manual_url'),
)


def _urls_persistidas():
    """URLs de fotos de producto en nuestro Storage."""
    from backend.db import get_db_connection

    urls = set()
    with get_db_connection(row_factory=True) as conexion:
        cursor = conexion.cursor()
        for tabla, columna in CONSULTAS:
            cursor.execute(
                f"""
                SELECT {columna} AS url FROM {tabla}
                WHERE POSITION(? IN COALESCE({columna}, '')) > 0
                """,
                (MARCA_STORAGE,),
            )
            urls.update(
                str(fila['url']).strip() for fila in cursor.fetchall() if fila['url']
            )
    return urls


def _partes(url):
    """(bucket, ruta) si la URL es de nuestro bucket; si no, (None, None)."""
    from backend.supabase_client import SUPABASE_BUCKET_IMAGENES, SUPABASE_URL

    try:
        trozos = urlparse(url)
        esperado = urlparse(SUPABASE_URL or '')
        if not esperado.netloc or trozos.netloc != esperado.netloc:
            return None, None
        if MARCA_STORAGE not in trozos.path:
            return None, None
        resto = trozos.path.split(MARCA_STORAGE, 1)[1]
        bucket, _, ruta = resto.partition('/')
        if not ruta or bucket != (SUPABASE_BUCKET_IMAGENES or 'imagenes'):
            return None, None
        return bucket, unquote(ruta)
    except Exception:
        return None, None


def _ruta_miniatura(ruta):
    """``productos/x.webp`` -> ``productos/thumb_x.webp`` (misma carpeta)."""
    carpeta, _, nombre = str(ruta).rpartition('/')
    if not nombre:
        return ''
    if nombre.startswith('thumb_'):
        return ruta
    return f'{carpeta}/thumb_{nombre}' if carpeta else f'thumb_{nombre}'


def _info(bucket, ruta):
    """Metadata del objeto (o None si no existe)."""
    import httpx

    from backend.supabase_client import SUPABASE_URL, headers_storage_service_role

    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as http:
            respuesta = http.get(
                f'{SUPABASE_URL}/storage/v1/object/info/{bucket}/{ruta}',
                headers=headers_storage_service_role({}),
            )
        return respuesta.json() if respuesta.status_code == 200 else None
    except Exception:
        return None


def _generar(item):
    """Descarga la foto, crea la miniatura y la sube. (url, aviso)."""
    import httpx

    from backend.images import crear_miniatura_producto
    from backend.supabase_storage import _subir_bytes_al_bucket

    url, ruta_mini = item
    try:
        with httpx.Client(
            timeout=httpx.Timeout(40.0, connect=6.0), follow_redirects=True
        ) as http:
            respuesta = http.get(url)
        if respuesta.status_code != 200:
            return url, f'GET {respuesta.status_code}'
        datos = respuesta.content
        miniatura = crear_miniatura_producto(datos)
        if not miniatura:
            return url, 'sin ganancia (ya es pequena o no se pudo comprimir)'
        _subir_bytes_al_bucket(
            None, ruta_mini, miniatura, 'image/webp', diferido=True
        )
        return url, f'OK ({len(datos)} -> {len(miniatura)} bytes)'
    except Exception as error:
        return url, f'{type(error).__name__}: {error}'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--max', type=int, default=0, help='0 = todas')
    args = parser.parse_args()

    urls = sorted(_urls_persistidas())
    if not urls:
        print('No hay fotos de producto en Storage. Nada que hacer.')
        return 0
    if args.max:
        urls = urls[: int(args.max)]

    candidatos = []
    ajenas = 0
    ya_tienen = 0
    for url in urls:
        bucket, ruta = _partes(url)
        if not ruta:
            ajenas += 1
            continue
        ruta_mini = _ruta_miniatura(ruta)
        if ruta_mini == ruta or _info(bucket, ruta_mini):
            ya_tienen += 1
            continue
        candidatos.append((url, ruta_mini))

    print(f'Fotos de producto en Storage: {len(urls)}')
    print(f'  ya con miniatura: {ya_tienen}')
    print(f'  por generar: {len(candidatos)}')
    if ajenas:
        print(f'  fuera de nuestro bucket (se omiten): {ajenas}')
    for url, ruta_mini in candidatos[:10]:
        print(f'    {ruta_mini}')

    if not args.apply:
        print('\nDry-run: nada se modificó. Repite con --apply.')
        return 0
    if not candidatos:
        print('Todas tienen miniatura. Nada que hacer.')
        return 0

    print(f'\nGenerando {len(candidatos)} miniatura(s)...')
    fallos = 0
    for item in candidatos:
        url, aviso = _generar(item)
        print(f'  {aviso} :: {url[:80]}')
        if not aviso.startswith('OK'):
            fallos += 1

    print('\nVerificando (metadata autoritativa)...')
    def _ok(item):
        bucket, ruta = _partes(item[0])
        return bool(_info(bucket, item[1]))

    with ThreadPoolExecutor(max_workers=6) as pool:
        confirmadas = sum(1 for x in pool.map(_ok, candidatos) if x)
    print(f'  con miniatura confirmada: {confirmadas}/{len(candidatos)}')
    if fallos:
        print(f'  avisos: {fallos}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
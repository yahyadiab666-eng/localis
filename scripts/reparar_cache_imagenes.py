#!/usr/bin/env python3
"""
Aplica el ``cache-control`` largo a las imágenes ya subidas a Storage.

Motivo (medido): los objetos subidos sin la opción devolvían ``Cache-Control:
no-cache``, así que el navegador volvía a pedir cada foto en cada visita. El
código ya sube con ``cache-control: max-age=31536000`` (Supabase lo sirve como
``public, max-age=31536000``); este script lo reaplica a lo subido antes:
**mismos bytes**, misma URL, ``x-upsert`` (no recompone ni cambia rutas).

Cómo se audita (importante, medido):

- **Fuente autoritativa:** ``GET /storage/v1/object/info/<bucket>/<ruta>``
  devuelve el JSON del objeto con ``"cache_control"``. Es la verdad.
- **El CDN va con retraso:** la URL pública puede seguir sirviendo la copia
  vieja (``cf-cache-status: HIT`` con ``no-cache``) hasta que Supabase invalida
  el borde. El cache-busting por query **no** lo evita: Smart CDN no lo respeta.
- **``HEAD`` no sirve:** responde ``private, no-cache`` y ``DYNAMIC`` aunque el
  objeto ya esté arreglado.

Es idempotente: salta los objetos cuyo ``cache_control`` ya es largo.

Dry-run por defecto; con ``--apply`` aplica los cambios.

Uso::

    python scripts/reparar_cache_imagenes.py
    python scripts/reparar_cache_imagenes.py --apply
    python scripts/reparar_cache_imagenes.py --apply --max 40
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
# Por debajo de una semana no vale la pena depender del CDN.
MIN_MAX_AGE = 604800
CONSULTAS = (
    ('productos', 'imagen_url'),
    ('productos', 'imagen_manual_url'),
    ('comercios', 'logo_url'),
    ('comercios', 'banner_url'),
    ('comercios', 'imagen_portada'),
    ('catalogo_maestro_imagenes', 'url_imagen'),
)


def _urls_persistidas():
    """Conjunto de URLs de nuestro Storage guardadas en la base."""
    from backend.db import get_db_connection

    urls = set()
    with get_db_connection(row_factory=True) as conexion:
        cursor = conexion.cursor()
        for tabla, columna in CONSULTAS:
            try:
                cursor.execute(
                    f"""
                    SELECT {columna} AS url FROM {tabla}
                    WHERE POSITION(? IN COALESCE({columna}, '')) > 0
                    """,
                    (MARCA_STORAGE,),
                )
                urls.update(
                    str(fila['url']).strip()
                    for fila in cursor.fetchall()
                    if fila['url']
                )
            except Exception as error:
                print(f'  aviso: {tabla}.{columna} omitido ({type(error).__name__})')
    return urls


def _partes(url):
    """(bucket, ruta) si la URL es de nuestro bucket; si no, (None, None)."""
    from backend.supabase_client import SUPABASE_BUCKET_IMAGENES, SUPABASE_URL

    try:
        partes = urlparse(url)
        esperado = urlparse(SUPABASE_URL or '')
        if not esperado.netloc or partes.netloc != esperado.netloc:
            return None, None
        if MARCA_STORAGE not in partes.path:
            return None, None
        resto = partes.path.split(MARCA_STORAGE, 1)[1]
        bucket, _, ruta = resto.partition('/')
        if not ruta or bucket != (SUPABASE_BUCKET_IMAGENES or 'imagenes'):
            return None, None
        return bucket, unquote(ruta)
    except Exception:
        return None, None


def _info_objeto(url):
    """Metadata autoritativa: dict del objeto, o None."""
    import httpx

    from backend.supabase_client import SUPABASE_URL, headers_storage_service_role

    bucket, ruta = _partes(url)
    if not ruta:
        return None
    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as http:
            respuesta = http.get(
                f'{SUPABASE_URL}/storage/v1/object/info/{bucket}/{ruta}',
                headers=headers_storage_service_role({}),
            )
        if respuesta.status_code != 200:
            return None
        datos = respuesta.json()
        return datos if isinstance(datos, dict) else None
    except Exception:
        return None


def _cache_control_publico(url):
    """Cabecera que sirve el CDN ahora mismo (informativa; puede ir con retraso)."""
    import httpx

    try:
        with httpx.Client(
            timeout=httpx.Timeout(15.0, connect=5.0), follow_redirects=True
        ) as http:
            respuesta = http.get(url)
        return respuesta.headers.get('cache-control', '')
    except Exception as error:
        return f'error:{type(error).__name__}'


def _necesita_arreglo(valor):
    """True si no hay ``max-age`` o es corto (p. ej. 'no-cache')."""
    if not valor or str(valor).startswith('error:'):
        return True
    for trozo in str(valor).lower().split(','):
        trozo = trozo.strip()
        if trozo.startswith('max-age='):
            try:
                return int(trozo.split('=', 1)[1]) < MIN_MAX_AGE
            except ValueError:
                return True
    return True


def _reaplicar(item):
    """Re-sube los mismos bytes con el cache-control largo. (url, aviso)."""
    import httpx

    from backend.supabase_storage import CACHE_CONTROL_IMAGENES, _subir_bytes_al_bucket

    url, ruta, content_type = item
    try:
        with httpx.Client(
            timeout=httpx.Timeout(25.0, connect=5.0), follow_redirects=True
        ) as http:
            respuesta = http.get(url)
        if respuesta.status_code != 200:
            return url, f'GET {respuesta.status_code}'
        _subir_bytes_al_bucket(
            None, ruta, respuesta.content, content_type or 'image/webp', diferido=True
        )
        return url, f'OK ({len(respuesta.content)} bytes, {CACHE_CONTROL_IMAGENES})'
    except Exception as error:
        return url, f'{type(error).__name__}: {error}'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--max', type=int, default=0, help='0 = todas')
    args = parser.parse_args()

    urls = sorted(_urls_persistidas())
    if not urls:
        print('No hay URLs de Storage en la base. Nada que revisar.')
        return 0
    if args.max:
        urls = urls[: int(args.max)]

    print(f'Auditando metadata de {len(urls)} objeto(s) (fuente autoritativa)...')
    with ThreadPoolExecutor(max_workers=6) as pool:
        infos = list(zip(urls, pool.map(_info_objeto, urls)))

    por_arreglar = []
    sin_info = 0
    ajenas = 0
    for url, info in infos:
        if info is None:
            sin_info += 1
            continue
        if not _necesita_arreglo(info.get('cache_control')):
            continue
        bucket, ruta = _partes(url)
        if not ruta:
            ajenas += 1
            continue
        por_arreglar.append((url, ruta, str(info.get('content_type') or '')))

    print(f'  ya con caché largo: {len(infos) - len(por_arreglar) - sin_info - ajenas}')
    print(f'  por arreglar: {len(por_arreglar)}')
    if sin_info:
        print(f'  sin metadata legible: {sin_info}')
    if ajenas:
        print(f'  fuera de nuestro bucket (se omiten): {ajenas}')
    for url, _ruta, _tipo in por_arreglar[:10]:
        print(f'    {url[:100]}')

    if not args.apply:
        print('\nDry-run: nada se modificó. Repite con --apply.')
        return 0
    if not por_arreglar:
        print('Todo con caché largo en Storage. Nada que hacer.')
        return 0

    print(f'\nReaplicando cache-control a {len(por_arreglar)} objeto(s)...')
    fallos = 0
    for item in por_arreglar:
        url, aviso = _reaplicar(item)
        print(f'  {aviso} :: {url[:90]}')
        if not aviso.startswith('OK'):
            fallos += 1

    print('\nVerificando metadata (autoritativa)...')
    con_url = [u for u, _r, _t in por_arreglar]
    with ThreadPoolExecutor(max_workers=6) as pool:
        despues = list(
            pool.map(lambda u: (_info_objeto(u) or {}).get('cache_control'), con_url)
        )
    bien = sum(1 for valor in despues if not _necesita_arreglo(valor))
    print(f'  corregidos en Storage: {bien}/{len(por_arreglar)}')
    if fallos:
        print(f'  fallos al re-subir: {fallos}')

    print('\nInformativo: cabecera que sirve el CDN ahora mismo (puede ir con retraso)')
    with ThreadPoolExecutor(max_workers=6) as pool:
        publicos = list(pool.map(_cache_control_publico, con_url))
    al_dia = sum(1 for valor in publicos if not _necesita_arreglo(valor))
    print(f'  el CDN ya sirve caché largo en {al_dia}/{len(con_url)} (el resto se '
          f'invalidará solo; Supabase tarda minutos)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
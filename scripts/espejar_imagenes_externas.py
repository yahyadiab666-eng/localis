#!/usr/bin/env python3
"""
Espeja en Storage las fotos de producto que hoy apuntan a terceros (hotlink).

Motivo (medido): 83 productos enlazan imágenes externas de 105-310 KB con
cabeceras irregulares y sin copia propia. Si el tercero cambia o bloquea el
archivo, el producto queda sin foto (por eso existe reparar_imagenes_rotas.py).

Qué hace, por producto: descarga la imagen, la reencoda a WebP de 800 px máximo
(sin recortar), la sube al bucket como ``espejo_<id>_<sha1>.webp`` y actualiza
``productos.imagen_url``. Como la subida pasa por el camino normal, la foto
espejada **también recibe su miniatura**.

Idempotente: el nombre lleva el hash del contenido y la URL ya espejada no se
vuelve a tocar.

Dry-run por defecto; con ``--apply`` aplica los cambios.

Uso::

    python scripts/espejar_imagenes_externas.py
    python scripts/espejar_imagenes_externas.py --apply
    python scripts/espejar_imagenes_externas.py --apply --max 20
"""

from __future__ import annotations

import argparse
import hashlib
import io
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

MARCA_STORAGE = '/storage/v1/object/public/'
MAX_BYTES_DESCARGA = 8 * 1024 * 1024
NO_ES_IMAGEN = 'no es una imagen valida'


def _productos_externos(cursor, maximo):
    cursor.execute(
        """
        SELECT id, nombre, imagen_url
        FROM productos
        WHERE POSITION('http' IN COALESCE(imagen_url, '')) = 1
          AND POSITION(? IN COALESCE(imagen_url, '')) = 0
        ORDER BY id
        LIMIT ?
        """,
        (MARCA_STORAGE, int(maximo)),
    )
    return [dict(fila) for fila in cursor.fetchall()]


def _descargar(url):
    """(bytes, content_type) o (None, motivo).

    Usa el cliente HTTP robusto del repositorio (rotación de User-Agent,
    backoff exponencial, proxies opcionales): muchos hosts bloquean al
    datacenter y un GET simple falla con 403 o corta la conexión.
    """
    from urllib.parse import urlparse

    from backend.http_client import get as http_get

    partes = urlparse(url)
    referer = f'{partes.scheme}://{partes.netloc}/' if partes.netloc else None
    try:
        respuesta = http_get(url, tipo='img', referer=referer, timeout=25.0, reintentos=3)
        if respuesta is None:
            return None, 'sin respuesta'
        if respuesta.status_code != 200:
            return None, f'HTTP {respuesta.status_code}'
        datos = respuesta.content
        if not datos:
            return None, 'vacia'
        if len(datos) > MAX_BYTES_DESCARGA:
            return None, f'demasiado grande ({len(datos)} bytes)'
        return datos, respuesta.headers.get('content-type') or ''
    except Exception as error:
        return None, f'{type(error).__name__}'


def _espejar(fila):
    """Descarga, recomprime y sube. Devuelve (id, url_nueva, aviso, bytes)."""
    from PIL import Image

    from backend.images import comprimir_bytes_a_bytes
    from backend.supabase_storage import intentar_subir_bytes

    pid = int(fila['id'])
    original = str(fila['imagen_url'])
    datos, tipo = _descargar(original)
    if not datos:
        return pid, None, f'no descargada ({tipo})', 0
    try:
        imagen = Image.open(io.BytesIO(datos))
        imagen.load()
    except Exception:
        return pid, None, NO_ES_IMAGEN, 0
    del imagen, tipo
    try:
        comprimido, _tipo, _nombre = comprimir_bytes_a_bytes(
            datos, prefijo='espejo', max_dimension=800, quality=80, formato='WEBP'
        )
    except Exception as error:
        return pid, None, f'compresion ({type(error).__name__})', 0
    nombre = f'espejo_{pid}_{hashlib.sha1(comprimido).hexdigest()[:10]}.webp'
    url, aviso = intentar_subir_bytes(
        comprimido, nombre, content_type='image/webp', carpeta='productos'
    )
    if not url:
        return pid, None, f'sin subir ({aviso or "Storage"})', len(comprimido)
    del aviso
    return pid, url, 'OK', len(comprimido)


def _actualizar_urls(conexion, espejadas, no_imagenes):
    """Aplica las URLs espejadas y devuelve al pipeline las que no eran imagen.

    La guarda no compara la URL exacta sino que la fila **siga apuntando a un
    tercero**: el backfill de imágenes reasigna URLs del catálogo maestro en
    paralelo, y aquí sí queremos sustituir esa reasignación.

    Además marca ``imagen_estado='real'``: sin eso, el backfill vuelve a resolver
    el producto desde el catálogo maestro (URL externa) y pisa el espejo.
    """
    aplicadas = 0
    cursor = conexion.cursor()
    for pid, url in espejadas:
        cursor.execute(
            """
            UPDATE productos
            SET imagen_url = ?, imagen_estado = 'real'
            WHERE id = ?
              AND POSITION('http' IN COALESCE(imagen_url, '')) = 1
              AND POSITION(? IN COALESCE(imagen_url, '')) = 0
            """,
            (url, pid, MARCA_STORAGE),
        )
        aplicadas += int(cursor.rowcount or 0)
    for pid in no_imagenes:
        cursor.execute(
            """
            UPDATE productos
            SET imagen_url = NULL, imagen_fuente = NULL, imagen_estado = 'pendiente'
            WHERE id = ?
              AND POSITION('http' IN COALESCE(imagen_url, '')) = 1
              AND POSITION(? IN COALESCE(imagen_url, '')) = 0
            """,
            (pid, MARCA_STORAGE),
        )
    conexion.commit()
    return aplicadas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--max', type=int, default=300)
    args = parser.parse_args()

    from backend.db import get_db_connection

    with get_db_connection(row_factory=True) as conexion:
        filas = _productos_externos(conexion.cursor(), args.max)

    if not filas:
        print('No hay fotos externas por espejar. Nada que hacer.')
        return 0

    print(f'Fotos externas detectadas: {len(filas)}')
    for fila in filas[:10]:
        print(f"  id={fila['id']} {str(fila['imagen_url'])[:95]}")

    if not args.apply:
        print('\nDry-run: nada se modificó. Repite con --apply.')
        return 0

    print(f'\nEspejando {len(filas)} foto(s) a Storage...')
    resultados = []
    for fila in filas:
        pid, url, aviso, tamano = _espejar(fila)
        resultados.append((pid, url, aviso, tamano, str(fila['imagen_url'])))
        print(f"  id={pid} {aviso} {f'({tamano} bytes)' if tamano else ''}")

    espejadas = [(pid, url) for pid, url, aviso, _t, _original in resultados if url]
    no_imagenes = [
        pid for pid, _u, aviso, _t, _original in resultados if aviso == NO_ES_IMAGEN
    ]

    aplicadas = 0
    if espejadas or no_imagenes:
        with get_db_connection() as conexion:
            aplicadas = _actualizar_urls(conexion, espejadas, no_imagenes)
    print(f'\nActualizadas en la base: {aplicadas} de {len(espejadas)} espejadas')
    saltadas = len(espejadas) - aplicadas
    if saltadas:
        print(
            f'  {saltadas} ya no apuntaban a un tercero: el pipeline de imagenes '
            'las resolvio antes (no se pisa un cambio ajeno)'
        )

    sin_espejar = [r for r in resultados if not r[1] and r[2] != NO_ES_IMAGEN]
    if sin_espejar:
        print(f'Sin espejar: {len(sin_espejar)}')
        for pid, _url, aviso, _t, original in sin_espejar[:15]:
            print(f'  id={pid} {aviso} :: {original[:80]}')
        print('  (hosts que bloquean al datacenter; en el navegador siguen viendose)')

    if no_imagenes:
        print(f'\nDevueltas al pipeline (no eran imagenes): {no_imagenes}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
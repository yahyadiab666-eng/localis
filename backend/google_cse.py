"""Cliente de **Google Custom Search JSON API** (búsqueda de imágenes).

Condicionado por variables de entorno; si falta alguna, queda deshabilitado y no
realiza ninguna llamada (no se gasta cuota):

    GOOGLE_CSE_API_KEY  (alias: GOOGLE_CSE_KEY, GOOGLE_API_KEY)
    GOOGLE_CSE_CX       (alias: GOOGLE_CSE_ID)

Todas las llamadas están protegidas con ``try/except``: ante cualquier error de
red, cuota o formato, devuelve ``[]`` y registra el motivo. Nunca lanza.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

ENDPOINT = 'https://www.googleapis.com/customsearch/v1'
_LOG = '[Localis GoogleCSE]'


def clave_cse():
    """(api_key, cx) desde el entorno, admitiendo alias."""
    api_key = (
        os.getenv('GOOGLE_CSE_API_KEY')
        or os.getenv('GOOGLE_CSE_KEY')
        or os.getenv('GOOGLE_API_KEY')
        or ''
    ).strip()
    cx = (
        os.getenv('GOOGLE_CSE_CX')
        or os.getenv('GOOGLE_CSE_ID')
        or ''
    ).strip()
    return api_key, cx


def habilitado():
    """True si hay clave y motor configurados (no implica conectividad)."""
    api_key, cx = clave_cse()
    return bool(api_key and cx)


def _dominio(url):
    try:
        return (urlparse(str(url or '')).hostname or '').lower()
    except Exception:
        return ''


def buscar_imagenes(consulta, limite=8, *, timeout=12.0):
    """Resultados de imagen de Google CSE.

    Devuelve una lista de diccionarios ``{url, titulo, dominio, ancho, alto,
    contexto}``. Ante cualquier fallo (sin clave, error HTTP, cuota agotada,
    JSON inválido) devuelve ``[]``.
    """
    api_key, cx = clave_cse()
    consulta = ' '.join(str(consulta or '').split()).strip()
    if not api_key or not cx or not consulta:
        return []

    try:
        from backend.http_client import get as http_get

        respuesta = http_get(
            ENDPOINT,
            params={
                'key': api_key,
                'cx': cx,
                'q': consulta,
                'searchType': 'image',
                'num': max(1, min(10, int(limite))),
                'safe': 'off',
            },
            timeout=timeout,
            tipo='json',
        )
    except Exception as error:
        print(f'{_LOG} excepción consultando {consulta!r}: {type(error).__name__}: {error}')
        return []

    if respuesta is None:
        return []
    if getattr(respuesta, 'status_code', 0) != 200:
        detalle = ''
        try:
            detalle = (respuesta.json() or {}).get('error', {}).get('message', '')
        except Exception:
            detalle = ''
        print(
            f'{_LOG} HTTP {getattr(respuesta, "status_code", "?")} '
            f'para {consulta!r}: {detalle[:160]}'
        )
        return []

    try:
        datos = respuesta.json() or {}
    except Exception as error:
        print(f'{_LOG} JSON inválido: {type(error).__name__}')
        return []

    resultados = []
    vistos = set()
    for item in datos.get('items') or []:
        if not isinstance(item, dict):
            continue
        imagen = item.get('image') or {}
        url = item.get('link') or imagen.get('thumbnailLink')
        if not url or url in vistos:
            continue
        vistos.add(url)
        resultados.append(
            {
                'url': url,
                'titulo': str(item.get('title') or ''),
                'dominio': _dominio(url),
                'ancho': imagen.get('width'),
                'alto': imagen.get('height'),
                'contexto': str(item.get('snippet') or ''),
            }
        )
        if len(resultados) >= limite:
            break
    return resultados


def buscar_por_codigo(codigo_barras, limite=8):
    """Priority 1: búsqueda por EAN/UPC exacto."""
    codigo = str(codigo_barras or '').strip()
    if not codigo:
        return []
    return buscar_imagenes(codigo, limite=limite)


def buscar_por_nombre_descripcion(nombre, descripcion=None, limite=8):
    """Priority 2: consulta optimizada ``nombre + descripción corta``."""
    partes = [str(nombre or '').strip()]
    desc = ' '.join(str(descripcion or '').split())
    if desc:
        partes.append(desc[:80])
    consulta = ' '.join(p for p in partes if p).strip()
    if not consulta:
        return []
    return buscar_imagenes(consulta, limite=limite)

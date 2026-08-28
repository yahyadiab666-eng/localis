"""Cliente PostgREST ligero (httpx) sin SDK de Supabase — catálogo maestro."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

from backend.supabase_client import (
    SUPABASE_KEY,
    SUPABASE_URL,
    SUPABASE_URL_VALIDA,
    abrir_circuito_postgrest,
    es_error_red_supabase,
)

LOG_PREFIX = '[Localis PostgREST]'
TABLA_CATALOGO_MAESTRO = 'catalogo_maestro_imagenes'
_CONNECT_TIMEOUT = 3.0
_READ_TIMEOUT = 8.0


def postgrest_http_configurado() -> bool:
    return bool(SUPABASE_URL_VALIDA and SUPABASE_URL and SUPABASE_KEY)


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(
        connect=_CONNECT_TIMEOUT,
        read=_READ_TIMEOUT,
        write=_READ_TIMEOUT,
        pool=_CONNECT_TIMEOUT,
    )


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Accept': 'application/json',
    }
    if extra:
        headers.update(extra)
    return headers


def _url_rest(ruta: str) -> str:
    base = SUPABASE_URL.rstrip('/')
    ruta_limpia = ruta if ruta.startswith('/') else f'/{ruta}'
    return f'{base}{ruta_limpia}'


def _filtro_in_codigos(codigos: list[str]) -> str:
    """PostgREST: codigo_barras=in.("a","b")"""
    partes = []
    for codigo in codigos:
        texto = str(codigo).replace('\\', '\\\\').replace('"', '\\"')
        partes.append(f'"{texto}"')
    return f'in.({",".join(partes)})'


def _get_json(url: str, params: dict[str, str] | None = None) -> httpx.Response:
    with httpx.Client(timeout=_timeout(), follow_redirects=True) as cliente:
        return cliente.get(url, headers=_headers(), params=params or {})


def _manejar_fallo_red(error: Exception, contexto: str) -> None:
    if es_error_red_supabase(error):
        abrir_circuito_postgrest(
            f'{contexto}: {type(error).__name__} hacia {SUPABASE_URL}'
        )
        print(
            f'{LOG_PREFIX} Red no alcanzable ({type(error).__name__}). '
            'Catálogo maestro continúa vía PostgreSQL directo.'
        )
    else:
        print(f'{LOG_PREFIX} Error en {contexto}: {type(error).__name__}: {error}')


def consultar_imagen_maestro(codigo: str) -> str | None:
    """SELECT url_imagen por codigo_barras vía GET /rest/v1/."""
    if not postgrest_http_configurado():
        return None

    codigo_q = quote(str(codigo), safe='')
    url = _url_rest(f'/rest/v1/{TABLA_CATALOGO_MAESTRO}')

    try:
        respuesta = _get_json(
            url,
            {
                'select': 'url_imagen',
                'codigo_barras': f'eq.{codigo_q}',
                'limit': '1',
            },
        )
        if respuesta.status_code >= 500:
            raise httpx.HTTPStatusError(
                'PostgREST error',
                request=respuesta.request,
                response=respuesta,
            )
        if respuesta.status_code not in (200, 206):
            return None
        datos = respuesta.json()
        if not datos:
            return None
        return (datos[0] or {}).get('url_imagen')
    except Exception as error:
        _manejar_fallo_red(error, f'consulta {codigo}')
        return None


def consultar_mapa_maestro(codigos: list[str]) -> dict[str, str]:
    """SELECT lote codigo_barras, url_imagen vía PostgREST."""
    if not postgrest_http_configurado() or not codigos:
        return {}

    filtro = _filtro_in_codigos(codigos)
    url = _url_rest(f'/rest/v1/{TABLA_CATALOGO_MAESTRO}')

    try:
        respuesta = _get_json(
            url,
            {
                'select': 'codigo_barras,url_imagen',
                'codigo_barras': filtro,
            },
        )
        if respuesta.status_code >= 500:
            raise httpx.HTTPStatusError(
                'PostgREST error',
                request=respuesta.request,
                response=respuesta,
            )
        if respuesta.status_code not in (200, 206):
            return {}
        resultado: dict[str, str] = {}
        for fila in respuesta.json() or []:
            codigo = (fila or {}).get('codigo_barras')
            imagen = (fila or {}).get('url_imagen')
            if codigo and imagen and codigo not in resultado:
                resultado[str(codigo)] = str(imagen)
        return resultado
    except Exception as error:
        _manejar_fallo_red(error, f'lote ({len(codigos)} códigos)')
        return {}


def guardar_imagen_maestro_http(codigo: str, url_imagen: str) -> bool:
    """UPSERT vía POST /rest/v1/ con Prefer: resolution=merge-duplicates."""
    if not postgrest_http_configurado():
        return False

    payload = [{'codigo_barras': codigo, 'url_imagen': url_imagen}]
    url = _url_rest(f'/rest/v1/{TABLA_CATALOGO_MAESTRO}')
    headers = _headers(
        {
            'Content-Type': 'application/json',
            'Prefer': 'resolution=merge-duplicates,return=minimal',
        }
    )

    try:
        with httpx.Client(timeout=_timeout(), follow_redirects=True) as cliente:
            respuesta = cliente.post(url, headers=headers, content=json.dumps(payload))
        if respuesta.status_code in (200, 201, 204):
            return True
        if respuesta.status_code >= 500:
            raise httpx.HTTPStatusError(
                'PostgREST upsert error',
                request=respuesta.request,
                response=respuesta,
            )
        print(
            f'{LOG_PREFIX} Upsert rechazado HTTP {respuesta.status_code}: '
            f'{respuesta.text[:200]}'
        )
        return False
    except Exception as error:
        _manejar_fallo_red(error, f'upsert {codigo}')
        return False

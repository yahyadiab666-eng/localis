"""Cliente HTTP con camuflaje y reintentos (anti-bloqueo de datacenter).

Técnicas:
  - Rotación de User-Agents de navegadores reales.
  - Cabeceras de navegador completas (Accept, Accept-Language, Sec-Fetch-*…).
  - Reintentos con backoff exponencial ante 429/5xx y errores de red.
  - Soporte opcional de proxies HTTP(S) vía variables de entorno
    (``LOCALIS_HTTP_PROXY`` / ``HTTPS_PROXY``), útil cuando la IP del VPS está
    bloqueada por buscadores/CDN.

Todas las peticiones de búsqueda/descarga del pipeline pasan por aquí, de modo
que el camuflaje y los reintentos se aplican de forma uniforme.
"""

from __future__ import annotations

import os
import random
import time

import requests

_UA_POOL = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Version/17.4 Safari/605.1.15',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 '
    '(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Linux; Android 14; SM-S911B) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36',
)

_ACCEPT_HTML = (
    'text/html,application/xhtml+xml,application/xml;q=0.9,'
    'image/avif,image/webp,image/apng,*/*;q=0.8'
)
_ACCEPT_JSON = 'application/json, text/plain, */*'
_ACCEPT_IMG = 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8'
_ACCEPT_LANG = 'es-VE,es-419;q=0.9,es;q=0.8,en-US;q=0.7,en;q=0.6'

_REINTENTOS_DEFECTO = max(1, int(os.getenv('LOCALIS_HTTP_REINTENTOS', '3')))
_BACKOFF_BASE = float(os.getenv('LOCALIS_HTTP_BACKOFF_SEG', '0.4'))


def user_agent():
    return random.choice(_UA_POOL)


def proxies():
    """Proxy HTTP(S) opcional desde el entorno (para saltar bloqueos de IP)."""
    proxy = (
        os.getenv('LOCALIS_HTTP_PROXY')
        or os.getenv('HTTPS_PROXY')
        or os.getenv('https_proxy')
        or os.getenv('HTTP_PROXY')
        or os.getenv('http_proxy')
    )
    proxy = (proxy or '').strip()
    if not proxy:
        return None
    return {'http': proxy, 'https': proxy}


def cabeceras(extra=None, *, tipo='html', referer=None):
    base = {
        'User-Agent': user_agent(),
        'Accept': {'html': _ACCEPT_HTML, 'json': _ACCEPT_JSON, 'image': _ACCEPT_IMG}.get(
            tipo, _ACCEPT_HTML
        ),
        'Accept-Language': _ACCEPT_LANG,
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
        'DNT': '1',
        'Connection': 'keep-alive',
    }
    if tipo == 'html':
        base.update(
            {
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
            }
        )
    if referer:
        base['Referer'] = referer
    for clave, valor in (extra or {}).items():
        if valor is None:
            continue
        # La rotación de UA manda sobre cualquier UA fijo del llamador.
        if clave.lower() == 'user-agent':
            continue
        base[clave] = valor
    return base


def get(url, *, params=None, headers=None, timeout=10.0, stream=False,
        reintentos=None, tipo='html', referer=None, **kwargs):
    """GET con rotación de UA y reintentos con backoff exponencial."""
    intentos = max(1, int(reintentos or _REINTENTOS_DEFECTO))
    cabeceras_base = dict(headers or {})
    ultimo = None
    for intento in range(intentos):
        cabecera = cabeceras(
            cabeceras_base,
            tipo=(
                'json'
                if 'application/json' in str(cabeceras_base.get('Accept', '')).lower()
                else tipo
            ),
            referer=referer,
        )
        try:
            kwargs.setdefault('allow_redirects', True)
            respuesta = requests.get(
                url,
                params=params,
                headers=cabecera,
                timeout=timeout,
                stream=stream,
                proxies=proxies(),
                **kwargs,
            )
            ultimo = respuesta
            if respuesta.status_code in (403, 429, 500, 502, 503, 504) and intento + 1 < intentos:
                time.sleep(_BACKOFF_BASE * (2 ** intento))
                continue
            return respuesta
        except Exception:
            if intento + 1 >= intentos:
                raise
            time.sleep(_BACKOFF_BASE * (2 ** intento))
    return ultimo

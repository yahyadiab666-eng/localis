"""Cliente de **Google Custom Search JSON API** (búsqueda de imágenes).

Variables de entorno (se admiten alias):

    GOOGLE_SEARCH_API_KEY   (alias: GOOGLE_CSE_API_KEY, GOOGLE_CSE_KEY, GOOGLE_API_KEY)
    GOOGLE_SEARCH_CX        (alias: GOOGLE_CSE_CX, GOOGLE_CSE_ID)
    LOCALIS_GOOGLE_CSE_LIMITE_DIARIO=100     # tope diario del plan gratuito
    LOCALIS_GOOGLE_CSE_COOLDOWN_SEG=86400    # pausa tras agotar cuota/429

Comportamiento ante errores (nunca lanza):
  - Sin clave/CX: deshabilitado, cero llamadas.
  - HTTP 429 o error de cuota (403/quota): marca la cuota como agotada, registra
    una advertencia y devuelve ``[]`` sin propagar la excepción.
  - Tope diario alcanzado: no llama más hasta el cambio de día.
  - Cualquier otra excepción de red/formato: devuelve ``[]``.
"""

from __future__ import annotations

import datetime as _dt
import os
import threading
from urllib.parse import urlparse

ENDPOINT = 'https://www.googleapis.com/customsearch/v1'
_LOG = '[Localis GoogleCSE]'

_lock = threading.Lock()
_cuota_agotada_hasta = 0.0          # timestamp epoch hasta el que no se consulta
_config_invalida_hasta = 0.0        # 400/401/403: clave/API mal configurada
_llamadas_dia = 0
_dia_actual = None


def clave_cse():
    """(api_key, cx) desde el entorno, admitiendo alias."""
    api_key = (
        os.getenv('GOOGLE_SEARCH_API_KEY')
        or os.getenv('GOOGLE_CSE_API_KEY')
        or os.getenv('GOOGLE_CSE_KEY')
        or os.getenv('GOOGLE_API_KEY')
        or ''
    ).strip()
    cx = (
        os.getenv('GOOGLE_SEARCH_CX')
        or os.getenv('GOOGLE_CSE_CX')
        or os.getenv('GOOGLE_CSE_ID')
        or ''
    ).strip()
    return api_key, cx


def habilitado():
    """True si hay clave y motor configurados (no implica conectividad)."""
    api_key, cx = clave_cse()
    return bool(api_key and cx)


def _limite_diario():
    try:
        return max(0, int(str(os.getenv('LOCALIS_GOOGLE_CSE_LIMITE_DIARIO', '100')).strip() or 100))
    except (TypeError, ValueError):
        return 100


def _cooldown_seg():
    try:
        return max(60, int(str(os.getenv('LOCALIS_GOOGLE_CSE_COOLDOWN_SEG', '86400')).strip() or 86400))
    except (TypeError, ValueError):
        return 86400


def _cooldown_config_seg():
    try:
        return max(
            60,
            int(str(os.getenv('LOCALIS_GOOGLE_CSE_CONFIG_COOLDOWN_SEG', '3600')).strip() or 3600),
        )
    except (TypeError, ValueError):
        return 3600


def _ahora():
    return _dt.datetime.now().timestamp()


def _hoy():
    return _dt.date.today()


def _reiniciar_si_nuevo_dia():
    global _llamadas_dia, _dia_actual
    hoy = _hoy()
    if _dia_actual != hoy:
        _dia_actual = hoy
        _llamadas_dia = 0


def cuota_agotada():
    """True si no se debe consultar la API (429/cuota o tope diario)."""
    with _lock:
        return _ahora() < _cuota_agotada_hasta


def api_invalida():
    """True si la clave/CX fue rechazada por Google (400/401/403)."""
    with _lock:
        return _ahora() < _config_invalida_hasta


def estado_cuota():
    """Diagnóstico de cuota/configuración (para health/logs)."""
    with _lock:
        return {
            'llamadas_dia': _llamadas_dia,
            'limite_diario': _limite_diario(),
            'agotada': _ahora() < _cuota_agotada_hasta,
            'agotada_hasta': _cuota_agotada_hasta or None,
            'api_invalida': _ahora() < _config_invalida_hasta,
            'api_invalida_hasta': _config_invalida_hasta or None,
        }


def reiniciar_estado():
    """Reinicia contadores y banderas (uso en pruebas/diagnóstico)."""
    global _cuota_agotada_hasta, _config_invalida_hasta, _llamadas_dia, _dia_actual
    with _lock:
        _cuota_agotada_hasta = 0.0
        _config_invalida_hasta = 0.0
        _llamadas_dia = 0
        _dia_actual = None


def _marcar_cuota_agotada(motivo):
    global _cuota_agotada_hasta
    with _lock:
        _cuota_agotada_hasta = _ahora() + _cooldown_seg()
    print(
        f'{_LOG} ADVERTENCIA cuota agotada ({motivo}); '
        f'se pausan las consultas {_cooldown_seg()} s. '
        'Las imágenes automáticas quedan pendientes para el próximo ciclo.',
        flush=True,
    )


def _marcar_config_invalida(motivo):
    """Clave/CX rechazada por Google: se pausa y NO se cachea negativo."""
    global _config_invalida_hasta
    with _lock:
        _config_invalida_hasta = _ahora() + _cooldown_config_seg()
    print(
        f'{_LOG} ADVERTENCIA configuración de Google CSE inválida ({motivo}); '
        f'se pausan las consultas {_cooldown_config_seg()} s. '
        'Revisa GOOGLE_SEARCH_API_KEY/GOOGLE_SEARCH_CX y que la '
        '"Custom Search API" esté habilitada en el proyecto. '
        'Los productos quedan pendientes (no se marca error permanente).',
        flush=True,
    )


def _registrar_llamada():
    """Suma una llamada y marca cuota agotada si se alcanzó el tope diario."""
    global _llamadas_dia
    with _lock:
        _reiniciar_si_nuevo_dia()
        _llamadas_dia += 1
        limite = _limite_diario()
        alcanzado = limite > 0 and _llamadas_dia >= limite
    if alcanzado:
        _marcar_cuota_agotada(f'tope diario {limite}')
    return alcanzado


def _dominio(url):
    try:
        return (urlparse(str(url or '')).hostname or '').lower()
    except Exception:
        return ''


def _es_error_cuota(status_code, detalle):
    if status_code == 429:
        return True
    texto = str(detalle or '').lower()
    return any(
        marca in texto
        for marca in ('quota', 'rate limit', 'ratelimit', 'too many requests', 'daily limit')
    )


def buscar_imagenes(consulta, limite=8, *, timeout=12.0):
    """Resultados de imagen de Google CSE.

    Devuelve ``[{url, titulo, dominio, ancho, alto, contexto}]``. Ante cuota
    agotada, error HTTP o JSON inválido devuelve ``[]`` (sin lanzar).
    """
    api_key, cx = clave_cse()
    consulta = ' '.join(str(consulta or '').split()).strip()
    if not api_key or not cx or not consulta:
        return []

    if cuota_agotada() or api_invalida():
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

    status = getattr(respuesta, 'status_code', 0)
    detalle = ''
    datos = None
    try:
        datos = respuesta.json() or {}
    except Exception:
        datos = None

    if status == 200:
        _registrar_llamada()
    else:
        detalle = ''
        if isinstance(datos, dict):
            detalle = str(((datos.get('error') or {}).get('message')) or '')
        if _es_error_cuota(status, detalle):
            _marcar_cuota_agotada(f'HTTP {status}')
            return []
        if status in (400, 401, 403):
            # Clave/CX inválida o API no habilitada: no envenenar el caché.
            _marcar_config_invalida(f'HTTP {status} {detalle[:120]}')
            return []
        print(f'{_LOG} HTTP {status} para {consulta!r}: {detalle[:160]}')
        return []

    resultados = []
    vistos = set()
    for item in (datos or {}).get('items') or []:
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
    """Prioridad 1: búsqueda por EAN/UPC exacto."""
    codigo = str(codigo_barras or '').strip()
    if not codigo:
        return []
    return buscar_imagenes(codigo, limite=limite)


def buscar_por_nombre_descripcion(nombre, descripcion=None, limite=8):
    """Prioridad 2: consulta optimizada ``nombre + descripción corta``."""
    partes = [str(nombre or '').strip()]
    desc = ' '.join(str(descripcion or '').split())
    if desc:
        partes.append(desc[:80])
    consulta = ' '.join(p for p in partes if p).strip()
    if not consulta:
        return []
    return buscar_imagenes(consulta, limite=limite)

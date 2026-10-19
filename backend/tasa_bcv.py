"""Tasa oficial del dólar del Banco Central de Venezuela (BCV).

Módulo autónomo y defensivo pensado para usarse desde Flask sin bloquear ni
romper la aplicación:

* **Scraping seguro** de ``https://www.bcv.org.ve/`` con ``requests``
  (``User-Agent`` de navegador, ``verify=False`` por los certificados del BCV y
  ``timeout`` de 10 s) y ``BeautifulSoup``.
* **Caché diaria en memoria**: se consulta el BCV **una sola vez por día**; el
  resto de las peticiones obtienen el valor instantáneamente.
* **Fallback en cascada**: caché del día → caché anterior → tasa manual de la
  base de datos → valor por defecto configurable. La función **nunca lanza**:
  siempre devuelve un diccionario con un valor seguro y ``error`` controlado.

API pública
===========
``obtener_tasa_bcv(forzar_actualizacion=False) -> dict``
    Diccionario con la tasa y el estado de la consulta.
``obtener_valor_bcv(forzar_actualizacion=False) -> float``
    Atajo numérico (nunca ``None``).
``estado_tasa_bcv() -> dict``
    Metadatos de la caché para diagnóstico.
``calentar_cache_bcv() -> None``
    Pre-carga en segundo plano; se dispara solo al importar.

Configuración por variables de entorno
--------------------------------------
``LOCALIS_BCV_TIMEOUT``          timeout de la petición en segundos (por defecto 10).
``LOCALIS_BCV_TASA_FALLBACK``    valor seguro si no hay ninguna otra fuente.
``LOCALIS_BCV_WARMUP``           ``0`` desactiva el precalentamiento al importar.
``LOCALIS_BCV_URL``              sobrescribe la URL objetivo (tests).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from datetime import date, datetime, timezone

import requests

_LOG = logging.getLogger('localis.tasa_bcv')

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
URL_BCV = os.getenv('LOCALIS_BCV_URL', 'https://www.bcv.org.ve/').strip()

# User-Agent de un navegador moderno para evitar bloqueos básicos.
CABECERAS_NAVEGADOR = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'es-VE,es;q=0.9,en;q=0.8',
}

try:
    TIMEOUT_SEG = float(os.getenv('LOCALIS_BCV_TIMEOUT', '10') or 10)
except (TypeError, ValueError):
    TIMEOUT_SEG = 10.0


def _leer_tasa_defecto() -> float:
    """Valor seguro de último recurso (configurable)."""
    try:
        return float(os.getenv('LOCALIS_BCV_TASA_FALLBACK', '1.0') or 1.0)
    except (TypeError, ValueError):
        return 1.0


TASA_POR_DEFECTO = _leer_tasa_defecto()

# Desactiva las advertencias por ``verify=False`` (certificados del BCV).
try:
    import urllib3
    from urllib3.exceptions import InsecureRequestWarning

    urllib3.disable_warnings(InsecureRequestWarning)
except Exception:  # pragma: no cover - urllib3 siempre viene con requests
    pass

# ---------------------------------------------------------------------------
# Caché diaria en memoria (compartida por proceso/worker)
# ---------------------------------------------------------------------------
_cache: dict = {
    'fecha': None,          # date del día en que se obtuvo el valor
    'tasa': None,           # float o None
    'fecha_bcv': None,      # texto de fecha publicado por el BCV (si existe)
    'actualizado_en': None,  # ISO UTC de la última descarga exitosa
}
_cache_lock = threading.Lock()


def _es_hoy(fecha) -> bool:
    return fecha == date.today()


# ---------------------------------------------------------------------------
# Parseo
# ---------------------------------------------------------------------------
_RE_NUMERO = re.compile(r'\d[\d.,]*')


def _limpiar_numero(texto) -> float:
    """Convierte el texto del BCV a ``float``.

    Soporta formato venezolano (``36,50`` / ``3.650,25``) y también el formato
    anglosajón (``36.50``). Lanza ``ValueError`` si no hay un número válido.
    """
    if texto is None:
        raise ValueError('sin texto para la tasa')
    limpio = re.sub(r'[^\d.,]', '', str(texto)).strip()
    if not limpio:
        raise ValueError(f'no se encontró un número en {texto!r}')

    if ',' in limpio and '.' in limpio:
        # El separador decimal es el que aparece más a la derecha.
        if limpio.rfind(',') > limpio.rfind('.'):
            limpio = limpio.replace('.', '').replace(',', '.')
        else:
            limpio = limpio.replace(',', '')
    elif ',' in limpio:
        # Formato BCV: la coma es decimal.
        limpio = limpio.replace(',', '.')
    # Si solo hay puntos, se asume que el punto es el decimal.

    numero = float(limpio)
    if numero <= 0:
        raise ValueError(f'tasa no válida: {numero}')
    return numero


def _extraer_tasa_html(html: str):
    """Extrae ``(tasa, fecha_bcv)`` del HTML del BCV.

    Estrategia en capas para resistir cambios de maquetación:

    1. ``div#dolar`` (contenedor oficial; también ``#dolar2``/#euro por si acaso).
    2. Selectores genéricos de respaldo.
    3. Último recurso: buscar un número con formato de tasa (``\\d+,\\d+``).

    Lanza ``ValueError`` si no se pudo extraer la tasa.
    """
    from bs4 import BeautifulSoup

    try:
        sopa = BeautifulSoup(html, 'lxml')
    except Exception:
        sopa = BeautifulSoup(html, 'html.parser')

    contenedor = (
        sopa.find('div', id='dolar')
        or sopa.find('div', id='dolar2')
        or sopa.find('div', id='euro')
    )

    candidato = None
    if contenedor is not None:
        # El número suele venir dentro de un <strong>.
        etiqueta = contenedor.find('strong') or contenedor.find('span')
        candidato = etiqueta.get_text(' ', strip=True) if etiqueta else None
        if not candidato:
            candidato = contenedor.get_text(' ', strip=True)

    if not candidato:
        # Respaldo: cualquier <strong>/<span> cuyo texto parezca una tasa.
        for nodo in sopa.find_all(['strong', 'span']):
            texto = nodo.get_text(' ', strip=True)
            if texto and _RE_NUMERO.search(texto):
                candidato = texto
                break

    tasa = _limpiar_numero(candidato)  # lanza ValueError si no hay número

    # Fecha "valor" publicada por el BCV (opcional, nunca obligatoria).
    fecha_bcv = None
    try:
        patron = re.compile(r'fecha\s*(?:valor|de\s*valor)', re.IGNORECASE)
        for nodo in sopa.find_all(string=patron):
            texto = ' '.join(str(nodo).split())
            if texto:
                fecha_bcv = texto[:120]
                break
    except Exception:
        fecha_bcv = None

    return tasa, fecha_bcv


def _descargar_tasa():
    """Descarga y parsea la tasa del BCV. Lanza excepción si algo falla."""
    respuesta = requests.get(
        URL_BCV,
        headers=CABECERAS_NAVEGADOR,
        timeout=TIMEOUT_SEG,
        verify=False,  # crítico: los certificados del BCV fallan con la CA estándar
    )
    respuesta.raise_for_status()
    if not respuesta.text or len(respuesta.text) < 200:
        raise ValueError('respuesta del BCV vacía o demasiado corta')
    return _extraer_tasa_html(respuesta.text)


# ---------------------------------------------------------------------------
# Respaldo desde la configuración manual de la base de datos
# ---------------------------------------------------------------------------
def _tasa_respaldo_db():
    """Tasa manual guardada por el admin (``configuracion_sistema``). Nunca lanza."""
    try:
        from backend.stores import obtener_tasa_dolar

        valor = obtener_tasa_dolar()
        return float(valor) if valor else None
    except Exception as error:
        _LOG.debug('sin tasa manual de respaldo: %s: %s', type(error).__name__, error)
        return None


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def _respuesta_ok(tasa, fuente, fecha_bcv=None, actualizado_en=None):
    return {
        'ok': True,
        'tasa': float(tasa),
        'fecha': date.today().isoformat(),
        'fecha_bcv': fecha_bcv,
        'fuente': fuente,
        'actualizado_en': actualizado_en,
        'error': None,
    }


def _respuesta_error(mensaje, tasa):
    return {
        'ok': False,
        'tasa': float(tasa),
        'fecha': None,
        'fecha_bcv': None,
        'fuente': 'fallback',
        'actualizado_en': None,
        'error': str(mensaje),
    }


def obtener_tasa_bcv(forzar_actualizacion: bool = False) -> dict:
    """Tasa oficial del BCV (con caché diaria y fallback).

    Orden de resolución:

    1. Caché **del día en curso** (salvo ``forzar_actualizacion=True``).
    2. Scraping de ``https://www.bcv.org.ve/``.
    3. Si el scraping falla: caché del día → caché anterior → tasa manual de la
       BD → valor por defecto.

    Nunca lanza: siempre devuelve un diccionario con ``ok`` y ``tasa``.
    """
    with _cache_lock:
        # 1) Caché válida del día: retorno instantáneo, sin HTTP.
        if not forzar_actualizacion and _cache['tasa'] and _es_hoy(_cache['fecha']):
            return _respuesta_ok(
                _cache['tasa'],
                'cache',
                fecha_bcv=_cache['fecha_bcv'],
                actualizado_en=_cache['actualizado_en'],
            )

    # 2) Intento de scraping (fuera del lock: no bloquear otras lecturas).
    error_descarga = None
    try:
        tasa, fecha_bcv = _descargar_tasa()
        actualizado_en = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with _cache_lock:
            _cache.update(
                {
                    'fecha': date.today(),
                    'tasa': tasa,
                    'fecha_bcv': fecha_bcv,
                    'actualizado_en': actualizado_en,
                }
            )
        _LOG.info('tasa BCV actualizada: %.4f', tasa)
        return _respuesta_ok(tasa, 'bcv', fecha_bcv=fecha_bcv, actualizado_en=actualizado_en)
    except Exception as error:  # red, timeout, parseo, SSL, etc.
        error_descarga = f'{type(error).__name__}: {error}'
        _LOG.warning('no se pudo obtener la tasa del BCV (%s)', error_descarga)

    # 3) Fallbacks, en orden de calidad.
    with _cache_lock:
        tasa_cache = _cache['tasa']
        fecha_cache = _cache['fecha']
        fecha_bcv_cache = _cache['fecha_bcv']
        actualizado_cache = _cache['actualizado_en']

    if tasa_cache:
        fuente = 'cache' if _es_hoy(fecha_cache) else 'cache_anterior'
        _LOG.info('usando caché %s de la tasa BCV (%s)', fuente, fecha_cache)
        return _respuesta_ok(
            tasa_cache, fuente, fecha_bcv=fecha_bcv_cache, actualizado_en=actualizado_cache
        )

    tasa_db = _tasa_respaldo_db()
    if tasa_db:
        _LOG.info('usando tasa manual de respaldo: %.4f', tasa_db)
        return _respuesta_ok(tasa_db, 'respaldo_db')

    _LOG.error('sin tasa BCV ni respaldo; usando valor por defecto %.4f', TASA_POR_DEFECTO)
    return _respuesta_error(
        error_descarga or 'no se pudo obtener la tasa del BCV', TASA_POR_DEFECTO
    )


def obtener_valor_bcv(forzar_actualizacion: bool = False) -> float:
    """Atajo numérico: devuelve siempre un ``float`` (nunca ``None``)."""
    return float(obtener_tasa_bcv(forzar_actualizacion=forzar_actualizacion).get('tasa') or TASA_POR_DEFECTO)


def estado_tasa_bcv() -> dict:
    """Metadatos de la caché (para /health o diagnósticos). No hace red."""
    with _cache_lock:
        return {
            'fecha': _cache['fecha'].isoformat() if _cache['fecha'] else None,
            'tasa': _cache['tasa'],
            'fecha_bcv': _cache['fecha_bcv'],
            'actualizado_en': _cache['actualizado_en'],
            'vigente': bool(_cache['tasa'] and _es_hoy(_cache['fecha'])),
        }


def calentar_cache_bcv() -> bool:
    """Fuerza una consulta (usado por el hilo de precalentamiento / arranque)."""
    return bool(obtener_tasa_bcv(forzar_actualizacion=False).get('ok'))


def _precalentar_en_segundo_plano():
    """Pre-carga la tasa al arrancar el worker para no bloquear el primer render."""
    try:
        calentar_cache_bcv()
    except Exception as error:  # pragma: no cover - defensivo
        _LOG.debug('precalentamiento BCV falló: %s', error)


def _programar_precalentamiento():
    if str(os.getenv('LOCALIS_BCV_WARMUP', '1')).strip().lower() in {'0', 'false', 'no', 'off'}:
        return
    if os.getenv('FLASK_DEBUG') or os.getenv('LOCALIS_BCV_WARMUP_SKIP'):
        # En modo debug/reloader evitar peticiones duplicadas por recarga.
        return
    hilo = threading.Thread(
        target=_precalentar_en_segundo_plano,
        name='localis-tasa-bcv',
        daemon=True,
    )
    hilo.start()


_programar_precalentamiento()

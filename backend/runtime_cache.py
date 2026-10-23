"""Caché TTL en memoria para datos que cambian poco (config, planes, tasa, listados).

Con límite de tamaño: si se superan las entradas máximas se descartan primero las
caducadas y, si aún hace falta, las más antiguas. Sin ese tope, cachear por término
de búsqueda podría crecer sin control y comer memoria del worker.
"""

import os
import threading
import time

_lock = threading.Lock()
_entries = {}


def _max_entradas():
    try:
        return max(16, int(os.getenv('LOCALIS_CACHE_MAX_ENTRIES', '2000')))
    except (TypeError, ValueError):
        return 2000


def _podar(ahora, maximo):
    """Descarta caducadas y, si sigue lleno, las más antiguas (en memoria)."""
    if len(_entries) < maximo:
        return
    for clave in [k for k, v in _entries.items() if ahora - v['loaded_at'] >= v['ttl']]:
        _entries.pop(clave, None)
    if len(_entries) >= maximo:
        sobran = len(_entries) - maximo + 1
        for clave, _ in sorted(_entries.items(), key=lambda kv: kv[1]['loaded_at'])[:sobran]:
            _entries.pop(clave, None)


def get_or_load(key, loader, ttl_seconds=120):
    """Retorna valor cacheado o ejecuta loader() si expiró."""
    now = time.monotonic()
    with _lock:
        entry = _entries.get(key)
        # OJO: la validez se mide con el TTL de ESTA llamada (contrato original:
        # llamar con ttl 0 fuerza recarga aunque la entrada se guardara con 60).
        # El ttl guardado solo se usa para saber qué se puede podar.
        if entry and now - entry['loaded_at'] < ttl_seconds:
            return entry['value']

    value = loader()
    with _lock:
        _podar(now, _max_entradas())
        _entries[key] = {'value': value, 'loaded_at': time.monotonic(), 'ttl': ttl_seconds}
    return value


def invalidate(prefix=None):
    """Invalida toda la caché o entradas cuyo key empieza con prefix."""
    with _lock:
        if prefix is None:
            _entries.clear()
            return
        for key in list(_entries):
            if key.startswith(prefix):
                del _entries[key]


def estadisticas():
    """Diagnóstico: nº de entradas y aciertos/fallos acumulados."""
    with _lock:
        return {'entradas': len(_entries), 'maximo': _max_entradas()}

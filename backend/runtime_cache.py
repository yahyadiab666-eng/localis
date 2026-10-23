"""Caché TTL en memoria para datos que cambian poco (config, planes, tasa, listados).

Con límite de tamaño: si se superan las entradas máximas se descartan primero las
caducadas y, si aún hace falta, las más antiguas. Sin ese tope, cachear por término
de búsqueda podría crecer sin control y comer memoria del worker.

Además hace **single flight**: si una entrada expiró y varias peticiones la piden a
la vez, solo UNA ejecuta ``loader()`` (una sola consulta a la BD) y las demás
esperan y reutilizan el resultado. Esto evita el "thundering herd" (una ráfaga de
consultas a la BD justo al expirar las cachés públicas), que es lo que dispara la
latencia cuando hay muchos usuarios.
"""

import os
import threading
import time

_lock = threading.Lock()
_entries = {}

# Locks por clave para el "single flight" (solo uno recarga cada clave).
_load_locks = {}
_load_locks_guard = threading.Lock()
_load_lock_fallback = threading.RLock()


def _max_entradas():
    try:
        return max(16, int(os.getenv('LOCALIS_CACHE_MAX_ENTRIES', '2000')))
    except (TypeError, ValueError):
        return 2000


def _max_locks():
    try:
        return max(32, int(os.getenv('LOCALIS_CACHE_MAX_LOCKS', '512')))
    except (TypeError, ValueError):
        return 512


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


def _lock_de_carga(key):
    """Lock reentrante por clave (single flight). Nunca lanza."""
    with _load_locks_guard:
        existente = _load_locks.get(key)
        if existente is not None:
            return existente
        if len(_load_locks) >= _max_locks():
            # Limpiar solo los que no estén en uso para no romper un single flight
            # en curso. Si aun así está lleno, se usa un lock global (menos granular
            # pero seguro).
            for otra in list(_load_locks):
                if not _load_locks[otra].locked():
                    _load_locks.pop(otra, None)
            if len(_load_locks) >= _max_locks():
                return _load_lock_fallback
        nuevo = threading.RLock()
        _load_locks[key] = nuevo
        return nuevo


def get_or_load(key, loader, ttl_seconds=120):
    """Valor cacheado o recargado (con single flight)."""
    now = time.monotonic()
    with _lock:
        entry = _entries.get(key)
        # OJO: la validez se mide con el TTL de ESTA llamada (contrato original:
        # llamar con ttl 0 fuerza recarga aunque la entrada se guardara con 60).
        # El ttl guardado solo se usa para saber qué se puede podar.
        if entry and now - entry['loaded_at'] < ttl_seconds:
            return entry['value']

    # Solo un hilo recarga esta clave; los demás esperan y reutilizan el valor.
    with _lock_de_carga(key):
        now = time.monotonic()
        with _lock:
            entry = _entries.get(key)
            if entry and now - entry['loaded_at'] < ttl_seconds:
                return entry['value']

        value = loader()
        with _lock:
            _podar(now, _max_entradas())
            _entries[key] = {
                'value': value,
                'loaded_at': time.monotonic(),
                'ttl': ttl_seconds,
            }
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
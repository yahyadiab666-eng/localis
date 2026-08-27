"""Caché TTL en memoria para datos que cambian poco (config, planes, tasa)."""

import threading
import time

_lock = threading.Lock()
_entries = {}


def get_or_load(key, loader, ttl_seconds=120):
    """Retorna valor cacheado o ejecuta loader() si expiró."""
    now = time.monotonic()
    with _lock:
        entry = _entries.get(key)
        if entry and now - entry['loaded_at'] < ttl_seconds:
            return entry['value']

    value = loader()
    with _lock:
        _entries[key] = {'value': value, 'loaded_at': time.monotonic()}
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

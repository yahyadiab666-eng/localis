"""Contadores en memoria del asistente (diagnostico) y feedback de los usuarios.

Por proceso (coherente con WEB_CONCURRENCY=1). No persiste: sirve para ver de un
vistazo, en ``/api/asistente/estado``, cuantas consultas se han atendido, la
latencia media y la utilidad percibida.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_datos = {
    'mensajes': 0,
    'respuestas_ok': 0,
    'respuestas_error': 0,
    'ms_total': 0,
    'ms_max': 0,
    'feedback_util': 0,
    'feedback_no_util': 0,
}


def registrar_mensaje(ok, ms):
    """Registra una consulta atendida y su latencia. Nunca lanza."""
    try:
        with _lock:
            _datos['mensajes'] += 1
            _datos['respuestas_ok' if ok else 'respuestas_error'] += 1
            ms = int(ms or 0)
            _datos['ms_total'] += ms
            if ms > _datos['ms_max']:
                _datos['ms_max'] = ms
    except Exception:
        pass


def registrar_feedback(util):
    """Registra 👍 (util) o 👎 (no util). Nunca lanza."""
    try:
        with _lock:
            _datos['feedback_util' if util else 'feedback_no_util'] += 1
    except Exception:
        pass


def estado():
    with _lock:
        datos = dict(_datos)
    mensajes = datos.get('mensajes') or 1
    datos['ms_promedio'] = round(datos.get('ms_total', 0) / mensajes, 1)
    datos.pop('ms_total', None)
    return datos
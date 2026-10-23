"""Presupuesto diario de tokens del asistente (en memoria del worker).

Evita que un uso masivo o un abuso dispare el coste del LLM. Si se agota, el
asistente avisa y deriva en vez de seguir gastando. Por defecto esta DESACTIVADO
(``LOCALIS_ASISTENTE_PRESUPUESTO_DIA_TOKENS=0``), para no cortar el servicio.

Es por proceso (coherente con WEB_CONCURRENCY=1). Con varios workers el limite
efectivo se multiplicaria por el numero de procesos.
"""

from __future__ import annotations

import threading
from datetime import date, datetime

from config import ASISTENTE_PRESUPUESTO_DIA_TOKENS

_lock = threading.Lock()
_dia = None
_usado = 0


def _reiniciar_si_cambio(ahora):
    global _dia, _usado
    hoy = ahora.date() if isinstance(ahora, datetime) else date.today()
    if _dia != hoy:
        _dia = hoy
        _usado = 0


def disponible():
    """True si aun queda presupuesto (o si esta desactivado)."""
    if ASISTENTE_PRESUPUESTO_DIA_TOKENS <= 0:
        return True
    with _lock:
        _reiniciar_si_cambio(datetime.now())
        return _usado < ASISTENTE_PRESUPUESTO_DIA_TOKENS


def registrar(tokens):
    """Suma los tokens consumidos por una respuesta."""
    global _usado
    if ASISTENTE_PRESUPUESTO_DIA_TOKENS <= 0:
        return
    with _lock:
        _reiniciar_si_cambio(datetime.now())
        _usado += int(tokens or 0)


def estado():
    with _lock:
        _reiniciar_si_cambio(datetime.now())
        return {'usado': _usado, 'limite': ASISTENTE_PRESUPUESTO_DIA_TOKENS}

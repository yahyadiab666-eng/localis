"""Tope de consultas simultaneas al LLM (patron del semaforo de OCR).

Con 1 worker y pocos hilos, una rafaga de chats no debe acaparar el proceso: las
consultas que no consiguen cupo reciben un aviso amable (y la opcion de hablar
con una persona) en vez de bloquear un hilo durante segundos.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

from config import ASISTENTE_MAX_CONCURRENTES

_semaforo = threading.BoundedSemaphore(max(1, ASISTENTE_MAX_CONCURRENTES))


@contextmanager
def cupo_disponible(espera=0.0):
    """Cede un cupo (True) o no (False). Con ``espera`` 0 no bloquea."""
    obtenido = False
    try:
        if espera and espera > 0:
            obtenido = _semaforo.acquire(timeout=espera)
        else:
            obtenido = _semaforo.acquire(blocking=False)
        yield obtenido
    finally:
        if obtenido:
            _semaforo.release()

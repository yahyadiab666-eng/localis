"""Asistente de IA de Localis (atencion al cliente, SOLO informativo).

Modulo autocontenido y apagable por variable de entorno (``LOCALIS_ASISTENTE``).
Por ahora NO toca la base de datos: responde con la documentacion indexada en
memoria y deriva a WhatsApp cuando hace falta una intervencion humana. El
contexto de la conversacion lo aporta el navegador y no se persiste.

Piezas:
  - ``proveedor``: cliente del LLM (compatible OpenAI) sobre httpx.
  - ``conocimiento``: recuperacion lexica (BM25) sobre la documentacion.
  - ``prompts``: system prompt por rol y armado de mensajes.
  - ``seguridad``: saneado de entrada y redaccion de secretos.
  - ``semaforo``: tope de consultas simultaneas al LLM.
  - ``orquestador``: une todo y devuelve la respuesta final.

Futuro (fuera de este alcance): herramientas para consultar/editar con permiso,
verificar pagos y actuar sobre comercios, siempre de forma segura.
"""

from __future__ import annotations

import hashlib
import os

from config import RUTA_RAIZ

_VERSION_ASSETS = None


def version_assets():
    """Version corta de los estaticos del asistente, para cache-busting.

    El Service Worker sirve /static/ en modo cache-first, asi que si se cambia
    el JS/CSS hay que cambiar la URL o los navegadores seguiran con la copia
    vieja (y un JS viejo con un HTML nuevo rompe el widget). Esta version se
    calcula de la huella de los archivos y cambia sola en cada despliegue.
    """
    global _VERSION_ASSETS
    if _VERSION_ASSETS is not None:
        return _VERSION_ASSETS
    partes = []
    for relativo in ('static/js/asistente.js', 'static/css/asistente.css'):
        try:
            estado = os.stat(os.path.join(RUTA_RAIZ, relativo))
            partes.append(f'{estado.st_mtime_ns}:{estado.st_size}')
        except Exception:
            partes.append(relativo)
    _VERSION_ASSETS = hashlib.md5('|'.join(partes).encode('utf-8')).hexdigest()[:8]
    return _VERSION_ASSETS

"""Cliente del proveedor LLM (compatible OpenAI) sobre httpx.

Sin SDK nuevo: se habla directo con ``/chat/completions``, asi que el proveedor
(OpenRouter por defecto) se cambia solo con variables de entorno. Los errores se
normalizan a ``ProveedorNoDisponible`` (recuperable) para que el asistente nunca
propague un 500.
"""

from __future__ import annotations

import logging

import httpx

from config import (
    ASISTENTE_BASE_URL,
    ASISTENTE_MAX_TOKENS,
    ASISTENTE_MODELO,
    ASISTENTE_SITIO,
    ASISTENTE_TEMPERATURA,
    ASISTENTE_TIMEOUT_SEG,
    ASISTENTE_TITULO,
    clave_asistente,
)

_log = logging.getLogger('localis.asistente.proveedor')


class ProveedorNoDisponible(Exception):
    """El LLM no esta configurado o no respondio. Es recuperable."""


def disponible():
    return bool(clave_asistente())


def responder(mensajes, herramientas=None, timeout=None):
    """Envia los mensajes y devuelve contenido, tool_calls y tokens.

    ``herramientas`` (function calling) queda listo para la fase de soporte
    tecnico con acciones; en el MVP no se usa.
    """
    clave = clave_asistente()
    if not clave:
        raise ProveedorNoDisponible('falta LOCALIS_ASISTENTE_API_KEY')

    payload = {
        'model': ASISTENTE_MODELO,
        'messages': mensajes,
        'temperature': ASISTENTE_TEMPERATURA,
        'max_tokens': ASISTENTE_MAX_TOKENS,
    }
    if herramientas:
        payload['tools'] = herramientas
        payload['tool_choice'] = 'auto'

    cabeceras = {
        'Authorization': f'Bearer {clave}',
        'Content-Type': 'application/json',
        'HTTP-Referer': ASISTENTE_SITIO,
        'X-Title': ASISTENTE_TITULO,
    }

    try:
        with httpx.Client(timeout=timeout or ASISTENTE_TIMEOUT_SEG) as cliente:
            respuesta = cliente.post(
                f'{ASISTENTE_BASE_URL}/chat/completions',
                json=payload,
                headers=cabeceras,
            )
    except Exception as error:
        raise ProveedorNoDisponible(f'error de red: {type(error).__name__}') from error

    if respuesta.status_code >= 400:
        # No se registra el cuerpo: puede contener datos de la peticion.
        _log.error('LLM respondio HTTP %s', respuesta.status_code)
        raise ProveedorNoDisponible(f'HTTP {respuesta.status_code}')

    try:
        datos = respuesta.json()
    except Exception as error:
        raise ProveedorNoDisponible('respuesta no es JSON') from error

    opciones = datos.get('choices') or []
    if not opciones:
        raise ProveedorNoDisponible('respuesta sin choices')
    mensaje = opciones[0].get('message') or {}
    uso = datos.get('usage') or {}

    return {
        'contenido': mensaje.get('content') or '',
        'tool_calls': mensaje.get('tool_calls') or [],
        'tokens': int(uso.get('total_tokens') or 0),
        'modelo': datos.get('model') or ASISTENTE_MODELO,
    }

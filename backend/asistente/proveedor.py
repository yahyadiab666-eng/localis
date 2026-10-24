"""Cliente del proveedor LLM (compatible OpenAI) sobre httpx.

Sin SDK nuevo: se habla directo con ``/chat/completions``, asi que el proveedor
(OpenRouter por defecto) se cambia solo con variables de entorno. Los errores se
normalizan a ``ProveedorNoDisponible`` (recuperable) para que el asistente nunca
propague un 500.
"""

from __future__ import annotations

import logging
import threading
import time

import httpx

from config import (
    ASISTENTE_BASE_URL,
    ASISTENTE_MAX_TOKENS,
    ASISTENTE_MODELO,
    ASISTENTE_MODELO_RESPALDO,
    ASISTENTE_ORDEN_PROVEEDOR,
    ASISTENTE_SITIO,
    ASISTENTE_TEMPERATURA,
    ASISTENTE_TIMEOUT_SEG,
    ASISTENTE_TITULO,
    clave_asistente,
)

_log = logging.getLogger('localis.asistente.proveedor')

# Cliente HTTP por hilo (los workers de Gunicorn son hilos): reutiliza el pool de
# conexiones y evita un handshake TLS nuevo en cada consulta. El timeout se ajusta
# por peticion, asi que el valor por defecto aqui es solo un tope de seguridad.
_local = threading.local()
_REINTENTOS = 2
_ESPERA_REINTENTO = 0.3


def _cliente():
    cliente = getattr(_local, 'cliente', None)
    if cliente is None:
        cliente = httpx.Client(timeout=ASISTENTE_TIMEOUT_SEG)
        _local.cliente = cliente
    return cliente


class ProveedorNoDisponible(Exception):
    """El LLM no esta configurado o no respondio. Es recuperable."""


def disponible():
    return bool(clave_asistente())


def responder(mensajes, herramientas=None, timeout=None):
    """Intenta el modelo principal y, si falla, el de respaldo.

    ``herramientas`` (function calling) queda listo para acciones futuras; en
    esta fase no se usa. Devuelve contenido, tool_calls y tokens.
    """
    modelos = [ASISTENTE_MODELO]
    if ASISTENTE_MODELO_RESPALDO and ASISTENTE_MODELO_RESPALDO not in modelos:
        modelos.append(ASISTENTE_MODELO_RESPALDO)

    ultimo_error = None
    for modelo in modelos:
        try:
            return _llamar(modelo, mensajes, herramientas, timeout)
        except ProveedorNoDisponible as error:
            ultimo_error = error
            _log.warning('modelo %s no disponible: %s', modelo, error)

    raise ultimo_error or ProveedorNoDisponible('sin modelos configurados')


def _llamar(modelo, mensajes, herramientas=None, timeout=None):
    clave = clave_asistente()
    if not clave:
        raise ProveedorNoDisponible('falta LOCALIS_ASISTENTE_API_KEY')

    payload = {
        'model': modelo,
        'messages': mensajes,
        'temperature': ASISTENTE_TEMPERATURA,
        'max_tokens': ASISTENTE_MAX_TOKENS,
    }
    if herramientas:
        payload['tools'] = herramientas
        payload['tool_choice'] = 'auto'
    if ASISTENTE_ORDEN_PROVEEDOR:
        # OpenRouter: elegir el proveedor mas rapido para bajar la latencia.
        payload['provider'] = {'sort': ASISTENTE_ORDEN_PROVEEDOR}

    cabeceras = {
        'Authorization': f'Bearer {clave}',
        'Content-Type': 'application/json',
        'HTTP-Referer': ASISTENTE_SITIO,
        'X-Title': ASISTENTE_TITULO,
    }

    url = f'{ASISTENTE_BASE_URL}/chat/completions'
    tiempo = timeout or ASISTENTE_TIMEOUT_SEG
    respuesta = None
    ultimo_error = None
    for intento in range(_REINTENTOS):
        try:
            respuesta = _cliente().post(
                url, json=payload, headers=cabeceras, timeout=tiempo
            )
        except Exception as error:
            # Error de red/transporte: un reintento corto antes de rendirse.
            ultimo_error = error
            if intento < _REINTENTOS - 1:
                time.sleep(_ESPERA_REINTENTO)
                continue
            raise ProveedorNoDisponible(
                f'error de red: {type(error).__name__}'
            ) from error
        # 5xx y 429 suelen ser transitorios (proveedor saturado): reintenta una vez.
        if respuesta.status_code >= 500 or respuesta.status_code == 429:
            if intento < _REINTENTOS - 1:
                time.sleep(_ESPERA_REINTENTO)
                continue
        break

    if respuesta is None:
        raise ProveedorNoDisponible(
            f'error de red: {type(ultimo_error).__name__}'
        ) from ultimo_error

    if respuesta.status_code >= 400:
        # No se registra el cuerpo: puede contener datos de la peticion.
        _log.error('LLM (%s) respondio HTTP %s', modelo, respuesta.status_code)
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
        'modelo': datos.get('model') or modelo,
    }

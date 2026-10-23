"""OCR remoto con respaldo local (opcional, apagado por defecto).

El OCR de comprobantes consume CPU. Si ``LOCALIS_OCR_SERVICE_URL`` esta definida,
el OCR se delega por HTTP a un **worker** aparte (que es quien gasta la CPU); si
la variable no esta definida, o si el worker falla, se ejecuta el OCR **local**
(comportamiento identico al de siempre).

Garantias de seguridad (para no romper el flujo de pagos):
  - Devuelve EXACTAMENTE el mismo dict que ``backend.payment_ocr``.
  - Nunca lanza: ante cualquier error/timeout cae al OCR local.
  - **Cortacircuitos**: tras varios fallos seguidos deja de intentar el remoto
    durante un tiempo, para no penalizar cada pago con un timeout.
  - El OCR local sigue acotado por ``OCR_SEMAFORO``.
"""

from __future__ import annotations

import base64
import logging
import os
import time

_log = logging.getLogger('localis.ocr_remoto')

_FALLOS_PARA_ABRIR = 3
_ESPERA_CIRCUITO_SEG = 60

_fallos_seguidos = 0
_circuito_abierto_hasta = 0.0


def _url():
    return (os.environ.get('LOCALIS_OCR_SERVICE_URL') or '').strip().rstrip('/')


def _token():
    return (
        (os.environ.get('LOCALIS_OCR_TOKEN') or '').strip()
        or (os.environ.get('LOCALIS_CRON_TOKEN') or '').strip()
    )


def _timeout():
    try:
        return max(3.0, float(os.environ.get('LOCALIS_OCR_REMOTO_TIMEOUT_SEG', '12') or 12))
    except (TypeError, ValueError):
        return 12.0


def _motor_local(metodo):
    from backend.payment_ocr import OCR_SEMAFORO, validar_comprobante_binance
    from backend.payment_ocr import validar_comprobante_pago_movil

    return OCR_SEMAFORO, (
        validar_comprobante_binance if metodo == 'binance' else validar_comprobante_pago_movil
    )


def _ejecutar_local(metodo, data_bytes, **kwargs):
    semaforo, funcion = _motor_local(metodo)
    with semaforo:
        return funcion(data_bytes, **kwargs)


def configurado():
    return bool(_url() and _token())


def _circuito_abierto():
    return time.time() < _circuito_abierto_hasta


def _registrar_fallo(error):
    global _fallos_seguidos, _circuito_abierto_hasta
    _fallos_seguidos += 1
    if _fallos_seguidos >= _FALLOS_PARA_ABRIR:
        _circuito_abierto_hasta = time.time() + _ESPERA_CIRCUITO_SEG
        _fallos_seguidos = 0
        _log.warning(
            '[Localis OCR remoto] cortacircuitos abierto %ss tras fallos: %s',
            _ESPERA_CIRCUITO_SEG,
            error,
        )


def _registrar_ok():
    global _fallos_seguidos
    _fallos_seguidos = 0


def _llamar_remoto(accion, payload):
    """Devuelve el dict del OCR remoto o ``None`` (para caer al local)."""
    import requests

    try:
        respuesta = requests.post(
            f'{_url()}/tareas/ocr/{accion}',
            headers={'X-Localis-Ocr-Token': _token(), 'Content-Type': 'application/json'},
            json=payload,
            timeout=_timeout(),
        )
        if respuesta.status_code >= 400:
            _registrar_fallo(f'HTTP {respuesta.status_code}')
            return None
        datos = respuesta.json() or {}
        resultado = datos.get('resultado')
        if not isinstance(resultado, dict) or 'ok' not in resultado:
            _registrar_fallo('respuesta invalida')
            return None
        _registrar_ok()
        return resultado
    except Exception as error:
        _registrar_fallo(f'{type(error).__name__}: {error}')
        return None


def _codificar(data_bytes):
    try:
        return base64.b64encode(bytes(data_bytes)).decode('ascii')
    except Exception:
        return None


def validar_comprobante_pago_movil(
    data_bytes, monto_esperado_bs, banco=None, rif=None, telefono=None
):
    """OCR del pago movil (remoto si esta configurado; si no, local)."""
    if configurado() and not _circuito_abierto():
        imagen = _codificar(data_bytes)
        if imagen:
            resultado = _llamar_remoto(
                'pago-movil',
                {
                    'imagen_b64': imagen,
                    'monto_esperado_bs': monto_esperado_bs,
                    'banco': banco,
                    'rif': rif,
                    'telefono': telefono,
                },
            )
            if resultado is not None:
                return resultado
    return _ejecutar_local(
        'pago-movil',
        data_bytes,
        monto_esperado_bs=monto_esperado_bs,
        banco=banco,
        rif=rif,
        telefono=telefono,
    )


def validar_comprobante_binance(data_bytes, monto_esperado_usd):
    """OCR del comprobante Binance (remoto si esta configurado; si no, local)."""
    if configurado() and not _circuito_abierto():
        imagen = _codificar(data_bytes)
        if imagen:
            resultado = _llamar_remoto(
                'binance',
                {'imagen_b64': imagen, 'monto_esperado_usd': monto_esperado_usd},
            )
            if resultado is not None:
                return resultado
    return _ejecutar_local(
        'binance', data_bytes, monto_esperado_usd=monto_esperado_usd
    )


def estado():
    """Diagnostico (para /health)."""
    return {
        'configurado': configurado(),
        'circuito_abierto': _circuito_abierto(),
        'fallos_seguidos': _fallos_seguidos,
        'timeout_seg': _timeout(),
    }
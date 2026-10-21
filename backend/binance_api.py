"""Verificación de pagos de Binance Pay contra la API (solo lectura).

Confirma que una transacción **existe realmente** en la cuenta (por su ID de
orden), en vez de fiarse de una captura (que se puede falsificar).

Requiere ``LOCALIS_BINANCE_API_KEY`` y ``LOCALIS_BINANCE_API_SECRET`` (permiso
**solo lectura**, sin retiros). Nunca lanza:

  - ``True``  → la orden existe (y el monto cuadra).
  - ``False`` → la API respondió y la orden NO está.
  - ``None``  → no está configurada o la API falló (no bloquea: el OCR sigue).

Ojo: el endpoint de historial de Binance Pay puede requerir que la cuenta tenga
Pay habilitado; si la API no lo permite, se degrada a ``None`` y el respaldo es
la verificación manual del admin.

La búsqueda se limita a los últimos ``LOCALIS_BINANCE_VENTANA_DIAS`` días (3 por
defecto, frente a 1 día máximo exigido al comprobante) y pagina por tramos si
una ventana viene llena (límite de 100 movimientos por respuesta), para no
rechazar una orden legítima que quedó fuera de la primera página.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse
import urllib.request

logger = logging.getLogger('localis.binance_api')

_LOG = '[Localis Binance API]'
_API_BASE = os.environ.get(
    'LOCALIS_BINANCE_API_BASE', 'https://api.binance.com'
).rstrip('/')
_TIMEOUT = 12

# La captura del comprobante se valida con fecha máxima de 1 día
# (LOCALIS_COMPROBANTE_MAX_DIAS), así que 3 días de margen cubren cualquier
# desfase de reloj/zona horaria. Cada ventana pide como máximo 100 movimientos.
_VENTANA_DIAS = 3
_TAMANO_PAGINA = 100
_SLICE_MS = 12 * 60 * 60 * 1000  # 12 h: ventana de barrido si hay truncamiento
_MAX_SLICES = 12


def _dias_ventana():
    try:
        return max(1, int(os.environ.get('LOCALIS_BINANCE_VENTANA_DIAS', '3')))
    except (TypeError, ValueError):
        return _VENTANA_DIAS


def configurado():
    return bool(
        (os.environ.get('LOCALIS_BINANCE_API_KEY') or '').strip()
        and (os.environ.get('LOCALIS_BINANCE_API_SECRET') or '').strip()
    )


def _firmar(query, secret):
    """Firma HMAC-SHA256 (hex) del query string, según la API de Binance."""
    return hmac.new(
        str(secret).encode('utf-8'), str(query).encode('utf-8'), hashlib.sha256
    ).hexdigest()


def _get_firmado(path, params):
    key = (os.environ.get('LOCALIS_BINANCE_API_KEY') or '').strip()
    secret = (os.environ.get('LOCALIS_BINANCE_API_SECRET') or '').strip()
    datos = dict(params or {})
    datos['timestamp'] = int(time.time() * 1000)
    datos.setdefault('recvWindow', 10000)
    query = urllib.parse.urlencode(datos)
    url = f'{_API_BASE}{path}?{query}&signature={_firmar(query, secret)}'
    peticion = urllib.request.Request(url, headers={'X-MBX-APIKEY': key})
    with urllib.request.urlopen(peticion, timeout=_TIMEOUT) as respuesta:
        return json.loads(respuesta.read().decode('utf-8'))


def _extraer_transacciones(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for clave in ('data', 'rows', 'transactions', 'list'):
            if isinstance(data.get(clave), list):
                return data[clave]
    return []


def _coincide_orden(tx, order_id):
    objetivo = str(order_id)
    for clave in ('orderId', 'orderID', 'transactionId', 'transactionID', 'id'):
        if clave in tx and str(tx.get(clave)) == objetivo:
            return True
    return False


def _monto_tx(tx):
    for clave in ('amount', 'totalAmount', 'receiveAmount', 'funds'):
        try:
            if tx.get(clave) is not None:
                return abs(float(tx.get(clave)))
        except (TypeError, ValueError):
            continue
    return None


def _consultar_ventana(desde_ms, hasta_ms):
    """Movimientos de Binance Pay en el rango (máx. 100 por respuesta)."""
    data = _get_firmado(
        '/sapi/v1/pay/transactions',
        {
            'startTime': int(desde_ms),
            'endTime': int(hasta_ms),
            'limit': _TAMANO_PAGINA,
        },
    )
    return _extraer_transacciones(data)


def _primera_coincidencia(transacciones, order_id):
    for tx in transacciones:
        if _coincide_orden(tx, order_id):
            return tx
    return None


def _buscar_orden(order_id, desde_ms, hasta_ms):
    """Devuelve (encontrada, tx, concluyente).

    ``concluyente`` es False solo si la API devolvió páginas llenas en toda la
    ventana (volumen altísimo) y no se puede afirmar que la orden no exista.
    """
    transacciones = _consultar_ventana(desde_ms, hasta_ms)
    tx = _primera_coincidencia(transacciones, order_id)
    if tx is not None:
        return True, tx, True
    if len(transacciones) < _TAMANO_PAGINA:
        return False, None, True

    # Página llena: puede haber truncamiento. Barrer la ventana en tramos de
    # 12 h, de lo más reciente a lo más antiguo.
    fin = hasta_ms
    for _ in range(_MAX_SLICES):
        if fin <= desde_ms:
            break
        inicio = max(desde_ms, fin - _SLICE_MS)
        transacciones = _consultar_ventana(inicio, fin)
        tx = _primera_coincidencia(transacciones, order_id)
        if tx is not None:
            return True, tx, True
        if len(transacciones) < _TAMANO_PAGINA:
            return False, None, True
        fin = inicio - 1

    return False, None, False


def verificar_orden_binance(order_id, monto_usd=None):
    """True/False/None según exista la orden (ver docstring del módulo)."""
    if not configurado() or not order_id:
        return None

    objetivo_monto = None
    try:
        if monto_usd is not None:
            objetivo_monto = abs(float(monto_usd))
    except (TypeError, ValueError):
        objetivo_monto = None

    hasta_ms = int(time.time() * 1000)
    desde_ms = hasta_ms - (_dias_ventana() * 24 * 60 * 60 * 1000)

    try:
        encontrada, tx, concluyente = _buscar_orden(order_id, desde_ms, hasta_ms)
        if not encontrada:
            if concluyente:
                logger.warning(
                    f'{_LOG} orden {order_id} NO está en Binance '
                    f'(ventana {_dias_ventana()} días).'
                )
                return False
            logger.warning(
                f'{_LOG} no se pudo confirmar {order_id} (volumen alto en la '
                f'ventana); se deja a revisión manual.'
            )
            return None

        monto = _monto_tx(tx)
        if (
            objetivo_monto
            and monto
            and abs(monto - objetivo_monto) > max(1.0, objetivo_monto * 0.05)
        ):
            logger.warning(
                f'{_LOG} orden {order_id} existe pero el monto {monto} no '
                f'cuadra con {objetivo_monto}.'
            )
            return False

        logger.info(f'{_LOG} orden {order_id} confirmada en Binance.')
        return True
    except Exception as error:
        logger.error(
            f'{_LOG} no se pudo verificar {order_id}: '
            f'{type(error).__name__}: {error}'
        )
        return None

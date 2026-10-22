"""Verificación de pagos de Binance Pay contra la API (solo lectura).

Confirma que una transacción **existe realmente** en la cuenta (por su ID de
orden), en vez de fiarse de una captura (que se puede falsificar).

Requiere ``LOCALIS_BINANCE_API_KEY`` y ``LOCALIS_BINANCE_API_SECRET`` (permiso
**solo lectura**, sin retiros). Nunca lanza:

  - ``True``  → la orden existe (en USDT y con el monto exacto).
  - ``False`` → la API respondió y la orden NO está (o no es USDT / el monto
    no cuadra: incompleto o excedido).
  - ``None``  → no está configurada, la API falló o la orden no informa monto
    (no bloquea: el OCR sigue y el admin revisa).

Ojo: el endpoint de historial de Binance Pay puede requerir que la cuenta tenga
Pay habilitado; si la API no lo permite, se degrada a ``None`` y el respaldo es
la verificación manual del admin.

La búsqueda se limita a los últimos ``LOCALIS_BINANCE_VENTANA_DIAS`` días (3 por
defecto, frente a 1 día máximo exigido al comprobante) y pagina por tramos si
una ventana viene llena (límite de 100 movimientos por respuesta), para no
rechazar una orden legítima que quedó fuera de la primera página.

Toda la operación tiene un **presupuesto de tiempo** duro
(``LOCALIS_BINANCE_PRESUPUESTO_SEG``, 20 s por defecto). Si se agota, se
devuelve ``None`` (no verificado → revisión manual) en lugar de seguir
esperando: sin ese tope, el peor caso podía encadenar 13 peticiones de 12 s
(156 s) y superar el timeout de Gunicorn, matando el worker a mitad de un pago.
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

# PRESUPUESTO DE TIEMPO (crítico).
# Sin límite, la búsqueda podía hacer 1 + _MAX_SLICES peticiones de 12 s cada
# una = 156 s, muy por encima del timeout de Gunicorn (120 s). Si la API de
# Binance no falla pero se cuelga, el worker moría a mitad de un pago y el
# comerciante se quedaba sin saber si había pagado. Ahora toda la operación
# tiene un tope duro y, al agotarse, se responde "no verificado" (el pago sigue
# a revisión manual) en vez de seguir esperando.
_PRESUPUESTO_SEG = 20.0
_TIMEOUT_MAX_PETICION = 8.0

# La captura del comprobante se valida con fecha máxima de 1 día
# (LOCALIS_COMPROBANTE_MAX_DIAS), así que 3 días de margen cubren cualquier
# desfase de reloj/zona horaria. Cada ventana pide como máximo 100 movimientos.
_VENTANA_DIAS = 3
_TAMANO_PAGINA = 100
_SLICE_MS = 12 * 60 * 60 * 1000  # 12 h: ventana de barrido si hay truncamiento
_MAX_SLICES = 12

# La verificación solo acepta pagos en USDT y exige el importe exacto (con
# centavos de margen). Nunca se acepta un dólar menos: en destacados de $2-4
# eso equivalía a regalar hasta el 50 % del servicio.
MONEDA_ESPERADA = 'USDT'
_TOLERANCIA_USD_ABS = 0.02
_TOLERANCIA_USD_REL = 0.01


def _presupuesto_segundos():
    """Tope de tiempo para toda la verificación de una orden."""
    try:
        return max(1.0, float(os.environ.get('LOCALIS_BINANCE_PRESUPUESTO_SEG') or _PRESUPUESTO_SEG))
    except (TypeError, ValueError):
        return _PRESUPUESTO_SEG


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


def _get_firmado(path, params, timeout=None):
    key = (os.environ.get('LOCALIS_BINANCE_API_KEY') or '').strip()
    secret = (os.environ.get('LOCALIS_BINANCE_API_SECRET') or '').strip()
    datos = dict(params or {})
    datos['timestamp'] = int(time.time() * 1000)
    datos.setdefault('recvWindow', 10000)
    query = urllib.parse.urlencode(datos)
    url = f'{_API_BASE}{path}?{query}&signature={_firmar(query, secret)}'
    peticion = urllib.request.Request(url, headers={'X-MBX-APIKEY': key})
    espera = _TIMEOUT if timeout is None else max(0.5, min(float(timeout), _TIMEOUT))
    with urllib.request.urlopen(peticion, timeout=espera) as respuesta:
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


def _moneda_tx(tx):
    """Moneda del movimiento (`currency`/`coin`/`asset`), en mayúsculas."""
    for clave in ('currency', 'coin', 'asset'):
        valor = tx.get(clave)
        if valor:
            return str(valor).strip().upper()
    return None


def _consultar_ventana(desde_ms, hasta_ms, presupuesto_restante=None):
    """Movimientos de Binance Pay en el rango (máx. 100 por respuesta).

    ``presupuesto_restante`` recorta el timeout de la petición para que una
    sola llamada no se coma el presupuesto total de la verificación.
    """
    timeout = None
    if presupuesto_restante is not None:
        timeout = min(_TIMEOUT_MAX_PETICION, max(0.5, float(presupuesto_restante)))
    data = _get_firmado(
        '/sapi/v1/pay/transactions',
        {
            'startTime': int(desde_ms),
            'endTime': int(hasta_ms),
            'limit': _TAMANO_PAGINA,
        },
        timeout=timeout,
    )
    return _extraer_transacciones(data)


def _primera_coincidencia(transacciones, order_id):
    for tx in transacciones:
        if _coincide_orden(tx, order_id):
            return tx
    return None


def _buscar_orden(order_id, desde_ms, hasta_ms, deadline=None):
    """Devuelve (encontrada, tx, concluyente).

    ``concluyente`` es False cuando no se puede afirmar que la orden no exista:
    la API devolvió páginas llenas en toda la ventana (volumen altísimo) o se
    agotó el presupuesto de tiempo.

    ``deadline`` es un instante de ``time.monotonic()``; antes de cada petición
    se comprueba el tiempo restante y, si no queda, se corta y se devuelve
    inconcluyente en lugar de seguir esperando a la API.
    """
    def _restante():
        if deadline is None:
            return None
        return deadline - time.monotonic()

    restante = _restante()
    if restante is not None and restante <= 0:
        return False, None, False

    transacciones = _consultar_ventana(desde_ms, hasta_ms, restante)
    tx = _primera_coincidencia(transacciones, order_id)
    if tx is not None:
        return True, tx, True
    if len(transacciones) < _TAMANO_PAGINA:
        return False, None, True

    # Página llena: puede haber truncamiento. Barrer la ventana en tramos de
    # 12 h, de lo más reciente a lo más antiguo, sin pasarse del presupuesto.
    fin = hasta_ms
    for _ in range(_MAX_SLICES):
        if fin <= desde_ms:
            break
        restante = _restante()
        if restante is not None and restante <= 0:
            logger.warning(
                f'{_LOG} presupuesto de tiempo agotado buscando {order_id}; '
                'se deja a revisión manual.'
            )
            return False, None, False
        inicio = max(desde_ms, fin - _SLICE_MS)
        transacciones = _consultar_ventana(inicio, fin, restante)
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
    # Tope duro: la verificación completa nunca puede superar el presupuesto.
    deadline = time.monotonic() + _presupuesto_segundos()

    try:
        encontrada, tx, concluyente = _buscar_orden(
            order_id, desde_ms, hasta_ms, deadline
        )
        if not encontrada:
            if concluyente:
                logger.warning(
                    f'{_LOG} orden {order_id} NO está en Binance '
                    f'(ventana {_dias_ventana()} días).'
                )
                return False
            logger.warning(
                f'{_LOG} no se pudo confirmar {order_id} (volumen alto en la '
                f'ventana o presupuesto agotado); se deja a revisión manual.'
            )
            return None

        monto = _monto_tx(tx)
        moneda = _moneda_tx(tx)
        if moneda and moneda != MONEDA_ESPERADA:
            logger.warning(
                f'{_LOG} orden {order_id} existe pero en {moneda}, '
                f'no en {MONEDA_ESPERADA}.'
            )
            return False

        if objetivo_monto:
            if monto is None:
                logger.warning(
                    f'{_LOG} orden {order_id} sin monto en la respuesta; '
                    f'no se puede validar el importe (revisión manual).'
                )
                return None
            tolerancia = max(_TOLERANCIA_USD_ABS, objetivo_monto * _TOLERANCIA_USD_REL)
            diferencia = round(monto - objetivo_monto, 2)
            if diferencia < -tolerancia:
                logger.warning(
                    f'{_LOG} orden {order_id} incompleta: {monto:.2f} '
                    f'de {objetivo_monto:.2f} {MONEDA_ESPERADA} '
                    f'(faltan {abs(diferencia):.2f}).'
                )
                return False
            if diferencia > tolerancia:
                logger.warning(
                    f'{_LOG} orden {order_id} excedida: {monto:.2f} '
                    f'de {objetivo_monto:.2f} {MONEDA_ESPERADA} '
                    f'(sobran {diferencia:.2f}).'
                )
                return False

        logger.info(
            f'{_LOG} orden {order_id} confirmada en Binance '
            f'({monto if monto is not None else "monto no informado"} '
            f'{MONEDA_ESPERADA}).'
        )
        return True
    except Exception as error:
        logger.error(
            f'{_LOG} no se pudo verificar {order_id}: '
            f'{type(error).__name__}: {error}'
        )
        return None

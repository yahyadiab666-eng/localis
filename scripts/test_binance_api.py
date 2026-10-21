"""Verificación de Binance (API): pruebas SIN red.

Comprueba la firma HMAC, el parseo de transacciones, la búsqueda por ventana,
la validación de moneda/importe (incompleto, excedido, sin monto) y que sin
configurar la API devuelve None (no bloquea el flujo).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend import binance_api as b  # noqa: E402

FALLOS = []
ORDEN = '459426196324909056'


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def _tx(order_id, monto='20.00', moneda='USDT'):
    tx = {'orderId': order_id, 'amount': monto}
    if moneda is not None:
        tx['currency'] = moneda
    return tx


def _probar_busqueda():
    """Verificación completa con ``_get_firmado`` simulado (sin red)."""
    original = b._get_firmado
    os.environ['LOCALIS_BINANCE_API_KEY'] = 'clave-prueba'
    os.environ['LOCALIS_BINANCE_API_SECRET'] = 'secreto-prueba'
    llamadas = []
    guion = {'fn': lambda n: {'data': []}}

    def _falso_get(path, params):
        llamadas.append((path, dict(params)))
        return guion['fn'](len(llamadas))

    b._get_firmado = _falso_get
    try:
        print('\n=== Orden en la primera página ===')
        guion['fn'] = lambda n: {'data': [_tx('OTRA'), _tx(ORDEN)]}
        llamadas.clear()
        _ok(b.verificar_orden_binance(ORDEN, 20) is True, 'encontrada -> True')
        _ok(len(llamadas) == 1, 'una sola consulta si aparece de inmediato')
        params = llamadas[0][1]
        _ok(bool(params.get('startTime')) and bool(params.get('endTime')),
            'consulta con ventana startTime/endTime')
        _ok(params.get('limit') == 100, 'límite de 100 por página')

        print('\n=== Monto (tolerancia estricta en USD) ===')
        casos = [
            ('20.00', 20, True),
            ('19.99', 20, True),
            ('19.00', 20, False),
            ('18.99', 20, False),
            ('20.50', 20, False),
            ('1.00', 2, False),
            ('1.98', 2, True),
        ]
        for monto, esperado, valido in casos:
            guion['fn'] = lambda n, m=monto: {'data': [_tx(ORDEN, m)]}
            resultado = b.verificar_orden_binance(ORDEN, esperado) is True
            _ok(resultado == valido, f'{monto} vs {esperado} -> {resultado}')

        print('\n=== Moneda distinta a USDT ===')
        guion['fn'] = lambda n: {'data': [_tx(ORDEN, '20.00', 'BUSD')]}
        _ok(b.verificar_orden_binance(ORDEN, 20) is False, 'BUSD -> False')

        guion['fn'] = lambda n: {'data': [_tx(ORDEN, '20.00', None)]}
        _ok(b.verificar_orden_binance(ORDEN, 20) is True, 'sin campo moneda -> True')

        print('\n=== Orden sin monto informado ===')
        guion['fn'] = lambda n: {'data': [{'orderId': ORDEN, 'currency': 'USDT'}]}
        _ok(b.verificar_orden_binance(ORDEN, 20) is None, 'sin monto -> None (revisión)')
        _ok(b.verificar_orden_binance(ORDEN, None) is True, 'sin monto esperado -> True')

        print('\n=== No está (respuesta no truncada) ===')
        guion['fn'] = lambda n: {'data': [_tx('OTRA')]}
        llamadas.clear()
        _ok(b.verificar_orden_binance(ORDEN, 20) is False, 'ausente -> False')
        _ok(len(llamadas) == 1, 'sin paginar cuando hay menos de 100 movimientos')

        print('\n=== Página llena: pagina hacia atrás ===')

        def _guion_truncado(n):
            if n == 1:
                return {'data': [_tx(f'X{i}') for i in range(100)]}
            return {'data': [_tx(ORDEN)]}

        guion['fn'] = _guion_truncado
        llamadas.clear()
        _ok(b.verificar_orden_binance(ORDEN, 20) is True,
            'la orden aparece en la 2.ª página -> True')
        _ok(len(llamadas) == 2, 'dos consultas al truncarse la primera')
        _ok(
            llamadas[1][1]['startTime'] > llamadas[0][1]['startTime']
            and llamadas[1][1]['endTime'] <= llamadas[0][1]['endTime'],
            'la segunda consulta afina el tramo más reciente',
        )

        print('\n=== Página llena en toda la ventana -> ambiguo ===')
        guion['fn'] = lambda n: {'data': [_tx(f'X{i}') for i in range(100)]}
        llamadas.clear()
        _ok(b.verificar_orden_binance(ORDEN, 20) is None,
            'sin concluir -> None (no bloquea, va a revisión manual)')
        _ok(len(llamadas) > 1, 'barre la ventana cuando la página viene llena')
        _ok(len(llamadas) <= 1 + b._MAX_SLICES, 'barrido limitado por _MAX_SLICES')

        print('\n=== Error de red -> None ===')

        def _falla(path, params):
            raise TimeoutError('sin red')

        b._get_firmado = _falla
        _ok(b.verificar_orden_binance(ORDEN, 20) is None, 'excepción -> None')
    finally:
        b._get_firmado = original
        os.environ.pop('LOCALIS_BINANCE_API_KEY', None)
        os.environ.pop('LOCALIS_BINANCE_API_SECRET', None)


def main() -> int:
    print('=== Sin credenciales -> None (no bloquea) ===')
    os.environ.pop('LOCALIS_BINANCE_API_KEY', None)
    os.environ.pop('LOCALIS_BINANCE_API_SECRET', None)
    _ok(not b.configurado(), 'configurado() False sin llaves')
    _ok(b.verificar_orden_binance('123456789012', 10) is None, 'sin llaves -> None')

    print('\n=== Firma HMAC-SHA256 correcta ===')
    firma = b._firmar('a=1&b=2', 'secreto')
    esperado = hmac.new(b'secreto', b'a=1&b=2', hashlib.sha256).hexdigest()
    _ok(firma == esperado and len(firma) == 64, f'firma coincide ({firma[:12]}…)')

    print('\n=== Parseo de transacciones ===')
    _ok(b._extraer_transacciones({'data': [{'a': 1}]}) == [{'a': 1}], 'lista en "data"')
    _ok(b._extraer_transacciones([1, 2]) == [1, 2], 'lista directa')
    _ok(b._extraer_transacciones({'x': 1}) == [], 'sin lista -> []')

    print('\n=== Coincidencia de orden y monto ===')
    _ok(b._coincide_orden({'orderId': '45942'}, '45942'), 'coincide por orderId')
    _ok(not b._coincide_orden({'orderId': '1'}, '45942'), 'no coincide distinto')
    _ok(b._monto_tx({'amount': '20.00'}) == 20.0, 'monto parseado')
    _ok(b._monto_tx({'otro': 1}) is None, 'sin monto -> None')
    _ok(b._moneda_tx({'currency': 'usdt'}) == 'USDT', 'moneda normalizada')
    _ok(b._moneda_tx({'otro': 1}) is None, 'sin moneda -> None')

    _probar_busqueda()

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK Binance API: firma, ventana, moneda, importes y degradación segura')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

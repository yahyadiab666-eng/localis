"""Destacados: validación de referencias de pago (sin BD ni red).

Comprueba que el formato de referencia distingue Pago Móvil (6 dígitos) de
Binance Pay (ID de orden de 6-24 dígitos) y que precios/opciones son estables.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend.boosts import (  # noqa: E402
    boost_activo,
    opciones_boost,
    precio_boost,
    referencia_valida,
    sanitizar_referencia,
)

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def main() -> int:
    print('=== Sanitización ===')
    _ok(sanitizar_referencia(' 12-34 56 ') == '123456', 'deja solo dígitos')
    _ok(sanitizar_referencia(None) == '', 'None -> cadena vacía')

    print('\n=== Pago móvil: exactamente 6 dígitos ===')
    _ok(referencia_valida('123456') is True, '6 dígitos válidos')
    _ok(referencia_valida('123456', 'pago_movil_ocr') is True, 'método explícito')
    _ok(referencia_valida('12345') is False, '5 dígitos rechazados')
    _ok(referencia_valida('1234567') is False, '7 dígitos rechazados')
    _ok(referencia_valida('') is False, 'vacía rechazada')
    _ok(referencia_valida('12A4567') is True, 'normaliza letras antes de validar')

    print('\n=== Binance Pay: ID de orden 6-24 dígitos ===')
    _ok(referencia_valida('459421234567', 'binance') is True, '12 dígitos válidos')
    _ok(referencia_valida('123456', 'binance') is True, '6 dígitos válidos')
    _ok(referencia_valida('1' * 24, 'binance') is True, '24 dígitos válidos')
    _ok(referencia_valida('1' * 25, 'binance') is False, '25 dígitos rechazados')
    _ok(referencia_valida('', 'binance') is False, 'vacía rechazada')
    _ok(referencia_valida('12345', 'binance') is False, '5 dígitos rechazados')

    print('\n=== Precios y opciones ===')
    _ok(precio_boost(3) == 2.0 and precio_boost(5) == 3.0 and precio_boost(7) == 4.0,
        'precios 3/5/7 días')
    _ok(precio_boost(4) is None and precio_boost('x') is None, 'duración inválida -> None')
    opciones = opciones_boost()
    _ok([o['dias'] for o in opciones] == [3, 5, 7], 'opciones ordenadas 3/5/7')
    _ok(all(o['precio_usd'] > 0 for o in opciones), 'precios positivos')

    print('\n=== Vigencia del boost ===')
    ahora = datetime.now(timezone.utc)
    _ok(boost_activo(ahora + timedelta(days=1)) is True, 'futuro -> activo')
    _ok(boost_activo(ahora - timedelta(days=1)) is False, 'pasado -> inactivo')
    _ok(boost_activo(None) is False, 'None -> inactivo')
    _ok(boost_activo('no-es-fecha') is False, 'texto inválido -> inactivo')

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK Destacados: referencias de pago móvil/Binance y precios')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

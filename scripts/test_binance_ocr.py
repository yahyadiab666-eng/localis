"""OCR de Binance Pay: verificacion SIN red ni BD (usa el texto de una captura real).

Comprueba que el evaluador extrae monto (USDT), fecha e ID de orden, que
distingue monto incompleto de excedido y que rechaza capturas invalidas.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend.payment_ocr import (  # noqa: E402
    MONTO_EXCEDIDO,
    MONTO_INCOMPLETO,
    MONTO_OK,
    evaluar_texto_binance,
)

# Texto tal como aparece en una captura real de Binance Pay (receptor).
RECIBO = (
    'Detalles del pago Monto +20 USDT Completado Se envio cripto a tu cuenta '
    'De User-ae830 Nota -- Fecha 2026-10-11 22:56:34 '
    'ID de la orden 459426196324909056'
)

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def main() -> int:
    hoy = date(2026, 10, 11)

    print('=== Captura válida (monto correcto) ===')
    r = evaluar_texto_binance(RECIBO, 20, ahora=hoy)
    _ok(r['ok'], f"ok -> {r.get('errores')}")
    _ok(r['referencia'] == '459426196324909056', f"ID de orden -> {r.get('referencia')}")
    _ok(r['monto_usd'] == 20.0, f"monto -> {r.get('monto_usd')}")
    _ok(r['fecha_comprobante'] == '2026-10-11', f"fecha -> {r.get('fecha_comprobante')}")
    _ok(r['estado_monto'] == MONTO_OK, f"estado -> {r.get('estado_monto')}")

    print('\n=== Monto excedido: error específico ===')
    r2 = evaluar_texto_binance(RECIBO, 10, ahora=hoy)
    _ok(not r2['ok'] and r2['estado_monto'] == MONTO_EXCEDIDO, '20 vs 10 -> excedido')
    _ok(any('supera al esperado' in e for e in r2['errores']), 'mensaje de excedido')

    print('\n=== Monto incompleto: error específico ===')
    for esperado, texto in ((20, '19.5'), (20, '19'), (2, '1')):
        recibo = RECIBO.replace('+20 USDT', f'+{texto} USDT')
        r3 = evaluar_texto_binance(recibo, esperado, ahora=hoy)
        _ok(
            not r3['ok'] and r3['estado_monto'] == MONTO_INCOMPLETO,
            f'{texto} vs {esperado} -> incompleto',
        )
        _ok(
            any('es menor al esperado' in e for e in r3['errores']),
            f'mensaje de incompleto para {texto}',
        )

    print('\n=== Centavos dentro de tolerancia ===')
    r4 = evaluar_texto_binance(RECIBO.replace('+20 USDT', '+19.99 USDT'), 20, ahora=hoy)
    _ok(r4['ok'] and r4['estado_monto'] == MONTO_OK, '19.99 vs 20 -> ok')

    print('\n=== Captura en inglés (fecha con mes) ===')
    recibo_en = RECIBO.replace('2026-10-11 22:56:34', 'Oct 11, 2026')
    r5 = evaluar_texto_binance(recibo_en, 20, ahora=hoy)
    _ok(r5['ok'] and r5['fecha_comprobante'] == '2026-10-11', f"fecha EN -> {r5.get('fecha_comprobante')}")

    print('\n=== Rechaza si no es de Binance/USDT ===')
    r6 = evaluar_texto_binance('Pago movil Banco Caribe ref 123456 monto 500 Bs', 20, ahora=hoy)
    _ok(not r6['ok'], 'captura sin USDT/Binance rechazada')

    print('\n=== Rechaza fecha vieja ===')
    r7 = evaluar_texto_binance(RECIBO, 20, ahora=date(2026, 12, 1))
    _ok(not r7['ok'] and any('fuera del rango' in e for e in r7['errores']), 'fecha antigua rechazada')

    print('\n=== Tolera basura (nunca lanza) ===')
    crash = False
    for malo in ('', None, 'x' * 5000):
        try:
            evaluar_texto_binance(malo, 20, ahora=hoy)
        except Exception as error:
            crash = True
            print(f'    fallo: {error}')
    _ok(not crash, 'entradas basura -> sin excepción')

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK OCR Binance: montos exactos, incompleto/excedido, fechas y rechazos')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

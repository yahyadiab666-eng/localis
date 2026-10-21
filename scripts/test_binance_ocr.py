"""OCR de Binance Pay: verificacion SIN red ni BD (usa el texto de una captura real).

Comprueba que el evaluador extrae monto (USDT), fecha e ID de orden, y que
rechaza montos incorrectos, capturas que no son de Binance y fechas viejas.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend.payment_ocr import evaluar_texto_binance  # noqa: E402

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

    print('\n=== Rechaza monto incorrecto ===')
    r2 = evaluar_texto_binance(RECIBO, 10, ahora=hoy)
    _ok(not r2['ok'] and any('no coincide' in e for e in r2['errores']), 'monto 10 vs 20 rechazado')

    print('\n=== Rechaza si no es de Binance/USDT ===')
    r3 = evaluar_texto_binance('Pago movil Banco Caribe ref 123456 monto 500 Bs', 20, ahora=hoy)
    _ok(not r3['ok'], 'captura sin USDT/Binance rechazada')

    print('\n=== Rechaza fecha vieja ===')
    r4 = evaluar_texto_binance(RECIBO, 20, ahora=date(2026, 12, 1))
    _ok(not r4['ok'] and any('fuera del rango' in e for e in r4['errores']), 'fecha antigua rechazada')

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
    print('OK OCR Binance: monto, fecha e ID de orden; rechaza lo inválido')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

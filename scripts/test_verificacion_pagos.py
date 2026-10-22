"""Auditoría de la verificación de pagos: pruebas SIN BD ni red.

Comprueba el mapeo de resultados (Binance confirmado / no encontrado / no
verificado, y pago móvil) y que ``guardar_verificacion`` nunca lanza (sin
comercio/referencia válidos falla en silencio).
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend.verificacion_pagos import (  # noqa: E402
    TIPO_REVISION_PAGO,
    VERIF_BINANCE_CONFIRMADO,
    VERIF_BINANCE_NO_ENCONTRADO,
    VERIF_BINANCE_NO_VERIFICADO,
    VERIF_PAGO_MOVIL_OCR,
    describir_revision_pago,
    describir_verificacion,
    guardar_verificacion,
    motivo_revision,
)

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def main() -> int:
    print('=== Binance: confirmado ===')
    codigo, detalle = describir_verificacion('binance', True, 'ok', 2.0)
    _ok(codigo == VERIF_BINANCE_CONFIRMADO, f'codigo={codigo}')
    _ok('api_binance=confirmado' in detalle, f'detalle={detalle}')
    _ok('monto=2.0' in detalle and 'estado_monto=ok' in detalle, 'incluye monto y estado')

    print('\n=== Binance: no encontrado ===')
    codigo, _ = describir_verificacion('binance', False)
    _ok(codigo == VERIF_BINANCE_NO_ENCONTRADO, f'codigo={codigo}')

    print('\n=== Binance: no verificado (API caída/sin monto) ===')
    codigo, detalle = describir_verificacion('binance', None)
    _ok(codigo == VERIF_BINANCE_NO_VERIFICADO, f'codigo={codigo}')
    _ok('api_binance=no_verificado' in detalle, 'detalle no verificado')

    print('\n=== Pago móvil ===')
    codigo, _ = describir_verificacion('pago_movil_ocr', None, 'ok', 2000.0)
    _ok(codigo == VERIF_PAGO_MOVIL_OCR, f'codigo={codigo}')
    codigo, _ = describir_verificacion('', None)
    _ok(codigo == VERIF_PAGO_MOVIL_OCR, 'método vacío -> pago móvil')

    print('\n=== Sin datos no inventa ===')
    _, detalle = describir_verificacion('binance', True, None, None)
    _ok('monto=' not in detalle and 'estado_monto=' not in detalle,
        'sin monto/estado cuando no se informan')

    print('\n=== guardar_verificacion nunca lanza ===')
    _ok(guardar_verificacion(1, '', 'binance', True) is False, 'referencia vacía -> False')

    print('\n=== Motivo de revisión ===')
    _ok(motivo_revision(True) is None, 'confirmado -> sin revisión')
    _ok(motivo_revision(False) == 'no_encontrado', 'no encontrado')
    _ok(motivo_revision(None) == 'no_verificado', 'no verificado')

    print('\n=== Bandeja: mensaje de pago RECHAZADO ===')
    motivo, mensaje = describir_revision_pago(
        'binance', False, referencia='459426196324909056', monto=2.0,
        moneda='USDT', fecha_comprobante='2026-10-21',
        nombre_comercio='Tienda Demo', comercio_id=7,
    )
    _ok(motivo == 'no_encontrado', f'motivo={motivo}')
    _ok('RECHAZADO' in mensaje, 'marca rechazo')
    _ok('459426196324909056' in mensaje, 'incluye ID de orden')
    _ok('2.0 USDT' in mensaje, 'incluye monto')
    _ok('Tienda Demo (ID 7)' in mensaje, 'incluye comercio')
    _ok('2026-10-21' in mensaje, 'incluye fecha del comprobante')

    print('\n=== Bandeja: mensaje de pago SIN VERIFICAR ===')
    motivo, mensaje = describir_revision_pago('binance', None, referencia='123456789012')
    _ok(motivo == 'no_verificado', f'motivo={motivo}')
    _ok('SIN VERIFICAR' in mensaje, 'marca no verificado')
    _ok('Monto esperado' not in mensaje, 'sin monto no lo inventa')

    print('\n=== Bandeja: no genera alerta si está confirmado ===')
    _ok(describir_revision_pago('binance', True, referencia='x') == (None, ''),
        'confirmado -> sin mensaje')
    _ok(TIPO_REVISION_PAGO == 'revision_pago', 'tipo interno estable')

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK auditoría de verificación: códigos y detalle por método')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

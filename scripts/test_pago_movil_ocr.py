"""OCR de Pago Móvil: extracción y validación (sin BD ni red).

Cubre referencia etiquetada, montos con/sin decimales y formato US, fechas
numéricas y con mes, datos destino configurables y clasificación de montos
(incompleto / excedido / ok).
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
    MONTO_NO_DETECTADO,
    MONTO_OK,
    _contiene_banco,
    _contiene_rif,
    _contiene_telefono,
    _extraer_fechas,
    _extraer_montos_candidatos,
    _extraer_referencia,
    _parse_monto_ve,
    clasificar_monto,
    tolerancia_monto,
)

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def main() -> int:
    print('=== Referencia: prioriza la etiquetada ===')
    _ok(_extraer_referencia('Referencia: 123456 Fecha: 02/10/2026') == '123456',
        'referencia etiquetada')
    _ok(_extraer_referencia('Ref123456 Fecha: 02/10/2026') == '123456',
        'referencia pegada a la etiqueta')
    _ok(_extraer_referencia('Pago Nro. 998877 monto 500 Bs') == '998877',
        'Nro. etiquetado')
    _ok(_extraer_referencia('Banco Caribe monto 500000,00 Bs sin referencia') is None,
        'no confunde un monto con la referencia')
    _ok(_extraer_referencia('Pago 123456789 6 digitos sueltos 654321') == '654321',
        'token suelto válido (no número largo)')
    _ok(_extraer_referencia('') is None, 'texto vacío -> None')

    print('\n=== Montos: formatos y etiquetas ===')
    _ok(_extraer_montos_candidatos('Monto 500,00 Bs') == [500.0], '500,00')
    _ok(_extraer_montos_candidatos('Monto 500 Bs') == [500.0], 'sin decimales')
    _ok(_extraer_montos_candidatos('Monto 1.234,56 Bs') == [1234.56], '1.234,56')
    _ok(_extraer_montos_candidatos('Monto 1,234.56 Bs') == [1234.56], '1,234.56 (US)')
    _ok(_extraer_montos_candidatos('Total: Bs. 2.000,00') == [2000.0], 'Bs. 2.000,00')
    _ok(_parse_monto_ve('1.234,56') == 1234.56, 'parse VE')
    _ok(_parse_monto_ve('1,234.56') == 1234.56, 'parse US')

    print('\n=== Clasificación de montos (Bs) ===')
    _ok(clasificar_monto(2000, 2000, 'BS')[0] == MONTO_OK, 'exacto -> ok')
    _ok(clasificar_monto(2040, 2000, 'BS')[0] == MONTO_OK, '2% dentro de tolerancia')
    _ok(clasificar_monto(2050, 2000, 'BS')[0] == MONTO_EXCEDIDO, 'sobrepago -> excedido')
    _ok(clasificar_monto(1950, 2000, 'BS')[0] == MONTO_INCOMPLETO, 'subpago -> incompleto')
    _ok(clasificar_monto(None, 2000, 'BS')[0] == MONTO_NO_DETECTADO, 'sin monto')
    _ok(round(tolerancia_monto(2000, 'BS'), 2) == 40.0, 'tolerancia 2% en Bs')

    print('\n=== Clasificación de montos (USD) ===')
    _ok(clasificar_monto(2, 2, 'USD')[0] == MONTO_OK, 'exacto -> ok')
    _ok(clasificar_monto(1.99, 2, 'USD')[0] == MONTO_OK, '1 centavo -> ok')
    _ok(clasificar_monto(1.90, 2, 'USD')[0] == MONTO_INCOMPLETO, '1.90 vs 2 -> incompleto')
    _ok(clasificar_monto(1, 2, 'USD')[0] == MONTO_INCOMPLETO, '1 vs 2 -> incompleto')
    _ok(clasificar_monto(2.50, 2, 'USD')[0] == MONTO_EXCEDIDO, '2.50 vs 2 -> excedido')
    _ok(round(tolerancia_monto(2, 'USD'), 2) == 0.02, 'tolerancia en centavos')

    print('\n=== Fechas ===')
    hoy = date(2026, 10, 21)
    casos = [
        ('02/10/2026', date(2026, 10, 2)),
        ('2026-10-02', date(2026, 10, 2)),
        ('2 oct 2026', date(2026, 10, 2)),
        ('2 de octubre de 2026', date(2026, 10, 2)),
        ('Oct 2, 2026', date(2026, 10, 2)),
    ]
    for texto, esperada in casos:
        _ok(esperada in _extraer_fechas(texto), f'fecha {texto!r}')
    _ok(hoy not in _extraer_fechas('sin fecha'), 'texto sin fecha -> []')

    print('\n=== Datos destino configurables ===')
    _ok(_contiene_banco('pagaste a banco banesco', 'Banco Banesco'), 'banco distinto')
    _ok(_contiene_rif('RIF 12345678', '12345678'), 'RIF configurado')
    _ok(_contiene_telefono('telefono 04141234567', '04141234567'), 'teléfono configurado')
    _ok(not _contiene_telefono('telefono 04141234567', '04149999999'),
        'teléfono distinto no pasa')

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK OCR Pago Móvil: referencias, montos, fechas y destinos')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Robustez del OCR de pago móvil (SIN red ni BD; no necesita motor OCR).

Objetivo doble:

1. **No rechazar comprobantes legítimos.** Variantes reales de formato y de
   confusiones típicas del OCR (O por 0, referencias con separadores, montos sin
   decimales, teléfonos con guiones...).
2. **No aceptar lo que no debe.** Los casos negativos son la garantía de que
   aflojar la lectura no abrió la puerta a un comprobante ajeno, con el monto
   equivocado, de otro banco o con fecha vieja.

Trabaja sobre TEXTO ya extraído (``evaluar_texto_pago_movil``), así que corre en
milisegundos y es determinista.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend.payment_ocr import (  # noqa: E402
    _extraer_montos_candidatos,
    _extraer_referencia,
    _parse_monto_ve,
    evaluar_texto_binance,
    evaluar_texto_pago_movil,
)

FALLOS = []
HOY = date(2026, 10, 21)
ESPERADO = 2000.0

BASE = """Pago Movil
Banco Caribe
Fecha: 21/10/2026 10:35 AM
Referencia: 456789
Monto: Bs. 2.000,00
Telefono destino: 04127957989
RIF: 30209716
Beneficiario: LOCALIS"""


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def _acepta(texto, esperado=ESPERADO):
    return evaluar_texto_pago_movil(texto, esperado, ahora=HOY)


def main() -> int:
    print('=== POSITIVOS: variantes legítimas (deben ACEPTARSE) ===')
    positivos = {
        'base': BASE,
        'referencia pegada': BASE.replace('Referencia: 456789', 'Referencia:456789'),
        'referencia con punto de miles': BASE.replace('Referencia: 456789', 'Referencia: 456.789'),
        'referencia con espacio': BASE.replace('Referencia: 456789', 'Referencia: 45 6789'),
        'referencia tras Nro.': BASE.replace('Referencia: 456789', 'Nro. de operacion 456789'),
        'referencia con barra': BASE.replace('Referencia: 456789', 'Ref:456789|'),
        'banco con C.A.': BASE.replace('Banco Caribe', 'BANCO CARIBE, C.A.'),
        'banco solo Caribe': BASE.replace('Banco Caribe', 'Caribe'),
        'banco con O por cero': BASE.replace('Banco Caribe', 'Banc0 Caribe'),
        'telefono con O por cero': BASE.replace('04127957989', 'O4127957989'),
        'telefono con guiones': BASE.replace('04127957989', '0412-795-79-89'),
        'telefono con espacios': BASE.replace('04127957989', '0412 795 7989'),
        'telefono sin cero inicial': BASE.replace('04127957989', '4127957989'),
        'telefono con +58': BASE.replace('04127957989', '+58 412 795 7989'),
        'rif con guiones': BASE.replace('30209716', '302-09716'),
        'monto sin decimales (2.000 son 2000)': BASE.replace('Bs. 2.000,00', 'Bs. 2.000'),
        'monto formato US': BASE.replace('Bs. 2.000,00', 'Bs. 2000.00'),
        'monto sin etiqueta': BASE.replace('Monto: Bs. 2.000,00', '2.000,00'),
        'monto con espacio de miles': BASE.replace('Bs. 2.000,00', 'Bs. 2 000,00'),
        'monto con O por cero': BASE.replace('Bs. 2.000,00', 'Bs. 2.OOO,OO'),
        'monto en linea aparte': BASE.replace('Monto: Bs. 2.000,00', 'Bs.\n2.000,00'),
        'fecha con guiones': BASE.replace('21/10/2026', '21-10-2026'),
        'fecha con mes en texto': BASE.replace('21/10/2026', '21 oct 2026'),
        'fecha en formato ISO': BASE.replace('21/10/2026', '2026-10-21'),
        'todo en una línea': ' '.join(BASE.split()),
        'todo en mayúsculas': BASE.upper(),
        'palabra cortada por salto': BASE.replace('Referencia: 456789', 'Referen\ncia: 456789'),
        'con ruido alrededor': 'CONSULTA #### ' + BASE + ' #### GRACIAS',
    }
    for nombre, texto in positivos.items():
        r = _acepta(texto)
        _ok(r['ok'], f'acepta: {nombre}  {"-> " + str(r["errores"]) if not r["ok"] else ""}')

    print('\n=== NEGATIVOS: no debe aceptarlos (antifraude intacto) ===')
    negativos = {
        'sin referencia': 'Banco Caribe monto 500000,00 Bs 21/10/2026 '
                          'telefono 04127957989 RIF 30209716',
        'monto excedido': BASE.replace('Bs. 2.000,00', 'Bs. 5.000,00'),
        'monto incompleto': BASE.replace('Bs. 2.000,00', 'Bs. 1.000,00'),
        'monto ausente': BASE.replace('Monto: Bs. 2.000,00', 'Sin monto visible'),
        'otro banco': BASE.replace('Banco Caribe', 'Banco Mercantil'),
        'rif ajeno': BASE.replace('30209716', '99999999'),
        'telefono ajeno': BASE.replace('04127957989', '04129999999'),
        'fecha vieja': BASE.replace('21/10/2026', '01/10/2026'),
        'sin fecha': BASE.replace('Fecha: 21/10/2026 10:35 AM', 'Sin fecha'),
        'referencia mezclada con fecha': BASE.replace(
            'Referencia: 456789\nMonto', 'Referencia: 456.789 21/10/2026\nMonto'
        ),
        'referencia de 5 digitos': BASE.replace('Referencia: 456789', 'Referencia: 45678'),
        'comprobante de otro banco con monto ok': (
            'Banco Nacional de Credito\nFecha: 21/10/2026\nReferencia: 456789\n'
            'Monto: Bs. 2.000,00\nTelefono: 04127957989\nRIF: 30209716'
        ),
    }
    for nombre, texto in negativos.items():
        r = _acepta(texto)
        _ok(not r['ok'], f'rechaza: {nombre}  {"(!!) " + str(r["referencia"]) if r["ok"] else ""}')

    print('\n=== Lecturas puntuales (números y referencias) ===')
    _ok(_parse_monto_ve('2.000') == 2000.0, '2.000 -> 2000 (miles, formato VE)')
    _ok(_parse_monto_ve('2.00') == 2.0, '2.00 -> 2 (decimal)')
    _ok(_parse_monto_ve('1.250.000') == 1250000.0, '1.250.000 -> 1250000')
    _ok(_parse_monto_ve('2.OOO,OO') == 2000.0, 'O por cero -> 2000')
    _ok(_parse_monto_ve('1.234,56') == 1234.56, '1.234,56 -> 1234.56')
    _ok(_parse_monto_ve('1,234.56') == 1234.56, '1,234.56 -> 1234.56')
    _ok(_parse_monto_ve('') is None, 'vacío -> None')

    _ok(_extraer_referencia('Referencia: 456.789') == '456789', 'une punto de miles')
    _ok(_extraer_referencia('Referencia: 45 6789') == '456789', 'une espacio')
    _ok(_extraer_referencia('Referencia: 456789') == '456789', '6 dígitos directos')
    _ok(_extraer_referencia('sin etiqueta ni numero de 6') is None, 'sin referencia -> None')
    _ok(_extraer_referencia('monto 500000,00 Bs') is None, 'no confunde monto con referencia')
    _ok(
        _extraer_referencia('Referencia: 456.789 21/10/2026') is None,
        'no mezcla la referencia con la fecha siguiente',
    )

    print('\n=== Montos: no se cuelan cifras parciales ===')
    _ok(_extraer_montos_candidatos('Monto 2.000 Bs') == [2000.0], '2.000 sin decimales = 2000')
    _ok(_extraer_montos_candidatos('Monto 500,00 Bs') == [500.0], '500,00')
    _ok(
        _extraer_montos_candidatos('Vence 21.10.2026') == [],
        'una fecha no produce un monto',
    )

    print('\n=== Binance: variantes legítimas y fraudes ===')
    captura = (
        'Detalles del pago Monto +20 USDT Completado Se envio cripto a tu cuenta '
        'De User-ae830 Nota -- Fecha 2026-10-21 22:56:34 '
        'ID de la orden 459426196324909056'
    )
    r = evaluar_texto_binance(captura, 20, ahora=HOY)
    _ok(r['ok'], f'captura base aceptada  {r["errores"] if not r["ok"] else ""}')
    _ok(r['referencia'] == '459426196324909056', 'extrae el ID de la orden')
    _ok(r['monto_usd'] == 20.0, 'extrae el monto en USDT')

    variantes = {
        'monto sin decimales': captura.replace('+20 USDT', '+20 USDT'),
        'monto con dos decimales': captura.replace('+20 USDT', '+20.00 USDT'),
        'monto con espacio': captura.replace('+20 USDT', '+ 20 USDT'),
        'moneda en minuscula': captura.replace('USDT', 'usdt'),
        'fecha con mes en texto': captura.replace('2026-10-21 22:56:34', 'Oct 21, 2026'),
        'id con espacio previo': captura.replace('ID de la orden', 'ID de la 0rden'),
        'lineas separadas': captura.replace(' Monto ', '\nMonto\n').replace(' Fecha ', '\nFecha\n'),
    }
    for nombre, texto in variantes.items():
        rr = evaluar_texto_binance(texto, 20, ahora=HOY)
        _ok(rr['ok'], f'Binance acepta: {nombre}  {rr["errores"] if not rr["ok"] else ""}')

    rechazos = {
        'monto incompleto': captura.replace('+20 USDT', '+19 USDT'),
        'monto excedido': captura.replace('+20 USDT', '+25 USDT'),
        'sin ID de orden': captura.replace('ID de la orden 459426196324909056', ''),
        'sin USDT ni Binance': 'Pago movil Banco Caribe ref 123456 monto 500 Bs',
        'fecha vieja': captura,
    }
    for nombre, texto in rechazos.items():
        esperado = date(2026, 12, 1) if nombre == 'fecha vieja' else HOY
        rr = evaluar_texto_binance(texto, 20, ahora=esperado)
        _ok(not rr['ok'], f'Binance rechaza: {nombre}')

    print('\n=== Disparador de la segunda pasada de OCR ===')
    from backend.payment_ocr import (  # noqa: E402
        _texto_binance_incompleto,
        _texto_pago_movil_incompleto,
    )

    completo = BASE
    _ok(
        _texto_pago_movil_incompleto(completo) is False,
        'texto completo -> NO paga un OCR extra',
    )
    _ok(
        _texto_pago_movil_incompleto(completo.replace('04127957989', '')) is True,
        'sin teléfono -> segunda pasada (antes se rechazaba sin intentarlo)',
    )
    _ok(
        _texto_pago_movil_incompleto('Banco Caribe Referencia: 456789') is True,
        'sin monto ni fecha -> segunda pasada',
    )
    _ok(_texto_pago_movil_incompleto('') is True, 'texto vacío -> segunda pasada')

    _ok(
        _texto_binance_incompleto(captura, 20) is False,
        'Binance completo -> NO paga un OCR extra',
    )
    _ok(
        _texto_binance_incompleto(captura.replace('+20 USDT', ''), 20) is True,
        'Binance sin monto -> segunda pasada',
    )
    _ok(
        _texto_binance_incompleto('ID de la orden 459426196324909056', 20) is True,
        'Binance sin fecha ni monto -> segunda pasada',
    )

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK robustez OCR pago móvil: más aceptaciones sin aflojar el antifraude')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

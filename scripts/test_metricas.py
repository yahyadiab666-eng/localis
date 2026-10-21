"""Metricas y error-tracking: verificacion SIN red ni BD.

Comprueba el comportamiento observable de ``backend/metricas.py`` y que
``backend/error_tracking.py`` es no-op sin DSN (no cambia nada por defecto).
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend import metricas  # noqa: E402
from backend.error_tracking import iniciar_error_tracking  # noqa: E402

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def main() -> int:
    print('=== Metricas: registrar y snapshot ===')
    metricas.registrar(200, 10.0)
    metricas.registrar(302, 20.0)
    metricas.registrar(500, 30.0)
    s = metricas.snapshot()
    _ok(s['peticiones']['total'] >= 3, f"total={s['peticiones']['total']}")
    _ok(s['peticiones']['5xx'] >= 1, f"5xx={s['peticiones']['5xx']}")
    _ok(s['peticiones']['2xx'] >= 1, f"2xx={s['peticiones']['2xx']}")
    _ok(s['latencia_ms']['p95'] >= 10.0, f"p95={s['latencia_ms']['p95']}")

    print('=== Metricas: formato Prometheus ===')
    texto = metricas.render_prometheus()
    _ok('localis_requests_total' in texto, 'expone contador total')
    _ok('quantile="0.95"' in texto, 'expone p95')
    _ok('localis_uptime_seconds' in texto, 'expone uptime')

    print('=== Metricas: OCR de comprobantes ===')
    metricas.registrar_ocr('binance', 'ok', 250.0, 'ok')
    metricas.registrar_ocr('binance', 'rechazado', 300.0, 'incompleto')
    metricas.registrar_ocr('pago_movil', 'rechazado', 420.0, 'excedido')
    s_ocr = metricas.snapshot()['ocr']
    _ok(s_ocr['total'] >= 3, f"ocr total={s_ocr['total']}")
    _ok(s_ocr['por_metodo']['binance']['ok'] >= 1, 'cuenta ok de binance')
    _ok(
        s_ocr['estado_monto']['binance']['incompleto'] >= 1,
        'cuenta monto incompleto',
    )
    _ok(
        s_ocr['estado_monto']['pago_movil']['excedido'] >= 1,
        'cuenta monto excedido',
    )
    _ok(s_ocr['latencia_ms']['p95'] >= 200.0, 'percentiles de OCR')
    texto_ocr = metricas.render_prometheus()
    _ok('localis_ocr_total{' in texto_ocr, 'Prometheus expone OCR')
    _ok('localis_ocr_monto_total{' in texto_ocr, 'Prometheus expone estado de monto')
    _ok('localis_ocr_duration_ms' in texto_ocr, 'Prometheus expone latencia OCR')

    print('=== Metricas: gating por entorno ===')
    os.environ.pop('LOCALIS_METRICS', None)
    _ok(not metricas.habilitado(), 'deshabilitado por defecto')
    os.environ['LOCALIS_METRICS'] = '1'
    _ok(metricas.habilitado(), 'habilitado con LOCALIS_METRICS=1')
    os.environ.pop('LOCALIS_METRICS', None)

    print('=== Sentry: no-op sin DSN ===')
    os.environ.pop('LOCALIS_SENTRY_DSN', None)
    _ok(
        iniciar_error_tracking(types.SimpleNamespace()) is False,
        'sin DSN no hace nada (devuelve False)',
    )

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK metricas + error-tracking: contadores, Prometheus y gating')
    return 0


if __name__ == '__main__':
    sys.exit(main())

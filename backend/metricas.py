"""Metricas ligeras en memoria (formato Prometheus text).

Sin dependencias externas. El estado vive **por proceso** (1 worker), suficiente
para observabilidad basica y alertas: contadores por clase de estado y
percentiles de latencia recientes.

Se publica en ``/metrics`` solo si ``LOCALIS_METRICS=1``.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

_lock = threading.Lock()
_inicio = time.time()
_totales = {'2xx': 0, '3xx': 0, '4xx': 0, '5xx': 0, 'otros': 0, 'total': 0}
_buffer = int(os.environ.get('LOCALIS_METRICS_BUFFER', '500') or 500)
_dur = deque(maxlen=max(50, _buffer))

# OCR de comprobantes (pago móvil / Binance): volumen, resultado y latencia.
_ocr_totales = {}   # metodo -> {resultado: n}
_ocr_estados = {}   # metodo -> {estado_monto: n}
_ocr_dur = deque(maxlen=max(50, _buffer))


def habilitado():
    """True si LOCALIS_METRICS activa la exposicion de /metrics."""
    return (os.environ.get('LOCALIS_METRICS') or '').strip().lower() in (
        '1', 'true', 'yes', 'on'
    )


def registrar(status, duracion_ms):
    """Registra una peticion. Nunca lanza (la observabilidad no rompe el request)."""
    try:
        clase = f'{int(status) // 100}xx'
    except (TypeError, ValueError):
        clase = 'otros'
    try:
        with _lock:
            _totales['total'] += 1
            if clase in _totales:
                _totales[clase] += 1
            else:
                _totales['otros'] += 1
            _dur.append(float(duracion_ms or 0.0))
    except Exception:
        pass


def _percentil(valores, p):
    if not valores:
        return 0.0
    orden = sorted(valores)
    k = int(round((p / 100.0) * (len(orden) - 1)))
    k = max(0, min(len(orden) - 1, k))
    return orden[k]


def registrar_ocr(metodo, resultado, duracion_ms, estado_monto=None):
    """Registra un OCR de comprobante. Nunca lanza.

    ``metodo``: ``pago_movil`` o ``binance``. ``resultado``: ``ok``,
    ``rechazado`` o ``invalido``. ``estado_monto``: ``ok``/``incompleto``/
    ``excedido``/``no_detectado``.
    """
    try:
        metodo = str(metodo or 'otro').strip().lower() or 'otro'
        resultado = str(resultado or 'otro').strip().lower() or 'otro'
        with _lock:
            por_resultado = _ocr_totales.setdefault(metodo, {})
            por_resultado[resultado] = por_resultado.get(resultado, 0) + 1
            if estado_monto:
                estado = str(estado_monto).strip().lower()
                por_estado = _ocr_estados.setdefault(metodo, {})
                por_estado[estado] = por_estado.get(estado, 0) + 1
            _ocr_dur.append(float(duracion_ms or 0.0))
    except Exception:
        pass


def snapshot():
    with _lock:
        durs = list(_dur)
        totales = dict(_totales)
        ocr_totales = {m: dict(r) for m, r in _ocr_totales.items()}
        ocr_estados = {m: dict(e) for m, e in _ocr_estados.items()}
        ocr_durs = list(_ocr_dur)
    return {
        'uptime_segundos': round(time.time() - _inicio, 1),
        'peticiones': totales,
        'latencia_ms': {
            'muestras': len(durs),
            'p50': round(_percentil(durs, 50), 1),
            'p95': round(_percentil(durs, 95), 1),
            'max': round(max(durs), 1) if durs else 0.0,
        },
        'ocr': {
            'total': sum(sum(r.values()) for r in ocr_totales.values()),
            'por_metodo': ocr_totales,
            'estado_monto': ocr_estados,
            'latencia_ms': {
                'muestras': len(ocr_durs),
                'p50': round(_percentil(ocr_durs, 50), 1),
                'p95': round(_percentil(ocr_durs, 95), 1),
                'max': round(max(ocr_durs), 1) if ocr_durs else 0.0,
            },
        },
    }


def render_prometheus():
    s = snapshot()
    p = s['peticiones']
    lat = s['latencia_ms']
    lineas = [
        '# HELP localis_uptime_seconds Segundos desde el arranque del worker.',
        '# TYPE localis_uptime_seconds gauge',
        f'localis_uptime_seconds {s["uptime_segundos"]}',
        '# HELP localis_requests_total Peticiones HTTP por clase de estado.',
        '# TYPE localis_requests_total counter',
        f'localis_requests_total{{clase="2xx"}} {p.get("2xx", 0)}',
        f'localis_requests_total{{clase="3xx"}} {p.get("3xx", 0)}',
        f'localis_requests_total{{clase="4xx"}} {p.get("4xx", 0)}',
        f'localis_requests_total{{clase="5xx"}} {p.get("5xx", 0)}',
        f'localis_requests_total{{clase="total"}} {p.get("total", 0)}',
        '# HELP localis_request_duration_ms Latencia de peticion (percentiles).',
        '# TYPE localis_request_duration_ms gauge',
        f'localis_request_duration_ms{{quantile="0.5"}} {lat["p50"]}',
        f'localis_request_duration_ms{{quantile="0.95"}} {lat["p95"]}',
        f'localis_request_duration_ms{{quantile="1"}} {lat["max"]}',
        f'localis_request_duration_ms_muestras {lat["muestras"]}',
    ]

    ocr = s.get('ocr') or {}
    lineas += [
        '# HELP localis_ocr_total Comprobantes procesados por OCR (metodo/resultado).',
        '# TYPE localis_ocr_total counter',
    ]
    for metodo, resultados in sorted((ocr.get('por_metodo') or {}).items()):
        for resultado, total in sorted(resultados.items()):
            lineas.append(
                f'localis_ocr_total{{metodo="{metodo}",resultado="{resultado}"}} {total}'
            )
    lineas += [
        '# HELP localis_ocr_monto_total Resultado de la validacion de monto.',
        '# TYPE localis_ocr_monto_total counter',
    ]
    for metodo, estados in sorted((ocr.get('estado_monto') or {}).items()):
        for estado, total in sorted(estados.items()):
            lineas.append(
                f'localis_ocr_monto_total{{metodo="{metodo}",estado="{estado}"}} {total}'
            )
    ocr_lat = ocr.get('latencia_ms') or {}
    lineas += [
        '# HELP localis_ocr_duration_ms Latencia del OCR (percentiles).',
        '# TYPE localis_ocr_duration_ms gauge',
        f'localis_ocr_duration_ms{{quantile="0.5"}} {ocr_lat.get("p50", 0)}',
        f'localis_ocr_duration_ms{{quantile="0.95"}} {ocr_lat.get("p95", 0)}',
        f'localis_ocr_duration_ms{{quantile="1"}} {ocr_lat.get("max", 0)}',
        f'localis_ocr_duration_ms_muestras {ocr_lat.get("muestras", 0)}',
    ]
    return '\n'.join(lineas) + '\n'

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


def snapshot():
    with _lock:
        durs = list(_dur)
        totales = dict(_totales)
    return {
        'uptime_segundos': round(time.time() - _inicio, 1),
        'peticiones': totales,
        'latencia_ms': {
            'muestras': len(durs),
            'p50': round(_percentil(durs, 50), 1),
            'p95': round(_percentil(durs, 95), 1),
            'max': round(max(durs), 1) if durs else 0.0,
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
    return '\n'.join(lineas) + '\n'

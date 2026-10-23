"""Servidor temporal de generacion de carga (solo para medir; se apaga despues).

Expone:

    GET /health
    GET /run?token=...&path=/&conc=100&total=2000

Dispara peticiones concurrentes al sitio con conexiones reutilizadas (como un
navegador) y devuelve las estadisticas en JSON. Pensado para correr en un
servicio TEMPORAL de Render en la MISMA region que el sitio, de modo que la
medicion no dependa de la red del operador.

Seguridad: exige token (``LOADGEN_TOKEN``) y acota ``conc``/``total`` para que no
se pueda usar como herramienta de abuso.

NO forma parte de la app: es un util de operaciones, se despliega aparte y se
borra al terminar la prueba.
"""

from __future__ import annotations

import json
import os
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import requests

TOKEN = (os.environ.get('LOADGEN_TOKEN') or '').strip()
BASE = (os.environ.get('LOADGEN_BASE') or 'https://localis.onrender.com').rstrip('/')
MAX_CONC = 600
MAX_TOTAL = 20000

_lock = threading.Lock()
_tiempos = []
_errores = {}


def _peticion(sesion, url):
    inicio = time.perf_counter()
    try:
        r = sesion.get(url, timeout=30)
        ms = (time.perf_counter() - inicio) * 1000
        with _lock:
            _tiempos.append(ms)
            if r.status_code >= 400:
                _errores[r.status_code] = _errores.get(r.status_code, 0) + 1
    except Exception as error:
        clave = type(error).__name__
        with _lock:
            _errores[clave] = _errores.get(clave, 0) + 1


def ejecutar_carga(path, conc, total):
    conc = max(1, min(MAX_CONC, int(conc)))
    total = max(1, min(MAX_TOTAL, int(total)))
    por_hilo = max(1, total // conc)
    url = f'{BASE}{path}'

    global _tiempos, _errores
    with _lock:
        _tiempos = []
        _errores = {}

    def trabajador(_):
        sesion = requests.Session()
        for _ in range(por_hilo):
            _peticion(sesion, url)

    inicio = time.perf_counter()
    with ThreadPoolExecutor(max_workers=conc) as pool:
        list(pool.map(trabajador, range(conc)))
    duracion = time.perf_counter() - inicio

    with _lock:
        tiempos = sorted(_tiempos)
        errores = dict(_errores)

    n = len(tiempos)
    if not n:
        return {'ok': False, 'errores': errores, 'duracion_s': round(duracion, 2)}
    return {
        'ok': True,
        'path': path,
        'concurrencia': conc,
        'peticiones': n,
        'duracion_s': round(duracion, 2),
        'req_s': round(n / duracion, 1),
        'ms_p50': round(tiempos[n // 2], 1),
        'ms_p95': round(tiempos[min(n - 1, int(n * 0.95))], 1),
        'ms_p99': round(tiempos[min(n - 1, int(n * 0.99))], 1),
        'ms_max': round(tiempos[-1], 1),
        'ms_media': round(statistics.mean(tiempos), 1),
        'errores': errores,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, formato, *args):  # silencio (no ensuciar logs)
        return

    def _responder(self, codigo, datos):
        cuerpo = json.dumps(datos).encode('utf-8')
        self.send_response(codigo)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        partes = urlparse(self.path)
        params = parse_qs(partes.query)
        if partes.path == '/health':
            self._responder(200, {'ok': True, 'servicio': 'loadgen'})
            return
        token = (params.get('token') or [''])[0]
        if not TOKEN or token != TOKEN:
            self._responder(401, {'ok': False, 'error': 'no autorizado'})
            return
        if partes.path != '/run':
            self._responder(404, {'ok': False, 'error': 'no existe'})
            return
        try:
            resultado = ejecutar_carga(
                (params.get('path') or ['/'])[0],
                (params.get('conc') or ['50'])[0],
                (params.get('total') or ['200'])[0],
            )
        except Exception as error:
            self._responder(500, {'ok': False, 'error': f'{type(error).__name__}: {error}'})
            return
        self._responder(200, resultado)


if __name__ == '__main__':
    puerto = int(os.environ.get('PORT', '10000'))
    print(f'[loadgen] escuchando en 0.0.0.0:{puerto} -> {BASE}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', puerto), Handler).serve_forever()
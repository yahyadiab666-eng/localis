"""Servidor temporal de generacion de carga (solo para medir; se apaga despues).

Expone:

    GET /health
    GET /run?token=...&path=/&conc=100&total=600
    GET /self?token=...&conc=100&total=600

``/run`` dispara peticiones concurrentes al sitio (``LOADGEN_BASE``) y ``/self``
hace lo mismo contra el PROPIO servidor por loopback (``/health``): sirve de
**control** para saber si el generador es el cuello de la medicion.

Usa **asyncio + httpx** (muchas peticiones en vuelo con pocas conexiones
reutilizadas), en vez de cientos de hilos creando TLS: eso era lo que daba
``SSLError`` y falseaba los resultados.

Seguridad: exige token (``LOADGEN_TOKEN``) y acota ``conc``/``total``.

NO forma parte de la app: es un util de operaciones, se despliega aparte y se
borra al terminar la prueba.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import httpx

TOKEN = (os.environ.get('LOADGEN_TOKEN') or '').strip()
BASE = (os.environ.get('LOADGEN_BASE') or 'https://localis.onrender.com').rstrip('/')
PORT = int(os.environ.get('PORT', '10000'))
MAX_CONC = 1000
MAX_TOTAL = 40000


async def cargar(url_base, path, conc, total, identidad=False):
    conc = max(1, min(MAX_CONC, int(conc)))
    total = max(1, min(MAX_TOTAL, int(total)))
    url = f'{url_base}{path}'
    cabeceras = {'Accept-Encoding': 'identity'} if identidad else {}

    limits = httpx.Limits(
        max_connections=conc + 10,
        max_keepalive_connections=conc + 10,
    )
    tiempos = []
    errores = {}
    semaforo = asyncio.Semaphore(conc)

    async with httpx.AsyncClient(limits=limits, timeout=30.0) as cliente:
        try:
            await cliente.get(url, headers=cabeceras)
        except Exception:
            pass

        async def una():
            async with semaforo:
                inicio = time.perf_counter()
                try:
                    r = await cliente.get(url, headers=cabeceras)
                    tiempos.append((time.perf_counter() - inicio) * 1000)
                    if r.status_code >= 400:
                        errores[r.status_code] = errores.get(r.status_code, 0) + 1
                except Exception as error:
                    clave = type(error).__name__
                    errores[clave] = errores.get(clave, 0) + 1

        inicio = time.perf_counter()
        await asyncio.gather(*[una() for _ in range(total)])
        duracion = time.perf_counter() - inicio

    n = len(tiempos)
    if not n:
        return {'ok': False, 'errores': errores, 'duracion_s': round(duracion, 2)}
    t = sorted(tiempos)
    return {
        'ok': True,
        'url': url,
        'concurrencia': conc,
        'peticiones': n,
        'duracion_s': round(duracion, 2),
        'req_s': round(n / duracion, 1),
        'ms_p50': round(t[n // 2], 1),
        'ms_p95': round(t[min(n - 1, int(n * 0.95))], 1),
        'ms_p99': round(t[min(n - 1, int(n * 0.99))], 1),
        'ms_max': round(t[-1], 1),
        'ms_media': round(statistics.mean(t), 1),
        'errores': errores,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, formato, *args):
        return

    def _responder(self, codigo, datos):
        cuerpo = json.dumps(datos).encode('utf-8')
        self.send_response(codigo)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _params(self):
        return parse_qs(urlparse(self.path).query)

    def _autorizado(self, params):
        token = (params.get('token') or [''])[0]
        return bool(TOKEN) and token == TOKEN

    def do_GET(self):
        ruta = urlparse(self.path).path
        params = self._params()
        if ruta == '/health':
            self._responder(200, {'ok': True, 'servicio': 'loadgen'})
            return
        if not self._autorizado(params):
            self._responder(401, {'ok': False, 'error': 'no autorizado'})
            return

        try:
            conc = (params.get('conc') or ['50'])[0]
            total = (params.get('total') or ['200'])[0]
            if ruta == '/run':
                resultado = asyncio.run(
                    cargar(BASE, (params.get('path') or ['/'])[0], conc, total)
                )
            elif ruta == '/self':
                resultado = asyncio.run(
                    cargar(f'http://127.0.0.1:{PORT}', '/health', conc, total)
                )
            else:
                self._responder(404, {'ok': False, 'error': 'no existe'})
                return
        except Exception as error:
            self._responder(500, {'ok': False, 'error': f'{type(error).__name__}: {error}'})
            return
        self._responder(200, resultado)


if __name__ == '__main__':
    print(f'[loadgen] escuchando en 0.0.0.0:{PORT} -> {BASE}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
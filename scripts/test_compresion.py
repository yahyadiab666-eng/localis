"""Compresión gzip de respuestas: pruebas SIN red ni BD.

Comprueba que se comprime solo lo que debe (texto de tamaño razonable), que se
respeta ``Accept-Encoding``, que se declara ``Vary``, que no se tocan binarios ni
respuestas en streaming, y que ``LOCALIS_GZIP=0`` la desactiva.
"""

from __future__ import annotations

import gzip
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from flask import Flask, Response  # noqa: E402

from backend.compresion import comprimir_respuesta, configurar_compresion  # noqa: E402

FALLOS = []
GRANDE = '<html><body>' + ('contenido repetido para comprimir ' * 200) + '</body></html>'


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def _app():
    app = Flask(__name__)
    app.after_request(comprimir_respuesta)

    @app.route('/html')
    def html():
        return GRANDE, 200, {'Content-Type': 'text/html; charset=utf-8'}

    @app.route('/pequeno')
    def pequeno():
        return '<p>corto</p>', 200, {'Content-Type': 'text/html; charset=utf-8'}

    @app.route('/json')
    def json_ruta():
        return {'datos': 'x' * 2000}, 200

    @app.route('/binario')
    def binario():
        return Response(b'\x89PNG\r\n\x1a\n' + b'\x00' * 2000, mimetype='image/png')

    @app.route('/stream')
    def stream():
        respuesta = Response(iter([b'a' * 3000]), mimetype='text/plain')
        respuesta.direct_passthrough = True
        return respuesta

    return app


def main() -> int:
    cliente = _app().test_client()
    GZIP = {'Accept-Encoding': 'gzip, deflate'}

    print('=== HTML grande: se comprime ===')
    r = cliente.get('/html', headers=GZIP)
    _ok(r.headers.get('Content-Encoding') == 'gzip', 'declara Content-Encoding: gzip')
    _ok('Accept-Encoding' in (r.headers.get('Vary') or ''), 'declara Vary: Accept-Encoding')
    _ok(int(r.headers.get('Content-Length') or 0) == len(r.data), 'Content-Length correcto')
    _ok(len(r.data) < len(GRANDE), f'comprime ({len(r.data)} < {len(GRANDE)})')
    _ok(gzip.decompress(r.data).decode() == GRANDE, 'se descomprime al original')

    print('\n=== Sin Accept-Encoding: sin comprimir ===')
    r2 = cliente.get('/html')
    _ok(r2.headers.get('Content-Encoding') is None, 'no comprime si el cliente no lo acepta')
    _ok(r2.data.decode() == GRANDE, 'entrega el original')
    _ok('Accept-Encoding' in (r2.headers.get('Vary') or ''), 'aun así declara Vary')

    print('\n=== Respuesta pequeña: no vale la pena ===')
    r3 = cliente.get('/pequeno', headers=GZIP)
    _ok(r3.headers.get('Content-Encoding') is None, 'no comprime cuerpos diminutos')

    print('\n=== JSON: se comprime ===')
    r4 = cliente.get('/json', headers=GZIP)
    _ok(r4.headers.get('Content-Encoding') == 'gzip', 'comprime JSON')

    print('\n=== Binario: intacto ===')
    r5 = cliente.get('/binario', headers=GZIP)
    _ok(r5.headers.get('Content-Encoding') is None, 'no recomprime imágenes')
    _ok(r5.data.startswith(b'\x89PNG'), 'bytes intactos')

    print('\n=== Streaming: no se materializa ===')
    r6 = cliente.get('/stream', headers=GZIP)
    _ok(r6.headers.get('Content-Encoding') is None, 'no toca direct_passthrough')

    print('\n=== Interruptor LOCALIS_GZIP=0 ===')
    os.environ['LOCALIS_GZIP'] = '0'
    try:
        r7 = cliente.get('/html', headers=GZIP)
        _ok(r7.headers.get('Content-Encoding') is None, 'desactivada no comprime')
    finally:
        os.environ.pop('LOCALIS_GZIP', None)

    print('\n=== Idempotencia: registrar dos veces no re-comprime ===')
    app2 = Flask(__name__)

    @app2.route('/x')
    def x():
        return GRANDE, 200, {'Content-Type': 'text/html; charset=utf-8'}

    configurar_compresion(app2)
    configurar_compresion(app2)
    c2 = app2.test_client()
    r8 = c2.get('/x', headers=GZIP)
    _ok(r8.headers.get('Content-Encoding') == 'gzip', 'comprime')
    _ok(gzip.decompress(r8.data).decode() == GRANDE, 'no se comprime dos veces')

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK compresión gzip: selectiva, respeta Accept-Encoding y es reversible')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Smoke E2E por HTTP contra una instancia en ejecucion (local o produccion).

No necesita base de datos ni navegador: valida que las rutas criticas respondan
como se espera. Sirve como contrato antes de refactorizar (Bloque 4).

Uso:
    python scripts/e2e_smoke.py                      # local http://127.0.0.1:10000
    python scripts/e2e_smoke.py --base-url https://localis.onrender.com

Devuelve 0 si todo pasa; distinto de 0 si algo falla.
"""

from __future__ import annotations

import argparse
import re
import sys

import requests

TIEMPO = 90  # Render en frio puede tardar; damos margen.


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    return bool(cond)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--base-url', default='http://127.0.0.1:10000', help='URL base'
    )
    args = parser.parse_args()
    base = args.base_url.rstrip('/')

    sesion = requests.Session()
    fallos = []

    def pedir(metodo, ruta, **kw):
        kw.setdefault('timeout', TIEMPO)
        kw.setdefault('allow_redirects', False)
        return sesion.request(metodo, base + ruta, **kw)

    print(f'=== E2E contra {base} ===')

    try:
        r = pedir('GET', '/health/live')
        vivo = r.status_code == 200 and (r.json() or {}).get('ok') is True
        fallos.append(not _ok(vivo, f'GET /health/live -> {r.status_code} {r.text[:80]}'))
        if vivo:
            print(f'         commit={r.json().get("commit")} branch={r.json().get("branch")}')
    except Exception as e:
        fallos.append(not _ok(False, f'/health/live: {type(e).__name__}: {e}'))

    try:
        r = pedir('GET', '/health')
        fallos.append(not _ok(r.status_code == 200, f'GET /health -> {r.status_code}'))
    except Exception as e:
        fallos.append(not _ok(False, f'/health: {type(e).__name__}: {e}'))

    try:
        r = pedir('GET', '/')
        fallos.append(
            not _ok(
                r.status_code == 200 and len(r.text) > 1000,
                f'GET / -> {r.status_code} ({len(r.text)} bytes)',
            )
        )
    except Exception as e:
        fallos.append(not _ok(False, f'/ : {type(e).__name__}: {e}'))

    try:
        r = pedir('GET', '/login')
        fallos.append(not _ok(r.status_code == 200, f'GET /login -> {r.status_code}'))
    except Exception as e:
        fallos.append(not _ok(False, f'/login: {type(e).__name__}: {e}'))

    try:
        r = pedir('GET', '/api/tasa-bcv')
        fallos.append(
            not _ok(
                r.status_code == 200 and 'tasa_bcv' in (r.json() or {}),
                f'GET /api/tasa-bcv -> {r.status_code}',
            )
        )
    except Exception as e:
        fallos.append(not _ok(False, f'/api/tasa-bcv: {type(e).__name__}: {e}'))

    try:
        r = pedir('GET', '/admin')
        fallos.append(
            not _ok(
                r.status_code in (301, 302, 303, 307, 308),
                f'GET /admin sin sesion -> {r.status_code} (redirige a login)',
            )
        )
    except Exception as e:
        fallos.append(not _ok(False, f'/admin: {type(e).__name__}: {e}'))

    try:
        r = pedir('GET', '/api/actualizar-tasa')
        fallos.append(
            not _ok(r.status_code == 405, f'GET /api/actualizar-tasa -> {r.status_code} (405 esperado)')
        )
    except Exception as e:
        fallos.append(not _ok(False, f'GET /api/actualizar-tasa: {type(e).__name__}: {e}'))

    try:
        r = pedir('POST', '/api/actualizar-tasa')
        fallos.append(
            not _ok(r.status_code == 401, f'POST /api/actualizar-tasa anon -> {r.status_code} (401 esperado)')
        )
    except Exception as e:
        fallos.append(not _ok(False, f'POST /api/actualizar-tasa: {type(e).__name__}: {e}'))

    print('=== Descubrimiento desde la portada ===')
    try:
        home = pedir('GET', '/')
        texto = home.text
        productos = re.findall(r'["\'](/producto/[^"\']+)["\']', texto)
        if productos:
            r = pedir('GET', productos[0], allow_redirects=True)
            ok_prod = r.status_code == 200 and ('/producto/' in r.url or '/tienda/' in r.url)
            fallos.append(not _ok(ok_prod, f'GET {productos[0]} -> {r.status_code} {r.url}'))
        else:
            print('  SKIP  sin productos en la portada (BD vacia)')
        tiendas = re.findall(r'["\'](/tienda/[^"\']+)["\']', texto)
        if tiendas:
            r = pedir('GET', tiendas[0], allow_redirects=True)
            ok_tie = r.status_code == 200 and '/tienda/' in r.url
            fallos.append(not _ok(ok_tie, f'GET {tiendas[0]} -> {r.status_code} {r.url}'))
        else:
            print('  SKIP  sin tiendas en la portada')
        assets = re.findall(r'href="(/static/[^"]+\.css)"', texto)
        if assets:
            r = pedir('GET', assets[0])
            fallos.append(not _ok(r.status_code == 200, f'GET {assets[0]} -> {r.status_code}'))
        else:
            print('  SKIP  sin asset CSS enlazado')
    except Exception as e:
        fallos.append(not _ok(False, f'descubrimiento: {type(e).__name__}: {e}'))

    print('\n=== RESULTADO ===')
    total = sum(1 for f in fallos if f)
    if total:
        print(f'FALLOS: {total}')
        return 1
    print('OK E2E smoke: rutas criticas responden como se espera')
    return 0


if __name__ == '__main__':
    sys.exit(main())

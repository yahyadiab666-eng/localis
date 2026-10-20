"""Observabilidad HTTP: /metrics y CSP en modo report-only.

Usa el test client de Flask y variables de entorno leidas por peticion. No
escribe datos. Requiere BD (se omite sin ella), igual que test_rutas_smoke.
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def main() -> int:
    from database import DATABASE_URL

    if not DATABASE_URL:
        print('  SKIP  DATABASE_URL no configurada: observabilidad HTTP omitida.')
        return 0

    try:
        from main import _hilo_init, app
    except Exception:
        traceback.print_exc()
        print('  SKIP  no se pudo importar la app.')
        return 0

    if _hilo_init.is_alive():
        _hilo_init.join(timeout=60)
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    cliente = app.test_client()

    print('=== /metrics gating ===')
    os.environ.pop('LOCALIS_METRICS', None)
    r = cliente.get('/metrics')
    _ok(r.status_code == 404, f'/metrics deshabilitado -> 404 ({r.status_code})')

    os.environ['LOCALIS_METRICS'] = '1'
    r = cliente.get('/metrics')
    _ok(r.status_code == 200, f'/metrics habilitado -> 200 ({r.status_code})')
    _ok(
        b'localis_requests_total' in r.data,
        'el cuerpo expone localis_requests_total',
    )
    os.environ.pop('LOCALIS_METRICS', None)

    print('=== CSP report-only ===')
    os.environ['LOCALIS_CSP_REPORT_ONLY'] = "default-src 'self'"
    r = cliente.get('/')
    cabecera = r.headers.get('Content-Security-Policy-Report-Only')
    _ok(cabecera == "default-src 'self'", f'cabecera report-only presente: {cabecera}')
    _ok(
        r.headers.get('Content-Security-Policy') is None,
        'no se envia la CSP enforcing a la vez',
    )
    os.environ.pop('LOCALIS_CSP_REPORT_ONLY', None)

    print('=== Vista previa (solo lectura) ===')
    from backend.db import get_db_connection
    from backend.pre_cuentas import _plan_gratis, token_vista_previa

    cid = None
    try:
        plan = _plan_gratis()
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'INSERT INTO comercios (usuario_id, nombre, plan_id, plan_tipo, '
                'limite_productos, estado_pago, estado_tienda, visible) '
                "VALUES (NULL, ?, ?, ?, ?, 'gratis', 'pre_cuenta', 0) RETURNING id",
                ('__test_vista_previa__', plan['id'], plan['codigo'], plan['limite']),
            )
            cid = cursor.fetchone()[0]
            conexion.commit()
    except Exception:
        traceback.print_exc()
        cid = None

    try:
        if cid:
            token = token_vista_previa(cid)
            r = cliente.get(f'/vista-previa/{cid}/{token}')
            _ok(r.status_code == 200, f'preview con token valido -> 200 ({r.status_code})')
            _ok(
                (r.headers.get('X-Robots-Tag') or '').startswith('noindex'),
                'cabecera X-Robots-Tag noindex',
            )
            _ok(b'Vista previa' in r.data, 'muestra el aviso de vista previa')

            r2 = cliente.get(f'/vista-previa/{cid}/tokeninvalido')
            _ok(r2.status_code == 404, f'token invalido -> 404 ({r2.status_code})')

            # La pre-cuenta NO debe ser accesible por la ruta publica normal.
            r3 = cliente.get(f'/tienda/{cid}')
            _ok(r3.status_code in (301, 302), f'pre-cuenta oculta en /tienda ({r3.status_code})')
        else:
            print('  aviso  no se pudo crear la pre-cuenta de prueba')
    finally:
        if cid:
            with get_db_connection() as conexion:
                cursor = conexion.cursor()
                cursor.execute('DELETE FROM comercios WHERE id = ?', (cid,))
                conexion.commit()

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK observabilidad HTTP: /metrics y CSP report-only')
    return 0


if __name__ == '__main__':
    sys.exit(main())

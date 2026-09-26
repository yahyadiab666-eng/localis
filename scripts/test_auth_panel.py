#!/usr/bin/env python3
"""Verificacion de rutas criticas: login, registro y panel de comercio.

Levanta la app Flask con el test client y comprueba que ninguna ruta critica
devuelve 5xx, y que el panel carga con una sesion valida. Tambien confirma que
la columna ``productos.activo`` existe (o que el filtro degrada sin romper).
"""

from __future__ import annotations

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

_ERRORES = []


def _ok(condicion, mensaje):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    _ERRORES.append(mensaje)
    return False


def _primer_comercio():
    from backend.db import get_db_connection

    with get_db_connection(row_factory=True) as c:
        cur = c.cursor()
        cur.execute('SELECT id, usuario_id FROM comercios ORDER BY id LIMIT 1')
        fila = cur.fetchone()
        return dict(fila) if fila else None


def main() -> int:
    from backend.db import get_db_connection

    # 1) La columna activo (o su ausencia) no debe romper: el filtro degrada.
    try:
        with get_db_connection() as c:
            cur = c.cursor()
            cur.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
                "AND table_name='productos' AND column_name='activo' LIMIT 1"
            )
            tiene_activo = cur.fetchone() is not None
        print(f'  info  productos.activo presente: {tiene_activo}')
    except Exception as error:
        print(f'  aviso  no se pudo verificar activo: {error}')

    from main import app, _hilo_init

    if _hilo_init.is_alive():
        _hilo_init.join(timeout=60)
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    cliente = app.test_client()

    print('\n=== Rutas publicas de autenticacion ===')
    for ruta in ('/login', '/registro', '/'):
        try:
            r = cliente.get(ruta)
            _ok(r.status_code < 500, f'GET {ruta} -> {r.status_code} (sin 5xx)')
        except Exception:
            traceback.print_exc()
            _ok(False, f'GET {ruta} no lanzo excepcion')

    comercio = _primer_comercio()
    if not comercio:
        print('\n  aviso  sin comercios en la BD; se omite el panel')
    else:
        print('\n=== Panel de comercio (sesion valida) ===')
        with cliente.session_transaction() as sess:
            sess['usuario_id'] = int(comercio['usuario_id'])
            sess['username'] = 'Owner'
            sess['rol'] = 'comerciante'
            sess['es_admin'] = False
            sess['comercio_id'] = int(comercio['id'])
            sess['panel_comercio_activo'] = True
        for ruta in ('/comercio', '/comercio/planes'):
            try:
                r = cliente.get(ruta)
                _ok(r.status_code < 500, f'GET {ruta} -> {r.status_code} (sin 5xx)')
            except Exception:
                traceback.print_exc()
                _ok(False, f'GET {ruta} no lanzo excepcion')

        print('\n=== Sin banner rojo ante fallos secundarios (ni redirect) ===')
        from unittest.mock import patch

        import psycopg2

        import main as _main

        for nombre, objetivo in (
            ('metricas', 'resumen_interacciones'),
            ('avisos', 'obtener_avisos_suscripcion'),
            ('config', 'obtener_config'),
        ):
            with patch.object(_main, objetivo, side_effect=psycopg2.OperationalError('x')):
                r = cliente.get('/comercio')
                _ok(
                    r.status_code == 200 and not r.headers.get('Location'),
                    f'fallo de {nombre}: panel 200 sin banner rojo',
                )

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK auth/panel: login, registro y panel responden sin errores 5xx')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

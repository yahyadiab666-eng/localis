"""Smoke de rutas renderizadas (publico + comercio + admin).

Levanta la app con el test client y comprueba que las rutas clave NO devuelven
5xx con sesiones validas. Es el contrato que protege el refactor de Blueprints:
si al mover rutas se rompe un ``url_for`` o una plantilla, esto lo detecta.

Solo hace GET (no escribe datos). Requiere BD (se omite sin ella).
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


def _fila(sql):
    from backend.db import get_db_connection

    with get_db_connection(row_factory=True) as c:
        cur = c.cursor()
        cur.execute(sql)
        fila = cur.fetchone()
        return dict(fila) if fila else None


def _revisar(cliente, rutas, etiqueta):
    print(f'\n=== {etiqueta} ===')
    for ruta in rutas:
        try:
            r = cliente.get(ruta)
            _ok(r.status_code < 500, f'GET {ruta} -> {r.status_code} (sin 5xx)')
        except Exception:
            traceback.print_exc()
            _ok(False, f'GET {ruta} no lanzo excepcion')


def main() -> int:
    from database import DATABASE_URL

    if not DATABASE_URL:
        print('  SKIP  DATABASE_URL no configurada: smoke de rutas omitido.')
        return 0

    from main import app, _hilo_init

    if _hilo_init.is_alive():
        _hilo_init.join(timeout=60)
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    cliente = app.test_client()

    _revisar(
        cliente,
        (
            '/',
            '/login',
            '/registro',
            '/ayuda',
            '/comercio/banner',
            '/health',
            '/health/live',
            '/health/ready',
        ),
        'Rutas publicas',
    )

    comercio = _fila('SELECT id, usuario_id FROM comercios ORDER BY id LIMIT 1')
    if comercio and comercio.get('usuario_id'):
        with cliente.session_transaction() as sess:
            sess['usuario_id'] = int(comercio['usuario_id'])
            sess['username'] = 'Owner'
            sess['rol'] = 'comerciante'
            sess['es_admin'] = False
            sess['comercio_id'] = int(comercio['id'])
            sess['panel_comercio_activo'] = True
        _revisar(
            cliente,
            (
                '/comercio',
                '/comercio/panel',
                '/comercio/planes',
                '/comercio/editar',
                '/comercio/producto/nuevo',
                '/comercio/banner',
                '/perfil',
            ),
            'Panel de comercio (sesion valida)',
        )
    else:
        print('\n  aviso  sin comercio con dueno; se omite el panel de comercio')

    usuario = _fila('SELECT id FROM usuarios ORDER BY id LIMIT 1')
    admin_ok = False
    with cliente.session_transaction() as sess:
        sess['usuario_id'] = int(usuario['id']) if usuario else 1
        sess['username'] = 'Admin'
        sess['rol'] = 'admin'
        sess['es_admin'] = True
        sess.pop('comercio_id', None)
    _revisar(cliente, ('/admin', '/admin/usuarios'), 'Panel de administracion')

    if comercio:
        cid = int(comercio['id'])
        _revisar(
            cliente,
            (
                f'/admin/comercio/{cid}',
                f'/admin/comercio/{cid}/editar',
                f'/admin/comercio/{cid}/producto/nuevo',
            ),
            'Ficha de comercio (admin)',
        )
        admin_ok = True

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print(f'OK rutas renderizadas: publicas + comercio + admin ({"ficha incluida" if admin_ok else "sin ficha"})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

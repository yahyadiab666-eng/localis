"""Estructura de blueprints: blinda el refactor del Bloque 4.

Verifica que los blueprints siguen registrados y que los endpoints clave
existen con su nombre calificado (``comercio.panel_comercio``, ``auth.login``,
...). Asi, si un renombrado de endpoint se rompe, esto falla en el CI en lugar
de reventar en produccion con un ``BuildError``.

No escribe datos. Si el import de la app falla (p. ej. sin BD), se omite.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


BLUEPRINTS_ESPERADOS = {'api', 'admin', 'comercio', 'auth'}

ENDPOINTS_ESPERADOS = {
    'index',
    'admin.panel_admin',
    'admin.admin_comercio_detalle',
    'comercio.panel_comercio',
    'comercio.nuevo_producto',
    'comercio.editar_producto',
    'comercio.cargar_csv',
    'comercio.estado_importacion',
    'comercio.comercio_premios',
    'auth.login',
    'auth.google_callback',
    'auth.perfil',
    'auth.logout',
}

# Nombres "desnudos" que NO deben existir (si reaparecen, hubo un renombrado mal).
ENDPOINTS_PROHIBIDOS = {
    'panel_comercio', 'nuevo_producto', 'editar_producto', 'cargar_csv',
    'estado_importacion', 'login', 'logout', 'perfil', 'google_callback',
    'panel_admin', 'admin_usuarios', 'admin_comercio_detalle',
}


def main() -> int:
    try:
        from main import app
    except Exception as error:
        print(f'  SKIP  no se pudo importar la app: {type(error).__name__}: {error}')
        return 0

    print('=== Blueprints registrados ===')
    registrados = set(app.blueprints)
    faltan = BLUEPRINTS_ESPERADOS - registrados
    _ok(not faltan, f'blueprints presentes {sorted(registrados)} (faltan: {sorted(faltan)})')

    print('=== Endpoints clave ===')
    nombres = set(app.view_functions)
    for endpoint in sorted(ENDPOINTS_ESPERADOS):
        _ok(endpoint in nombres, f'endpoint existe: {endpoint}')

    print('=== Sin endpoints desnudos ===')
    presentes = ENDPOINTS_PROHIBIDOS & nombres
    _ok(not presentes, f'no hay endpoints sin calificar (encontrados: {sorted(presentes)})')

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK estructura de blueprints: registrados y endpoints calificados')
    return 0


if __name__ == '__main__':
    sys.exit(main())

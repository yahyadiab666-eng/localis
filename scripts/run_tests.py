"""Runner de pruebas de Localis (local y CI).

Un solo comando que compila todo y ejecuta las pruebas en orden:
- Siempre: las que **no** necesitan BD ni red.
- Ademas, si hay ``DATABASE_URL``: las de integracion (crean datos sandbox y
  los limpian). Se omiten con ``--solo-sin-red`` o ``--sin-integracion``.

Uso:
    python scripts/run_tests.py                 # todo lo posible
    python scripts/run_tests.py --solo-sin-red  # solo lo que no toca BD/red

Devuelve 0 si todo pasa; distinto de 0 si algo falla.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Carga .env (override=False) para que las pruebas de integracion tengan
# DATABASE_URL en local; en CI manda la variable de entorno.
try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass

SIN_RED = [
    'test_config_seguridad.py',
    'test_estructura_blueprints.py',
    'test_cache.py',
    'test_clasificador_categorias.py',
    'test_cobertura_universal.py',
]
INTEGRACION = [
    'test_migraciones.py',
    'test_pre_cuentas_roles.py',
    'test_auth_panel.py',
    'test_rutas_smoke.py',
    'test_rate_limit.py',
]
COMPILAR = [
    'config.py', 'main.py', 'database.py', 'init_db.py',
    'backend', 'services', 'scripts',
]


def _ejecutar(script):
    print(f'\n=== {script} ===', flush=True)
    proceso = subprocess.run(
        [sys.executable, '-u', str(RAIZ / 'scripts' / script)],
        cwd=str(RAIZ),
    )
    return proceso.returncode


def _compilar():
    print('\n=== compileall ===', flush=True)
    proceso = subprocess.run(
        [sys.executable, '-m', 'compileall', '-q', *COMPILAR],
        cwd=str(RAIZ),
    )
    return proceso.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description='Runner de pruebas de Localis')
    parser.add_argument('--solo-sin-red', action='store_true')
    parser.add_argument('--sin-integracion', action='store_true')
    args = parser.parse_args()

    fallos = 0
    if _compilar() != 0:
        fallos += 1

    for script in SIN_RED:
        if _ejecutar(script) != 0:
            fallos += 1

    if not args.solo_sin_red and not args.sin_integracion:
        if (os.environ.get('DATABASE_URL') or '').strip():
            for script in INTEGRACION:
                if _ejecutar(script) != 0:
                    fallos += 1
        else:
            print('\n=== SKIP integracion: sin DATABASE_URL ===', flush=True)

    print('\n' + ('=' * 44))
    if fallos:
        print(f'RESUMEN: {fallos} FALLO(S)')
        return 1
    print('RESUMEN: OK (todo verificado)')
    return 0


if __name__ == '__main__':
    sys.exit(main())

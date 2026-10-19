"""Prueba rapida y SIN red: configuracion de seguridad y controles duros.

Blinda el bloque P0 para que una regresion futura (volver a hardcodear el
admin, quitar el pin de dependencias, volver al lock de sesion o borrar las
cabeceras) falle en el CI en lugar de llegar a produccion.

No necesita base de datos ni red.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    return bool(cond)


def main():
    fallos = []

    # 1) Admins configurables por entorno (multiples, con separadores , y ;).
    print('=== Admins por entorno ===')
    os.environ['LOCALIS_ADMIN_EMAILS'] = 'uno@x.com, Dos@Y.com;tres@z.com'
    import config
    importlib.reload(config)
    esperado = ('uno@x.com', 'dos@y.com', 'tres@z.com')
    fallos.append(
        not _ok(config.ADMIN_EMAILS == esperado, f'lista normalizada: {config.ADMIN_EMAILS}')
    )
    fallos.append(not _ok(config.es_correo_admin('DOS@y.com'), 'case-insensitive'))
    fallos.append(not _ok(not config.es_correo_admin('intruso@x.com'), 'rechaza ajenos'))
    fallos.append(not _ok(not config.es_correo_admin(''), 'rechaza vacio'))

    # 2) Fallback historico si no se define nada.
    print('=== Fallback de admin ===')
    os.environ.pop('LOCALIS_ADMIN_EMAILS', None)
    os.environ.pop('LOCALIS_ADMIN_EMAIL', None)
    importlib.reload(config)
    fallos.append(
        not _ok(
            config.es_correo_admin('ydiab.t@gmail.com') and len(config.ADMIN_EMAILS) >= 1,
            'fallback al correo historico',
        )
    )

    # 3) Sin correo de administrador hardcodeado en el codigo de auth.
    print('=== Sin admin hardcodeado en auth.py ===')
    texto_auth = (RAIZ / 'backend' / 'auth.py').read_text(encoding='utf-8', errors='ignore')
    fallos.append(
        not _ok('ydiab.t@gmail.com' not in texto_auth, 'auth.py no contiene el correo fijo')
    )

    # 4) Dependencias fijadas (todas con ==).
    print('=== requirements.txt fijado ===')
    lineas = [
        l.strip()
        for l in (RAIZ / 'requirements.txt').read_text(encoding='utf-8').splitlines()
        if l.strip() and not l.strip().startswith('#')
    ]
    sin_pin = [l for l in lineas if '==' not in l]
    fallos.append(not _ok(not sin_pin, f'sin pin: {sin_pin}'))

    # 5) init_db usa el lock de transaccion (no el de sesion que fuga en pgbouncer).
    print('=== Lock de init_db por transaccion ===')
    texto_bd = (RAIZ / 'database.py').read_text(encoding='utf-8', errors='ignore')
    fallos.append(
        not _ok('pg_try_advisory_xact_lock' in texto_bd, 'usa pg_try_advisory_xact_lock')
    )
    fallos.append(
        not _ok(
            'pg_try_advisory_lock(' not in texto_bd,
            'no reintroduce el lock de sesion',
        )
    )

    # 6) Cabeceras de seguridad presentes.
    print('=== Cabeceras de seguridad ===')
    texto_main = (RAIZ / 'main.py').read_text(encoding='utf-8', errors='ignore')
    for cabecera in (
        'X-Content-Type-Options',
        'X-Frame-Options',
        'Referrer-Policy',
        'Strict-Transport-Security',
    ):
        fallos.append(
            not _ok(cabecera in texto_main, f'define {cabecera}')
        )

    # 7) Toda ruta /admin va precedida por el guard de administrador.
    print('=== Rutas /admin protegidas ===')
    lineas_main = texto_main.splitlines()
    rutas_admin = 0
    desprotegidas = []
    for i, linea in enumerate(lineas_main):
        if '@app.route(' in linea and "'/admin" in linea:
            rutas_admin += 1
            ventana = []
            for j in range(i + 1, min(i + 6, len(lineas_main))):
                if lineas_main[j].lstrip().startswith('def '):
                    break
                ventana.append(lineas_main[j])
            if 'admin_requerido' not in '\n'.join(ventana):
                desprotegidas.append(linea.strip())
    fallos.append(not _ok(rutas_admin > 0, f'{rutas_admin} rutas /admin detectadas'))
    fallos.append(not _ok(not desprotegidas, f'rutas sin guard: {desprotegidas}'))

    # 8) El endpoint de sincronizacion de tasa es solo POST y requiere sesion.
    print('=== Endpoint de tasa endurecido ===')
    fallos.append(
        not _ok(
            "@app.route('/api/actualizar-tasa', methods=['POST'])" in texto_main,
            'solo acepta POST',
        )
    )
    fallos.append(
        not _ok('No autorizado.' in texto_main, 'requiere sesion (guard 401)')
    )

    print('\n=== RESULTADO ===')
    total = sum(1 for f in fallos if f)
    if total:
        print(f'FALLOS: {total}')
        return 1
    print('OK configuracion de seguridad: admins, deps, lock y cabeceras')
    return 0

if __name__ == '__main__':
    sys.exit(main())

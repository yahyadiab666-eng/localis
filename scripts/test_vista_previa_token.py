"""Token de vista previa de comercio: firma y validacion. SIN red ni BD."""

from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

os.environ.setdefault('LOCALIS_SECRET_KEY', 'clave-de-prueba-vista-previa')

from backend.pre_cuentas import (  # noqa: E402
    token_vista_previa,
    token_vista_previa_valido,
)

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def main() -> int:
    print('=== Token: determinista y URL-safe ===')
    t1 = token_vista_previa(123)
    t2 = token_vista_previa(123)
    _ok(t1 == t2, 'mismo comercio -> mismo token')
    _ok(len(t1) == 32, f'longitud 32 (={len(t1)})')
    _ok(all(c.isalnum() or c in '-_' for c in t1), 'solo caracteres URL-safe')
    _ok(token_vista_previa(123) != token_vista_previa(124), 'comercios distintos -> tokens distintos')

    print('=== Token: validacion ===')
    _ok(token_vista_previa_valido(123, t1), 'valido para su comercio')
    _ok(not token_vista_previa_valido(124, t1), 'rechaza otro comercio')
    _ok(not token_vista_previa_valido(123, 'x' * 32), 'rechaza token falso')
    _ok(not token_vista_previa_valido(123, ''), 'rechaza token vacio')
    _ok(not token_vista_previa_valido(123, None), 'rechaza None')

    print('=== Token: cambia con el secreto ===')
    anterior = token_vista_previa(123)
    os.environ['LOCALIS_PREVIEW_SECRET'] = 'otro-secreto-distinto'
    nuevo = token_vista_previa(123)
    _ok(nuevo != anterior, 'al rotar el secreto cambian los tokens (revocacion)')
    os.environ.pop('LOCALIS_PREVIEW_SECRET', None)

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK token de vista previa: firma, validacion y rotacion')
    return 0


if __name__ == '__main__':
    sys.exit(main())

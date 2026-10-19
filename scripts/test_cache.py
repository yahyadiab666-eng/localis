"""Prueba de la cache TTL en memoria (sin BD ni red).

Verifica el contrato de ``backend.runtime_cache``: devuelve el valor cacheado
dentro del TTL, recarga al expirar y respeta la invalidacion por prefijo.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    return bool(cond)


def main() -> int:
    from backend.runtime_cache import get_or_load, invalidate

    fallos = []
    estado = {'n': 0}

    def cargador():
        estado['n'] += 1
        return estado['n']

    print('=== Cachea dentro del TTL ===')
    invalidate()
    v1 = get_or_load('prueba:clave', cargador, 60)
    v2 = get_or_load('prueba:clave', cargador, 60)
    fallos.append(not _ok(v1 == 1 and v2 == 1, 'misma instancia sin recargar'))
    fallos.append(not _ok(estado['n'] == 1, f'cargador llamado 1 vez ({estado["n"]})'))

    print('=== Expira y recarga ===')
    v3 = get_or_load('prueba:clave', cargador, 0)
    fallos.append(not _ok(v3 == 2 and estado['n'] == 2, 'recarga tras TTL'))

    print('=== Invalidacion por prefijo ===')
    get_or_load('grupoA:x', cargador, 60)
    get_or_load('grupoB:x', cargador, 60)
    invalidate('grupoA')
    antes = estado['n']
    get_or_load('grupoA:x', cargador, 60)
    get_or_load('grupoB:x', cargador, 60)
    fallos.append(not _ok(estado['n'] == antes + 1, 'solo recarga el prefijo invalidado'))

    print('\n=== RESULTADO ===')
    total = sum(1 for f in fallos if f)
    if total:
        print(f'FALLOS: {total}')
        return 1
    print('OK cache TTL: aciertos, expiracion e invalidacion por prefijo')
    return 0


if __name__ == '__main__':
    sys.exit(main())

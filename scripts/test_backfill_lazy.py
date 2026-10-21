"""Arranque perezoso del backfill de imágenes: verificación SIN red ni BD.

Un hilo creado al importar el módulo no sobrevive al fork de gunicorn en
producción; el backfill debe arrancar desde el primer request (post-fork). Se
comprueba el throttle, la idempotencia y el respeto a ``LOCALIS_IMG_BACKFILL``
sin arrancar hilos reales (se sustituye el arranque por un espía).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend import image_backfill as ib  # noqa: E402

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def main() -> int:
    llamadas = []
    original = ib.iniciar_backfill_periodico
    ib.iniciar_backfill_periodico = lambda: llamadas.append(1)

    def _reset(iniciado=False):
        ib._iniciado = iniciado
        ib._ultimo_intento_arranque = 0.0

    try:
        print('=== Arranque perezoso: throttle de 30 s ===')
        os.environ['LOCALIS_IMG_BACKFILL'] = '1'
        _reset()
        ib.asegurar_backfill_activo()  # vencido -> arranca
        ib.asegurar_backfill_activo()  # dentro de 30 s -> no
        _ok(len(llamadas) == 1, f'arranca 1 de 2 veces -> {len(llamadas)}')

        print('\n=== Idempotente si ya está iniciado ===')
        _reset(iniciado=True)
        ib.asegurar_backfill_activo()
        _ok(len(llamadas) == 1, 'no arranca de nuevo si ya está activo')

        print('\n=== Respeta LOCALIS_IMG_BACKFILL=0 ===')
        os.environ['LOCALIS_IMG_BACKFILL'] = '0'
        _reset()
        ib.asegurar_backfill_activo()
        _ok(len(llamadas) == 1, 'no arranca si está deshabilitado')
    finally:
        ib.iniciar_backfill_periodico = original
        os.environ['LOCALIS_IMG_BACKFILL'] = '1'

    if FALLOS:
        print(f'\nFALLOS: {len(FALLOS)}')
        return 1
    print('\nOK backfill: arranque perezoso, throttle e idempotencia')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

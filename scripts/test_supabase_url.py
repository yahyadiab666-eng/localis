#!/usr/bin/env python3
"""SUPABASE_URL se acepta o cae al origen por defecto del proyecto."""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

REF = 'wesnnnvoavprgqcczzsg'
CANONICA = f'https://{REF}.supabase.co'


def main() -> int:
    from backend.supabase_connectivity import (
        SUPABASE_URL_POR_DEFECTO,
        sanitizar_url_supabase,
    )

    errores = []

    limpia = sanitizar_url_supabase(CANONICA)
    if not limpia.valida or limpia.url != CANONICA or limpia.errores:
        errores.append(f'URL legitima rechazada: {limpia}')

    for crudo, esperado in (
        (CANONICA, CANONICA),
        (f'{REF}.supabase.co', CANONICA),
        (f'https://https://{REF}.supabase.co', CANONICA),
        (f'{CANONICA}/rest/v1', CANONICA),
        (f'http://{REF}.supabase.co/', CANONICA),
        ('', SUPABASE_URL_POR_DEFECTO),
        ('   ', SUPABASE_URL_POR_DEFECTO),
        ('https://ejemplo.com', SUPABASE_URL_POR_DEFECTO),
        (REF, SUPABASE_URL_POR_DEFECTO),
    ):
        resultado = sanitizar_url_supabase(crudo)
        if not resultado.valida or resultado.url != esperado or resultado.errores:
            errores.append(
                f'{crudo!r} -> valida={resultado.valida} url={resultado.url!r} '
                f'errores={resultado.errores} (esperado {esperado})'
            )

    if SUPABASE_URL_POR_DEFECTO != CANONICA:
        errores.append(f'defecto {SUPABASE_URL_POR_DEFECTO!r} != {CANONICA!r}')

    if errores:
        print('FALLO sanitizacion SUPABASE_URL:')
        for item in errores:
            print(f'  - {item}')
        return 1

    print(f'OK: URL valida se respeta; vacia/corrupta usa {CANONICA}.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validacion minima: https:// + *.supabase.co no se rechaza ni se vacia."""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

REF = 'wesnnnvoavprgqcczzsg'
CANONICA = f'https://{REF}.supabase.co'


def main() -> int:
    from backend.supabase_connectivity import sanitizar_url_supabase

    errores = []

    limpia = sanitizar_url_supabase(CANONICA)
    if not limpia.valida or limpia.url != CANONICA or limpia.errores:
        errores.append(f'URL legitima rechazada: {limpia}')
    if not limpia.url:
        errores.append('URL legitima vaciada (omitiria Storage)')

    for crudo, esperado in (
        (f'{REF}.supabase.co', CANONICA),
        (f'https://https://{REF}.supabase.co', CANONICA),
        (f'"{CANONICA}/rest/v1"', CANONICA),
        (f'http://{REF}.supabase.co/', CANONICA),
        (f'https://cdn.{REF}.supabase.co', f'https://cdn.{REF}.supabase.co'),
        (f'{REF}https://{REF}.supabase.co', CANONICA),
    ):
        resultado = sanitizar_url_supabase(crudo)
        if not resultado.valida or resultado.url != esperado:
            errores.append(
                f'{crudo!r} -> valida={resultado.valida} url={resultado.url!r} '
                f'(esperado {esperado})'
            )

    invalida = sanitizar_url_supabase('https://ejemplo.com')
    if invalida.valida or invalida.url:
        errores.append('https://ejemplo.com debia rechazarse')

    if errores:
        print('FALLO sanitizacion SUPABASE_URL:')
        for item in errores:
            print(f'  - {item}')
        return 1

    print(
        f'OK: {CANONICA} se acepta y no se vacia. '
        'Criterio: https:// + host *.supabase.co.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

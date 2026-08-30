#!/usr/bin/env python3
"""Validacion basica: https:// + .supabase.co se acepta y no se vacia."""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

REF = 'wesnnnvoavprgqcczzsg'
CANONICA = f'https://{REF}.supabase.co'


def main() -> int:
    from backend.supabase_connectivity import (
        _origen_basico_supabase,
        sanitizar_url_supabase,
    )

    errores = []

    limpia = sanitizar_url_supabase(CANONICA)
    if not limpia.valida or limpia.url != CANONICA or limpia.errores:
        errores.append(f'URL legitima rechazada: {limpia}')
    if _origen_basico_supabase(CANONICA) != CANONICA:
        errores.append('_origen_basico_supabase no devolvio la URL canonica')

    for crudo, esperado in (
        (CANONICA, CANONICA),
        (f'{REF}.supabase.co', CANONICA),
        (f'https://https://{REF}.supabase.co', CANONICA),
        (f'{CANONICA}/rest/v1', CANONICA),
        (f'http://{REF}.supabase.co/', CANONICA),
    ):
        resultado = sanitizar_url_supabase(crudo)
        if not resultado.valida or resultado.url != esperado:
            errores.append(
                f'{crudo!r} -> valida={resultado.valida} url={resultado.url!r} '
                f'(esperado {esperado})'
            )

    invalida = sanitizar_url_supabase('https://ejemplo.com')
    if invalida.valida or invalida.url:
        errores.append('https://ejemplo.com (sin .supabase.co) debia rechazarse')
    if any('contener .supabase.co' in e for e in (invalida.errores or [])):
        errores.append('el mensaje estricto de https:// + host no debe volver a emitirse')

    if errores:
        print('FALLO sanitizacion SUPABASE_URL:')
        for item in errores:
            print(f'  - {item}')
        return 1

    print(f'OK: {CANONICA} pasa con https:// + .supabase.co y se asigna al cliente.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

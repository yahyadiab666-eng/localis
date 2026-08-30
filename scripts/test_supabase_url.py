#!/usr/bin/env python3
"""Pruebas de sanitizacion de SUPABASE_URL (prefijos malformados y host canonico)."""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

REF = 'wesnnnvoavprgqcczzsg'
CANONICA = f'https://{REF}.supabase.co'
DATABASE_URL = f'postgresql://postgres.{REF}:secret@aws-0-us-east-1.pooler.supabase.com:5432/postgres'


def main() -> int:
    from backend.supabase_connectivity import sanitizar_url_supabase

    casos = [
        (CANONICA, CANONICA, False),
        (f'{REF}.supabase.co', CANONICA, False),
        (f'https://https://{REF}.supabase.co', CANONICA, True),
        (f'{REF}https://{REF}.supabase.co', CANONICA, True),
        (f'{REF}.supabase.cohttps://{REF}.supabase.co', CANONICA, True),
        (f'https://{REF}.{REF}.supabase.co', CANONICA, True),
        (f'https://{REF}.supabase.co/{REF}.supabase.co', CANONICA, True),
        (f'https://db.{REF}.supabase.co', CANONICA, True),
        (f'"{CANONICA}/rest/v1"', CANONICA, False),
        (f'http://{REF}.supabase.co/', CANONICA, False),
    ]

    errores = []
    for crudo, esperado, sospechoso in casos:
        resultado = sanitizar_url_supabase(crudo, database_url=DATABASE_URL)
        if not resultado.valida:
            errores.append(f'{crudo!r} invalida: {resultado.errores}')
            continue
        if resultado.url != esperado:
            errores.append(f'{crudo!r} -> {resultado.url!r} (esperado {esperado})')
        if resultado.url_recomendada != esperado:
            errores.append(
                f'{crudo!r} url_recomendada={resultado.url_recomendada!r}'
            )
        if sospechoso and not (
            resultado.id_sospechoso or resultado.advertencias
        ):
            errores.append(f'{crudo!r} debia marcarse como sanitizado/sospechoso')

    sucia = f'{REF}.supabase.cohttps://{REF}.supabase.co'
    resultado_sucia = sanitizar_url_supabase(sucia)
    if resultado_sucia.url != CANONICA or not resultado_sucia.valida:
        errores.append(f'sin DATABASE_URL no limpio prefijo pegado: {resultado_sucia}')

    invalida = sanitizar_url_supabase('https://ejemplo.com')
    if invalida.valida or not invalida.errores:
        errores.append('https://ejemplo.com debia rechazarse')

    if errores:
        print('FALLO sanitizacion SUPABASE_URL:')
        for item in errores:
            print(f'  - {item}')
        return 1

    print(
        f'OK sanitizacion SUPABASE_URL: {len(casos)} casos + rechazo de host ajeno. '
        f'Valor correcto en Render: SUPABASE_URL={CANONICA}'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

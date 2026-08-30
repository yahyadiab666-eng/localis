#!/usr/bin/env python3
"""Pruebas: cualquier *.supabase.co es valido y no se vacia."""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

REF = 'wesnnnvoavprgqcczzsg'
CANONICA = f'https://{REF}.supabase.co'
DATABASE_URL = (
    f'postgresql://postgres.{REF}:secret@aws-0-us-east-1.pooler.supabase.com:5432/postgres'
)


def _asignar_cliente(resultado) -> str:
    """Misma regla que backend.supabase_client: no vaciar si hay origen usable."""
    return resultado.url or ''


def main() -> int:
    from backend.supabase_connectivity import sanitizar_url_supabase

    errores = []

    limpia = sanitizar_url_supabase(CANONICA, database_url=DATABASE_URL)
    if not limpia.valida or limpia.url != CANONICA or limpia.errores:
        errores.append(f'URL legitima rechazada: {limpia}')
    if limpia.id_sospechoso:
        errores.append('URL legitima marcada como id_sospechoso')
    if _asignar_cliente(limpia) != CANONICA:
        errores.append(f'cliente no recibio la URL: {_asignar_cliente(limpia)!r}')

    subdominio_extra = sanitizar_url_supabase(f'https://cdn.{REF}.supabase.co')
    if not subdominio_extra.valida or not subdominio_extra.url.endswith('.supabase.co'):
        errores.append(f'subdominio extra rechazado: {subdominio_extra}')
    if not _asignar_cliente(subdominio_extra):
        errores.append('subdominio extra vaciado para el cliente')

    casos_sanos = [
        (f'{REF}.supabase.co', CANONICA),
        (f'https://https://{REF}.supabase.co', CANONICA),
        (f'"{CANONICA}/rest/v1"', CANONICA),
        (f'http://{REF}.supabase.co/', CANONICA),
        (f'https://db.{REF}.supabase.co', f'https://db.{REF}.supabase.co'),
    ]
    for crudo, esperado in casos_sanos:
        resultado = sanitizar_url_supabase(crudo, database_url=DATABASE_URL)
        asignada = _asignar_cliente(resultado)
        if not resultado.valida or asignada != esperado:
            errores.append(
                f'{crudo!r} -> valida={resultado.valida} url={asignada!r} '
                f'(esperado {esperado}) errores={resultado.errores}'
            )

    sucia = f'{REF}https://{REF}.supabase.co'
    resultado_sucia = sanitizar_url_supabase(sucia)
    if resultado_sucia.url != CANONICA or not resultado_sucia.valida:
        errores.append(f'prefijo pegado no se recupero: {resultado_sucia}')
    if _asignar_cliente(resultado_sucia) != CANONICA:
        errores.append('prefijo pegado dejo la URL vacia en el cliente')

    invalida = sanitizar_url_supabase('https://ejemplo.com')
    if invalida.valida or _asignar_cliente(invalida):
        errores.append('https://ejemplo.com debia rechazarse y no asignarse')

    if errores:
        print('FALLO sanitizacion SUPABASE_URL:')
        for item in errores:
            print(f'  - {item}')
        return 1

    print(
        f'OK: {CANONICA} se acepta, no se vacia y se asigna al cliente. '
        'Cualquier *.supabase.co es valido.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

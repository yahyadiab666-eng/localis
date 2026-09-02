#!/usr/bin/env python3
"""Diagnóstico seguro: imprime el claim `role` del JWT, nunca la clave completa."""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _limpiar(valor: str | None) -> str:
    if valor is None:
        return ''
    return str(valor).strip().strip('"').strip("'").replace('\r', '').replace('\n', '')


def rol_jwt(token: str | None) -> str | None:
    texto = _limpiar(token)
    if not texto:
        return None
    if texto.startswith('sb_secret_'):
        return 'sb_secret'
    if texto.startswith('sb_publishable_'):
        return 'sb_publishable'
    if texto.startswith('sb_anon_'):
        return 'sb_anon'
    if texto.count('.') < 2:
        return 'not_jwt'
    try:
        payload = texto.split('.', 2)[1]
        payload += '=' * (-len(payload) % 4)
        datos = json.loads(base64.urlsafe_b64decode(payload.encode('ascii')))
        rol = datos.get('role')
        return str(rol) if rol else 'jwt_sin_role'
    except Exception as error:
        return f'decode_error:{type(error).__name__}'


def _reporte(titulo: str, getter) -> None:
    nombres = (
        'SUPABASE_SERVICE_ROLE_KEY',
        'SUPABASE_SECRET_KEY',
        'SUPABASE_SERVICE_ROL_KEY',
        'SUPABASE_KEY',
        'SUPABASE_ANON_KEY',
    )
    print(f'=== {titulo} ===')
    for nombre in nombres:
        crudo = getter(nombre)
        texto = _limpiar(crudo)
        print(
            f'  {nombre}: presente={bool(texto)} '
            f'jwt_role={rol_jwt(texto)!r} len={len(texto)}'
        )


def main() -> int:
    print('=== Diagnostico JWT Supabase (sin secretos) ===')
    _reporte('os.environ ANTES de dotenv', os.environ.get)

    from dotenv import dotenv_values, load_dotenv

    archivo = RAIZ / '.env'
    valores_archivo = dotenv_values(archivo)
    print('=== Claves SUPABASE en .env (nombres) ===')
    for clave in valores_archivo:
        if 'SUPABASE' in clave.upper() or 'SERVICE' in clave.upper():
            print(
                f'  {clave!r}: presente={bool(_limpiar(valores_archivo.get(clave)))} '
                f'jwt_role={rol_jwt(valores_archivo.get(clave))!r}'
            )

    load_dotenv(archivo, override=True)
    _reporte('os.environ DESPUES de load_dotenv(override=True)', os.environ.get)

    from backend.supabase_client import auditar_claves_supabase

    informe = auditar_claves_supabase()
    print('=== auditar_claves_supabase() (cliente en memoria) ===')
    for clave in (
        'nombre_env_leido',
        'nombre_env_canonico',
        'nombre_variable_typo',
        'service_jwt_role',
        'anon_jwt_role',
        'claves_identicas',
        'service_ok',
        'service_rechazada_al_iniciar',
        'storage_cliente_ok',
        'modo_hibrido',
    ):
        print(f'  {clave}: {informe.get(clave)!r}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

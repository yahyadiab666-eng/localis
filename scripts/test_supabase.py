#!/usr/bin/env python3
"""
Auditoría local de Supabase para Localis.

Verifica (sin imprimir secretos):
  1. Que SUPABASE_SERVICE_ROLE_KEY sea JWT role=service_role (no anon).
  2. Que el bucket "imagenes" acepte una subida con esa llave.
  3. Que las consultas de productos respondan vía DATABASE_URL.

Uso:
  python scripts/test_supabase.py
  python scripts/test_supabase.py --sin-subida
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# PNG 1x1 válido (probe de Storage; se borra al terminar).
_PNG_1X1 = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00'
    b'\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
)
_RUTA_PROBE = '_diagnostico/localis-probe.png'


def _cargar_entorno() -> None:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=True)


def _imprimir_auditoria(informe: dict) -> None:
    print('=== Auditoria de llaves (sin secretos) ===')
    print(f"  SUPABASE_URL presente:     {informe.get('supabase_url_presente')}")
    print(f"  Anon presente:             {informe.get('anon_presente')}")
    print(f"  Anon jwt_role:             {informe.get('anon_jwt_role')}")
    print(f"  Service presente:          {informe.get('service_presente')}")
    print(f"  Service jwt_role:          {informe.get('service_jwt_role')}")
    print(f"  Claves identicas:          {informe.get('claves_identicas')}")
    print(f"  Typo ROL (falta E):        {informe.get('nombre_variable_typo')}")
    print(f"  Service OK:                {informe.get('service_ok')}")
    print(f"  Rechazada al iniciar:      {informe.get('service_rechazada_al_iniciar')}")
    print(f"  Cliente Storage OK:        {informe.get('storage_cliente_ok')}")
    print(f"  Bucket:                    {informe.get('bucket')}")


def _probar_listado_bucket() -> tuple[bool, str]:
    import httpx

    from backend.supabase_client import (
        SUPABASE_BUCKET_IMAGENES,
        SUPABASE_URL,
        headers_storage_service_role,
    )

    url = f'{SUPABASE_URL.rstrip("/")}/storage/v1/bucket/{SUPABASE_BUCKET_IMAGENES}'
    try:
        headers = headers_storage_service_role()
    except RuntimeError as error:
        return False, str(error)

    try:
        with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=15.0)) as http:
            respuesta = http.get(url, headers=headers)
    except Exception as error:
        return False, f'{type(error).__name__}: {error}'

    if respuesta.status_code == 200:
        return True, f'HTTP {respuesta.status_code} bucket accesible'
    detalle = (respuesta.text or respuesta.reason_phrase or '')[:240]
    return False, f'HTTP {respuesta.status_code}: {detalle}'


def _probar_subida() -> tuple[bool, str]:
    from backend.supabase_client import (
        SUPABASE_BUCKET_IMAGENES,
        SUPABASE_URL,
        headers_storage_service_role,
        obtener_cliente_storage,
    )
    from backend.supabase_storage import (
        SupabaseUploadError,
        _subir_bytes_al_bucket,
        _url_objeto_storage,
    )

    try:
        url = _subir_bytes_al_bucket(
            obtener_cliente_storage(),
            _RUTA_PROBE,
            _PNG_1X1,
            'image/png',
        )
    except SupabaseUploadError as error:
        return False, str(error)
    except Exception as error:
        return False, f'{type(error).__name__}: {error}'

    if not url or 'None' in str(url):
        return False, f'subida devolvio URL invalida: {url!r}'

    try:
        headers = headers_storage_service_role()
        with __import__('httpx').Client(timeout=10.0) as http:
            http.delete(_url_objeto_storage(_RUTA_PROBE), headers=headers)
    except Exception:
        pass

    public_ok = f'/storage/v1/object/public/{SUPABASE_BUCKET_IMAGENES}/' in url
    extra = '' if public_ok else ' (URL no coincide con el prefijo publico esperado)'
    host = (SUPABASE_URL or '').split('://', 1)[-1].split('/', 1)[0]
    return True, f'OK url publica en {host}{extra}'


def _probar_productos() -> tuple[bool, str]:
    from backend.db import DATABASE_URL, get_db_connection, using_postgres

    if not DATABASE_URL:
        return False, 'DATABASE_URL ausente'

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute('SELECT COUNT(*) FROM productos')
            fila = cursor.fetchone()
            total = fila[0] if not isinstance(fila, dict) else next(iter(fila.values()))
            cursor.execute(
                """
                SELECT COUNT(*) FROM productos
                WHERE imagen_url IS NULL OR TRIM(imagen_url) = ''
                """
            )
            fila_vacias = cursor.fetchone()
            vacias = (
                fila_vacias[0]
                if not isinstance(fila_vacias, dict)
                else next(iter(fila_vacias.values()))
            )
            motor = 'postgres' if using_postgres() else 'sqlite'
            return True, f'{motor}: {total} productos, {vacias} sin imagen_url'
    except Exception as error:
        return False, f'{type(error).__name__}: {error}'


def main() -> int:
    parser = argparse.ArgumentParser(description='Auditoria local de Supabase para Localis')
    parser.add_argument(
        '--sin-subida',
        action='store_true',
        help='No subir el PNG de prueba al bucket imagenes',
    )
    args = parser.parse_args()

    _cargar_entorno()

    from backend.supabase_client import auditar_claves_supabase

    informe = auditar_claves_supabase()
    _imprimir_auditoria(informe)

    fallos = []

    if not informe.get('service_ok'):
        rol = informe.get('service_jwt_role') or 'ausente'
        fallos.append(
            f'SUPABASE_SERVICE_ROLE_KEY no es service_role (jwt_role={rol}). '
            'En Render: Dashboard -> Settings -> API -> copia service_role (secret), '
            'no anon/publishable. Reinicia el servicio tras cambiar la variable.'
        )
        if informe.get('nombre_variable_typo'):
            fallos.append(
                'La variable local/Render se llama SUPABASE_SERVICE_ROL_KEY (falta la E). '
                'Renombrala a SUPABASE_SERVICE_ROLE_KEY.'
            )
        if informe.get('claves_identicas'):
            fallos.append(
                'SUPABASE_SERVICE_ROLE_KEY es identica a SUPABASE_KEY (anon pegada dos veces).'
            )

    print('\n=== Bucket imagenes (GET) ===')
    if informe.get('service_ok'):
        ok_bucket, detalle_bucket = _probar_listado_bucket()
        print(f'  {detalle_bucket}')
        if not ok_bucket:
            fallos.append(f'Listado bucket: {detalle_bucket}')
    else:
        print('  omitido: no hay service_role valida')

    print('\n=== Subida probe al bucket imagenes ===')
    if args.sin_subida:
        print('  omitida (--sin-subida)')
    elif not informe.get('service_ok'):
        print('  omitida: no hay service_role valida')
    else:
        ok_subida, detalle_subida = _probar_subida()
        print(f'  {detalle_subida}')
        if not ok_subida:
            fallos.append(f'Subida: {detalle_subida}')

    print('\n=== Consulta productos (DATABASE_URL) ===')
    ok_prod, detalle_prod = _probar_productos()
    print(f'  {detalle_prod}')
    if not ok_prod:
        fallos.append(f'Productos: {detalle_prod}')

    print('')
    if fallos:
        print('RESULTADO: FALLO')
        for item in fallos:
            print(f'  - {item}')
        return 1

    print('RESULTADO: OK (service_role valida, Storage y productos responden)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

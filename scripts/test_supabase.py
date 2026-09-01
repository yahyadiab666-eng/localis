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
    from backend.utils import url_imagen_catalogo_valida
    from utils.images import url_publica_producto_desde_bd

    if not DATABASE_URL:
        return False, 'DATABASE_URL ausente'

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT id, nombre, codigo_barras, imagen_url
                FROM productos
                ORDER BY id
                """
            )
            filas = [dict(r) if isinstance(r, dict) else {
                'id': r[0], 'nombre': r[1], 'codigo_barras': r[2], 'imagen_url': r[3],
            } for r in cursor.fetchall()]
        total = len(filas)
        con_url = 0
        vacias = []
        for fila in filas:
            url = url_publica_producto_desde_bd(fila.get('imagen_url')) or url_imagen_catalogo_valida(
                fila.get('imagen_url')
            )
            vista = url or 'None'
            print(
                f"  id={fila.get('id')} nombre={fila.get('nombre')!r} "
                f"codigo={fila.get('codigo_barras')!r} url_bd={fila.get('imagen_url')!r} "
                f"vista={vista!r}"
            )
            if url:
                con_url += 1
            else:
                vacias.append(fila.get('nombre') or fila.get('id'))
        motor = 'postgres' if using_postgres() else 'sqlite'
        if vacias:
            return False, (
                f'{motor}: {con_url}/{total} con imagen; sin URL: {vacias}'
            )
        return True, f'{motor}: {con_url}/{total} productos con URL operativa'
    except Exception as error:
        return False, f'{type(error).__name__}: {error}'


def _probar_head_imagenes() -> tuple[bool, str]:
    import httpx

    from backend.db import get_db_connection
    from utils.images import url_publica_producto_desde_bd

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT imagen_url FROM productos
            WHERE imagen_url IS NOT NULL AND TRIM(CAST(imagen_url AS TEXT)) <> ''
            """
        )
        urls = []
        for fila in cursor.fetchall():
            crudo = fila[0] if not isinstance(fila, dict) else fila.get('imagen_url')
            url = url_publica_producto_desde_bd(crudo)
            if url:
                urls.append(url)
    if not urls:
        return False, 'no hay URLs para HEAD'
    fallos_head = []
    with httpx.Client(
        timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
        follow_redirects=True,
    ) as http:
        for url in urls:
            try:
                resp = http.head(url)
                if resp.status_code >= 400:
                    resp = http.get(url, headers={'Range': 'bytes=0-64'})
                if resp.status_code >= 400:
                    fallos_head.append(f'{resp.status_code} {url[:80]}')
            except Exception as error:
                fallos_head.append(f'{type(error).__name__} {url[:80]}')
    if fallos_head:
        return False, f'{len(fallos_head)} URL(s) no responden: {fallos_head[:3]}'
    return True, f'{len(urls)} URL(s) responden HTTP < 400'


def main() -> int:
    parser = argparse.ArgumentParser(description='Auditoria local de Supabase para Localis')
    parser.add_argument(
        '--sin-subida',
        action='store_true',
        help='No subir el PNG de prueba al bucket imagenes',
    )
    parser.add_argument(
        '--sin-relleno',
        action='store_true',
        help='No rellenar imagen_url vacias desde OpenFoodFacts',
    )
    args = parser.parse_args()

    _cargar_entorno()

    from backend.supabase_client import auditar_claves_supabase
    from backend.supabase_storage import asegurar_politicas_bucket_imagenes

    informe = auditar_claves_supabase()
    _imprimir_auditoria(informe)

    fallos = []

    print('\n=== Politicas RLS bucket imagenes ===')
    ok_pol = asegurar_politicas_bucket_imagenes()
    print(f'  {"OK" if ok_pol else "FALLO o no postgres"}')
    if not ok_pol:
        fallos.append('No se pudieron crear/verificar politicas RLS de Storage')

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
        print('  omitido: no hay service_role valida (las subidas manuales seguiran en 403)')

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

    print('\n=== Relleno cascada (maestro / EAN / nombre) ===')
    if args.sin_relleno:
        print('  omitido (--sin-relleno)')
    else:
        from backend.image_lookup import rellenar_imagenes_catalogo

        n_fill = rellenar_imagenes_catalogo()
        print(f'  actualizados={n_fill}')

    print('\n=== Consulta productos (url_bd no None) ===')
    ok_prod, detalle_prod = _probar_productos()
    print(f'  {detalle_prod}')
    if not ok_prod:
        fallos.append(f'Productos: {detalle_prod}')
    else:
        print('\n=== HEAD/GET de URLs de imagen ===')
        ok_head, detalle_head = _probar_head_imagenes()
        print(f'  {detalle_head}')
        if not ok_head:
            fallos.append(f'HEAD imagenes: {detalle_head}')

    print('')
    if fallos:
        print('RESULTADO: FALLO')
        for item in fallos:
            print(f'  - {item}')
        return 1

    print('RESULTADO: OK (catalogo con URLs operativas; Storage service_role listo si la llave es valida)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

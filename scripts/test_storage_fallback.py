#!/usr/bin/env python3
"""Prueba de integridad: Storage sin respaldo local y validacion de URL canonica."""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def main():
    from backend.supabase_client import SUPABASE_URL, construir_url_publica_storage
    from backend.supabase_storage import (
        SupabaseUploadError,
        _persistir_con_respaldo,
        intentar_subir_imagen,
    )
    from backend.utils import imagen_url_almacenada, url_imagen_subida_storage_valida

    errores = []

    url_mayusculas = (
        'https://ejemplo.supabase.co/Storage/V1/Object/Public/imagenes/productos/x.webp'
    )
    if not url_imagen_subida_storage_valida(url_mayusculas):
        errores.append('Validacion case-insensitive fallo para ruta /Storage/V1/...')

    if SUPABASE_URL:
        ruta = 'productos/test_canonical.webp'
        url_canonica = construir_url_publica_storage(ruta)
        if not url_canonica:
            errores.append('construir_url_publica_storage devolvio vacio')
        elif not url_imagen_subida_storage_valida(url_canonica):
            errores.append(f'URL canonica rechazada: {url_canonica}')
        elif imagen_url_almacenada(url_canonica) != url_canonica:
            errores.append('imagen_url_almacenada no reconoce URL de Storage')
    else:
        url_canonica = (
            'https://ejemplo.supabase.co/storage/v1/object/public/imagenes/productos/x.webp'
        )
        if imagen_url_almacenada(url_canonica) != url_canonica:
            errores.append('imagen_url_almacenada no reconoce URL de Storage (sin env)')

    try:
        _persistir_con_respaldo(
            b'fallback-bytes',
            'integrity_no_client.webp',
            'image/webp',
            'productos',
            supabase_client=None,
        )
        errores.append(
            '_persistir_con_respaldo sin cliente Supabase deberia lanzar SupabaseUploadError'
        )
    except SupabaseUploadError as error:
        mensaje = str(error).lower()
        if 'supabase' not in mensaje and 'storage' not in mensaje:
            errores.append(f'Mensaje poco claro sin cliente: {error}')
    except Exception as error:
        errores.append(f'_persistir_con_respaldo sin Supabase: {type(error).__name__}: {error}')

    try:
        url_h, aviso_h = intentar_subir_imagen(None)
        if url_h is not None or aviso_h is not None:
            errores.append('intentar_subir_imagen(None) debe devolver (None, None)')
    except Exception as error:
        errores.append(f'modo hibrido lanzo: {type(error).__name__}: {error}')

    if errores:
        print('FALLO integridad storage:')
        for item in errores:
            print(f'  - {item}')
        return 1

    print(
        'OK integridad storage: URL canonica valida, case-insensitive OK, '
        'bajo nivel lanza SupabaseUploadError, hibrido no interrumpe.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

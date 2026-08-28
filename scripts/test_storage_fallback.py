#!/usr/bin/env python3
"""Prueba de integridad: respaldo local cuando Supabase Storage no responde."""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def main():
    from backend.local_storage import UPLOADS_STATIC_PREFIX, guardar_bytes_local
    from backend.supabase_storage import _persistir_con_respaldo
    from backend.utils import imagen_url_almacenada, url_imagen_local_valida

    errores = []

    try:
        url = guardar_bytes_local(b'test-bytes', 'integrity_local.webp', 'comercios')
        if not url.startswith(UPLOADS_STATIC_PREFIX):
            errores.append(f'URL local invalida: {url}')
        if not url_imagen_local_valida(url):
            errores.append('url_imagen_local_valida rechazo la ruta local')
        if imagen_url_almacenada(url) != url:
            errores.append('imagen_url_almacenada no reconoce ruta local')
    except Exception as error:
        errores.append(f'guardar_bytes_local: {error}')

    try:
        url_fallback = _persistir_con_respaldo(
            b'fallback-bytes',
            'integrity_fallback.webp',
            'image/webp',
            'productos',
            supabase_client=None,
        )
        if not url_fallback.startswith(UPLOADS_STATIC_PREFIX):
            errores.append(f'fallback URL invalida: {url_fallback}')
    except Exception as error:
        errores.append(f'_persistir_con_respaldo sin Supabase: {error}')

    if errores:
        print('FALLO integridad storage:')
        for item in errores:
            print(f'  - {item}')
        return 1

    print('OK integridad storage: respaldo local y resolucion de URLs funcionan.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Purga segura de assets manuales en Storage (sin tocar recursos compartidos).

Pruebas sin red ni base de datos:
  - ``ruta_storage_desde_url`` reconoce URLs locales y públicas de Storage.
  - ``_es_eliminable`` solo autoriza assets manuales (manual_/logo_/banner_).
  - ``eliminar_asset`` borra la copia local y el objeto de Storage.
  - ``limpiar_asset_anterior`` no toca el asset si es el mismo.
  - ``limpiar_huerfanos`` borra solo lo no referenciado y nunca lo compartido.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

_ERRORES = []


def _ok(condicion, mensaje):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    _ERRORES.append(mensaje)
    return False


def _probar_rutas():
    print('\n=== Interpretación de rutas de asset ===')
    from backend.storage_cleanup import ruta_storage_desde_url

    base = 'https://x.supabase.co/storage/v1/object/public/imagenes'
    _ok(
        ruta_storage_desde_url(f'{base}/productos/manual_1_ab.webp') == ('productos', 'manual_1_ab.webp'),
        'URL pública de Storage -> (carpeta, archivo)',
    )
    _ok(
        ruta_storage_desde_url('/static/uploads/productos/manual_1_ab.webp') == ('productos', 'manual_1_ab.webp'),
        'URL local -> (carpeta, archivo)',
    )
    _ok(
        ruta_storage_desde_url(f'{base}/marcas/marca_diana.png') == ('marcas', 'marca_diana.png'),
        'URL de marca reconocida',
    )
    _ok(ruta_storage_desde_url('/static/img/placeholder-otros.svg') is None, 'placeholder -> None')
    _ok(ruta_storage_desde_url('') is None, 'vacío -> None')


def _probar_elegibilidad():
    print('\n=== Solo assets manuales son eliminables ===')
    from backend.storage_cleanup import _es_eliminable

    _ok(_es_eliminable('productos', 'manual_1_ab.webp'), 'productos/manual_* eliminable')
    _ok(not _es_eliminable('productos', 'auto_coca_abc.webp'), 'productos/auto_* (maestro) NO eliminable')
    _ok(not _es_eliminable('marcas', 'marca_diana.png'), 'marcas/* (compartido) NO eliminable')
    _ok(not _es_eliminable('genericos', 'producto_abc.png'), 'genericos/* (compartido) NO eliminable')
    _ok(_es_eliminable('comercios', 'logo_1_ab.webp'), 'comercios/logo_* eliminable')
    _ok(_es_eliminable('banners', 'banner_app_ab.webp'), 'banners/banner_* eliminable')
    _ok(not _es_eliminable('comercios', 'otro_1.webp'), 'otros nombres NO eliminables')


def _probar_eliminar_asset():
    print('\n=== Borrado de un asset manual (local + Storage) ===')
    import config
    from backend import storage_cleanup as sc

    tmp = tempfile.mkdtemp()
    try:
        carpeta = Path(tmp) / 'static' / 'uploads' / 'productos'
        carpeta.mkdir(parents=True)
        archivo = carpeta / 'manual_9_zz.webp'
        archivo.write_bytes(b'contenido')

        with patch.object(config, 'RUTA_RAIZ', tmp), patch.object(
            sc, 'eliminar_objetos_storage', side_effect=lambda rutas: len(rutas)
        ) as espia:
            resultado = sc.eliminar_asset('/static/uploads/productos/manual_9_zz.webp')
            _ok(resultado is True, 'eliminar_asset devuelve True')
            _ok(not archivo.exists(), 'la copia local se borra')
            _ok(espia.called, 'se invoca la purga en Storage')

        with patch.object(config, 'RUTA_RAIZ', tmp), patch.object(
            sc, 'eliminar_objetos_storage', side_effect=lambda rutas: len(rutas)
        ):
            _ok(
                sc.eliminar_asset('/static/img/placeholder-otros.svg') is False,
                'un placeholder no es eliminable',
            )
            _ok(
                sc.limpiar_asset_anterior('/static/uploads/productos/manual_a.webp',
                                          '/static/uploads/productos/manual_a.webp') is False,
                'no se borra si el asset es el mismo',
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _probar_huerfanos():
    print('\n=== Barrido de huérfanos: solo lo no referenciado ===')
    import config
    from backend import storage_cleanup as sc

    tmp = tempfile.mkdtemp()
    try:
        base = Path(tmp) / 'static' / 'uploads' / 'productos'
        base.mkdir(parents=True)
        (base / 'manual_keep.webp').write_bytes(b'x')
        (base / 'manual_orphan.webp').write_bytes(b'x')
        (base / 'auto_shared.webp').write_bytes(b'x')

        listado = {
            'productos': ['manual_keep.webp', 'manual_orphan.webp', 'auto_shared.webp'],
            'comercios': ['logo_1_old.webp', 'otro.webp'],
            'banners': ['banner_app_x.webp'],
        }
        with patch.object(config, 'RUTA_RAIZ', tmp), patch.object(
            sc, '_referencias_bd', return_value={'productos/manual_keep.webp'}
        ), patch.object(sc, '_listar_storage', side_effect=lambda c: listado.get(c, [])), patch.object(
            sc, 'eliminar_objetos_storage', side_effect=lambda rutas: len(rutas)
        ):
            resultado = sc.limpiar_huerfanos(max_por_carpeta=50)

        _ok(not (base / 'manual_orphan.webp').exists(), 'huérfano manual purgado')
        _ok((base / 'manual_keep.webp').exists(), 'asset referenciado conservado')
        _ok((base / 'auto_shared.webp').exists(), 'asset compartido (auto_) conservado')
        _ok(resultado['borrados'] >= 1, f"se reportan borrados ({resultado['borrados']})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main_test() -> int:
    _probar_rutas()
    _probar_elegibilidad()
    _probar_eliminar_asset()
    _probar_huerfanos()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK limpieza de assets: purga segura y barrido de huérfanos')
    return 0


if __name__ == '__main__':
    raise SystemExit(main_test())

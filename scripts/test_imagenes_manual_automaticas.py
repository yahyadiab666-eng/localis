#!/usr/bin/env python3
"""Imágenes manuales vs. automáticas: registro permanente y ciclo de vida.

Pruebas sin base de datos (se parchean las funciones de persistencia):
  - Clave de producto y categorías permitidas (Hardware/Tech/Appliances/Health/Food).
  - Google Custom Search: deshabilitado sin claves; parseo correcto con clave.
  - ``buscar_o_cachear_automatica``: caché positiva/negativa, una consulta por
    producto, EAN primero y nombre+descripción como respaldo.
  - El registro automático nunca degrada un acierto previo (COALESCE).
  - La purga manual solo toca carpetas/prefijos manuales.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
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


def _probar_clave_y_categorias():
    print('\n=== Clave de producto y categorías permitidas ===')
    from backend.imagenes_producto import categoria_permitida, normalizar_clave

    _ok(normalizar_clave('7591001001234') == 'ean:7591001001234',
        'con EAN -> clave ean:')
    clave_nombre = normalizar_clave(None, 'Harina P.A.N. 1kg')
    _ok(bool(clave_nombre) and clave_nombre.startswith('nom:') and 'harina' in clave_nombre,
        f'sin EAN -> clave por nombre normalizado ({clave_nombre})')
    _ok(normalizar_clave(None, None, None) is None, 'sin datos -> None')

    for cat in ('ferreteria', 'tecnologia', 'hogar', 'salud', 'alimentos'):
        _ok(categoria_permitida(cat), f'categoría permitida: {cat}')
    for cat in ('ropa', 'juguetes', 'mascotas'):
        _ok(not categoria_permitida(cat), f'categoría no permitida: {cat}')
    _ok(categoria_permitida('Electrodomésticos'), 'texto libre "Electrodomésticos" permitido')


def _probar_google_cse():
    print('\n=== Cliente Google Custom Search ===')
    import os

    from backend import google_cse

    with patch.dict(os.environ, {'GOOGLE_CSE_API_KEY': '', 'GOOGLE_CSE_CX': ''}, clear=False):
        _ok(not google_cse.habilitado(), 'sin claves -> deshabilitado')
        _ok(google_cse.buscar_imagenes('algo') == [], 'sin claves no llama a la API')

    with patch.dict(
        os.environ,
        {'GOOGLE_CSE_API_KEY': 'k', 'GOOGLE_CSE_CX': 'cx'},
        clear=False,
    ), patch.object(google_cse, 'clave_cse', return_value=('k', 'cx')):
        _ok(google_cse.habilitado(), 'con GOOGLE_CSE_API_KEY + CX -> habilitado')
        respuesta = SimpleNamespace(
            status_code=200,
            json=lambda: {
                'items': [
                    {'title': 'X', 'link': 'https://cdn.tienda.com/foto.webp',
                     'image': {'width': 800, 'height': 800, 'thumbnailLink': 'https://t/f.jpg'}},
                ]
            },
        )
        with patch('backend.http_client.get', return_value=respuesta):
            datos = google_cse.buscar_imagenes('taladro bosch', limite=5)
        _ok(len(datos) == 1 and datos[0]['dominio'] == 'cdn.tienda.com',
            'parsea los resultados de imagen')

    # Nombre+descripción como respaldo.
    with patch('backend.google_cse.buscar_imagenes', return_value=[{'url': 'u'}]) as espia:
        google_cse.buscar_por_nombre_descripcion('Licuadora Oster', '2 velocidades')
        consulta = espia.call_args[0][0]
        _ok('Licuadora Oster' in consulta and '2 velocidades' in consulta,
            'la consulta combina nombre + descripción')


def _probar_cache_automatica():
    print('\n=== Una consulta por producto (caché permanente) ===')
    from backend import imagenes_producto as ip

    # 1) Caché positiva: no consulta la API.
    with patch.object(ip, 'obtener_automatica', return_value={'url_imagen': 'auto.webp', 'fuente': 'google_cse', 'encontrada': 1}), patch.object(
        ip, 'registrar_automatica'
    ) as reg:
        res = ip.buscar_o_cachear_automatica(1, categoria='tecnologia', nombre='X', codigo_barras='123')
        _ok(res['url'] == 'auto.webp' and res['desde_cache'] and not reg.called,
            'acierto en caché: no se gasta API')

    # 2) Caché negativa: tampoco consulta.
    with patch.object(ip, 'obtener_automatica', return_value={'url_imagen': None, 'fuente': 'google_cse', 'encontrada': 0}), patch.object(
        ip, 'registrar_automatica'
    ) as reg, patch('backend.google_cse.buscar_por_codigo') as por_codigo:
        res = ip.buscar_o_cachear_automatica(1, categoria='salud', nombre='Y', codigo_barras='9')
        _ok(res['url'] is None and res['desde_cache'] and not reg.called and not por_codigo.called,
            'caché negativa: no se repite la consulta')

    # 3) Categoría no permitida: no consulta ni registra.
    with patch.object(ip, 'obtener_automatica', return_value=None), patch.object(
        ip, 'registrar_automatica'
    ) as reg, patch('backend.google_cse.habilitado', return_value=True):
        res = ip.buscar_o_cachear_automatica(2, categoria='ropa', nombre='Camisa')
        _ok(res['url'] is None and not reg.called, 'categoría fuera de alcance: sin API')

    # 4) API no configurada: no registra (no envenena el caché).
    with patch.object(ip, 'obtener_automatica', return_value=None), patch.object(
        ip, 'registrar_automatica'
    ) as reg, patch('backend.google_cse.habilitado', return_value=False):
        res = ip.buscar_o_cachear_automatica(3, categoria='tecnologia', nombre='TV')
        _ok(res['url'] is None and not reg.called and res['fuente'] == 'api_no_configurada',
            'sin API configurada no se registra negativo')

    # 5) EAN primero y registro positivo.
    with patch.object(ip, 'obtener_automatica', return_value=None), patch.object(
        ip, 'registrar_automatica'
    ) as reg, patch('backend.google_cse.habilitado', return_value=True), patch(
        'backend.google_cse.buscar_por_codigo', return_value=[{'url': 'ean.webp'}]
    ) as por_codigo, patch(
        'backend.google_cse.buscar_por_nombre_descripcion'
    ) as por_nombre:
        res = ip.buscar_o_cachear_automatica(4, categoria='tecnologia', nombre='TV', codigo_barras='759')
        _ok(res['url'] == 'ean.webp' and por_codigo.called and not por_nombre.called,
            'Priority 1: se usa el código de barras')
        _ok(reg.called, 'el acierto se registra de forma permanente')

    # 6) Sin barcode: nombre+descripción, y negativo si no hay resultados.
    with patch.object(ip, 'obtener_automatica', return_value=None), patch.object(
        ip, 'registrar_automatica'
    ) as reg, patch('backend.google_cse.habilitado', return_value=True), patch(
        'backend.google_cse.buscar_por_nombre_descripcion', return_value=[{'url': 'nom.webp'}]
    ):
        res = ip.buscar_o_cachear_automatica(5, categoria='alimentos', nombre='Harina', descripcion='maíz')
        _ok(res['url'] == 'nom.webp', 'Priority 2: nombre + descripción')
        _ok(reg.call_args.kwargs.get('encontrada') is True, 'registra acierto')

    with patch.object(ip, 'obtener_automatica', return_value=None), patch.object(
        ip, 'registrar_automatica'
    ) as reg, patch('backend.google_cse.habilitado', return_value=True), patch(
        'backend.google_cse.buscar_por_nombre_descripcion', return_value=[]
    ):
        res = ip.buscar_o_cachear_automatica(6, categoria='alimentos', nombre='Nada')
        _ok(res['url'] is None and reg.call_args.kwargs.get('encontrada') is False,
            'sin resultados se registra caché negativo permanente')


def _probar_registro_no_borra():
    print('\n=== El registro automático nunca se degrada ===')
    import inspect

    from backend import imagenes_producto as ip

    fuente = inspect.getsource(ip.registrar_automatica)
    _ok('COALESCE(EXCLUDED.url_imagen' in fuente,
        'la URL previa se conserva (COALESCE) al reintentar')
    _ok('DELETE' not in fuente.upper(), 'el registro automático no ejecuta DELETE')


def _probar_purga_manual():
    print('\n=== La purga solo alcanza assets manuales ===')
    from backend.imagenes_producto import _es_manual, _partes_asset

    _ok(_es_manual('productos', 'manual_1_ab.webp'), 'productos/manual_* es manual')
    _ok(_es_manual('comercios', 'logo_1.webp'), 'comercios/logo_* es manual')
    _ok(_es_manual('banners', 'banner_app.webp'), 'banners/banner_* es manual')
    _ok(not _es_manual('productos', 'auto_abc.webp'), 'productos/auto_* NO es manual')
    _ok(not _es_manual('marcas', 'marca_x.png'), 'marcas/* NO es manual')

    base = 'https://x.supabase.co/storage/v1/object/public/imagenes'
    _ok(_partes_asset(f'{base}/productos/manual_1_a.webp') == ('productos', 'manual_1_a.webp'),
        'parsea URL de Storage')
    _ok(_partes_asset('/static/uploads/productos/manual_1_a.webp') == ('productos', 'manual_1_a.webp'),
        'parsea URL local')


def _probar_proteccion_manual():
    print('\n=== Protección de subidas manuales ===')
    from backend.motor_imagenes import es_imagen_manual, puede_reemplazar

    _ok(es_imagen_manual('/static/uploads/productos/manual_9_x.webp'), 'manual local detectada')
    _ok(
        es_imagen_manual(
            'https://x.supabase.co/storage/v1/object/public/imagenes/productos/manual_9_x.webp'
        ),
        'manual en Storage detectada',
    )
    _ok(not puede_reemplazar('/static/uploads/productos/manual_9_x.webp'), 'no se pisa la manual')
    _ok(
        not puede_reemplazar(
            'https://x.supabase.co/storage/v1/object/public/imagenes/productos/auto_x.webp', 'real'
        ),
        'no se pisa una automática ya asignada',
    )


def main() -> int:
    _probar_clave_y_categorias()
    _probar_google_cse()
    _probar_cache_automatica()
    _probar_registro_no_borra()
    _probar_purga_manual()
    _probar_proteccion_manual()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK imágenes manuales/automáticas: registro permanente y ciclo de vida')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

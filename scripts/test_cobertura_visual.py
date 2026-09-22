#!/usr/bin/env python3
"""Integridad de assets: sin fabricados, sin rotos (estado neutro honesto).

Pruebas sin base de datos:
  - ``es_asset_generado`` / ``es_asset_verificado`` clasifican correctamente.
  - ``_ruta_local`` resuelve assets locales y rechaza rutas inseguras/URL remotas.
  - ``validar_cobertura`` lanza error duro ante assets **fabricados o rotos**,
    pero **no** penaliza que un producto no tenga imagen (estado neutro).
  - el reporte honesto cuenta la ausencia de imagen como pendiente (nunca real).
"""

from __future__ import annotations

import sys
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


def _probar_integridad_assets():
    print('\n=== Clasificación de assets (fabricado vs verificado) ===')
    from backend.activos_verificados import es_asset_generado, es_asset_verificado

    fabricados = [
        ('/static/img/placeholder-alimentos.svg', 'placeholder_categoria'),
        ('/static/uploads/marcas/acme.png', 'logo_monograma'),
        ('/static/uploads/genericos/producto_a.png', 'tarjeta_producto'),
        ('https://x.supabase.co/storage/v1/object/public/imagenes/marcas/producto_a.png', 'tarjeta_producto'),
    ]
    for url, fuente in fabricados:
        _ok(es_asset_generado(url, fuente), f'fabricado: {url[:52]} ({fuente})')

    verificados = [
        ('https://x.supabase.co/storage/v1/object/public/imagenes/productos/a.webp', None),
        ('/static/uploads/productos/manual_1_a.webp', None),
        ('https://cdn.simpleicons.org/samsung', 'logo_simpleicons'),
    ]
    for url, fuente in verificados:
        _ok(
            not es_asset_generado(url, fuente) and es_asset_verificado(url, fuente),
            f'verificado: {url[:52]}',
        )

    _ok(
        not es_asset_verificado('https://sitio-ajeno.example/foto.jpg'),
        'URL externa no confiable -> no verificada',
    )
    _ok(not es_asset_verificado('', None), 'vacío -> no verificado')


def _probar_ruta_local():
    print('\n=== Resolución segura de rutas locales ===')
    from backend.cobertura_visual import _ruta_local

    ruta = _ruta_local('/static/img/placeholder-producto.svg')
    _ok(ruta is not None and ruta.is_file(), 'resuelve un asset estático existente')
    _ok(_ruta_local('https://x.supabase.co/storage/v1/object/public/a.png') is None,
        'las URL remotas no son rutas locales')
    _ok(_ruta_local('/static/uploads/../config.py') is None, 'rechaza path traversal')
    _ok(_ruta_local('') is None, 'URL vacía -> None')


def _probar_validez_asset():
    print('\n=== Validez real de un asset ===')
    import backend.cobertura_visual as cv

    _ok(cv._url_real_es_valida('') is False, 'URL vacía -> no válida')
    _ok(
        cv._url_real_es_valida('/static/img/no-existe-jamas.svg') is False,
        'asset local inexistente -> no válido',
    )
    _ok(
        cv._url_real_es_valida('/static/img/placeholder-producto.svg') is True,
        'asset local existente -> válido',
    )
    _ok(
        cv._url_real_es_valida('https://x/y.png', verificar_remotas=False) is True,
        'URL remota sin verificar -> asumida válida',
    )


def _probar_error_duro():
    print('\n=== Cobertura: fabricado/roto falla; faltante NO ===')
    import backend.cobertura_visual as cv

    sin_imagen = cv.ReporteCobertura(
        comercio_id=1, total=244, con_imagen=200, sin_imagen=44
    )
    with patch.object(cv, 'auditar_comercio', return_value=sin_imagen):
        try:
            salida = cv.validar_cobertura(1)
            _ok(salida is sin_imagen, 'sin imagen NO es error (estado neutro)')
        except cv.ErrorCoberturaVisual:
            _ok(False, 'no debe lanzar solo por faltar imágenes')

    with patch.object(cv, 'auditar_comercio', return_value=sin_imagen):
        try:
            cv.validar_cobertura(1, 1.0)
            _ok(False, 'con mínimo explícito 100% debe lanzar')
        except cv.ErrorCoberturaVisual:
            _ok(True, 'el mínimo explícito sí exige cobertura')

    fabricado = cv.ReporteCobertura(
        comercio_id=1, total=244, con_imagen=244, generadas=3,
        problemas=['producto=9 asset fabricado: /static/uploads/marcas/x.png'],
    )
    with patch.object(cv, 'auditar_comercio', return_value=fabricado):
        try:
            cv.validar_cobertura(1)
            _ok(False, 'debe lanzar ante un asset fabricado')
        except cv.ErrorCoberturaVisual:
            _ok(True, 'lanza ErrorCoberturaVisual ante un asset fabricado')

    _ok(cv.ReporteCobertura(comercio_id=1, total=0).porcentaje == 1.0,
        'catálogo vacío no divide por cero')


def _probar_reporte_honesto():
    print('\n=== Honestidad: sin imagen nunca cuenta como real ===')
    from backend.estado_imagenes import construir_reporte_importacion

    _mensaje, meta = construir_reporte_importacion(244, reales=0, pendientes=244)
    _ok(meta['estado_imagenes'] == 'sin_reales',
        f'0 reales -> "sin_reales" (no completo): {meta["estado_imagenes"]}')
    _ok(meta['imagenes_pendientes'] == 244, 'cuenta los pendientes sin inventar')

    _mensaje2, meta2 = construir_reporte_importacion(244, reales=44, pendientes=200)
    _ok(meta2['estado_imagenes'] == 'parcial', '44 reales -> "parcial"')

    _mensaje3, meta3 = construir_reporte_importacion(244, reales=244, pendientes=0)
    _ok(meta3['estado_imagenes'] == 'completo', '244/244 reales -> "completo"')


def main() -> int:
    _probar_integridad_assets()
    _probar_ruta_local()
    _probar_validez_asset()
    _probar_error_duro()
    _probar_reporte_honesto()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK integridad de assets: sin fabricados, faltantes en estado neutro')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

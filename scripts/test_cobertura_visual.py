#!/usr/bin/env python3
"""Validación dura de cobertura visual de imágenes (100% obligatorio).

Prueba, sin base de datos, la lógica del validador:
  - la tarjeta por producto es un PNG válido y **distinto** por producto;
  - el monograma de marca es un PNG válido;
  - ``_ruta_local`` resuelve assets locales y rechaza rutas inseguras/URL remotas;
  - ``ReporteCobertura.porcentaje`` es correcto;
  - ``validar_cobertura`` lanza ``ErrorCoberturaVisual`` cuando la cobertura
    baja del 100% o hay un asset roto (error duro, no aviso silencioso);
  - el reporte honesto cuenta la tarjeta/placeholder como pendiente (nunca
    como imagen real).
"""

from __future__ import annotations

import io
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


def _es_png(data):
    return bool(data) and data[:8] == b'\x89PNG\r\n\x1a\n'


def _probar_tarjeta_producto():
    print('\n=== Tarjeta por producto (placeholder dinámico) ===')
    from PIL import Image

    from backend.marca_logo import monograma_png, tarjeta_producto_png

    datos = tarjeta_producto_png('Pan Canilla Integral 500g', 'Panadería')
    _ok(_es_png(datos), 'tarjeta PNG válida')
    img = Image.open(io.BytesIO(datos))
    img.load()
    _ok(img.size == (600, 600), f'tarjeta cuadrada 600x600 ({img.size})')

    otra = tarjeta_producto_png('ACEITE AMANECER 1L', 'Alimentos')
    _ok(datos != otra, 'cada producto genera una tarjeta distinta')

    vacia = tarjeta_producto_png('   ')
    _ok(_es_png(vacia), 'nombre vacío no rompe la generación')

    mono = monograma_png('Mavesa')
    _ok(_es_png(mono), 'monograma de marca PNG válido')


def _probar_ruta_local():
    print('\n=== Resolución segura de rutas locales ===')
    from backend.cobertura_visual import _ruta_local

    ruta = _ruta_local('/static/img/placeholder-producto.svg')
    _ok(ruta is not None and ruta.is_file(), 'resuelve un asset estático existente')
    _ok(_ruta_local('https://x.supabase.co/storage/v1/object/public/a.png') is None,
        'las URL remotas no son rutas locales')
    _ok(_ruta_local('/static/uploads/../config.py') is None,
        'rechaza path traversal')
    _ok(_ruta_local('') is None, 'URL vacía -> None')


def _probar_reporte_y_error_duro():
    print('\n=== Cobertura: métricas y error duro <100% ===')
    import backend.cobertura_visual as cv

    reporte = cv.ReporteCobertura(comercio_id=1, total=244, con_imagen=244)
    _ok(reporte.porcentaje == 1.0, 'cobertura 244/244 = 100%')

    reporte_parcial = cv.ReporteCobertura(comercio_id=1, total=244, con_imagen=200)
    _ok(abs(reporte_parcial.porcentaje - 200 / 244) < 1e-9,
        'cobertura 200/244 refleja el faltante')
    _ok(cv.ReporteCobertura(comercio_id=1, total=0).porcentaje == 1.0,
        'catálogo vacío no divide por cero')

    with patch.object(cv, 'auditar_comercio', return_value=reporte_parcial):
        try:
            cv.validar_cobertura(1, 1.0)
            _ok(False, 'debe lanzar ErrorCoberturaVisual con cobertura < 100%')
        except cv.ErrorCoberturaVisual:
            _ok(True, 'lanza ErrorCoberturaVisual con cobertura < 100%')

    con_roto = cv.ReporteCobertura(
        comercio_id=1, total=244, con_imagen=244,
        problemas=['producto=9 asset local faltante: /static/img/x.svg'],
    )
    with patch.object(cv, 'auditar_comercio', return_value=con_roto):
        try:
            cv.validar_cobertura(1, 1.0)
            _ok(False, 'debe lanzar ErrorCoberturaVisual con asset roto')
        except cv.ErrorCoberturaVisual:
            _ok(True, 'lanza ErrorCoberturaVisual si hay un asset roto')

    with patch.object(cv, 'auditar_comercio', return_value=reporte):
        salida = cv.validar_cobertura(1, 1.0)
        _ok(salida.porcentaje == 1.0, 'no lanza con cobertura 100%')


def _probar_reporte_honesto():
    print('\n=== Honestidad: tarjeta/placeholder nunca cuenta como real ===')
    from backend.estado_imagenes import construir_reporte_importacion

    # 200 con tarjeta (pendiente) + 44 reales: NO debe decir "completo".
    _mensaje, meta = construir_reporte_importacion(244, reales=44, pendientes=200)
    _ok(meta['estado_imagenes'] == 'parcial',
        f'244 con 44 reales -> "parcial" (no completo): {meta["estado_imagenes"]}')
    _ok(meta['imagenes_reales'] == 44, 'reporta solo las 44 reales')
    _ok(meta['imagenes_pendientes'] == 200, 'cuenta las 200 pendientes')

    _mensaje2, meta2 = construir_reporte_importacion(244, reales=244, pendientes=0)
    _ok(meta2['estado_imagenes'] == 'completo', '244/244 reales -> "completo"')


def _probar_validez_y_respaldo():
    print('\n=== Integridad de assets y respaldo de reparación ===')
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
        cv._url_real_es_valida('http://x/y.png', verificar_remotas=False) is True,
        'URL remota sin verificar -> asumida válida',
    )

    with patch(
        'backend.marca_logo.archivo_tarjeta_producto',
        return_value='/static/uploads/genericos/producto_abc.png',
    ):
        url, fuente = cv._respaldo_para_producto('Producto de prueba 1kg')
        _ok(
            url == '/static/uploads/genericos/producto_abc.png'
            and fuente == 'tarjeta_producto',
            'respaldo usa la tarjeta por producto',
        )

    with patch('backend.marca_logo.archivo_tarjeta_producto', return_value=None):
        url, fuente = cv._respaldo_para_producto('Producto de prueba 1kg')
        _ok(
            bool(url) and fuente == 'placeholder_categoria',
            'sin tarjeta cae al placeholder de categoría (nunca vacío)',
        )


def main() -> int:
    _probar_tarjeta_producto()
    _probar_ruta_local()
    _probar_reporte_y_error_duro()
    _probar_reporte_honesto()
    _probar_validez_y_respaldo()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK validación dura de cobertura visual')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

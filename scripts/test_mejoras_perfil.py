#!/usr/bin/env python3
"""Mejoras de perfil: apariencia, analítica, política de imágenes y compresión.

Pruebas sin base de datos (salvo la ruta beacon, que se ejerce dentro de un
``test_request_context`` con la analítica parcheada):

  - Paleta de banner: solo se aceptan colores de la paleta (nunca arbitrarios).
  - Política por sector: sectores estructurados usan fuentes oficiales; los no
    estructurados rechazan scraping de terceros no verificado.
  - Analítica: solo tipos de evento de la lista blanca.
  - Compresión: cualquier raster se convierte a WebP antes de Storage (idempotente).
  - Ruta beacon: responde 204 y registra el evento; ignora tipos inválidos.
"""

from __future__ import annotations

import io
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


def _probar_apariencia():
    print('\n=== Paleta de banner controlada ===')
    from backend.apariencia import (
        PALETA_BANNER,
        gradiente_banner,
        normalizar_color_banner,
        opciones_banner,
    )

    _ok(len(PALETA_BANNER) == 8, f'{len(PALETA_BANNER)} colores en la paleta')
    _ok(normalizar_color_banner('esmeralda') == 'esmeralda', 'acepta un id de la paleta')
    _ok(normalizar_color_banner('#059669') == 'esmeralda', 'mapea un hex de la paleta a su id')
    _ok(normalizar_color_banner('#123456') == 'ambar', 'hex fuera de paleta -> color por defecto')
    _ok(normalizar_color_banner('azul-chillon') == 'ambar', 'texto arbitrario -> color por defecto')
    _ok(normalizar_color_banner('') == 'ambar', 'vacío -> color por defecto')
    _ok('#0369A1' in gradiente_banner('oceano'), 'gradiente usa el hex del color elegido')
    _ok(
        all(set(c) >= {'id', 'nombre', 'hex', 'texto'} for c in opciones_banner()),
        'cada opción expone id, nombre, hex y texto',
    )


def _cand(fuente, dominio):
    return SimpleNamespace(
        fuente=fuente, dominio=dominio, url=f'https://{dominio}/foto.webp',
        titulo='', ancho=900, alto=900, score=0.0, confiable=False, urls_alternas=[],
    )


def _probar_politica_por_sector():
    print('\n=== Política de imágenes por sector ===')
    from backend.politica_imagenes import (
        categoria_estructurada,
        evaluar_candidato_por_sector,
    )

    _ok(categoria_estructurada('Tecnología'), 'tecnología es sector estructurado')
    _ok(categoria_estructurada('ferreteria'), 'ferretería es sector estructurado')
    _ok(not categoria_estructurada('alimentos'), 'alimentos NO es estructurado')

    _ok(evaluar_candidato_por_sector(_cand('catalogo_maestro', 'x.com'), 'alimentos')[0],
        'catálogo maestro siempre se acepta')
    _ok(evaluar_candidato_por_sector(_cand('vtex:www.carulla.com', 'carulla.com'), 'alimentos')[0],
        'VTEX (catálogo verificado) se acepta')
    _ok(evaluar_candidato_por_sector(_cand('bing-web', 'farmatodo.com.ve'), 'alimentos')[0],
        'dominio confiable se acepta')

    aceptado, motivo = evaluar_candidato_por_sector(
        _cand('bing-web', 'sitio-desconocido-abc.com'), 'alimentos'
    )
    _ok(not aceptado and motivo == 'terceros_no_verificado',
        'no estructurado rechaza scraping de terceros no verificado')

    aceptado, motivo = evaluar_candidato_por_sector(
        _cand('bing-web', 'tiendaoficialxyz.com'), 'tecnologia', marca='XYZ'
    )
    _ok(aceptado and motivo == 'dominio_marca',
        'estructurado acepta el dominio de la marca')

    aceptado, motivo = evaluar_candidato_por_sector(
        _cand('bing-web', 'sitio-desconocido-abc.com'), 'tecnologia', marca='Samsung'
    )
    _ok(not aceptado and motivo == 'estructurado_no_oficial',
        'estructurado rechaza fuentes no oficiales')


def _probar_analitica_tipos():
    print('\n=== Analítica: métrica principal consolidada ===')
    from backend.analytics import TIPOS_INTERACCION, normalizar_tipo, tipo_valido

    _ok(tipo_valido('clic_producto'), 'clic_producto es válido')
    _ok(tipo_valido('Visita_Tienda'), 'normaliza mayúsculas')
    _ok(not tipo_valido('drop_table'), 'tipo arbitrario rechazado')
    _ok(not tipo_valido(''), 'tipo vacío rechazado')
    _ok('visita_tienda' in TIPOS_INTERACCION, 'incluye visita_tienda')
    _ok('clic_tienda' not in TIPOS_INTERACCION, 'sin contador duplicado de "Ir a la tienda"')
    _ok(
        normalizar_tipo('clic_tienda') == 'visita_tienda',
        'el clic heredado "clic_tienda" se consolida como visita_tienda',
    )
    _ok(normalizar_tipo('no_permitido') is None, 'tipo desconocido -> None')


def _probar_compresion():
    print('\n=== Compresión previa a Storage ===')
    import numpy as np
    from PIL import Image

    from backend.images import normalizar_imagen_para_storage

    x = np.linspace(0, 255, 1600, dtype=np.uint8)
    xx, yy = np.meshgrid(x, x)
    arreglo = np.stack(
        [xx, yy, ((xx.astype(int) + yy) // 2).astype(np.uint8)], axis=-1
    ).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(arreglo, 'RGB').save(buffer, 'PNG')
    png = buffer.getvalue()

    data, nombre, tipo = normalizar_imagen_para_storage(
        png, 'banner.png', 'image/png', carpeta='banners'
    )
    _ok(tipo == 'image/webp', 'PNG de banner -> WebP')
    _ok(len(data) < len(png), f'el asset se reduce ({len(png)} -> {len(data)} bytes)')
    _ok(str(nombre).endswith('.webp'), 'el nombre cambia a .webp')

    webp, nombre2, tipo2 = normalizar_imagen_para_storage(
        data, nombre, 'image/webp', carpeta='banners'
    )
    _ok(webp is data and tipo2 == 'image/webp', 'WebP ya optimizado no se reencodea')

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    igual, _, tipo3 = normalizar_imagen_para_storage(svg, 'logo.svg', 'image/svg+xml')
    _ok(igual is svg and tipo3 == 'image/svg+xml', 'SVG vectorial intacto')


def _probar_ruta_beacon():
    print('\n=== Ruta beacon de interacción ===')
    import main

    with main.app.test_request_context(
        '/api/comercio/1/interaccion', method='POST',
        json={'tipo': 'clic_producto', 'producto_id': 11476},
    ):
        with patch('main.registrar_interaccion') as espia:
            respuesta = main.api_registrar_interaccion(1)
            _ok(respuesta == ('', 204), 'responde 204 sin cuerpo')
            _ok(espia.called, 'registra el evento válido')
            args, kwargs = espia.call_args
            _ok(args[0] == 1 and args[1] == 'clic_producto', 'liga comercio y tipo')
            _ok(kwargs.get('producto_id') == 11476, 'incluye el producto')

    with main.app.test_request_context(
        '/api/comercio/1/interaccion', method='POST',
        json={'tipo': 'no_permitido'},
    ):
        with patch('main.registrar_interaccion') as espia:
            respuesta = main.api_registrar_interaccion(1)
            _ok(respuesta == ('', 204) and not espia.called, 'ignora tipos inválidos')

    with main.app.test_request_context(
        '/api/comercio/1/interaccion', method='POST',
        json={'tipo': 'clic_tienda'},
    ):
        with patch('main.registrar_interaccion') as espia:
            respuesta = main.api_registrar_interaccion(1)
            _ok(respuesta == ('', 204), 'el clic heredado responde 204')
            _ok(
                espia.called and espia.call_args[0][1] == 'visita_tienda',
                'el clic heredado se consolida como visita_tienda',
            )


def main_test() -> int:
    _probar_apariencia()
    _probar_politica_por_sector()
    _probar_analitica_tipos()
    _probar_compresion()
    _probar_ruta_beacon()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK mejoras de perfil: apariencia, analítica, política y compresión')
    return 0


if __name__ == '__main__':
    raise SystemExit(main_test())

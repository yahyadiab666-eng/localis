#!/usr/bin/env python3
"""Pruebas unitarias del pipeline de pago (sin red)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _ok(condicion, mensaje, errores):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    errores.append(mensaje)
    return False


def main() -> int:
    from services.smart_image_pipeline import (
        PLACEHOLDER_PRODUCTO,
        resolver_imagen_automatica,
        url_catalogo_api_valida,
    )

    errores = []
    print('=== URL de API ===')
    _ok(
        url_catalogo_api_valida('https://cdn.upcitemdb.com/img/x.jpg'),
        'CDN de API permitido',
        errores,
    )
    _ok(
        not url_catalogo_api_valida('https://images.google.com/x.jpg'),
        'Google Images rechazado',
        errores,
    )

    print('\n=== Sin clave ===')
    with patch('services.smart_image_pipeline.hay_proveedor_pagado', return_value=False):
        r = resolver_imagen_automatica(nombre='iPhone 15', categoria='Tecnología')
    _ok(r.es_placeholder and r.url == PLACEHOLDER_PRODUCTO, 'sin key = placeholder', errores)

    print('\n=== EAN first ===')
    with patch(
        'services.smart_image_pipeline.hay_proveedor_pagado', return_value=True
    ), patch(
        'services.smart_image_pipeline.buscar_por_ean',
        return_value='https://cdn.upcitemdb.com/ean.jpg',
    ), patch(
        'services.smart_image_pipeline.buscar_por_nombre',
        return_value='https://cdn.upcitemdb.com/nombre.jpg',
    ):
        r = resolver_imagen_automatica(
            codigo_barras='012345678905', nombre='Producto'
        )
    _ok(r.fuente == 'barcode_api', f'prioridad EAN fuente={r.fuente}', errores)
    _ok('ean.jpg' in r.url, 'URL del EAN, no se busca por nombre', errores)

    print('\n=== Nombre y genericos ===')
    with patch(
        'services.smart_image_pipeline.hay_proveedor_pagado', return_value=True
    ), patch(
        'services.smart_image_pipeline.buscar_por_ean', return_value=None
    ), patch(
        'services.smart_image_pipeline.buscar_por_nombre', return_value=None
    ):
        r = resolver_imagen_automatica(nombre='Martillo', categoria='Ferretería')
    _ok(r.es_placeholder, 'martillo generico = placeholder', errores)

    print('\n=== RESULTADO ===')
    if errores:
        for item in errores:
            print(f'  - {item}')
        return 1
    print('OK smart image pipeline')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Reparto web/worker de imagenes: interruptor estricto. Sin red ni BD."""

from __future__ import annotations

import os
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


def main() -> int:
    print('=== Interruptor LOCALIS_IMG_PIPELINE_PESADO ===')
    from backend import image_lookup

    with patch.dict(os.environ, {'LOCALIS_IMG_PIPELINE_PESADO': '0'}, clear=False):
        _ok(image_lookup._pipeline_pesado_habilitado() is False, '0 -> desactivado')
        # Con el pipeline apagado NO se crea hilo ni se toca la BD.
        _ok(
            image_lookup.programar_asociacion_imagenes_inventario(0) is None,
            'asociacion no lanza hilo (queda pendiente para el worker)',
        )
        _ok(
            image_lookup.programar_descubrimiento_producto(123) is False,
            'descubrimiento no lanza hilo (queda pendiente para el worker)',
        )
        _ok(
            image_lookup.programar_asociacion_imagenes_inventario(None) is None,
            'sin comercio -> None',
        )

    with patch.dict(os.environ, {'LOCALIS_IMG_PIPELINE_PESADO': '1'}, clear=False):
        _ok(image_lookup._pipeline_pesado_habilitado() is True, '1 -> activado')
    with patch.dict(os.environ, {}, clear=True):
        _ok(image_lookup._pipeline_pesado_habilitado() is True, 'por defecto -> activado')
    with patch.dict(os.environ, {'LOCALIS_IMG_PIPELINE_PESADO': 'false'}, clear=False):
        _ok(image_lookup._pipeline_pesado_habilitado() is False, '"false" -> desactivado')

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK reparto web/worker de imagenes')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
#!/usr/bin/env python3
"""Correcciones de EAN, reintentos y no-canonización genérica.

Invariantes:
  1. La validación por EAN NO exige el código en el texto: se acepta por
     coherencia de modelo (a15…) y se rechaza otro modelo.
  2. Un negativo en caché no bloquea el reintento: con ``permitir_reintento``
     se ignora y se vuelve a consultar.
  3. Con EAN, el índice maestro NO cae a nombre/similitud (evita canonizar la
     imagen de otro producto).

Sin BD ni red.
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


def _probar_ean_sin_texto():
    print('\n=== Fix 1: EAN se valida por modelo, no por dígitos ===')
    from backend.imagenes_producto import _candidato_ean_valido, _tokens_identidad

    tokens = _tokens_identidad('Samsung Galaxy A15 128GB')
    # El título NO contiene el EAN; antes se exigía y fallaba.
    candidato = {
        'url': 'https://tienda.com/p/samsung-galaxy-a15',
        'dominio': 'tienda.com',
        'titulo': 'Galaxy A15',
        'contexto': 'Smartphone',
    }
    _ok(
        _candidato_ean_valido(candidato, '7591234567890', tokens),
        'se acepta por coherencia de modelo aunque el EAN no aparezca',
    )
    hermano = {
        'url': 'https://tienda.com/p/samsung-galaxy-a25',
        'dominio': 'tienda.com',
        'titulo': 'Galaxy A25',
        'contexto': 'Smartphone',
    }
    _ok(
        not _candidato_ean_valido(hermano, '7591234567890', tokens),
        'otro modelo (A25) se rechaza',
    )


def _probar_reintento_negativo():
    print('\n=== Fix 2: un negativo no bloquea el reintento ===')
    from backend import imagenes_producto as ip

    negativo = {'url_imagen': None, 'encontrada': 0, 'fuente': 'serper'}
    with patch.object(ip, 'obtener_automatica', return_value=negativo), patch.object(
        ip, '_negativo_vigente', return_value=True
    ), patch.object(ip, '_producto_con_imagen_valida', return_value=False), patch.object(
        ip, '_imagen_maestra_por_ean', return_value=None
    ), patch('backend.serper_images.habilitado', return_value=False):
        bloqueado = ip.buscar_o_cachear_automatica(
            1, nombre='Samsung A15', codigo_barras='7591234567890'
        )
        reintento = ip.buscar_o_cachear_automatica(
            1, nombre='Samsung A15', codigo_barras='7591234567890',
            permitir_reintento=True,
        )
    _ok(bloqueado.get('origen') == 'cache_bd', 'sin reintento usa la caché negativa')
    _ok(
        reintento.get('origen') != 'cache_bd',
        f'con permitir_reintento se ignora el negativo y se reconsulta ({reintento.get("origen")})',
    )


def _probar_indice_sin_fallback():
    print('\n=== Fix 3: con EAN no se cae a nombre/similitud ===')
    from backend.catalogo_maestro_index import IndiceMaestro

    indice = IndiceMaestro(filas=2)
    indice.por_codigo['111'] = 'https://x.supabase.co/storage/v1/object/public/imagenes/productos/ean.webp'
    indice.por_nombre['samsung galaxy'] = 'https://x.supabase.co/storage/v1/object/public/imagenes/productos/generic.webp'

    exacto, origen = indice.buscar(codigo='111')
    _ok(exacto and origen == 'codigo', 'código exacto se resuelve')

    sin_match, origen2 = indice.buscar(
        codigo='999', nombre='Samsung Galaxy A15', marca='Samsung'
    )
    _ok(
        sin_match is None and origen2 is None,
        'código desconocido NO se resuelve por nombre/similitud',
    )

    solo_nombre, origen3 = indice.buscar(nombre='Samsung Galaxy', marca='Samsung')
    _ok(solo_nombre and origen3 in ('nombre', 'nombre_similar'), 'sin código sí usa nombre')


def main() -> int:
    _probar_ean_sin_texto()
    _probar_reintento_negativo()
    _probar_indice_sin_fallback()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK EAN por modelo, reintentos limpios y sin canonización genérica')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

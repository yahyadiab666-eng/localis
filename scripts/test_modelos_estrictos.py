#!/usr/bin/env python3
"""Modelos estrictos: patrones con espacio y prohibición de marca genérica.

Invariantes:
  1. `_tokens_modelo` reconoce "Note 12", "12 Pro", "Galaxy 12" y NO confunde
     capacidades ("128 GB").
  2. Sin modelo reconocible, NUNCA se acepta una coincidencia solo por marca
     (ni en la validación EAN ni en `_relevante_web`).
  3. `_aceptacion_fuerte` distingue una coincidencia inequívoca (modelo/EAN) de
     una débil (solo nombre), para no canonizar ni marcar 'real'.
  4. El catálogo maestro pasa por `_relevante_web` (fuente filtrable).

Sin BD ni red.
"""

from __future__ import annotations

import sys
from pathlib import Path

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


def _probar_modelos_con_espacio():
    print('\n=== Modelos con espacio / numéricos ===')
    from backend.imagenes_producto import _tokens_identidad, _tokens_modelo

    for nombre, esperado in (
        ('Samsung Galaxy Note 12', '12'),
        ('Xiaomi 12 Pro', '12'),
        ('Samsung Galaxy 12', '12'),
        ('Samsung Galaxy A15', 'a15'),
        ('Xiaomi Redmi Note 12 Pro', '12'),
    ):
        modelos = _tokens_modelo(_tokens_identidad(nombre))
        _ok(esperado in modelos, f'{nombre!r} -> modelo {modelos}')

    modelos = _tokens_modelo(_tokens_identidad('Samsung Galaxy A15 128 GB'))
    _ok('a15' in modelos and '128' not in modelos, f'128 GB no es modelo, a15 sí ({modelos})')
    _ok(not _tokens_modelo(_tokens_identidad('Arroz blanco 1 kg')), 'unidad kg no es modelo')


def _probar_prohibicion_marca():
    print('\n=== Nunca aceptar solo por marca ===')
    from backend.imagenes_producto import _candidato_ean_valido, _tokens_identidad

    # Producto sin modelo: "Celular Samsung" (marca + genérico).
    tokens = _tokens_identidad('Celular Samsung')
    generico = {
        'url': 'https://tienda.com/celulares-samsung',
        'dominio': 'tienda.com',
        'titulo': 'Celulares Samsung en oferta',
        'contexto': '',
    }
    _ok(
        not _candidato_ean_valido(generico, '7591234567890', tokens),
        'marca+genérico sin modelo se RECHAZA',
    )

    from services.professional_image_pipeline import _relevante_web, _tokens_relevancia, Candidato

    t2 = _tokens_relevancia('Samsung Galaxy', 'Samsung', None)
    cand = Candidato(url='https://samsung.com/tienda', fuente='bing-web', dominio='samsung.com', titulo='Samsung Galaxy')
    _ok(not _relevante_web(cand, t2), 'dominio de marca sin modelo se RECHAZA')


def _probar_aceptacion_fuerte():
    print('\n=== Fuerte (modelo/EAN) vs débil (solo nombre) ===')
    from backend.imagenes_producto import _aceptacion_fuerte, _tokens_identidad

    con_modelo = _tokens_identidad('Samsung Galaxy A15')
    candidato_modelo = {'titulo': 'Samsung Galaxy A15', 'contexto': '', 'dominio': 'x.com', 'url': 'https://x.com/a15'}
    _ok(_aceptacion_fuerte(candidato_modelo, '759...', con_modelo), 'modelo presente -> fuerte')

    con_ean = _tokens_identidad('Leche en polvo')
    candidato_ean = {'titulo': 'Leche 7591234567890', 'contexto': '', 'dominio': 'x.com', 'url': 'https://x.com/l'}
    _ok(_aceptacion_fuerte(candidato_ean, '7591234567890', con_ean), 'EAN presente -> fuerte')

    candidato_nombre = {'titulo': 'Leche en polvo entera', 'contexto': '', 'dominio': 'x.com', 'url': 'https://x.com/l'}
    _ok(not _aceptacion_fuerte(candidato_nombre, '7591234567890', con_ean), 'solo nombre -> débil')


def _probar_catalogo_filtrable():
    print('\n=== Catálogo maestro sujeto a relevancia ===')
    fuente = (RAIZ / 'services' / 'professional_image_pipeline.py').read_text(encoding='utf-8')
    _ok(
        "'catalogo_maestro'," in fuente,
        "la fuente 'catalogo_maestro' está registrada en fuentes_filtrables",
    )


def main() -> int:
    _probar_modelos_con_espacio()
    _probar_prohibicion_marca()
    _probar_aceptacion_fuerte()
    _probar_catalogo_filtrable()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK modelos estrictos: patrones con espacio, sin marca genérica y sin canonización débil')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

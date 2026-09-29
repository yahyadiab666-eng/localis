#!/usr/bin/env python3
"""Matching de MODELOS: no mezclar variantes de la misma marca (Samsung A15 vs A25).

Invariantes:
  1. Los tokens de modelo (``a15``, ``s24``) NO se descartan por longitud.
  2. Si el producto tiene tokens de modelo, son OBLIGATORIOS para aceptar una
     imagen (no basta superar un porcentaje general de tokens).
  3. Un dominio de marca o catálogo no acepta a ciegas: sin evidencia del modelo
     se descarta (evita el logo corporativo).
  4. Las capacidades/unidades (128gb, 5g, 750ml) no cuentan como modelo.

Sin BD ni red: usa las funciones puras de matching.
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


def _candidato(titulo, dominio='samsung.com'):
    from services.professional_image_pipeline import Candidato

    return Candidato(url=f'https://{dominio}/producto', fuente='bing-web', dominio=dominio, titulo=titulo)


def _probar_tokens_modelo():
    print('\n=== Detección de tokens de modelo ===')
    from backend.imagenes_producto import _tokens_identidad, _tokens_modelo

    tokens = _tokens_identidad('Samsung Galaxy A15 128GB')
    _ok('a15' in tokens, f'el modelo a15 no se descarta ({tokens})')
    _ok(_tokens_modelo(tokens) == ['a15'], f'solo a15 es modelo, no 128gb ({_tokens_modelo(tokens)})')
    for capacidad in ('128gb', '5g', '750ml', '55w'):
        _ok(_tokens_modelo([capacidad]) == [], f'{capacidad} no es token de modelo')

    from services.professional_image_pipeline import _tokens_relevancia

    rel = _tokens_relevancia('Samsung Galaxy A15', 'Samsung', None)
    _ok('a15' in rel, f'la cascada conserva el modelo a15 ({sorted(rel)})')
    rel2 = _tokens_relevancia('Samsung Galaxy S24 Ultra', 'Samsung', None)
    _ok('s24' in rel2, f'la cascada conserva el modelo s24 ({sorted(rel2)})')


def _probar_nombre_confiable():
    print('\n=== Aceptación por nombre: modelo obligatorio ===')
    from backend.imagenes_producto import _candidato_nombre_confiable, _tokens_identidad

    tokens = _tokens_identidad('Samsung Galaxy A15 128GB')
    correcto = {'url': 'https://tienda.com/producto/samsung-galaxy-a15',
                'dominio': 'tienda.com',
                'titulo': 'Samsung Galaxy A15 128GB', 'contexto': ''}
    hermano = {'url': 'https://tienda.com/producto/samsung-galaxy-a25',
               'dominio': 'tienda.com',
               'titulo': 'Samsung Galaxy A25 128GB', 'contexto': ''}
    logo = {'url': 'https://samsung.com/',
            'dominio': 'samsung.com',
            'titulo': 'Samsung', 'contexto': ''}
    _ok(_candidato_nombre_confiable(correcto, tokens), 'A15 con su propio modelo se acepta')
    _ok(not _candidato_nombre_confiable(hermano, tokens), 'A25 (hermano) se RECHAZA para un A15')
    _ok(not _candidato_nombre_confiable(logo, tokens), 'título genérico de marca se RECHAZA')

    sin_modelo = _tokens_identidad('Leche en polvo entera')
    leche = {'url': 'https://tienda.com/producto/leche-en-polvo',
             'dominio': 'tienda.com',
             'titulo': 'Leche en polvo entera', 'contexto': ''}
    _ok(_candidato_nombre_confiable(leche, sin_modelo), 'sin modelo, la regla general sigue igual')


def _probar_dominio_no_ciego():
    print('\n=== Dominio de marca no acepta a ciegas ===')
    from services.professional_image_pipeline import _relevante_web, _tokens_relevancia

    tokens = _tokens_relevancia('Samsung Galaxy A15', 'Samsung', None)
    _ok(
        _relevante_web(_candidato('Samsung Galaxy A15'), tokens),
        'samsung.com con el modelo en el título se acepta',
    )
    _ok(
        not _relevante_web(_candidato('Samsung', dominio='samsung.com'), tokens),
        'samsung.com sin el modelo (logo) se RECHAZA',
    )
    _ok(
        not _relevante_web(_candidato('Samsung Galaxy A25'), tokens),
        'samsung.com con OTRO modelo se RECHAZA',
    )

    # Producto sin modelo: se mantiene el comportamiento previo (dominio confiable).
    tokens2 = _tokens_relevancia('Arroz blanco', '', None)
    _ok(
        _relevante_web(_candidato('Arroz blanco', dominio='tienda.com'), tokens2),
        'producto sin modelo sigue aceptando por relevancia de tokens',
    )


def main() -> int:
    _probar_tokens_modelo()
    _probar_nombre_confiable()
    _probar_dominio_no_ciego()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK matching de modelos: variantes aisladas y sin aceptación ciega por dominio')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

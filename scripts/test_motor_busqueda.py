#!/usr/bin/env python3
"""Pruebas del motor de búsqueda heurística (sin red).

Verifica las variantes de consulta tipo búsqueda humana, las variantes de URL
de Open Food Facts, el filtro de relevancia por título y la expansión de fuentes
VTEX regionales.
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


def main() -> int:
    from services import professional_image_pipeline as P

    print('=== Variantes de consulta (búsqueda humana) ===')
    variantes = P._consultas_busqueda(
        'AGUA MINALBA 1.5 LT', 'MINALBA', '1.5 LT', '', 'bebidas', '7591031001959'
    )
    print('  variantes:', variantes)
    _ok(len(variantes) >= 4, f'genera varias variantes ({len(variantes)})')
    _ok('7591031001959' in variantes, 'incluye el código de barras')
    _ok(any('venezuela' in v for v in variantes), 'incluye término de mercado')
    _ok(
        any(v.strip() == 'agua minalba' for v in variantes),
        'versión sin gramaje y sin tokens duplicados',
    )
    _ok(
        len(variantes) == len({v.lower() for v in variantes}),
        'sin variantes duplicadas',
    )

    print('\n=== Variantes de URL de Open Food Facts ===')
    url = 'https://images.openfoodfacts.org/images/products/759/118/400/2018/front_es.5.400.jpg'
    alternas = P._variantes_off(url)
    _ok(len(alternas) >= 2, f'genera alternativas de tamaño ({len(alternas)})')
    _ok(any('.full.' in a for a in alternas), 'incluye versión .full')

    producto = {
        'image_front_url': url,
        'images': {
            'front_es': {
                'sizes': {
                    '400': {'url': url},
                    'full': {'url': url.replace('.400.', '.full.')},
                }
            }
        },
    }
    urls = P._urls_imagen_off(producto)
    _ok(len(urls) >= 2, f'extrae URLs de la estructura images ({len(urls)})')
    candidatos = P._candidatos_desde_off(producto)
    _ok(candidatos and candidatos[0].variantes(), 'candidato OFF con variantes de descarga')

    print('\n=== Relevancia por título ===')
    tokens = P._tokens_relevancia('Celular Samsung A15', None, None)
    relevante = P.Candidato(url='https://x.vteximg.com.br/img.jpg', fuente='vtex:x', titulo='Celular Samsung A15 128GB')
    irrelevante = P.Candidato(url='https://x.vteximg.com.br/img.jpg', fuente='vtex:x', titulo='Lavadora Whirlpool')
    _ok(P._relevante_web(relevante, tokens), 'acepta candidato con título relacionado')
    _ok(not P._relevante_web(irrelevante, tokens), 'rechaza candidato sin relación')

    print('\n=== Fuentes VTEX regionales ===')
    from backend.fuentes_imagenes import catalogo_fuentes, fuentes_vtex

    hosts = fuentes_vtex()
    _ok('www.locatel.com.ve' in hosts, 'incluye Locatel (VE)')
    _ok(
        any(h in hosts for h in ('www.carulla.com', 'www.olimpica.com', 'www.plazavea.com.pe')),
        f'incluye distribuidores regionales ({hosts})',
    )
    ids = {f['id'] for f in catalogo_fuentes()}
    for fuente in ('vtex', 'openfacts', 'serpapi' if 'serpapi' in ids else 'bing_imagenes', 'logo_favicon'):
        _ok(fuente in ids or fuente == 'serpapi', f'registro de fuentes incluye {fuente}')

    print('\n=== Reintentos y buscadores API opcionales ===')
    _ok(hasattr(P, '_descargar') and P._DESCARGA_INTENTOS >= 1, 'descarga con reintentos')
    _ok(callable(getattr(P, '_buscar_serpapi', None)), 'soporte SerpAPI (opcional)')
    _ok(callable(getattr(P, '_buscar_brave', None)), 'soporte Brave Search (opcional)')
    _ok(callable(getattr(P, '_buscar_bing_og', None)), 'rastreo og:image de páginas de producto')

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK motor de búsqueda heurística y fuentes ampliadas')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

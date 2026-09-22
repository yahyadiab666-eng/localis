"""Política de verificación de imágenes (whitelist de dominios).

Regla única para **todos** los sectores: una imagen solo se acepta si proviene
de origen verificado:

  1. Catálogos estructurados (maestro por código de barras, VTEX, Mercado Libre,
     Open Food/Beauty/Products Facts).
  2. Dominios confiables (e-commerce verificados y marcas oficiales).
  3. El dominio del propio fabricante (coincide con la marca).

Cualquier buscador abierto en un dominio no verificado se **descarta** para no
guardar fotos ajenas, corruptas o inventadas. Para los sectores estructurados
(tecnología, electrodomésticos/hogar, ferretería, automotriz) la consulta además
se reduce a marca + modelo exacto (ver ``backend/consulta_producto.py``).

Configurable con ``LOCALIS_IMG_ESTRICTO=0`` solo para depuración.
"""

from __future__ import annotations

import os
import unicodedata

# Sectores con ficha oficial por producto.
CATEGORIAS_ESTRUCTURADAS = frozenset(
    {'tecnologia', 'ferreteria', 'automotriz', 'hogar'}
)

# Fuentes de catálogo verificadas (por código de barras o catálogo estructurado).
FUENTES_CATALOGO = frozenset(
    {
        'catalogo_maestro',
        'vtex',
        'mercadolibre',
        'openfoodfacts',
        'openbeautyfacts',
        'openproductsfacts',
    }
)

# Fuentes de logos de marca (nunca son scraping de producto).
FUENTES_LOGO = frozenset(
    {'logo_favicon', 'logo_wikidata', 'logo_simpleicons', 'logo_monograma'}
)

# Fuentes de buscador web (requieren verificación).
FUENTES_WEB = frozenset(
    {
        'bing-web',
        'ddg-web',
        'serpapi',
        'brave',
        'brave-og',
        'bing-og',
        'google-cse',
        'bing-api',
    }
)


def _estricto():
    valor = str(os.getenv('LOCALIS_IMG_ESTRICTO', '1')).strip().lower()
    return valor not in ('0', 'false', 'no', 'off')


def _texto_plano(valor):
    texto = unicodedata.normalize('NFKD', str(valor or ''))
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return texto.lower().strip()


def categoria_estructurada(categoria):
    """True si la categoría es de ficha oficial por producto."""
    return _texto_plano(categoria) in CATEGORIAS_ESTRUCTURADAS


def _dominio_confiable(dominio):
    dominio = str(dominio or '').lower()
    if not dominio:
        return False
    try:
        from backend.fuentes_imagenes import dominios_confiables

        return any(d in dominio for d in dominios_confiables())
    except Exception:
        return False


def _coincide_marca(candidato, marca):
    if not marca:
        return False
    marca_norm = _texto_plano(marca).replace(' ', '')
    if len(marca_norm) < 3:
        return False
    dominio = str(getattr(candidato, 'dominio', '') or '').lower()
    return marca_norm in dominio.replace('-', '').replace('.', '')


def evaluar_candidato_por_sector(candidato, categoria=None, marca=None):
    """(aceptado, motivo). Solo se aceptan assets de origen verificado.

    - Catálogos verificados (maestro, VTEX, Mercado Libre, Open*Facts): sí.
    - Logos oficiales de marca: sí.
    - Dominio confiable (retailer/e-commerce verificados): sí.
    - Dominio del fabricante (coincide con la marca): sí.
    - Cualquier buscador abierto en dominio no verificado: se descarta.
    """
    fuente_base = str(getattr(candidato, 'fuente', '') or '').split(':')[0]
    if fuente_base in FUENTES_CATALOGO:
        return True, 'catalogo_verificado'
    if fuente_base in FUENTES_LOGO:
        return True, 'logo_marca'

    dominio = getattr(candidato, 'dominio', '') or ''
    if _dominio_confiable(dominio):
        return True, 'dominio_confiable'
    if _coincide_marca(candidato, marca):
        return True, 'dominio_marca'

    if not _estricto():
        return True, 'no_verificado_relajado'
    motivo = (
        'estructurado_no_oficial'
        if categoria_estructurada(categoria)
        else 'terceros_no_verificado'
    )
    return False, motivo

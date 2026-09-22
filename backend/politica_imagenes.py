"""Política de verificación de imágenes por sector (estructurado vs. abierto).

Sectores **estructurados** (tecnología, electrodomésticos/hogar, ferretería y
automotriz) tienen fichas oficiales y assets limpios: aquí se **maximiza** el
uso de fuentes oficiales (marca, retailer confiable, catálogos verificados) y se
acepta el dominio de la marca como señal fuerte.

Sectores **no estructurados** (alimentos, panadería, bebidas, cuidado personal,
etc.) no suelen tener ficha oficial por producto: se toman fotos de catálogos
verificados (maestro, código de barras, VTEX/Mercado Libre) y de dominios
confiables, pero se **rechaza el scraping de terceros no verificado** para evitar
fotos ajenas o incorrectas.

Todo es configurable con ``LOCALIS_IMG_ESTRICTO=0`` para relajar la política.
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
    """(aceptado, motivo) según el sector del producto.

    - Catálogos verificados y logos: aceptados siempre.
    - Dominios confiables (marca oficial / retailer): aceptados.
    - Sectores estructurados: además se acepta el dominio de la marca.
    - Buscadores en dominios no confiables: rechazados (scraping no verificado).
    """
    fuente_base = str(getattr(candidato, 'fuente', '') or '').split(':')[0]
    if fuente_base in FUENTES_CATALOGO:
        return True, 'catalogo_verificado'
    if fuente_base in FUENTES_LOGO:
        return True, 'logo_marca'

    dominio = getattr(candidato, 'dominio', '') or ''
    if _dominio_confiable(dominio):
        return True, 'dominio_confiable'

    estructurada = categoria_estructurada(categoria)
    if estructurada and _coincide_marca(candidato, marca):
        return True, 'dominio_marca'

    if fuente_base in FUENTES_WEB or fuente_base:
        if not _estricto():
            return True, 'no_verificado_relajado'
        return False, ('estructurado_no_oficial' if estructurada else 'terceros_no_verificado')

    return False, 'fuente_desconocida'

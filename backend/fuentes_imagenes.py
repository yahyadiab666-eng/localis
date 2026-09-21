"""Registro modular de fuentes de imágenes (cascada multirrubro global).

Centraliza en un solo lugar la lista de fuentes activas, los dominios
confiables (Venezuela **y** marcas globales/importadas) y los hosts usados para
búsquedas restringidas por sitio. Añadir una fuente nueva es editar este módulo
o usar las variables de entorno documentadas, sin tocar la lógica del pipeline.

Orden conceptual de la cascada:
  1. Catálogo maestro indexado (instantáneo, sin red).
  2. Catálogos estructurados: VTEX local, Mercado Libre (token), Open Facts.
  3. Buscadores web (Bing/DuckDuckGo) + búsquedas `site:` en retailers.
  4. Logos de marca (favicon oficial / Simple Icons / monograma generado).
"""

from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Dominios confiables (premian la puntuación de candidatos)
# ---------------------------------------------------------------------------
DOMINIOS_CONFIABLES_VE = (
    # Farmacias / salud
    'farmatodo', 'locatel', 'farmahorro', 'farmaciasaavedra', 'farmarket',
    'farmacialaredoma', 'farma redoma', 'farmadon',
    # Supermercados / retail nacional (multirrubro)
    'centralmadeirense', 'gamaenex', 'makro.com.ve', 'plazas.com',
    'tuexito', 'elrecreo', 'supermercadosgama', 'hiperlider', 'garzon',
    'latodia', 'daka', 'casamia', 'bango', 'arenas', 'sigo', 'el patron',
    'venezolanadealimentos', 'alimentosve', 'mercado', 'supermercado',
    # Tecnología / electro local
    'traki', 'radioshack.com.ve', 'tiendasva', 'bumblebee', 'multimax',
    'beco', 'tgo', 'macoutlet', 'vertigo', 'innovacom', 'planetacom', 'sambil',
    # Ferretería / construcción / automotriz / calzado
    'epa.com.ve', 'ferretotal', 'tufesa', 'ferremaq', 'sodi', 'construcasa',
    'ferreter', 'cauchera', 'repuestos', 'autorepuestos', 'autoexpress',
    'multirepuestos', 'automotriz', 'calzados', 'bata', 'flexi', 'cuero',
    'distribuidor', 'mayorista',
)

DOMINIOS_CONFIABLES_GLOBAL = (
    # Marcas internacionales (tecnología, electro, cuidado, alimentos, ferretería…)
    'samsung.', 'lg.com', 'apple.com', 'xiaomi.', 'motorola.', 'huawei.',
    'lenovo.', 'asus.', 'acer.', 'sony.', 'panasonic.', 'toshiba.',
    'philips.', 'bosch.', 'siemens.', 'whirlpool.', 'electrolux.', 'canon.',
    'nikon.', 'nike.', 'adidas.', 'puma.', 'reebok.', 'unilever.', 'nestle.',
    'pepsi.', 'coca-cola.', 'colgate.', 'dove.', 'nivea.', 'gillette.',
    'loreal.', 'heineken.', 'redbull.', 'ferrero.', 'bayer.', '3m.',
    'makita.', 'dewalt.', 'stanleytools.', 'blackanddecker.', 'tefal.',
    'kenwoodworld.', 'oster.', 'mabe.', 'altunsa.',
    # Catálogos abiertos globales (ficha verificada por código de barras)
    'openfoodfacts', 'openbeautyfacts', 'openproductsfacts',
)

DOMINIOS_CONFIABLES_BASE = DOMINIOS_CONFIABLES_VE + DOMINIOS_CONFIABLES_GLOBAL

# Prensa/comercio que suele publicar fotos de producto (fuente débil pero útil).
DOMINIOS_PRENSA = ('eluniversal.com', 'elnacional.com', 'talcual', 'efectococuyo')

DOMINIOS_BLOQUEADOS = (
    'images.google', 'google.com', 'gstatic.com', 'bing.com', 'bing.net',
    'duckduckgo.com', 'pinterest', 'facebook', 'instagram', 'tiktok', 'twitter',
    'x.com', 'youtube.com', 'ytimg.com', 'wikimedia', 'wikipedia',
    'shutterstock', 'istockphoto', 'gettyimages', 'dreamstime', 'alamy',
    'depositphotos', 'stock.adobe', 'freepik', '123rf', 'unsplash', 'pexels',
    'amazon.', 'ebay.', 'aliexpress', 'alibaba', 'mercadolibre',
    'mercadolivre', 'mlstatic', 'wish.com', 'placeholder', 'example.com',
    'ejemplo.com', 'blogspot', 'wordpress.com', 'tumblr',
)

# ---------------------------------------------------------------------------
# Hosts para búsqueda restringida por sitio (site:host) y catálogos VTEX
# ---------------------------------------------------------------------------
FUENTES_SITE_BASE = (
    'farmatodo.com.ve', 'locatel.com.ve', 'traki.com', 'epa.com.ve',
    'centralmadeirense.com.ve', 'plazas.com', 'multimax.com.ve',
)

FUENTES_VTEX_BASE = (
    # Venezuela (farmacia/cuidado/hogar/bebés)
    'www.locatel.com.ve',
    # Distribuidores regionales VTEX con marcas globales e imagen directa
    'www.carulla.com',
    'www.olimpica.com',
    'www.plazavea.com.pe',
    'www.jumbo.com.ar',
)


# ---------------------------------------------------------------------------
# Catálogo de fuentes (metadatos para auditoría/documentación)
# ---------------------------------------------------------------------------
def catalogo_fuentes():
    return (
        {
            'id': 'catalogo_maestro',
            'tipo': 'indice_memoria',
            'descripcion': 'Catálogo maestro indexado (EAN + nombre/marca)',
            'requiere': None,
        },
        {
            'id': 'vtex',
            'tipo': 'catalogo_estructurado',
            'descripcion': 'API pública VTEX (Locatel y tiendas configurables)',
            'requiere': None,
        },
        {
            'id': 'mercadolibre',
            'tipo': 'catalogo_estructurado',
            'descripcion': 'Mercado Libre Venezuela (API oficial)',
            'requiere': 'MELI_ACCESS_TOKEN',
        },
        {
            'id': 'openfacts',
            'tipo': 'catalogo_estructurado',
            'descripcion': 'Open Food/Beauty/Products Facts (global, por EAN y marca)',
            'requiere': None,
        },
        {
            'id': 'bing_imagenes',
            'tipo': 'buscador',
            'descripcion': 'Bing Images (web global, best-effort)',
            'requiere': None,
        },
        {
            'id': 'duckduckgo_imagenes',
            'tipo': 'buscador',
            'descripcion': 'DuckDuckGo Images (best-effort)',
            'requiere': None,
        },
        {
            'id': 'site_retail',
            'tipo': 'buscador_dominio',
            'descripcion': 'Búsquedas site: en retailers locales y globales',
            'requiere': None,
        },
        {
            'id': 'logo_favicon',
            'tipo': 'logo_marca',
            'descripcion': 'Favicon oficial del dominio de la marca',
            'requiere': None,
        },
        {
            'id': 'simpleicons',
            'tipo': 'logo_marca',
            'descripcion': 'Simple Icons (logos SVG de marcas)',
            'requiere': None,
        },
        {
            'id': 'monograma',
            'tipo': 'logo_generado',
            'descripcion': 'Monograma de marca generado localmente (universal)',
            'requiere': None,
        },
    )


def _personalizados(variable):
    return tuple(
        v.strip().lower() for v in os.getenv(variable, '').split(',') if v.strip()
    )


def dominios_confiables():
    return (
        DOMINIOS_CONFIABLES_BASE
        + DOMINIOS_PRENSA
        + _personalizados('LOCALIS_IMG_DOMINIOS_CONFIABLES')
    )


def fuentes_site():
    return FUENTES_SITE_BASE + _personalizados('LOCALIS_IMG_SITIOS_EXTRA')


def fuentes_vtex():
    return FUENTES_VTEX_BASE + _personalizados('LOCALIS_IMG_VTEX_HOSTS')

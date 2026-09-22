"""Verificación de assets: solo se persisten imágenes reales y confiables.

Regla de oro (anti-invención): el sistema **nunca** fabrica una imagen. Si no
hay una foto de producto verificada ni el logo **oficial** de la marca, el campo
``imagen_url`` queda vacío y la interfaz muestra un estado neutro.

Se consideran *generados/falsos* (jamás se guardan ni se muestran como foto):
  - ``placeholder_categoria`` (SVG de categoría)
  - ``logo_monograma`` (iniciales de texto)
  - ``tarjeta_producto`` (tarjeta con el nombre)
  - cualquier ``/static/img/placeholder-*`` o ``/static/uploads/genericos|marcas/``

Se consideran *verificados*:
  - fotos de producto en Supabase Storage (bucket ``imagenes``)
  - subidas manuales del comercio (``/static/uploads/productos|comercios|banners/``)
  - logos oficiales de marca (Simple Icons / Wikidata / favicon del dominio)
"""

from __future__ import annotations

# Fuentes cuyo asset es generado por el sistema (no es una foto real).
FUENTES_GENERADAS = frozenset(
    {'logo_monograma', 'tarjeta_producto', 'placeholder_categoria', 'placeholder'}
)

# Fuentes de logo de marca que sí son arte oficial.
FUENTES_LOGO_OFICIAL = frozenset(
    {'logo_simpleicons', 'logo_wikidata', 'logo_favicon'}
)

_PREFIJO_PLACEHOLDER = '/static/img/placeholder-'
_GENERADOS_LOCALES = ('/static/uploads/genericos/', '/static/uploads/marcas/')


def _texto(url):
    return str(url or '').strip()


def es_asset_generado(url, fuente=None):
    """True si el asset fue fabricado por el sistema (placeholder/monograma/tarjeta)."""
    if str(fuente or '').strip().lower() in FUENTES_GENERADAS:
        return True
    texto = _texto(url).lower()
    if not texto:
        return False
    if texto.startswith(_PREFIJO_PLACEHOLDER):
        return True
    return any(marca in texto for marca in _GENERADOS_LOCALES)


def es_logo_oficial(fuente):
    return str(fuente or '').strip().lower() in FUENTES_LOGO_OFICIAL


def es_asset_verificado(url, fuente=None):
    """True solo si el asset es una foto real o un logo oficial verificable."""
    texto = _texto(url)
    if not texto:
        return False
    if es_asset_generado(texto, fuente):
        return False

    fuente_norm = str(fuente or '').strip().lower()
    if fuente_norm.startswith('logo_'):
        return es_logo_oficial(fuente_norm)

    bajo = texto.lower()
    if bajo.startswith('/static/uploads/'):
        # Solo uploads de producto/comercio; nunca marcas/genericos (generados).
        return '/static/uploads/productos/' in bajo or '/static/uploads/comercios/' in bajo

    if bajo.startswith(('http://', 'https://')):
        if '/storage/v1/object/public/' not in bajo:
            return False
        # Un logo alojado en marcas/ no es una foto de producto fiable.
        if '/imagenes/marcas/' in bajo:
            return False
        return True

    return False

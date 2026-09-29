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

# Rutas donde el sistema guarda logos de marca (nunca son foto de producto).
_RUTAS_LOGO_MARCA = (
    '/imagenes/marcas/',
    '/public/marcas/',
    '/static/uploads/marcas/',
)
# Prefijo de archivo con que ``marca_logo`` nombra los logos que sube.
_PREFIJO_ARCHIVO_LOGO = 'marca_'


def _texto(url):
    return str(url or '').strip()


def es_url_logo_marca(url):
    """True si la URL apunta a un logo/marca y NO a una fotografía de producto.

    Cubre los tres formatos que usa el sistema:
      - Storage: ``…/storage/v1/object/public/imagenes/marcas/…``
      - respaldo local: ``/static/uploads/marcas/…``
      - nombre de archivo con prefijo ``marca_`` (``logo_favicon``/Simple Icons).
    """
    texto = _texto(url).lower()
    if not texto:
        return False
    if any(ruta in texto for ruta in _RUTAS_LOGO_MARCA):
        return True
    nombre = texto.split('?', 1)[0].rstrip('/').rsplit('/', 1)[-1]
    return nombre.startswith(_PREFIJO_ARCHIVO_LOGO)


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


# Fuentes que acreditan una coincidencia FUERTE (modelo/EAN o subida del
# comerciante). Cualquier otra (p. ej. 'serper' histórico o sin fuente) es
# considerada DÉBIL y puede re-evaluarse en ciclos de fondo.
FUENTES_VERIFICADAS = frozenset(
    {'manual', 'archivo', 'comercio', 'catalogo_maestro', 'serper_verificado'}
)


def fuente_verificada(fuente):
    """True si la procedencia acredita una coincidencia fuerte.

    ``serper_verificado`` lo escribe el registro automático solo cuando la
    coincidencia fue inequívoca (modelo presente o EAN en el texto). El valor
    histórico 'serper' (sin marca de verificación) se considera DÉBIL a
    propósito, para permitir reevaluar imágenes antiguas.
    """
    f = str(fuente or '').strip().lower()
    if not f:
        return False
    if f in FUENTES_VERIFICADAS:
        return True
    return f.startswith('profesional_') or f.startswith('manual')


def es_asset_verificado(url, fuente=None):
    """True solo si el asset es una foto real o un logo oficial verificable."""
    texto = _texto(url)
    if not texto:
        return False
    if es_asset_generado(texto, fuente):
        return False
    # Un logo de marca NUNCA es una fotografía de producto verificada.
    if es_url_logo_marca(texto):
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

"""Motor de imágenes: enrutamiento por sector, verificación y excepción segura.

Reglas del motor:
  1. **Enrutamiento híbrido**
     - Sectores estructurados (tecnología, electrodomésticos/hogar, ferretería,
       automotriz): fuentes **globales** (fabricante, e-commerce internacional,
       catálogos globales). Se evita el ruido local.
     - Comida y retail local: fuentes **locales/regionales** (retailers VE,
       VTEX regional, catálogos abiertos de alimentos).
  2. **Whitelist de dominios**: una imagen solo se acepta si viene de un catálogo
     verificado, un dominio confiable o el dominio del propio fabricante.
  3. **Excepción segura**: cualquier fallo de descarga o validación termina en un
     estado neutro (imagen nula), nunca en una imagen ajena o inventada.
  4. **Subidas manuales protegidas**: un asset subido por el comerciante jamás se
     reemplaza, sobrescribe ni purga por el motor automático.
"""

from __future__ import annotations

import os
import unicodedata

# Sectores con ficha oficial por producto (fuentes globales).
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

# Logos de marca: solo arte oficial.
FUENTES_LOGO = frozenset(
    {'logo_simpleicons', 'logo_wikidata', 'logo_favicon'}
)

# Buscadores web (requieren verificación de dominio/relevancia).
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

# Fuentes que se consideran subida del comerciante (nunca automáticas).
_FUENTES_MANUALES = frozenset({'archivo', 'comercio', 'manual', 'manual_upload'})

_PREFIJOS_MANUALES = ('manual_', 'manual-', 'comercio_')


def _plano(valor):
    texto = unicodedata.normalize('NFKD', str(valor or ''))
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return texto.lower().strip()


def sector_de(categoria):
    """``'estructurado'`` (fuentes globales) o ``'local'`` (fuentes regionales)."""
    return 'estructurado' if _plano(categoria) in CATEGORIAS_ESTRUCTURADAS else 'local'


def es_estructurado(categoria):
    return sector_de(categoria) == 'estructurado'


def _estricto():
    valor = str(os.getenv('LOCALIS_IMG_ESTRICTO', '1')).strip().lower()
    return valor not in ('0', 'false', 'no', 'off')


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
    marca_norm = _plano(marca).replace(' ', '')
    if len(marca_norm) < 3:
        return False
    dominio = str(getattr(candidato, 'dominio', '') or '').lower()
    return marca_norm in dominio.replace('-', '').replace('.', '')


def evaluar_candidato(candidato, categoria=None, marca=None):
    """(aceptado, motivo). Solo se aceptan assets de origen verificado."""
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
    motivo = 'estructurado_no_oficial' if es_estructurado(categoria) else 'terceros_no_verificado'
    return False, motivo


def resultado_neutro(motivo='sin_asset_verificado'):
    """Estado neutro seguro (imagen nula): nunca un asset ajeno."""
    return {'ok': False, 'url': None, 'fuente': None, 'motivo': motivo}


def _nombre_archivo(url):
    return str(url or '').rstrip('/').split('?', 1)[0].split('/')[-1].lower()


def es_imagen_manual(url, fuente=None):
    """True si el asset es una subida del comerciante (nunca se toca)."""
    fuente_norm = str(fuente or '').strip().lower()
    if fuente_norm in _FUENTES_MANUALES or fuente_norm.startswith('manual'):
        return True
    bajo = str(url or '').strip().lower()
    if not bajo:
        return False
    if any(bajo.startswith(f'/static/uploads/{c}') for c in ('productos', 'comercios', 'banners')):
        return bool(
            any(_nombre_archivo(bajo).startswith(p) for p in _PREFIJOS_MANUALES)
            or '/static/uploads/comercios/' in bajo
            or '/static/uploads/banners/' in bajo
        )
    if '/storage/v1/object/public/' in bajo:
        if '/imagenes/comercios/' in bajo or '/imagenes/banners/' in bajo:
            return True
        if '/imagenes/productos/' in bajo:
            return _nombre_archivo(bajo).startswith(_PREFIJOS_MANUALES)
    return False


def puede_reemplazar(imagen_url, estado=None, fuente=None):
    """Predicado de protección: False si el asset no debe ser reemplazado.

    Nunca reemplaza una subida manual ni una foto ya almacenada; solo reemplaza
    vacíos, placeholders, logos de respaldo o URLs externas no persistidas.
    """
    estado = str(estado or '').strip().lower()
    if es_imagen_manual(imagen_url, fuente):
        return False
    if not imagen_url:
        return True
    if estado == 'real':
        return False
    if estado == 'logo':
        return True
    texto = str(imagen_url).strip()
    if not texto:
        return True
    bajo = texto.lower()
    if 'placeholder' in bajo:
        return True
    if '/storage/v1/object/public/' in bajo:
        return False
    if bajo.startswith('/static/uploads/'):
        return False
    if bajo.startswith(('http://', 'https://')):
        return True
    return False

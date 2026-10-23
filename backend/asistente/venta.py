"""Modo venta: tono comercial para planes, precios y beneficios.

Es el contacto directo con el cliente, asi que responde con tono atractivo. Hay
DOS variantes para NO mezclar beneficios:

  - **Comprador** (cliente/invitado): gratis, precios en USD y Bs, comparar entre
    comercios, contacto directo por WhatsApp.
  - **Comerciante** (comerciante/admin): publicar catalogo, clientes por WhatsApp,
    enlace/QR, plan Gratis/Prueba y planes para crecer, premios y destacados.

Reglas:
  - Es SOLO tono: no cambia la veracidad ni permite inventar cifras o prometer
    de mas (el prompt base sigue prohibiendolo).
  - NO se activa para precios de productos del catalogo ni para soporte tecnico.
"""

from __future__ import annotations

import re

MODO_VENTA_CLIENTE = (
    'MODO VENTA (habla pensando en el COMPRADOR): responde de forma atractiva '
    'destacando que Localis es **gratis**, que ves **precios en dolares y '
    'bolivares** (con la tasa del dia), que puedes **comparar entre comercios '
    'locales** y **contactar directo por WhatsApp**. Cierra con una invitacion a '
    'la accion (por ejemplo: "busca lo que necesitas en la barra de busqueda" o '
    '"toca el boton de WhatsApp del comercio"). NO prometas envios, garantias ni '
    'pagos dentro de la app (no existen). Tono cercano y vendedor; 3 a 5 frases; '
    'cifras exactas.'
)

MODO_VENTA_COMERCIANTE = (
    'MODO VENTA (habla pensando en el COMERCIANTE): responde de forma atractiva '
    'destacando que puede **publicar su catalogo**, que **los clientes lo '
    'contactan directo por WhatsApp**, el **enlace publico y el codigo QR**, el '
    '**plan Gratis/Prueba (30 dias, hasta 50 productos)** y los planes para crecer '
    '(Basico 10 USD/100, Pro 15 USD/300, Business 35 USD/ilimitados), ademas de '
    '**premios** y **destacados**. Cierra con una invitacion a la accion (por '
    'ejemplo: "crea tu tienda con tu cuenta de Google y publica tu catalogo" o '
    '"sube al plan Pro para publicar hasta 300 productos"). NO mezcles beneficios '
    'de comprador (como comparar precios). Tono cercano, positivo y vendedor; 3 a 5 '
    'frases; cifras exactas.'
)

_MODO_POR_ROL = {
    'cliente': MODO_VENTA_CLIENTE,
    'invitado': MODO_VENTA_CLIENTE,
    'comerciante': MODO_VENTA_COMERCIANTE,
    'admin': MODO_VENTA_COMERCIANTE,
}

# Consultas claramente de NEGOCIO (aunque quien pregunte sea visitante).
_PATRONES_COMERCIANTE = (
    r'\bplan(?:es)?\b',
    r'\bsuscrip\w*',
    r'\bmembres\w*',
    r'\btarif\w*',
    r'cu[aá]nto cuesta localis',
    r'precio de localis',
    r'prueba gratis',
    r'quiero vender',
    r'\bvend\w*',
    r'(crear|registrar|abrir) mi (tienda|comercio|negocio)',
    r'\bcomerciante\b',
    r'\bnegocio\b',
    r'\btienda\b.*\b(publicar|vender|productos)\b',
)
_RE_COMERCIANTE = re.compile('|'.join(_PATRONES_COMERCIANTE), re.I)


def modo_venta(rol, texto=None):
    """Instruccion de venta segun la audiencia.

    Comerciante si el rol lo es **o** si la consulta es claramente de negocio
    (planes, vender, crear tienda...). Si no, comprador.
    """
    rol_norm = str(rol or 'invitado').lower()
    if rol_norm in ('comerciante', 'admin'):
        return MODO_VENTA_COMERCIANTE
    if texto and _RE_COMERCIANTE.search(str(texto)):
        return MODO_VENTA_COMERCIANTE
    return MODO_VENTA_CLIENTE


# Intenciones comerciales (conservador: solo Localis como servicio).
_PATRONES = (
    r'\bplan(?:es)?\b',
    r'\bsuscrip\w*',
    r'\bmembres\w*',
    r'\bbenefici\w*',
    r'\btarif\w*',
    r'qu[eé] es localis',
    r'c[oó]mo funciona localis',
    r'cu[aá]nto cuesta localis',
    r'precio de localis',
    r'prueba gratis',
    r'es gratis',
    r'quiero vender',
    r'vender (en|por) localis',
    r'(crear|registrar|abrir) mi (tienda|comercio|negocio)',
)
_RE = re.compile('|'.join(_PATRONES), re.I)


def es_tema_comercial(texto):
    """True si conviene responder con tono de venta. Nunca lanza."""
    if not texto:
        return False
    try:
        return bool(_RE.search(str(texto)))
    except Exception:
        return False
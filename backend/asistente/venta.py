"""Modo venta: tono comercial para planes, precios y beneficios.

Cuando la consulta es sobre **Localis como servicio** (planes, precios,
beneficios, registro, prueba gratis), el asistente debe vender: destacar el valor
y cerrar con una llamada a la accion. Es el contacto directo con el cliente.

Reglas:
  - Es SOLO tono: no cambia la veracidad ni permite inventar cifras o prometer
    de mas (el prompt base sigue prohibiendolo).
  - NO se activa para precios de productos del catalogo ni para soporte tecnico
    (para no sonar comercial ante un problema).
"""

from __future__ import annotations

import re

MODO_VENTA = (
    'MODO VENTA (la consulta es sobre planes, precios, beneficios o registro de '
    'Localis): responde de forma atractiva y comercial, sin exagerar ni prometer '
    'nada que no este en el contexto. Destaca beneficios concretos, compara planes '
    'cuando venga al caso y cierra SIEMPRE con una invitacion clara a la accion '
    '(por ejemplo: "crea tu tienda con tu cuenta de Google y publica tu catalogo", '
    'o "sube al plan Pro para publicar hasta 300 productos"). Tono cercano, '
    'positivo y vendedor; 3 a 5 frases; cifras exactas.'
)

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
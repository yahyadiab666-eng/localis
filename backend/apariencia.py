"""Apariencia del perfil del comercio: paleta **controlada** de colores.

El comerciante puede personalizar el color del banner de su perfil, pero solo
dentro de una paleta profesional y cerrada. Nunca se acepta un color arbitrario
(se normaliza al más parecido de la paleta o al color por defecto), de modo que
la interfaz mantenga coherencia visual.
"""

from __future__ import annotations

import re

# Paleta de primarios limpios. ``id`` es estable (se guarda en BD), ``hex`` es el
# color de fondo y ``texto`` el color legible que va encima.
PALETA_BANNER = (
    {'id': 'ambar', 'nombre': 'Ámbar', 'hex': '#F59E0B', 'texto': '#1A1A1A'},
    {'id': 'terracota', 'nombre': 'Terracota', 'hex': '#EA580C', 'texto': '#FFFFFF'},
    {'id': 'esmeralda', 'nombre': 'Esmeralda', 'hex': '#059669', 'texto': '#FFFFFF'},
    {'id': 'oceano', 'nombre': 'Océano', 'hex': '#0369A1', 'texto': '#FFFFFF'},
    {'id': 'indigo', 'nombre': 'Índigo', 'hex': '#4F46E5', 'texto': '#FFFFFF'},
    {'id': 'ciruela', 'nombre': 'Ciruela', 'hex': '#9333EA', 'texto': '#FFFFFF'},
    {'id': 'rosa', 'nombre': 'Rosa', 'hex': '#E11D48', 'texto': '#FFFFFF'},
    {'id': 'grafito', 'nombre': 'Grafito', 'hex': '#1F2937', 'texto': '#FFFFFF'},
)

PALETA_BANNER_DEFECTO = 'ambar'

_HEX = re.compile(r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$')
_POR_ID = {color['id']: color for color in PALETA_BANNER}
_POR_HEX = {color['hex'].upper(): color for color in PALETA_BANNER}


def opciones_banner():
    """Lista de colores disponibles para la UI (copias independientes)."""
    return [dict(color) for color in PALETA_BANNER]


def normalizar_color_banner(valor, defecto=PALETA_BANNER_DEFECTO):
    """Devuelve un ``id`` válido de la paleta. Nunca un color arbitrario."""
    if defecto not in _POR_ID:
        defecto = PALETA_BANNER_DEFECTO
    texto = str(valor or '').strip()
    if not texto:
        return defecto
    clave = texto.lower()
    if clave in _POR_ID:
        return clave
    if _HEX.match(texto):
        color = _POR_HEX.get(texto.upper())
        if color:
            return color['id']
    return defecto


def color_banner(valor, defecto=PALETA_BANNER_DEFECTO):
    """Diccionario completo del color (id, nombre, hex, texto)."""
    identificador = normalizar_color_banner(valor, defecto=defecto)
    return dict(_POR_ID.get(identificador, _POR_ID[PALETA_BANNER_DEFECTO]))


def color_banner_hex(valor, defecto=PALETA_BANNER_DEFECTO):
    return color_banner(valor, defecto=defecto)['hex']


def color_banner_texto(valor, defecto=PALETA_BANNER_DEFECTO):
    return color_banner(valor, defecto=defecto)['texto']


def gradiente_banner(valor, defecto=PALETA_BANNER_DEFECTO):
    """CSS ``linear-gradient`` del color (banner sin imagen)."""
    base = color_banner_hex(valor, defecto=defecto)
    return f'linear-gradient(135deg, {base} 0%, {base}CC 55%, {base}99 100%)'


def es_color_valido(valor):
    return normalizar_color_banner(valor) == str(valor or '').strip().lower()

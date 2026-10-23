"""Guardarrailes del asistente: saneado de entrada y redaccion de secretos.

Es la segunda capa de defensa (la primera es el propio system prompt): aunque el
modelo intente repetir una clave o una cadena de conexion, aqui se tacha antes
de devolverla al navegador. Nunca lanza.
"""

from __future__ import annotations

import re

_REEMPLAZO = '[dato oculto]'

# Patrones de secretos evidentes. Se mantienen acotados para no tachar texto
# normal: URLs de BD, claves sk-/sk-or-v1-, JWT (Supabase service_role) y
# asignaciones de variables sensibles.
_PATRONES = (
    re.compile(r'postgres(?:ql)?://[^\s"\']+', re.I),
    re.compile(r'sk-or-v1-[A-Za-z0-9]{12,}', re.I),
    re.compile(r'sk-[A-Za-z0-9_\-]{16,}', re.I),
    re.compile(r'eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}'),
    re.compile(
        r'(?i)\b(DATABASE_URL|SUPABASE_SERVICE_ROLE_KEY|SUPABASE_KEY|'
        r'LOCALIS_SECRET_KEY|LOCALIS_ASISTENTE_API_KEY|API[_-]?KEY)\b\s*[=:]\s*\S+'
    ),
)


def redactar(texto):
    """Sustituye secretos evidentes por un marcador. Nunca lanza."""
    if not texto:
        return ''
    try:
        salida = str(texto)
    except Exception:
        return ''
    for patron in _PATRONES:
        salida = patron.sub(_REEMPLAZO, salida)
    return salida


def sanear_entrada(texto, limite=1500):
    """Normaliza y acota el mensaje del usuario. Nunca lanza."""
    if texto is None:
        return ''
    try:
        limpio = str(texto).replace('\r\n', '\n').replace('\r', '\n')
    except Exception:
        return ''
    limpio = re.sub(r'\n{3,}', '\n\n', limpio)
    limpio = re.sub(r'[ \t]{3,}', '  ', limpio).strip()
    if limite and len(limpio) > limite:
        limpio = limpio[:limite].rstrip() + '…'
    return limpio

"""Constructor de consultas de imagen (anti-ruido) por tipo de producto.

Para productos **estructurados** (tecnología, electrodomésticos/hogar,
ferretería, automotriz, donde existe ficha oficial) genera consultas con SOLO la
marca y el modelo exacto, descartando relleno descriptivo, unidades, empaque y
ruido local antes de buscar la imagen. Para el resto devuelve lista vacía y el
pipeline mantiene sus variantes normales.
"""

from __future__ import annotations

import re
import unicodedata

CATEGORIAS_MODELO = frozenset({'tecnologia', 'ferreteria', 'automotriz', 'hogar'})

# Relleno comercial/descriptivo que no aporta al modelo.
_RELLENO = frozenset(
    {
        'unidad', 'unidades', 'und', 'pza', 'pieza', 'piezas', 'par', 'juego',
        'nuevo', 'nueva', 'original', 'genuino', 'oferta', 'promocion',
        'gratis', 'color', 'colores', 'talla', 'modelo', 'marca', 'producto',
        'articulo', 'presentacion', 'contenido', 'tipo', 'kit', 'set', 'combo',
        'para', 'con', 'sin', 'de', 'del', 'la', 'el', 'los', 'las', 'y', 'en',
        'por', 'importado', 'nacional', 'garantia', 'envio', 'caja', 'empaque',
        'display', 'velocidad', 'velocidades', 'funcion', 'funciones',
        'capacidad', 'medida', 'medidas', 'alto', 'ancho', 'largo', 'peso',
    }
)

# Solo se descarta un número si lleva unidad pegada (p. ej. ``500w``, ``8gb``).
# Un número suelto puede ser el modelo exacto (``GSB 550``, ``Note 12``).
_RE_UNIDAD = re.compile(
    r'^\d+(?:[.,]\d+)?'
    r'(?:kg|kgs|g|gr|grs|mg|l|lt|lts|ml|cc|oz|lb|lbs|un|und|cm|mm|m|w|kw|v|hz|'
    r'gb|tb|mb|mah|rpm|pulg|inch|pulgadas?|pie|pies|ah)$'
)
_RE_ALNUM = re.compile(r'[^a-z0-9]+')


def _plano(valor):
    texto = unicodedata.normalize('NFKD', str(valor or ''))
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return texto.lower().strip()


def _marca(nombre, marca=None, descripcion=None):
    if marca and str(marca).strip():
        return str(marca).strip()
    try:
        from backend.marcas_ve import detectar_marca

        return detectar_marca(nombre, descripcion)
    except Exception:
        return None


def _tokens(nombre, marca=None, descripcion=None):
    texto = _plano(' '.join(str(v) for v in (nombre, marca, descripcion) if v))
    crudos = [t for t in _RE_ALNUM.sub(' ', texto).split() if t]
    limpios = []
    for token in crudos:
        if token in _RELLENO or _RE_UNIDAD.match(token):
            continue
        if len(token) < 2:
            continue
        if token not in limpios:
            limpios.append(token)
    return limpios


def _modelo(tokens, marca_norm):
    """Secuencia de marca/modelo: tokens con dígitos + hasta 2 palabras previas."""
    indices = set()
    for posicion, token in enumerate(tokens):
        if not any(c.isdigit() for c in token):
            continue
        indices.add(posicion)
        previos = 0
        retro = posicion - 1
        while retro >= 0 and previos < 2:
            candidato = tokens[retro]
            if candidato == marca_norm or any(c.isdigit() for c in candidato):
                retro -= 1
                continue
            indices.add(retro)
            previos += 1
            retro -= 1
        # Sufijos comerciales típicos (pro, plus, max, lite, ultra…).
        siguiente = posicion + 1
        if siguiente < len(tokens):
            sufijo = tokens[siguiente]
            if sufijo in {'pro', 'plus', 'max', 'lite', 'ultra', 'mini', 'se', 'neo'}:
                indices.add(siguiente)
    return ' '.join(tokens[i] for i in sorted(indices))


def _tipo(tokens, marca_norm):
    return ' '.join(t for t in tokens if t != marca_norm and not any(c.isdigit() for c in t))


def _dedup(valores, limite=3):
    salida = []
    vistos = set()
    for valor in valores:
        texto = ' '.join(str(valor or '').split()).strip()
        if len(texto) < 3:
            continue
        if texto in vistos:
            continue
        vistos.add(texto)
        salida.append(texto)
    return salida[:limite]


def consulta_estructurada(nombre, marca=None, descripcion=None, categoria=None):
    """Consultas limpias (marca + modelo exacto) para productos estructurados.

    Devuelve ``[]`` si el producto no es estructurado (el llamador conserva sus
    variantes normales).
    """
    clave = _plano(categoria)
    if clave not in CATEGORIAS_MODELO:
        return []

    marca_txt = _marca(nombre, marca, descripcion)
    marca_norm = _plano(marca_txt).replace(' ', '')
    tokens = _tokens(nombre, marca_txt, descripcion)
    if marca_norm:
        tokens = [t for t in tokens if t.replace(' ', '') != marca_norm]
    if not tokens and not marca_txt:
        return []

    modelo = _modelo(tokens, marca_norm)
    tipo = _tipo(tokens, marca_norm)

    consultas = []
    if marca_txt and modelo:
        consultas.append(f'{marca_txt} {modelo}')
    if marca_txt and tipo:
        consultas.append(f'{marca_txt} {tipo}')
    if marca_txt and modelo:
        consultas.append(modelo)
    if marca_txt:
        consultas.append(marca_txt)
    elif modelo:
        consultas.append(modelo)
    return _dedup(consultas)

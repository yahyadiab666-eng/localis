"""Reconocimiento de marcas criollas e importadas comunes en Venezuela.

IMPORTANTE: esto es **conocimiento de marcas** (metadatos), NO una lista de
productos. Se usa únicamente para:
  - extraer la marca cuando el archivo no trae columna ``marca``;
  - mejorar la relevancia del query de búsqueda y el puntaje de fuentes.

El motor sigue siendo universal: cualquier artículo de cualquier rubro se
clasifica y busca por sus propios metadatos.
"""

from __future__ import annotations

import re
import unicodedata

_RE_NO_ALFA = re.compile(r'[^a-z0-9]+')

# Marcas normalizadas (minúsculas, sin tildes). Se ordenan por longitud para
# preferir la coincidencia más específica.
MARCAS_CONOCIDAS = (
    # Alimentos y bebidas
    'pan', 'polar', 'mavesa', 'savoy', 'nestle', 'bimbo', 'mary', 'purolomo',
    'mimosa', 'diana', 'gustosa', 'festival', 'fruti', 'maltin', 'toddy',
    'ovomaltina', 'cerelac', 'nan', 'nido', 'la lechera', 'carnation',
    'tigresa', 'fruto', 'villa del sur', 'plumrose', 'mendocina',
    'fama de america', 'juana la aviadora', 'cristal', 'caracas', 'zulia',
    'regional', 'mi gente', 'santa teresa', 'cacique', 'pampero',
    'dona pepa', 'splenda', 'maizina', 'cornflakes', 'quaker', 'heinz',
    'underwood', 'capri', 'vatel', 'primor', 'danlac', 'todo rico',
    'la fina', 'monica', 'la campina', 'las llaves', 'coca cola', 'pepsi',
    'frescolita', 'pepsi cola', 'golden', 'ricacao', 'sampaka',
    # Cuidado personal y salud
    'colgate', 'pantene', 'sedal', 'dove', 'rexona', 'axe', 'nivea', 'ponds',
    'johnson', 'head shoulders', 'herbal essences', 'elvive', 'garnier',
    'genfar', 'la sante', 'calox', 'medifarma', 'row', 'procaps', 'bayer',
    'advil', 'tylenol', 'aspirina', 'sal de frutas', 'alucon',
    # Tecnología y electro
    'samsung', 'lg', 'hp', 'dell', 'xiaomi', 'motorola', 'apple', 'iphone',
    'lenovo', 'asus', 'acer', 'huawei', 'tintatek', 'viotto', 'haier',
    'kalley', 'oster', 'philips', 'genius', 'mabe', 'daewoo', 'panasonic',
    'sony', 'toshiba', 'western digital', 'kingston', 'sandisk', 'jbl',
    'logitech', 'tp link', 'tp-link', 'ecovacs', 'xiaomi', 'nokia', 'zte',
    # Ferretería y automotriz
    'bosch', 'dewalt', 'stanley', 'truper', 'hercules', 'black decker',
    'black+decker', 'makita', 'skil', 'pretul', 'truper', '3m', 'sika',
    'pintuco', 'renner', 'montana', 'chevron', 'shell', 'mobil', 'valvoline',
    'bridgestone', 'goodyear', 'pirelli', 'michelin', 'brembo',
    # Ropa / calzado / hogar
    'adidas', 'nike', 'puma', 'reebok', 'skechers', 'new balance', 'bata',
    'flexi', 'vans', 'converse', 'hush puppies', 'totto',
)

_MARCAS_ORDENADAS = tuple(
    sorted({_RE_NO_ALFA.sub(' ', unicodedata.normalize('NFKD', m).encode('ascii', 'ignore').decode()).strip()
            for m in MARCAS_CONOCIDAS},
           key=len, reverse=True)
)


def _normalizar(texto):
    base = unicodedata.normalize('NFKD', str(texto or ''))
    base = ''.join(c for c in base if not unicodedata.combining(c))
    return _RE_NO_ALFA.sub(' ', base.lower()).strip()


def detectar_marca(nombre=None, descripcion=None):
    """Marca conocida más específica encontrada en el texto, o None."""
    texto = _normalizar(f'{nombre or ""} {descripcion or ""}')
    if not texto:
        return None
    palabras = set(texto.split())
    for marca in _MARCAS_ORDENADAS:
        if not marca:
            continue
        if ' ' in marca:
            if f' {marca} ' in f' {texto} ':
                return marca
        elif len(marca) <= 4:
            if marca in palabras:
                return marca
        elif marca in texto:
            return marca
    return None


def reconocer_marcas(nombre=None, descripcion=None, limite=3):
    """Todas las marcas conocidas presentes (ordenadas por especificidad)."""
    texto = _normalizar(f'{nombre or ""} {descripcion or ""}')
    if not texto:
        return []
    palabras = set(texto.split())
    encontradas = []
    for marca in _MARCAS_ORDENADAS:
        if not marca:
            continue
        if ' ' in marca:
            if f' {marca} ' in f' {texto} ':
                encontradas.append(marca)
        elif len(marca) <= 4:
            if marca in palabras:
                encontradas.append(marca)
        elif marca in texto:
            encontradas.append(marca)
        if len(encontradas) >= limite:
            break
    return encontradas

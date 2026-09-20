"""Clasificación universal de productos por categoría (sin listas de productos).

Motor de fallbacks inteligentes: a partir de los metadatos de la fila
(nombre, descripción, marca y/o categoría declarada) infiere la categoría y
subcategoría de un artículo para asignarle un placeholder profesional limpio
(fondo blanco) cuando no hay imagen real disponible.

No contiene nombres de productos concretos: solo una **matriz de términos
genéricos por categoría** (tipos de artículo), de modo que funciona para
cualquier catálogo venezolano o internacional de cualquier rubro.

Categorías soportadas:
    alimentos, bebidas, tecnologia, hogar, ferreteria, belleza, ropa,
    salud, juguetes, mascotas, deportes, automotriz, bebes, papeleria, otros
"""

from __future__ import annotations

import re
import unicodedata

PLACEHOLDER_BASE = '/static/img/placeholder-'

# Orden = prioridad en caso de empate (más específicas primero).
CATEGORIAS = (
    'ferreteria',
    'tecnologia',
    'automotriz',
    'bebidas',
    'belleza',
    'salud',
    'bebes',
    'mascotas',
    'juguetes',
    'deportes',
    'papeleria',
    'ropa',
    'hogar',
    'alimentos',
    'otros',
)

CLAVES_CATEGORIA = frozenset(CATEGORIAS)

# Matriz de términos genéricos (stems). No son marcas ni productos concretos.
_MATRIZ = {
    'alimentos': (
        'aliment', 'viver', 'comestible', 'mercado', 'abarrote', 'harina',
        'arroz', 'pasta', 'espagueti', 'fideo', 'aceite', 'azucar', 'sal',
        'cafe', 'leche', 'atun', 'sardina', 'salsa', 'mayonesa', 'mostaza',
        'ketchup', 'cereal', 'avena', 'galleta', 'pan', 'panela', 'queso',
        'mantequilla', 'margarina', 'huevo', 'mermelada', 'chocolate', 'dulce',
        'lenteja', 'caraota', 'frijol', 'garbanzo', 'maiz', 'trigo',
        'condimento', 'especia', 'caldo', 'sopa', 'pure', 'compota', 'miel',
        'yogur', 'jamon', 'salchicha', 'mortadela', 'pollo', 'carne',
        'pescado', 'enlatado', 'snack', 'golosina', 'caramelo', 'gelatina',
    ),
    'bebidas': (
        'bebida', 'refresco', 'gaseosa', 'soda', 'jugo', 'nectar', 'agua',
        'cerveza', 'malta', 'vino', 'licor', 'ron', 'whisky', 'vodka',
        'energizante', 'hidratante', 'chicha', 'te helado',
    ),
    'tecnologia': (
        'tecnolog', 'electron', 'informatic', 'celular', 'telefono',
        'smartphone', 'laptop', 'comput', 'tablet', 'monitor', 'teclado',
        'mouse', 'impresora', 'audifon', 'auricular', 'parlante', 'bocina',
        'cargador', 'usb', 'memoria', 'pendrive', 'disco', 'router', 'modem',
        'televisor', 'camara', 'consola', 'videojuego', 'bateria',
        'power bank', 'smartwatch', 'reloj inteligente', 'proyector',
    ),
    'hogar': (
        'hogar', 'detergente', 'limpiador', 'limpieza', 'cloro',
        'desinfectante', 'escoba', 'trapeador', 'coleto', 'vajilla', 'plato',
        'vaso', 'cubierto', 'sarten', 'olla', 'caldera', 'toalla', 'sabana',
        'almohada', 'cobija', 'cortina', 'bombillo', 'foco', 'lampara',
        'organizador', 'cesta', 'basura', 'servilleta', 'papel higienico',
        'suavizante', 'ambientador', 'jabon de lavar', 'jabon en polvo',
    ),
    'ferreteria': (
        'ferreter', 'herramient', 'construccion', 'taladro', 'martillo',
        'destornillador', 'alicate', 'tornillo', 'clavo', 'tuerca',
        'arandela', 'sierra', 'broca', 'lija', 'pintura', 'barniz', 'thinner',
        'cemento', 'cabilla', 'tubo', 'pvc', 'grifo', 'candado', 'cerradura',
        'bisagra', 'cadena', 'extension', 'cinta aislante', 'guantes',
        'casco', 'manguera', 'valvula', 'soldadura', 'llave', 'llave inglesa',
        'metro', 'nivel', 'remachadora', 'compresor', 'generador',
    ),
    'belleza': (
        'belleza', 'cosmetic', 'perfum', 'cuidado personal', 'higiene',
        'maquillaje', 'shampoo', 'champu', 'acondicionador', 'jabon de tocador',
        'jabon de bano', 'crema', 'locion', 'desodorante', 'antitranspirante',
        'pasta dental', 'cepillo de dientes', 'enjuague bucal', 'colonia',
        'labial', 'rubor', 'sombra', 'esmalte', 'rastrillo', 'afeitadora',
        'toalla sanitaria', 'tampon', 'protector solar', 'serum',
    ),
    'ropa': (
        'ropa', 'textil', 'moda', 'calzado', 'camisa', 'camiseta', 'franela',
        'pantalon', 'jeans', 'short', 'falda', 'vestido', 'blusa', 'chaqueta',
        'abrigo', 'sueter', 'medias', 'calcetines', 'ropa interior', 'brasier',
        'zapato', 'sandalia', 'bota', 'tenis', 'gorra', 'sombrero',
        'cinturon', 'corbata', 'pijama',
    ),
    'salud': (
        'salud', 'farmac', 'medic', 'medicamento', 'medicina', 'pastilla',
        'tableta', 'capsula', 'jarabe', 'vitamina', 'suplemento', 'curita',
        'gasa', 'venda', 'algodon', 'antiseptico', 'termometro', 'jeringa',
        'mascarilla', 'tapaboca', 'ibuprofeno', 'acetaminofen', 'aspirina',
        'analgesico', 'antiacido', 'antibiotico', 'alcohol',
    ),
    'juguetes': (
        'juguete', 'munec', 'peluche', 'rompecabezas', 'bloques', 'didactic',
        'plastilina', 'carrito', 'cometa', 'trompo', 'yo-yo', 'yoyo', 'ajedrez',
    ),
    'mascotas': (
        'mascota', 'perro', 'gato', 'cachorro', 'croqueta', 'correa',
        'collar', 'arenero', 'arena para', 'alimento para perro',
        'alimento para gato', 'juguete para mascota', 'antipulgas',
    ),
    'deportes': (
        'deporte', 'gimnasio', 'bicicleta', 'pesas', 'mancuerna', 'raqueta',
        'patines', 'yoga', 'colchoneta', 'natacion', 'futbol', 'baloncesto',
        'beisbol', 'guante de boxeo', 'proteccion deportiva',
    ),
    'automotriz': (
        'automotriz', 'automotor', 'vehiculo', 'carro', 'moto', 'aceite de motor',
        'llanta', 'caucho', 'rin', 'filtro', 'bujia', 'freno',
        'pastilla de freno', 'amortiguador', 'limpiaparabrisas', 'aditivo',
        'refrigerante', 'grasa', 'correa', 'espejo retrovisor',
    ),
    'bebes': (
        'bebe', 'panal', 'formula', 'biberon', 'tetero', 'chupon',
        'mameluco', 'toallitas humedas', 'crema de bebe', 'cuna', 'coche de bebe',
    ),
    'papeleria': (
        'papeler', 'escolar', 'oficina', 'cuaderno', 'libreta', 'lapiz',
        'boligrafo', 'pluma', 'marcador', 'resaltador', 'cartulina', 'goma',
        'sacapuntas', 'regla', 'tijera', 'archivador', 'sobre', 'cinta adhesiva',
        'papel bond', 'block',
    ),
}

_RE_NO_ALFA = re.compile(r'[^a-z0-9]+')


def _texto_plano(valor):
    if valor is None:
        return ''
    texto = unicodedata.normalize('NFKD', str(valor))
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return texto.lower().strip()


def _tokens(*valores):
    texto = _texto_plano(' '.join(str(v) for v in valores if v))
    return _RE_NO_ALFA.sub(' ', texto)


def _coincide(keyword, tokens, frase):
    if ' ' in keyword:
        return keyword in frase
    if len(keyword) <= 4:
        return keyword in tokens.split()
    return any(keyword in token for token in tokens.split())


def _categoria_desde_texto(texto):
    """Retorna (categoria, puntaje). 'otros' si no hay coincidencias."""
    frase = _RE_NO_ALFA.sub(' ', _texto_plano(texto))
    if not frase.strip():
        return 'otros', 0

    mejor = 'otros'
    mejor_puntaje = 0
    for categoria in CATEGORIAS:
        if categoria == 'otros':
            continue
        terminos = _MATRIZ.get(categoria, ())
        puntaje = sum(1 for termino in terminos if _coincide(termino, frase, frase))
        if puntaje > mejor_puntaje:
            mejor, mejor_puntaje = categoria, puntaje
    return mejor, mejor_puntaje


def clasificar_categoria(nombre=None, descripcion=None, marca=None, categoria_hint=None):
    """Categoría inferida (clave) para un producto.

    1. Si el archivo trae una categoría declarada reconocible, se respeta.
    2. Si no, se infiere por los términos del nombre + descripción + marca.
    3. Sin coincidencias → 'otros' (nunca vacío).
    """
    if categoria_hint:
        cat_hint, puntaje_hint = _categoria_desde_texto(categoria_hint)
        if cat_hint != 'otros' and puntaje_hint > 0:
            return cat_hint

    cat, puntaje = _categoria_desde_texto(
        ' '.join(str(v) for v in (nombre, descripcion, marca) if v)
    )
    return cat if puntaje > 0 else 'otros'


def imagen_para_categoria(categoria):
    """URL del placeholder profesional (fondo blanco) para una categoría.

    Acepta tanto una clave interna como texto libre ("Tecnología", "Víveres").
    """
    clave = _texto_plano(categoria)
    if clave in CLAVES_CATEGORIA:
        return f'{PLACEHOLDER_BASE}{clave}.svg'
    cat, puntaje = _categoria_desde_texto(categoria)
    if cat != 'otros' and puntaje > 0:
        return f'{PLACEHOLDER_BASE}{cat}.svg'
    return f'{PLACEHOLDER_BASE}otros.svg'

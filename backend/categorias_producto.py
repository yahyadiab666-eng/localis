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
import logging
logger = logging.getLogger('localis.categorias_producto')

import os
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
        'aliment*', 'viver*', 'comestible', 'mercado', 'abarrote', 'harina',
        'arroz', 'pasta', 'espagueti', 'fideo', 'aceite', 'azucar', 'sal',
        'cafe', 'leche', 'atun', 'sardina', 'salsa', 'mayonesa', 'mostaza',
        'ketchup', 'cereal', 'avena', 'galleta', 'pan', 'panela', 'queso',
        'mantequilla', 'margarina', 'huevo', 'mermelada', 'chocolate', 'dulce',
        'lenteja', 'caraota', 'frijol', 'garbanzo', 'maiz', 'trigo',
        'condimento', 'especia', 'caldo', 'sopa', 'pure', 'compota', 'miel',
        'yogur', 'jamon', 'salchicha', 'mortadela', 'pollo', 'carne',
        'pescado', 'enlatado', 'snack', 'golosina', 'caramelo', 'gelatina',
        'chicle', 'goma de mascar',
    ),
    'bebidas': (
        'bebida', 'refresco', 'gaseosa', 'soda', 'jugo', 'nectar', 'agua',
        'cerveza', 'malta', 'vino', 'licor', 'ron', 'whisky', 'vodka',
        'energizante', 'hidratante', 'chicha', 'te helado',
    ),
    'tecnologia': (
        'tecnolog*', 'electron*', 'informatic*', 'celular', 'telefono',
        'smartphone', 'laptop', 'comput*', 'tablet', 'monitor', 'teclado',
        'mouse', 'raton', 'impresora', 'audifon*', 'auricular', 'parlante', 'bocina',
        'cargador', 'usb', 'memoria', 'pendrive', 'disco', 'router', 'modem',
        'televisor', 'camara', 'consola', 'videojuego', 'bateria',
        'power bank', 'smartwatch', 'reloj inteligente', 'proyector',
    ),
    'hogar': (
        'hogar', 'electrodomest*', 'linea blanca', 'nevera', 'refrigerador',
        'lavadora', 'secadora', 'microondas', 'detergente', 'limpiador',
        'limpieza', 'cloro', 'desinfectante', 'escoba', 'trapeador', 'coleto',
        'vajilla', 'plato', 'vaso', 'cubierto', 'sarten', 'olla', 'caldera',
        'toalla', 'sabana', 'almohada', 'cobija', 'cortina', 'bombillo', 'foco',
        'lampara', 'organizador', 'cesta', 'basura', 'servilleta',
        'papel higienico', 'suavizante', 'ambientador', 'jabon de lavar',
        'jabon en polvo', 'filtro de agua', 'purificador de agua',
    ),
    'ferreteria': (
        'ferreter*', 'herramient*', 'construccion', 'taladro', 'martillo',
        'destornillador', 'alicate', 'tornillo', 'clavo', 'tuerca',
        'arandela', 'sierra', 'broca', 'lija', 'pintura', 'barniz', 'thinner',
        'cemento', 'cabilla', 'tubo', 'pvc', 'grifo', 'candado', 'cerradura',
        'bisagra', 'cadena', 'extension', 'cinta aislante', 'guantes',
        'casco', 'manguera', 'valvula', 'soldadura', 'llave', 'llave inglesa',
        'metro', 'nivel', 'remachadora', 'compresor', 'generador',
        'llave de agua', 'llave de paso',
    ),
    'belleza': (
        'belleza', 'cosmetic*', 'perfum*', 'cuidado personal', 'higiene',
        'maquillaje', 'shampoo', 'champu', 'acondicionador', 'jabon de tocador',
        'jabon de bano', 'crema', 'locion', 'desodorante', 'antitranspirante',
        'pasta dental', 'cepillo de dientes', 'enjuague bucal', 'colonia',
        'labial', 'rubor', 'sombra de ojos', 'sombra para ojos', 'esmalte',
        'rastrillo', 'afeitadora', 'toalla sanitaria', 'tampon',
        'protector solar', 'serum', 'pasta de dientes', 'dentifrico',
    ),
    'ropa': (
        'ropa', 'textil', 'moda', 'calzado', 'camisa', 'camiseta', 'franela',
        'pantalon', 'jeans', 'short', 'falda', 'vestido', 'blusa', 'chaqueta',
        'abrigo', 'sueter', 'medias', 'calcetines', 'ropa interior', 'brasier',
        'zapato', 'sandalia', 'bota', 'tenis', 'gorra', 'sombrero',
        'cinturon', 'corbata', 'pijama',
    ),
    'salud': (
        'salud', 'farmac*', 'medic*', 'medicamento', 'medicina', 'pastilla',
        'tableta', 'capsula', 'jarabe', 'vitamina', 'suplemento', 'curita',
        'gasa', 'venda', 'algodon', 'antiseptico', 'termometro', 'jeringa',
        'mascarilla', 'tapaboca', 'ibuprofeno', 'acetaminofen', 'aspirina',
        'analgesico', 'antiacido', 'antibiotico', 'alcohol', 'agua oxigenada',
    ),
    'juguetes': (
        'juguete', 'munec*', 'peluche', 'rompecabezas', 'bloques', 'didactic*',
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
        'bateria de carro', 'bateria de vehiculo', 'motocicleta',
    ),
    'bebes': (
        'bebe', 'panal', 'formula', 'biberon', 'tetero', 'chupon',
        'mameluco', 'toallitas humedas', 'crema de bebe', 'cuna', 'coche de bebe',
    ),
    'papeleria': (
        'papeler*', 'escolar', 'oficina', 'cuaderno', 'libreta', 'lapiz',
        'boligrafo', 'pluma', 'marcador', 'resaltador', 'cartulina', 'goma',
        'sacapuntas', 'regla', 'tijera', 'archivador', 'sobre', 'cinta adhesiva',
        'papel bond', 'block',
    ),
}

_RE_NO_ALFA = re.compile(r'[^a-z0-9]+')

# Categoría por defecto cuando no hay certeza (NUNCA se adivina una categoría).
CATEGORIA_POR_DEFECTO = 'otros'

# Traza de depuración del flujo de clasificación. Se activa con la variable de
# entorno LOCALIS_DEBUG_CATEGORIA=1 (por defecto silencioso en producción).
_DEBUG_CATEGORIA = str(os.getenv('LOCALIS_DEBUG_CATEGORIA', '')).strip().lower() in {
    '1', 'true', 'si', 'sí', 'on', 'yes',
}


def _debug_categoria(mensaje):
    if _DEBUG_CATEGORIA:
        logger.info(f'[Localis Categoria] {mensaje}')


def _texto_plano(valor):
    if valor is None:
        return ''
    texto = unicodedata.normalize('NFKD', str(valor))
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return texto.lower().strip()


def _frase_y_palabras(texto):
    """Normaliza a ``(frase_con_espacios, lista_de_palabras)``.

    Se colapsan espacios y cualquier carácter no alfanumérico se vuelve un
    separador, de modo que la coincidencia sea por palabra y no por subcadena.
    """
    frase = _RE_NO_ALFA.sub(' ', _texto_plano(texto))
    frase = ' '.join(frase.split())
    return frase, frase.split()


def _coincide(keyword, frase, palabras):
    """Coincidencia por palabra completa (evita falsos positivos por subcadena).

    Convención de la matriz:

    - Un término terminado en ``*`` es una **raíz** y coincide por prefijo del
      token (``aliment*`` ⇒ ``alimento``, ``alimentación``; ``tecnolog*`` ⇒
      ``tecnología``). Así se cubren variantes sin abrir la puerta a falsos
      positivos.
    - Frase multi-palabra: debe aparecer completa y delimitada (``goma de
      mascar`` no coincide con ``goma de borrar``).
    - Palabra suelta sin ``*``: coincide exacta o en plural (``salchicha`` ⇒
      ``salchichas``). Esto evita que ``carne`` coincida con ``carnet`` o que
      ``sal`` coincida con ``salchichas``.
    """
    keyword = _texto_plano(keyword).strip()
    if not keyword:
        return False

    es_raiz = keyword.endswith('*')
    if es_raiz:
        keyword = keyword[:-1].strip()
    if not keyword:
        return False

    if ' ' in keyword:
        return f' {keyword} ' in f' {frase} '
    if es_raiz:
        return any(palabra.startswith(keyword) for palabra in palabras)
    return any(
        palabra == keyword
        or palabra == f'{keyword}s'
        or palabra == f'{keyword}es'
        for palabra in palabras
    )


def _peso_termino(keyword):
    """Los términos más específicos pesan más (frase multi-palabra > suelta)."""
    return len(keyword.split())


def _puntuar_categorias(frase, palabras):
    """{categoria: puntaje} sumando el peso de cada término que coincide."""
    puntajes = {}
    for categoria in CATEGORIAS:
        if categoria == CATEGORIA_POR_DEFECTO:
            continue
        puntaje = 0
        for termino in _MATRIZ.get(categoria, ()):
            if _coincide(termino, frase, palabras):
                puntaje += _peso_termino(termino)
        if puntaje:
            puntajes[categoria] = puntaje
    return puntajes


def _categoria_desde_texto(texto):
    """Retorna ``(categoria, puntaje)``.

    Devuelve ``(CATEGORIA_POR_DEFECTO, 0)`` cuando no hay coincidencias **o**
    cuando dos o más categorías empatan (ambigüedad): es preferible un producto
    sin categorizar antes que asignarle una categoría incorrecta.
    """
    frase, palabras = _frase_y_palabras(texto)
    if not frase:
        return CATEGORIA_POR_DEFECTO, 0

    puntajes = _puntuar_categorias(frase, palabras)
    if not puntajes:
        _debug_categoria(f'texto={frase!r} sin coincidencias -> {CATEGORIA_POR_DEFECTO}')
        return CATEGORIA_POR_DEFECTO, 0

    mejor_puntaje = max(puntajes.values())
    mejores = sorted(c for c, p in puntajes.items() if p == mejor_puntaje)
    _debug_categoria(f'texto={frase!r} puntajes={puntajes} mejor={mejores}')
    if len(mejores) > 1:
        _debug_categoria(
            f'ambigüedad entre {mejores} -> {CATEGORIA_POR_DEFECTO}'
        )
        return CATEGORIA_POR_DEFECTO, 0
    return mejores[0], mejor_puntaje


def clasificar_categoria(nombre=None, descripcion=None, marca=None, categoria_hint=None):
    """Categoría inferida (clave) para un producto.

    1. Si el archivo trae una categoría declarada reconocible, se respeta.
    2. Si no, se infiere por los términos del nombre + descripción + marca.
    3. Sin coincidencias o ante ambigüedad → ``otros`` (nunca vacío ni erróneo).

    Nunca lanza: ante cualquier fallo cae a la categoría por defecto.
    """
    try:
        if categoria_hint:
            cat_hint, _ = _categoria_desde_texto(categoria_hint)
            if cat_hint in CLAVES_CATEGORIA and cat_hint != CATEGORIA_POR_DEFECTO:
                _debug_categoria(
                    f'hint={categoria_hint!r} reconocido -> {cat_hint}'
                )
                return cat_hint

        texto = ' '.join(str(v) for v in (nombre, descripcion, marca) if v)
        cat, puntaje = _categoria_desde_texto(texto)
        if cat not in CLAVES_CATEGORIA:
            cat = CATEGORIA_POR_DEFECTO
        _debug_categoria(
            f'entrada nombre={nombre!r} descripcion={descripcion!r} '
            f'marca={marca!r} hints={categoria_hint!r} -> {cat} (puntaje={puntaje})'
        )
        return cat
    except Exception as error:  # nunca romper la importación por clasificar
        _debug_categoria(
            f'error clasificando nombre={nombre!r}: {type(error).__name__}: {error}'
        )
        return CATEGORIA_POR_DEFECTO


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


# Etiquetas legibles para chips de filtrado (clave -> nombre mostrado)
ETIQUETAS_CATEGORIA = {
    'ferreteria': 'Ferretería', 'tecnologia': 'Tecnología',
    'automotriz': 'Automotriz', 'bebidas': 'Bebidas', 'belleza': 'Belleza',
    'salud': 'Salud', 'bebes': 'Bebés', 'mascotas': 'Mascotas',
    'juguetes': 'Juguetes', 'deportes': 'Deportes', 'papeleria': 'Papelería',
    'ropa': 'Ropa', 'hogar': 'Hogar', 'alimentos': 'Alimentos', 'otros': 'Otros',
}


def etiqueta_categoria(categoria):
    return ETIQUETAS_CATEGORIA.get(str(categoria or '').strip().lower(), 'Otros')


def anotar_categoria_producto(producto):
    """Inyecta categoria_id/categoria_nombre en runtime sin tocar la BD.

    Retrocompatible: si el dict ya trae una categoría válida, se respeta.
    """
    try:
        manual = str(producto.get('categoria_manual') or '').strip().lower()
        if manual and manual in CLAVES_CATEGORIA:
            producto['categoria_id'] = manual
            producto['categoria_nombre'] = etiqueta_categoria(manual)
            return producto
        existente = str(producto.get('categoria_id') or '').strip().lower()
        if existente and existente in CLAVES_CATEGORIA:
            producto.setdefault('categoria_nombre', etiqueta_categoria(existente))
            return producto
        categoria = clasificar_categoria(
            nombre=producto.get('nombre'),
            descripcion=producto.get('descripcion'),
            marca=producto.get('marca'),
        )
        if categoria not in CLAVES_CATEGORIA:
            categoria = CATEGORIA_POR_DEFECTO
        producto['categoria_id'] = categoria
        producto['categoria_nombre'] = etiqueta_categoria(categoria)
        _debug_categoria(
            f'anotado producto={producto.get("nombre")!r} -> {categoria}'
        )
    except Exception as error:
        _debug_categoria(
            f'fallback por error en {producto.get("nombre")!r}: '
            f'{type(error).__name__}: {error}'
        )
        producto.setdefault('categoria_id', CATEGORIA_POR_DEFECTO)
        producto.setdefault('categoria_nombre', etiqueta_categoria(CATEGORIA_POR_DEFECTO))
    return producto


def anotar_categorias_productos(productos):
    return [anotar_categoria_producto(p) for p in (productos or [])]


# ==========================================
# Emojis para los chips de categoría (UI)
# ==========================================
# Mapeo por subcadena normalizada (sin acentos, minúsculas). El orden importa:
# el primer término que coincida gana. Categorías nuevas -> emoji por defecto.
EMOJI_CATEGORIA_POR_DEFECTO = '📦'

EMOJIS_CATEGORIA = (
    (('restaurant', 'comida', 'aliment', 'viver', 'mercado', 'abarrote'), '🍔'),
    (('bebida', 'licor', 'refresc', 'jugo', 'cerveza', 'vino'), '🥤'),
    (('tecnolog', 'electron', 'informatic', 'celular', 'comput', 'gadget'), '📱'),
    (('ropa', 'moda', 'textil', 'calzado'), '👕'),
    (('salud', 'belleza', 'estetic', 'farmac', 'cuidado', 'higiene'), '💊'),
    (('hogar', 'varios', 'mueble', 'decorac'), '🏠'),
    (('ferreter', 'construc', 'herramienta'), '🔧'),
    (('servicio',), '🛠️'),
    (('juguete', 'nino', 'nina', 'infantil'), '🧸'),
    (('mascota', 'perro', 'gato'), '🐾'),
    (('deporte', 'gimnasio', 'fitness'), '⚽'),
    (('papeler', 'escolar', 'oficina', 'librer'), '✏️'),
    (('bebe', 'panal', 'matern'), '🍼'),
    (('automotriz', 'vehiculo', 'carro', 'moto', 'repuesto'), '🚗'),
    (('otro', 'general'), '🏷️'),
)


def emoji_categoria(nombre):
    """Emoji representativo de una categoría (por nombre libre).

    Tolerante a acentos, mayúsculas y espacios. Categorías desconocidas o
    vacías devuelven ``EMOJI_CATEGORIA_POR_DEFECTO`` (📦). Nunca lanza.
    """
    try:
        texto = _texto_plano(nombre)
        if not texto:
            return EMOJI_CATEGORIA_POR_DEFECTO
        for terminos, emoji in EMOJIS_CATEGORIA:
            for termino in terminos:
                if termino in texto:
                    return emoji
        return EMOJI_CATEGORIA_POR_DEFECTO
    except Exception:
        return EMOJI_CATEGORIA_POR_DEFECTO

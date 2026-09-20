"""Índice en memoria del Catálogo Maestro de Imágenes (búsqueda instantánea).

Objetivo: durante una importación masiva (CSV/XLSX/XLS de 2.000+ filas) asignar
la imagen profesional correcta en **microsegundos**, sin web scraping ni rembg
en vivo por fila.

El catálogo (tabla ``catalogo_maestro_imagenes`` en Supabase) se carga **una vez**
y se indexa en diccionarios:

- ``por_codigo``: EAN/UPC normalizado → URL de imagen.
- ``por_nombre``: clave normalizada (tokens de nombre + marca, sin unidades ni
  palabras vacías, ordenada) → URL de imagen.

Así, la asignación por fila es un ``dict.get`` (O(1)), no una consulta a la BD.

Si un producto no está en el índice, la importación le asigna un placeholder
genérico limpio por categoría (para que la UI nunca quede vacía) y el producto
se encola para completar su imagen real en segundo plano.
"""

from __future__ import annotations

import os
import re
import time
import unicodedata
from dataclasses import dataclass, field

from backend.runtime_cache import get_or_load, invalidate

_LOG = '[Localis Índice]'
_CLAVE_CACHE = 'catalogo_maestro_index_v1'
_TTL_SEG = max(30, int(os.getenv('LOCALIS_MAESTRO_INDEX_TTL_SEC', '600')))
_MAX_FILAS = max(1000, int(os.getenv('LOCALIS_MAESTRO_INDEX_MAX', '100000')))
_UMBRAL_SIMILITUD = float(os.getenv('LOCALIS_MAESTRO_SIMILITUD_MIN', '0.6'))
_MAX_POSTINGS = max(50, int(os.getenv('LOCALIS_MAESTRO_POSTINGS_MAX', '200')))
_PLACEHOLDER_BASE = '/static/img/placeholder-'

_PALABRAS_VACIAS = frozenset({
    'de', 'del', 'la', 'el', 'los', 'las', 'un', 'una', 'unos', 'unas',
    'y', 'o', 'con', 'sin', 'para', 'por', 'en', 'al', 'a', 'e',
    'tipo', 'marca', 'producto', 'articulo', 'presentacion', 'contenido',
    'original', 'nuevo', 'nueva', 'pack', 'combo',
})

_UNIDAD_RE = re.compile(
    r'^\d+(?:[.,]\d+)?\s?'
    r'(?:kg|kgs|g|gr|grs|gramos|mg|l|lt|lts|litro|litros|ml|cc|oz|lb|lbs|'
    r'un|und|unid|unidad|unidades|tableta|tabletas|capsula|capsulas|sobre|'
    r'sobres|rollo|rollos|x\d*)$'
)


@dataclass
class IndiceMaestro:
    por_codigo: dict = field(default_factory=dict)
    por_nombre: dict = field(default_factory=dict)
    por_token: dict = field(default_factory=dict)
    filas: int = 0
    generado_en: float = 0.0
    error: str | None = None

    def _asegurar_tokens(self):
        if self.por_token or not self.por_nombre:
            return
        for clave in self.por_nombre:
            for token in clave.split():
                self.por_token.setdefault(token, []).append(clave)

    def _similitud(self, tokens):
        """Mejor coincidencia por Jaccard de tokens (variaciones de nombre)."""
        if len(tokens) < 2:
            return None
        self._asegurar_tokens()
        conteo = {}
        for token in tokens:
            for clave in self.por_token.get(token, ())[:_MAX_POSTINGS]:
                conteo[clave] = conteo.get(clave, 0) + 1
        if not conteo:
            return None
        mejor_clave = None
        mejor_score = 0.0
        for clave, interseccion in conteo.items():
            union = len(tokens) + len(clave.split()) - interseccion
            score = (interseccion / union) if union else 0.0
            if score > mejor_score:
                mejor_clave, mejor_score = clave, score
        if mejor_clave and mejor_score >= _UMBRAL_SIMILITUD:
            return self.por_nombre[mejor_clave]
        return None

    def buscar(self, *, codigo=None, nombre=None, marca=None):
        """(url, origen) en O(1) (con respaldo de similitud de tokens)."""
        if codigo and codigo in self.por_codigo:
            return self.por_codigo[codigo], 'codigo'
        if nombre:
            clave = normalizar_clave_producto(nombre, marca)
            if clave and clave in self.por_nombre:
                return self.por_nombre[clave], 'nombre'
            clave_sin_marca = normalizar_clave_producto(nombre)
            if clave_sin_marca and clave_sin_marca in self.por_nombre:
                return self.por_nombre[clave_sin_marca], 'nombre'
            tokens = (clave or clave_sin_marca or '').split()
            por_similitud = self._similitud(tokens)
            if por_similitud:
                return por_similitud, 'nombre_similar'
        return None, None


def _texto_plano(valor):
    if valor is None:
        return ''
    texto = unicodedata.normalize('NFKD', str(valor))
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return texto.lower().strip()


def normalizar_clave_producto(nombre, marca=None):
    """Clave de coincidencia: tokens útiles de nombre + marca, ordenados.

    Ej.: "Harina de Maíz P.A.N. 1kg" + marca "PAN" → "harina maiz pan".
    "Pepsi Cola 2L" → "cola pepsi".
    """
    partes = []
    for valor in (nombre, marca):
        if not valor:
            continue
        texto = _texto_plano(valor)
        texto = re.sub(r'[^a-z0-9]+', ' ', texto)
        partes.append(texto)

    tokens = set()
    for bloque in partes:
        for token in bloque.split():
            if not token or token in _PALABRAS_VACIAS:
                continue
            if token.isdigit() or _UNIDAD_RE.match(token):
                continue
            if len(token) < 2:
                continue
            tokens.add(token)
    return ' '.join(sorted(tokens))


def _url_maestro_valida(valor):
    try:
        from backend.catalogo_maestro import _url_maestro_valida as validador

        return validador(valor)
    except Exception:
        return None


def _cargar_filas_maestro():
    """Lee el catálogo maestro completo (preferentemente BD directa)."""
    from backend.db import get_db_connection, using_postgres

    if not using_postgres():
        return []

    consultas = [
        """
        SELECT codigo_barras, url_imagen, nombre, marca
        FROM catalogo_maestro_imagenes
        WHERE url_imagen IS NOT NULL
          AND TRIM(BOTH FROM CAST(url_imagen AS TEXT)) <> ''
        LIMIT ?
        """,
        """
        SELECT codigo_barras, url_imagen
        FROM catalogo_maestro_imagenes
        WHERE url_imagen IS NOT NULL
          AND TRIM(BOTH FROM CAST(url_imagen AS TEXT)) <> ''
        LIMIT ?
        """,
    ]
    ultimo_error = None
    for sql in consultas:
        try:
            with get_db_connection() as conexion:
                cursor = conexion.cursor()
                cursor.execute(sql, (_MAX_FILAS,))
                filas = cursor.fetchall()
            return filas
        except Exception as error:  # columna faltante u otro fallo
            ultimo_error = error
            continue
    if ultimo_error is not None:
        print(f'{_LOG} no se pudo cargar el catálogo maestro: {type(ultimo_error).__name__}')
    return []


def _fila_a_dict(fila):
    if isinstance(fila, dict):
        return fila
    return {
        'codigo_barras': fila[0] if len(fila) > 0 else None,
        'url_imagen': fila[1] if len(fila) > 1 else None,
        'nombre': fila[2] if len(fila) > 2 else None,
        'marca': fila[3] if len(fila) > 3 else None,
    }


def _construir_indice():
    inicio = time.perf_counter()
    indice = IndiceMaestro(generado_en=time.time())
    try:
        from backend.utils import normalizar_codigo_barras

        filas = _cargar_filas_maestro()
        for fila in filas:
            registro = _fila_a_dict(fila)
            url = _url_maestro_valida(registro.get('url_imagen'))
            if not url:
                continue
            codigo = normalizar_codigo_barras(registro.get('codigo_barras'))
            if codigo and codigo not in indice.por_codigo:
                indice.por_codigo[codigo] = url
            clave = normalizar_clave_producto(
                registro.get('nombre'), registro.get('marca')
            )
            if clave and clave not in indice.por_nombre:
                indice.por_nombre[clave] = url
        indice.filas = len(filas)
    except Exception as error:
        indice.error = f'{type(error).__name__}: {error}'
        print(f'{_LOG} error construyendo índice: {indice.error}')
    duracion = (time.perf_counter() - inicio) * 1000
    print(
        f'{_LOG} índice listo filas={indice.filas} '
        f'codigos={len(indice.por_codigo)} nombres={len(indice.por_nombre)} '
        f'en {duracion:.1f} ms'
    )
    return indice


def obtener_indice(forzar=False):
    """Índice cacheado (TTL). Se reconstruye si expira o si ``forzar=True``."""
    if forzar:
        invalidate(_CLAVE_CACHE)
    return get_or_load(_CLAVE_CACHE, _construir_indice, ttl_seconds=_TTL_SEG)


def invalidar_indice():
    """Fuerza la recarga en la próxima búsqueda (tras guardar imágenes nuevas)."""
    invalidate(_CLAVE_CACHE)


def buscar_imagen_maestro(*, codigo=None, nombre=None, marca=None):
    """Atajo: (url, origen) usando el índice cacheado."""
    return obtener_indice().buscar(codigo=codigo, nombre=nombre, marca=marca)


def estadisticas_indice():
    indice = obtener_indice()
    return {
        'filas': indice.filas,
        'codigos': len(indice.por_codigo),
        'nombres': len(indice.por_nombre),
        'error': indice.error,
        'generado_en': indice.generado_en,
    }


# ---------------------------------------------------------------------------
# Placeholder genérico por categoría (para productos nuevos sin coincidencia)
# ---------------------------------------------------------------------------
_CATEGORIAS_PLACEHOLDER = (
    ('alimentos', ('aliment', 'comida', 'viveres', 'harina', 'snack', 'cereal', 'mercado', 'abarrote')),
    ('bebidas', ('bebida', 'refresco', 'jugo', 'licor', 'cerveza', 'agua', 'vino')),
    ('tecnologia', ('tecnolog', 'electron', 'celular', 'telefono', 'comput', 'informatic', 'accesorio')),
    ('hogar', ('hogar', 'ferreter', 'herramient', 'mueble', 'limpieza', 'jardin', 'decoracion')),
    ('belleza', ('belleza', 'cosmetic', 'perfum', 'cuidado personal', 'maquillaje', 'higiene')),
    ('ropa', ('ropa', 'calzado', 'moda', 'textil', 'zapato', 'prenda')),
    ('salud', ('salud', 'farmac', 'medic', 'medicina', 'bienestar')),
)


def imagen_generica_categoria(categoria=None):
    """SVG limpio (fondo blanco) de la categoría, o el genérico de 'otros'."""
    clave = _texto_plano(categoria)
    if clave:
        for carpeta, pistas in _CATEGORIAS_PLACEHOLDER:
            if any(pista in clave for pista in pistas):
                return f'{_PLACEHOLDER_BASE}{carpeta}.svg'
    return f'{_PLACEHOLDER_BASE}otros.svg'


def categoria_de_comercio(comercio_id):
    """Nombre de la categoría del comercio (para el placeholder)."""
    if not comercio_id:
        return None
    try:
        from backend.db import get_db_connection, using_postgres

        if not using_postgres():
            return None
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT c.nombre
                FROM comercios co
                JOIN categorias c ON c.id = co.categoria_id
                WHERE co.id = ?
                LIMIT 1
                """,
                (int(comercio_id),),
            )
            fila = cursor.fetchone()
        if not fila:
            return None
        if isinstance(fila, dict):
            return fila.get('nombre')
        return fila[0]
    except Exception as error:
        print(f'{_LOG} categoría comercio={comercio_id} no resuelta: {type(error).__name__}')
        return None

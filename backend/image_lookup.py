"""Resolución de imágenes exclusivamente vía URLs del bucket Supabase."""

import sqlite3

import requests

from backend.db import get_db_connection
from backend.supabase_client import SUPABASE_URL, url_publica_bucket
from backend.utils import (
    normalizar_codigo_barras,
    texto_campo_imagen,
    url_imagen_producto_default,
    url_imagen_supabase_valida,
)

# PostgreSQL: normalización de código de barras en consultas SQL.
EXPR_CODIGO_BARRAS = (
    "regexp_replace("
    "regexp_replace(TRIM(BOTH FROM CAST(codigo_barras AS TEXT)), '\\s+', '', 'g'), "
    "'\\.0+$', '', 'g')"
)

_EXTENSIONES_BUCKET = ('webp', 'jpg', 'jpeg', 'png')
_CACHE_EXISTENCIA = {}


def _existe_en_bucket(url: str) -> bool:
    if not url:
        return False
    if url in _CACHE_EXISTENCIA:
        return _CACHE_EXISTENCIA[url]
    try:
        respuesta = requests.head(url, timeout=4, allow_redirects=True)
        ok = respuesta.status_code == 200
    except requests.RequestException:
        ok = False
    _CACHE_EXISTENCIA[url] = ok
    return ok


def buscar_imagen_supabase_por_codigo(codigo_barras):
    """Busca productos/{codigo}.{ext} en el bucket público de Supabase."""
    if not SUPABASE_URL:
        return None
    codigo = normalizar_codigo_barras(codigo_barras)
    if not codigo:
        return None
    for extension in _EXTENSIONES_BUCKET:
        candidata = url_publica_bucket('productos', f'{codigo}.{extension}')
        if _existe_en_bucket(candidata):
            return candidata
    return None


def normalizar_imagen_registro(imagen_url=None, codigo_barras=None):
    """
    Resuelve la imagen de un producto:
    1) URL válida del bucket Supabase en imagen_url
    2) Búsqueda por código de barras en productos/ del bucket
    3) Imagen por defecto (default-product.webp)
    """
    directa = url_imagen_supabase_valida(imagen_url)
    if directa:
        return directa

    referencia = texto_campo_imagen(imagen_url, default=None)
    codigo = normalizar_codigo_barras(codigo_barras) or normalizar_codigo_barras(referencia)
    if codigo:
        encontrada = buscar_imagen_supabase_por_codigo(codigo)
        if encontrada:
            return encontrada

    return url_imagen_producto_default()


def obtener_imagen_url_producto(producto_id):
    if not producto_id:
        return url_imagen_producto_default()
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT imagen_url, codigo_barras FROM productos WHERE id = ?',
                (int(producto_id),),
            )
            fila = cursor.fetchone()
            if not fila:
                return url_imagen_producto_default()
            registro = dict(fila)
            return normalizar_imagen_registro(
                registro.get('imagen_url'),
                registro.get('codigo_barras'),
            )
    except Exception as error:
        print(f'Error al leer imagen del producto {producto_id}: {error}')
        return url_imagen_producto_default()


def resolver_imagen_producto(
    imagen_url=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
    producto_id=None,
    buscar_web=False,
    excluir_url=None,
    persistir=False,
):
    del nombre, descripcion, buscar_web, persistir

    url = normalizar_imagen_registro(imagen_url, codigo_barras)
    if url and url != excluir_url:
        return url

    if producto_id:
        url_bd = obtener_imagen_url_producto(producto_id)
        if url_bd and url_bd != excluir_url:
            return url_bd

    return url_imagen_producto_default()


def aplicar_respaldo_imagenes(productos, persistir=False):
    del persistir
    if not productos:
        return productos
    for prod in productos:
        prod['imagen_url'] = normalizar_imagen_registro(
            prod.get('imagen_url'),
            prod.get('codigo_barras'),
        )
    return productos


def asociar_imagenes_inventario(comercio_id):
    """Tras CSV: normaliza URLs en memoria (sin búsqueda web ni disco local)."""
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT id, nombre, codigo_barras, descripcion, imagen_url
                FROM productos
                WHERE comercio_id = ?
                """,
                (int(comercio_id),),
            )
            productos = [dict(fila) for fila in cursor.fetchall()]
    except Exception as error:
        print(f'Error al leer productos para asociar imágenes: {error}')
        return 0

    aplicar_respaldo_imagenes(productos)
    return len(productos)

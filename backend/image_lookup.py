"""Imágenes: lectura instantánea desde BD; resolución HEAD solo al escribir."""

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


def _existe_en_bucket(url: str) -> bool:
    """Verificación remota: solo en rutas de escritura (import/create/update)."""
    if not url:
        return False
    try:
        respuesta = requests.head(url, timeout=4, allow_redirects=True)
        return respuesta.status_code == 200
    except requests.RequestException:
        return False


def buscar_imagen_supabase_por_codigo(codigo_barras):
    """Busca productos/{codigo}.{ext} en el bucket (solo al persistir en BD)."""
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


def imagen_url_para_catalogo(imagen_url=None):
    """
    Lectura para catálogos: usa la URL ya guardada en PostgreSQL.
    Sin HEAD ni búsquedas en bucket (O(1) por producto).
    """
    directa = url_imagen_supabase_valida(imagen_url)
    if directa:
        return directa
    return url_imagen_producto_default()


def resolver_imagen_url_definitiva(imagen_url=None, codigo_barras=None):
    """
    Escritura: resuelve la URL final para INSERT/UPDATE en BD.
    Puede usar HEAD por código de barras; si no hay imagen, guarda el default.
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


def normalizar_imagen_registro(imagen_url=None, codigo_barras=None):
    """Alias de lectura rápida (ignora codigo_barras en runtime de catálogo)."""
    del codigo_barras
    return imagen_url_para_catalogo(imagen_url)


def obtener_imagen_url_producto(producto_id):
    """Endpoint /imagen-producto: solo lee imagen_url de BD, sin HEAD."""
    if not producto_id:
        return url_imagen_producto_default()
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT imagen_url FROM productos WHERE id = ?',
                (int(producto_id),),
            )
            fila = cursor.fetchone()
            if not fila:
                return url_imagen_producto_default()
            registro = dict(fila)
            return imagen_url_para_catalogo(registro.get('imagen_url'))
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
    del nombre, descripcion, buscar_web
    escribir_bd = persistir

    if escribir_bd:
        url = resolver_imagen_url_definitiva(imagen_url, codigo_barras)
    else:
        url = imagen_url_para_catalogo(imagen_url)

    if url and url != excluir_url:
        return url

    if producto_id:
        url_bd = obtener_imagen_url_producto(producto_id)
        if url_bd and url_bd != excluir_url:
            return url_bd

    return url_imagen_producto_default()


def aplicar_respaldo_imagenes(productos, persistir=False):
    """Normaliza imagen_url para mostrar (lectura BD). persistir=True resuelve y escribe."""
    if not productos:
        return productos

    if persistir:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            for prod in productos:
                producto_id = prod.get('id')
                if not producto_id:
                    continue
                url_final = resolver_imagen_url_definitiva(
                    prod.get('imagen_url'),
                    prod.get('codigo_barras'),
                )
                cursor.execute(
                    'UPDATE productos SET imagen_url = ? WHERE id = ?',
                    (url_final, int(producto_id)),
                )
                prod['imagen_url'] = url_final
            conexion.commit()
        return productos

    for prod in productos:
        prod['imagen_url'] = imagen_url_para_catalogo(prod.get('imagen_url'))
    return productos


def asociar_imagenes_inventario(comercio_id):
    """Tras CSV: persiste URL definitiva en PostgreSQL (HEAD solo aquí, no en catálogo)."""
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT id, codigo_barras, imagen_url
                FROM productos
                WHERE comercio_id = ?
                """,
                (int(comercio_id),),
            )
            productos = [dict(fila) for fila in cursor.fetchall()]
    except Exception as error:
        print(f'Error al leer productos para asociar imágenes: {error}')
        return 0

    if not productos:
        return 0

    actualizados = 0
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            for prod in productos:
                if url_imagen_supabase_valida(prod.get('imagen_url')):
                    continue
                url_final = resolver_imagen_url_definitiva(
                    prod.get('imagen_url'),
                    prod.get('codigo_barras'),
                )
                cursor.execute(
                    """
                    UPDATE productos
                    SET imagen_url = ?
                    WHERE id = ? AND comercio_id = ?
                    """,
                    (url_final, int(prod['id']), int(comercio_id)),
                )
                actualizados += cursor.rowcount
            conexion.commit()
    except Exception as error:
        print(f'Error al persistir imágenes del inventario: {error}')
        return 0

    return actualizados

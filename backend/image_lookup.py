"""Resolución estricta de imágenes: solo URL exacta del producto, sin adivinar."""

import os
import sqlite3

from backend.db import get_db_connection
from backend.utils import (
    normalizar_codigo_barras,
    texto_campo_imagen,
    url_imagen_usable,
)

# PostgreSQL: CAST a texto + TRIM + quitar espacios (CSV/Excel suelen traer padding).
EXPR_CODIGO_BARRAS = (
    "regexp_replace("
    "regexp_replace(TRIM(BOTH FROM CAST(codigo_barras AS TEXT)), '\\s+', '', 'g'), "
    "'\\.0+$', '', 'g')"
)
EXPR_NOMBRE = (
    "regexp_replace(LOWER(TRIM(BOTH FROM CAST(nombre AS TEXT))), '\\s+', ' ', 'g')"
)

_EXTENSIONES_LOCALES = ('.webp', '.jpg', '.jpeg', '.png')
_CARPETAS_LOCALES = (
    os.path.join('static', 'images', 'productos'),
    os.path.join('static', 'images'),
)


def _raiz_proyecto():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def _url_si_archivo_existe(relativo_static):
    relativo_static = relativo_static.replace('\\', '/').lstrip('/')
    ruta = os.path.join(_raiz_proyecto(), 'static', relativo_static)
    if os.path.isfile(ruta):
        return f'/static/{relativo_static}'
    return None


def buscar_imagen_en_directorio(codigo_o_archivo):
    """Busca archivo local por nombre exacto (CSV) o código de barras como nombre de archivo."""
    if not codigo_o_archivo:
        return None

    bruto = str(codigo_o_archivo).strip().replace('\\', '/')
    if not bruto:
        return None

    nombre_archivo = os.path.basename(bruto)
    stem, ext = os.path.splitext(nombre_archivo)
    candidatos = []
    if nombre_archivo:
        candidatos.append(nombre_archivo)
    codigo = normalizar_codigo_barras(stem) or (stem.strip() if stem else None)
    if codigo and codigo not in candidatos:
        candidatos.append(codigo)

    vistos = set()
    for carpeta in _CARPETAS_LOCALES:
        for cand in candidatos:
            if not cand:
                continue
            clave = f'{carpeta}|{cand}'.lower()
            if clave in vistos:
                continue
            vistos.add(clave)
            rel_dir = os.path.relpath(carpeta, 'static')

            if ext and ext.lower() in _EXTENSIONES_LOCALES:
                url = _url_si_archivo_existe(os.path.join(rel_dir, cand))
                if url:
                    return url

            for extra in _EXTENSIONES_LOCALES:
                archivo = cand if cand.lower().endswith(extra) else f'{cand}{extra}'
                url = _url_si_archivo_existe(os.path.join(rel_dir, archivo))
                if url:
                    return url
    return None


def _url_usable_o_none(valor):
    texto = texto_campo_imagen(valor, default=None)
    if url_imagen_usable(texto):
        return texto
    return None


def normalizar_imagen_registro(imagen_url=None, codigo_barras=None):
    """
    Normaliza la imagen de UN solo registro sin consultar otros productos.
    1) URL http(s) o /static ya guardada (Supabase, CSV, manual)
    2) Archivo local explícito referenciado en la celda o por código exacto
    """
    directa = _url_usable_o_none(imagen_url)
    if directa:
        return directa

    referencia = texto_campo_imagen(imagen_url, default=None)
    if not referencia:
        referencia = normalizar_codigo_barras(codigo_barras)
    if referencia:
        return buscar_imagen_en_directorio(referencia)
    return None


def obtener_imagen_url_producto(producto_id):
    """Devuelve la URL exacta persistida en BD para este producto (sin cruzar catálogo)."""
    if not producto_id:
        return None
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT imagen_url FROM productos WHERE id = ?',
                (int(producto_id),),
            )
            fila = cursor.fetchone()
            if not fila:
                return None
            registro = dict(fila)
            return normalizar_imagen_registro(registro.get('imagen_url'))
    except Exception as error:
        print(f'Error al leer imagen del producto {producto_id}: {error}')
        return None


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
    """
    Resolución estricta: nunca adivina ni reutiliza fotos de otros productos.
    Prioridad: URL del registro → archivo local explícito → URL exacta en BD por id.
    """
    del nombre, descripcion, buscar_web, persistir  # sin fuzzy ni búsqueda web

    url = normalizar_imagen_registro(imagen_url, codigo_barras)
    if url and url != excluir_url:
        return url

    if producto_id:
        url_bd = obtener_imagen_url_producto(producto_id)
        if url_bd and url_bd != excluir_url:
            return url_bd

    return None


def aplicar_respaldo_imagenes(productos, persistir=False):
    """Normaliza imagen_url de cada producto usando solo su propio valor guardado."""
    del persistir  # no escribir URLs inferidas en BD
    if not productos:
        return productos

    for prod in productos:
        prod['imagen_url'] = normalizar_imagen_registro(
            prod.get('imagen_url'),
            prod.get('codigo_barras'),
        )
    return productos


def asociar_imagenes_inventario(comercio_id):
    """Tras CSV: normaliza URLs ya importadas. No encola búsquedas ni adivina imágenes."""
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

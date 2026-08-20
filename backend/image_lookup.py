"""Resolución de imágenes: manual (Supabase/URL), catálogo externo y default."""

import sqlite3

from backend.db import get_db_connection
from backend.utils import (
    es_imagen_generica,
    normalizar_codigo_barras,
    normalizar_nombre_producto,
    texto_campo_imagen,
    url_imagen_producto_default,
    url_imagen_supabase_valida,
)

EXPR_CODIGO_BARRAS = (
    "regexp_replace("
    "regexp_replace(TRIM(BOTH FROM CAST(codigo_barras AS TEXT)), '\\s+', '', 'g'), "
    "'\\.0+$', '', 'g')"
)
EXPR_NOMBRE = (
    "regexp_replace(LOWER(TRIM(BOTH FROM CAST(nombre AS TEXT))), '\\s+', ' ', 'g')"
)

_FILTRO_IMAGEN_REAL = """
    imagen_url IS NOT NULL
    AND TRIM(BOTH FROM CAST(imagen_url AS TEXT)) <> ''
    AND imagen_url NOT ILIKE '%%default-product%%'
    AND imagen_url NOT ILIKE '%%placeholder%%'
    AND TRIM(BOTH FROM CAST(imagen_url AS TEXT)) <> '__PENDING__'
    AND CAST(imagen_url AS TEXT) ILIKE 'https://%%'
"""


def _url_manual_o_none(valor):
    """URL explícita del usuario: Supabase subida o enlace https guardado en BD/CSV."""
    supabase = url_imagen_supabase_valida(valor)
    if supabase:
        return supabase
    texto = texto_campo_imagen(valor, default=None)
    if not texto or es_imagen_generica(texto):
        return None
    if texto.startswith('https://'):
        return texto
    return None


def _es_url_default(url):
    if not url:
        return False
    return 'default-product' in str(url).lower()


def _mapa_desde_filas(filas, clave_norm):
    resultado = {}
    for fila in filas or []:
        fila = dict(fila)
        clave = fila.get(clave_norm)
        url = _url_manual_o_none(fila.get('imagen_url'))
        if clave and url and clave not in resultado:
            resultado[clave] = url
    return resultado


def mapa_imagenes_por_codigos(codigos):
    """Reutiliza imagen ya conocida en PostgreSQL por código de barras."""
    normalizados = []
    vistos = set()
    for codigo in codigos or []:
        limpio = normalizar_codigo_barras(codigo)
        if limpio and limpio not in vistos:
            vistos.add(limpio)
            normalizados.append(limpio)
    if not normalizados:
        return {}

    placeholders = ','.join(['?'] * len(normalizados))
    sql = f"""
        SELECT {EXPR_CODIGO_BARRAS} AS codigo_norm, imagen_url
        FROM productos
        WHERE codigo_barras IS NOT NULL
          AND TRIM(BOTH FROM CAST(codigo_barras AS TEXT)) <> ''
          AND {EXPR_CODIGO_BARRAS} IN ({placeholders})
          AND {_FILTRO_IMAGEN_REAL}
        ORDER BY id DESC
    """
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(sql, tuple(normalizados))
            return _mapa_desde_filas(cursor.fetchall(), 'codigo_norm')
    except Exception as error:
        print(f'Error al buscar imágenes por código de barras: {error}')
        return {}


def mapa_imagenes_por_nombres(nombres):
    """Reutiliza imagen ya conocida en PostgreSQL por nombre."""
    normalizados = []
    vistos = set()
    for nombre in nombres or []:
        limpio = normalizar_nombre_producto(nombre)
        if limpio and limpio not in vistos:
            vistos.add(limpio)
            normalizados.append(limpio)
    if not normalizados:
        return {}

    placeholders = ','.join(['?'] * len(normalizados))
    sql = f"""
        SELECT {EXPR_NOMBRE} AS nombre_norm, imagen_url
        FROM productos
        WHERE nombre IS NOT NULL
          AND TRIM(BOTH FROM CAST(nombre AS TEXT)) <> ''
          AND {EXPR_NOMBRE} IN ({placeholders})
          AND {_FILTRO_IMAGEN_REAL}
        ORDER BY id DESC
    """
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(sql, tuple(normalizados))
            return _mapa_desde_filas(cursor.fetchall(), 'nombre_norm')
    except Exception as error:
        print(f'Error al buscar imágenes por nombre: {error}')
        return {}


def _cargar_mapas_catalogo_global():
    """Mapas código/nombre → imagen_url reutilizable desde PostgreSQL."""
    mapa_codigos = {}
    mapa_nombres = {}
    sql = f"""
        SELECT codigo_barras, nombre, imagen_url
        FROM productos
        WHERE {_FILTRO_IMAGEN_REAL}
        ORDER BY id DESC
    """
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(sql)
            for fila in cursor.fetchall():
                registro = dict(fila)
                url = _url_manual_o_none(registro.get('imagen_url'))
                if not url:
                    continue
                codigo = normalizar_codigo_barras(registro.get('codigo_barras'))
                if codigo and codigo not in mapa_codigos:
                    mapa_codigos[codigo] = url
                nombre = normalizar_nombre_producto(registro.get('nombre'))
                if nombre and nombre not in mapa_nombres:
                    mapa_nombres[nombre] = url
    except Exception as error:
        print(f'Error al cargar mapas de imágenes del catálogo: {error}')
    return mapa_codigos, mapa_nombres


def _resolver_url_externa(codigo_barras=None, nombre=None, descripcion=None):
    """Catálogo externo (OpenFoodFacts): solo devuelve URL, sin descargar."""
    try:
        from backend.image_search import obtener_url_imagen_automatica

        return obtener_url_imagen_automatica(
            nombre=nombre or '',
            codigo_barras=codigo_barras,
            descripcion=descripcion,
            modo_rapido=True,
        )
    except Exception as error:
        print(f'Error en resolución externa de imagen: {error}')
        return None


def _resolver_url_imagen(
    imagen_url=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
    mapa_codigos=None,
    mapa_nombres=None,
    incluir_default=False,
):
    """
    1) URL manual (Supabase o https explícita)
    2) Reutilización en PostgreSQL por código
    3) Catálogo externo (código → nombre+descripción → nombre)
    4) Reutilización en PostgreSQL por nombre
    5) default-product.webp (solo si incluir_default=True)
    """
    manual = _url_manual_o_none(imagen_url)
    if manual:
        return manual

    codigo = normalizar_codigo_barras(codigo_barras)
    if codigo:
        if mapa_codigos is not None:
            url = mapa_codigos.get(codigo)
        else:
            url = mapa_imagenes_por_codigos([codigo]).get(codigo)
        if url:
            return url

    url_ext = _resolver_url_externa(
        codigo_barras=codigo_barras,
        nombre=nombre,
        descripcion=descripcion,
    )
    if url_ext:
        return url_ext

    nombre_norm = normalizar_nombre_producto(nombre)
    if nombre_norm:
        if mapa_nombres is not None:
            url = mapa_nombres.get(nombre_norm)
        else:
            url = mapa_imagenes_por_nombres([nombre_norm]).get(nombre_norm)
        if url:
            return url

    if incluir_default:
        return url_imagen_producto_default()
    return None


def imagen_url_para_catalogo(
    imagen_url=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
):
    """Lectura para catálogos: resuelve URL dinámica; default solo al final."""
    url = _resolver_url_imagen(
        imagen_url=imagen_url,
        codigo_barras=codigo_barras,
        nombre=nombre,
        descripcion=descripcion,
        incluir_default=False,
    )
    return url if url else url_imagen_producto_default()


def resolver_imagen_url_definitiva(
    imagen_url=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
):
    """Escritura: persiste URL manual o externa; no guarda default en BD."""
    url = _resolver_url_imagen(
        imagen_url=imagen_url,
        codigo_barras=codigo_barras,
        nombre=nombre,
        descripcion=descripcion,
        incluir_default=False,
    )
    if url and not _es_url_default(url):
        return url
    return None


def normalizar_imagen_registro(
    imagen_url=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
):
    return imagen_url_para_catalogo(
        imagen_url=imagen_url,
        codigo_barras=codigo_barras,
        nombre=nombre,
        descripcion=descripcion,
    )


def obtener_imagen_url_producto(producto_id):
    """Resuelve imagen de un producto usando sus datos en PostgreSQL."""
    if not producto_id:
        return url_imagen_producto_default()
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                '''
                SELECT imagen_url, codigo_barras, nombre, descripcion
                FROM productos WHERE id = ?
                ''',
                (int(producto_id),),
            )
            fila = cursor.fetchone()
            if not fila:
                return url_imagen_producto_default()
            registro = dict(fila)
            return imagen_url_para_catalogo(
                registro.get('imagen_url'),
                codigo_barras=registro.get('codigo_barras'),
                nombre=registro.get('nombre'),
                descripcion=registro.get('descripcion'),
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
    buscar_web=True,
    excluir_url=None,
    persistir=False,
):
    del buscar_web

    if persistir:
        url = resolver_imagen_url_definitiva(
            imagen_url,
            codigo_barras,
            nombre=nombre,
            descripcion=descripcion,
        )
        if url and url != excluir_url:
            return url
        return None

    url = imagen_url_para_catalogo(
        imagen_url,
        codigo_barras,
        nombre=nombre,
        descripcion=descripcion,
    )
    if url and url != excluir_url:
        return url

    if producto_id:
        url_bd = obtener_imagen_url_producto(producto_id)
        if url_bd and url_bd != excluir_url:
            return url_bd

    return url_imagen_producto_default()


def aplicar_respaldo_imagenes(productos, persistir=False):
    """Asigna imagen_url: manual, catálogo PG/externo o default en vista."""
    if not productos:
        return productos

    mapa_codigos, mapa_nombres = _cargar_mapas_catalogo_global()
    default_url = url_imagen_producto_default()

    if persistir:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            for prod in productos:
                producto_id = prod.get('id')
                if not producto_id:
                    continue
                url_final = _resolver_url_imagen(
                    prod.get('imagen_url'),
                    prod.get('codigo_barras'),
                    nombre=prod.get('nombre'),
                    descripcion=prod.get('descripcion'),
                    mapa_codigos=mapa_codigos,
                    mapa_nombres=mapa_nombres,
                    incluir_default=False,
                )
                cursor.execute(
                    'UPDATE productos SET imagen_url = ? WHERE id = ?',
                    (url_final, int(producto_id)),
                )
                prod['imagen_url'] = url_final or default_url
            conexion.commit()
        return productos

    for prod in productos:
        url = _resolver_url_imagen(
            prod.get('imagen_url'),
            prod.get('codigo_barras'),
            nombre=prod.get('nombre'),
            descripcion=prod.get('descripcion'),
            mapa_codigos=mapa_codigos,
            mapa_nombres=mapa_nombres,
            incluir_default=False,
        )
        prod['imagen_url'] = url or default_url

    return productos


def asociar_imagenes_inventario(comercio_id):
    """Tras CSV: persiste URLs manuales o externas (sin default en BD)."""
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

    if not productos:
        return 0

    aplicar_respaldo_imagenes(productos, persistir=True)
    return len(productos)

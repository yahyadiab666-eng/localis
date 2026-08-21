"""Imágenes: resolución en escritura (import/alta); lectura instantánea desde PostgreSQL."""

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


def _url_almacenada_o_none(valor):
    """URL ya persistida en BD (Supabase, wsrv.nl u https explícita)."""
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
    return bool(url and 'default-product' in str(url).lower())


def preparar_url_imagen_persistida(url):
    """
    Formato final para guardar en PostgreSQL.
    Supabase se conserva; URLs externas pasan por wsrv.nl (300×300, webp).
    """
    if not url or _es_url_default(url):
        return None
    if url_imagen_supabase_valida(url):
        return url
    if 'wsrv.nl' in url.lower():
        return url
    try:
        from backend.image_search import optimizar_url_imagen

        return optimizar_url_imagen(url) or url
    except Exception as error:
        print(f'Aviso al optimizar URL de imagen: {error}')
        return url


def _mapa_desde_filas(filas, clave_norm):
    resultado = {}
    for fila in filas or []:
        fila = dict(fila)
        clave = fila.get(clave_norm)
        url = _url_almacenada_o_none(fila.get('imagen_url'))
        if clave and url and clave not in resultado:
            resultado[clave] = url
    return resultado


def mapa_imagenes_por_codigos(codigos):
    """Reutiliza imagen ya persistida en PostgreSQL por código de barras."""
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
    """Reutiliza imagen ya persistida en PostgreSQL por nombre."""
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


def _resolver_url_externa(codigo_barras=None, nombre=None, descripcion=None):
    """OpenFoodFacts: solo en escritura/importación (devuelve URL ya optimizada vía wsrv)."""
    try:
        from backend.image_search import obtener_url_imagen_automatica

        return obtener_url_imagen_automatica(
            nombre=nombre or '',
            codigo_barras=codigo_barras,
            descripcion=descripcion,
            modo_rapido=False,
        )
    except Exception as error:
        print(f'Error en resolución externa de imagen: {error}')
        return None


def _resolver_url_escritura(
    imagen_url=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
    mapa_codigos=None,
    mapa_nombres=None,
):
    """
    Resolución completa solo al crear/importar productos.
    1) URL manual explícita
    2) Reutilización PostgreSQL por código
    3) Catálogo externo (OpenFoodFacts)
    4) Reutilización PostgreSQL por nombre
    """
    manual = _url_almacenada_o_none(imagen_url)
    if manual:
        return preparar_url_imagen_persistida(manual)

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
        return preparar_url_imagen_persistida(url_ext)

    nombre_norm = normalizar_nombre_producto(nombre)
    if nombre_norm:
        if mapa_nombres is not None:
            url = mapa_nombres.get(nombre_norm)
        else:
            url = mapa_imagenes_por_nombres([nombre_norm]).get(nombre_norm)
        if url:
            return url

    return None


def imagen_url_para_catalogo(imagen_url=None, codigo_barras=None, nombre=None, descripcion=None):
    """
    Lectura de catálogo: solo la URL guardada en BD.
    Sin APIs externas ni búsquedas en tiempo real.
    """
    del codigo_barras, nombre, descripcion
    url = _url_almacenada_o_none(imagen_url)
    return url if url else url_imagen_producto_default()


def resolver_imagen_url_definitiva(
    imagen_url=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
    mapa_codigos=None,
    mapa_nombres=None,
):
    """
    Obligatorio en alta manual e importación CSV.
    Persiste URL optimizada; NULL si no hay imagen (default solo en vista).
    """
    return _resolver_url_escritura(
        imagen_url=imagen_url,
        codigo_barras=codigo_barras,
        nombre=nombre,
        descripcion=descripcion,
        mapa_codigos=mapa_codigos,
        mapa_nombres=mapa_nombres,
    )


def normalizar_imagen_registro(imagen_url=None, codigo_barras=None, nombre=None, descripcion=None):
    del codigo_barras, nombre, descripcion
    return imagen_url_para_catalogo(imagen_url=imagen_url)


def obtener_imagen_url_producto(producto_id):
    """Endpoint de respaldo: lee imagen_url de BD sin resolver en runtime."""
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
            return imagen_url_para_catalogo(dict(fila).get('imagen_url'))
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
    del buscar_web

    if persistir:
        url = resolver_imagen_url_definitiva(
            imagen_url,
            codigo_barras,
            nombre=nombre,
            descripcion=descripcion,
        )
        return url if url and url != excluir_url else None

    url = imagen_url_para_catalogo(imagen_url)
    if url and url != excluir_url:
        return url

    if producto_id:
        url_bd = obtener_imagen_url_producto(producto_id)
        if url_bd and url_bd != excluir_url:
            return url_bd

    return url_imagen_producto_default()


def aplicar_respaldo_imagenes(productos, persistir=False):
    """
    persistir=False (catálogo): solo normaliza URL desde BD + default en vista.
    persistir=True (post-import): resuelve y guarda URLs faltantes en BD.
    """
    if not productos:
        return productos

    if persistir:
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
                    url = _url_almacenada_o_none(registro.get('imagen_url'))
                    if not url:
                        continue
                    codigo = normalizar_codigo_barras(registro.get('codigo_barras'))
                    if codigo and codigo not in mapa_codigos:
                        mapa_codigos[codigo] = url
                    nombre = normalizar_nombre_producto(registro.get('nombre'))
                    if nombre and nombre not in mapa_nombres:
                        mapa_nombres[nombre] = url
        except Exception as error:
            print(f'Error al cargar mapas para persistir imágenes: {error}')

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            for prod in productos:
                producto_id = prod.get('id')
                if not producto_id or _url_almacenada_o_none(prod.get('imagen_url')):
                    continue
                url_final = resolver_imagen_url_definitiva(
                    prod.get('imagen_url'),
                    prod.get('codigo_barras'),
                    nombre=prod.get('nombre'),
                    descripcion=prod.get('descripcion'),
                    mapa_codigos=mapa_codigos,
                    mapa_nombres=mapa_nombres,
                )
                if not url_final:
                    continue
                cursor.execute(
                    'UPDATE productos SET imagen_url = ? WHERE id = ?',
                    (url_final, int(producto_id)),
                )
                prod['imagen_url'] = url_final
                codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
                if codigo:
                    mapa_codigos[codigo] = url_final
                nombre = normalizar_nombre_producto(prod.get('nombre'))
                if nombre:
                    mapa_nombres[nombre] = url_final
            conexion.commit()
        return productos

    for prod in productos:
        prod['imagen_url'] = imagen_url_para_catalogo(prod.get('imagen_url'))
    return productos


def asociar_imagenes_inventario(comercio_id):
    """Post-CSV: resuelve solo productos sin imagen_url persistida."""
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

    pendientes = [
        p for p in productos
        if not _url_almacenada_o_none(p.get('imagen_url'))
    ]
    if not pendientes:
        return 0

    aplicar_respaldo_imagenes(pendientes, persistir=True)
    return len(pendientes)

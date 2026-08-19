"""Respaldo inteligente de imágenes por código de barras, SKU o nombre."""

import os
import sqlite3

from backend.db import get_db_connection
from backend.utils import (
    normalizar_codigo_barras,
    normalizar_nombre_producto,
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

_FILTRO_IMAGEN_REAL = """
    imagen_url IS NOT NULL
    AND TRIM(BOTH FROM CAST(imagen_url AS TEXT)) <> ''
    AND imagen_url NOT ILIKE '%%default-product%%'
    AND imagen_url NOT ILIKE '%%placeholder%%'
    AND TRIM(BOTH FROM CAST(imagen_url AS TEXT)) <> '__PENDING__'
    AND (
        CAST(imagen_url AS TEXT) ILIKE 'http%%'
        OR CAST(imagen_url AS TEXT) LIKE '/%%'
    )
"""

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
    """Busca foto local nombrada por código de barras/SKU o por archivo del CSV."""
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


def _mapa_desde_filas(filas, clave_norm):
    resultado = {}
    for fila in filas or []:
        fila = dict(fila)
        clave = fila.get(clave_norm)
        url = _url_usable_o_none(fila.get('imagen_url'))
        if clave and url and clave not in resultado:
            resultado[clave] = url
    return resultado


def mapa_imagenes_por_codigos(codigos):
    """Primera imagen real por código de barras normalizado (ignora espacios y .0)."""
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
    """Respaldo por nombre/SKU textual cuando no hay código de barras."""
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


def buscar_imagen_catalogo(codigo_barras=None, nombre=None, excluir_url=None):
    """Consulta secundaria: catálogo PostgreSQL y luego directorio local."""
    codigo = normalizar_codigo_barras(codigo_barras)
    if codigo:
        url = mapa_imagenes_por_codigos([codigo]).get(codigo)
        if url and url != excluir_url:
            return url
        url_dir = buscar_imagen_en_directorio(codigo)
        if url_dir and url_dir != excluir_url:
            return url_dir

    nombre_norm = normalizar_nombre_producto(nombre)
    if nombre_norm:
        url = mapa_imagenes_por_nombres([nombre_norm]).get(nombre_norm)
        if url and url != excluir_url:
            return url
    return None


def persistir_imagen_rescatada(url, codigo_barras=None, nombre=None):
    """Guarda la URL rescatada en productos que aún no tienen foto real."""
    if not url_imagen_usable(url):
        return 0
    codigo = normalizar_codigo_barras(codigo_barras)
    nombre_norm = normalizar_nombre_producto(nombre)
    if not codigo and not nombre_norm:
        return 0

    actualizados = 0
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            if codigo:
                cursor.execute(
                    f"""
                    UPDATE productos
                    SET imagen_url = ?
                    WHERE {EXPR_CODIGO_BARRAS} = ?
                      AND NOT ({_FILTRO_IMAGEN_REAL})
                    """,
                    (url, codigo),
                )
                actualizados += cursor.rowcount or 0
            elif nombre_norm:
                cursor.execute(
                    f"""
                    UPDATE productos
                    SET imagen_url = ?
                    WHERE {EXPR_NOMBRE} = ?
                      AND NOT ({_FILTRO_IMAGEN_REAL})
                    """,
                    (url, nombre_norm),
                )
                actualizados += cursor.rowcount or 0
            conexion.commit()
    except Exception as error:
        print(f'Error al persistir imagen rescatada: {error}')
    return actualizados


def resolver_imagen_producto(
    imagen_url=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
    buscar_web=False,
    excluir_url=None,
    persistir=False,
):
    """
    1) URL directa usable
    2) Catálogo PostgreSQL / directorio por código o nombre
    3) Búsqueda web (OpenFoodFacts/Bing) si buscar_web=True
    Nunca devuelve la imagen genérica default-product.
    """
    directa = _url_usable_o_none(imagen_url)
    if directa and directa != excluir_url:
        return directa

    if imagen_url and not url_imagen_usable(imagen_url):
        por_archivo = buscar_imagen_en_directorio(imagen_url)
        if por_archivo and por_archivo != excluir_url:
            if persistir:
                persistir_imagen_rescatada(por_archivo, codigo_barras, nombre)
            return por_archivo

    url = buscar_imagen_catalogo(codigo_barras, nombre, excluir_url=excluir_url)
    if url:
        if persistir:
            persistir_imagen_rescatada(url, codigo_barras, nombre)
        return url

    if buscar_web:
        try:
            from backend.image_search import obtener_url_imagen_automatica

            url_web = obtener_url_imagen_automatica(
                nombre=nombre or '',
                codigo_barras=codigo_barras,
                descripcion=descripcion,
                modo_rapido=True,
            )
            if url_imagen_usable(url_web) and url_web != excluir_url:
                if persistir:
                    persistir_imagen_rescatada(url_web, codigo_barras, nombre)
                return url_web
        except Exception as error:
            print(f'Error en búsqueda web de imagen: {error}')

    return None


def aplicar_respaldo_imagenes(productos, persistir=False):
    """Completa imagen_url faltante en un listado, en lote (sin N+1)."""
    if not productos:
        return productos

    pendientes = []
    for prod in productos:
        if url_imagen_usable(prod.get('imagen_url')):
            prod['imagen_url'] = texto_campo_imagen(prod.get('imagen_url'))
            continue
        por_archivo = buscar_imagen_en_directorio(
            prod.get('imagen_url') or prod.get('codigo_barras')
        )
        if por_archivo:
            prod['imagen_url'] = por_archivo
            continue
        pendientes.append(prod)

    if not pendientes:
        return productos

    mapa_codigos = mapa_imagenes_por_codigos(
        [p.get('codigo_barras') for p in pendientes]
    )
    sin_codigo = [
        p for p in pendientes
        if not mapa_codigos.get(normalizar_codigo_barras(p.get('codigo_barras')))
    ]
    mapa_nombres = mapa_imagenes_por_nombres(
        [p.get('nombre') for p in sin_codigo]
    )

    for prod in pendientes:
        codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
        url = mapa_codigos.get(codigo) if codigo else None
        if not url:
            url = mapa_nombres.get(normalizar_nombre_producto(prod.get('nombre')))
        prod['imagen_url'] = url
        if persistir and url:
            persistir_imagen_rescatada(url, prod.get('codigo_barras'), prod.get('nombre'))

    return productos


def asociar_imagenes_inventario(comercio_id):
    """Tras un CSV: reutiliza fotos del catálogo y encola búsqueda web del resto."""
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

    aplicar_respaldo_imagenes(productos, persistir=True)

    pendientes = [
        prod for prod in productos
        if not url_imagen_usable(prod.get('imagen_url'))
    ]
    if pendientes:
        from backend.image_batch import encolar_procesamiento_imagenes

        encolar_procesamiento_imagenes(comercio_id, pendientes)
    return len(productos)

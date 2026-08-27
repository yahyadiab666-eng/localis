"""Imágenes: catálogo maestro + image_manager en escritura; lectura instantánea."""

import sqlite3

from backend.catalogo_maestro import imagen_maestro_por_codigo, mapa_imagenes_maestro
from backend.db import get_db_connection
from backend.image_manager import (
    completar_mapa_imagenes,
    resolver_imagen_catalogo,
    resolver_imagen_escritura,
)
from backend.utils import (
    es_imagen_generica,
    imagen_url_almacenada,
    normalizar_codigo_barras,
    texto_campo_imagen,
)

EXPR_CODIGO_BARRAS = (
    "regexp_replace("
    "regexp_replace(TRIM(BOTH FROM CAST(codigo_barras AS TEXT)), '\\s+', '', 'g'), "
    "'\\.0+$', '', 'g')"
)


def _url_almacenada_o_none(valor):
    """URL ya persistida en PostgreSQL (Supabase, local o https explícita)."""
    return imagen_url_almacenada(valor)


def _resolver_url_escritura(
    imagen_url=None,
    codigo_barras=None,
    mapa_maestro=None,
):
    """Resolución al crear/importar: manual → catálogo maestro → OpenFoodFacts."""
    return resolver_imagen_escritura(
        imagen_manual=imagen_url,
        codigo_barras=codigo_barras,
        mapa_maestro=mapa_maestro,
    )


def imagen_url_para_catalogo(imagen_url=None, codigo_barras=None):
    """URL para catálogo: PostgreSQL → catálogo maestro; None si no hay imagen."""
    url = resolver_imagen_catalogo(
        imagen_url=imagen_url,
        codigo_barras=codigo_barras,
    )
    return url or None


def imagen_urls_para_catalogo(productos):
    """Resuelve imágenes en lote (PostgreSQL → catálogo maestro; sin placeholder)."""
    if not productos:
        return productos

    codigos = []
    vistos = set()
    for prod in productos:
        if _url_almacenada_o_none(prod.get('imagen_url')):
            continue
        codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
        if codigo and codigo not in vistos:
            vistos.add(codigo)
            codigos.append(codigo)

    mapa = mapa_imagenes_maestro(codigos) if codigos else {}

    for prod in productos:
        url = _url_almacenada_o_none(prod.get('imagen_url'))
        if not url:
            url = resolver_imagen_catalogo(
                prod.get('imagen_url'),
                codigo_barras=prod.get('codigo_barras'),
                mapa_maestro=mapa,
            )
        prod['imagen_url'] = url or None

    # Re-consulta puntual al catálogo maestro por códigos que quedaron sin URL.
    codigos_faltantes = []
    vistos_falt = set()
    for prod in productos:
        if prod.get('imagen_url'):
            continue
        codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
        if codigo and codigo not in vistos_falt:
            vistos_falt.add(codigo)
            codigos_faltantes.append(codigo)

    if codigos_faltantes:
        mapa_extra = mapa_imagenes_maestro(codigos_faltantes)
        for prod in productos:
            if prod.get('imagen_url'):
                continue
            codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
            prod['imagen_url'] = (mapa_extra.get(codigo) if codigo else None) or None

    return productos


def resolver_imagen_url_definitiva(
    imagen_url=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
    mapa_codigos=None,
    mapa_nombres=None,
    mapa_maestro=None,
):
    """
    Alta manual e importación CSV.
    Persiste URL del catálogo maestro; NULL si no hay imagen.
    """
    del nombre, descripcion, mapa_codigos, mapa_nombres
    return _resolver_url_escritura(
        imagen_url=imagen_url,
        codigo_barras=codigo_barras,
        mapa_maestro=mapa_maestro,
    )


def normalizar_imagen_registro(imagen_url=None, codigo_barras=None, nombre=None, descripcion=None):
    del nombre, descripcion
    return imagen_url_para_catalogo(imagen_url=imagen_url, codigo_barras=codigo_barras)


def obtener_imagen_url_producto(producto_id):
    """Endpoint de respaldo: lee producto en BD y resuelve imagen sin APIs externas."""
    if not producto_id:
        return None
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT imagen_url, codigo_barras FROM productos WHERE id = ?',
                (int(producto_id),),
            )
            fila = cursor.fetchone()
            if not fila:
                return None
            registro = dict(fila)
            return imagen_url_para_catalogo(
                registro.get('imagen_url'),
                codigo_barras=registro.get('codigo_barras'),
            )
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
    del buscar_web, nombre, descripcion

    if persistir:
        url = resolver_imagen_url_definitiva(
            imagen_url,
            codigo_barras,
        )
        return url if url and url != excluir_url else None

    url = imagen_url_para_catalogo(imagen_url, codigo_barras=codigo_barras)
    if url and url != excluir_url:
        return url

    if producto_id:
        url_bd = obtener_imagen_url_producto(producto_id)
        if url_bd and url_bd != excluir_url:
            return url_bd

    return None


def aplicar_respaldo_imagenes(productos, persistir=False):
    """
    persistir=False (catálogo): PostgreSQL → catálogo maestro (sin placeholder).
    persistir=True (post-import): guarda URLs del catálogo maestro en PostgreSQL.
    """
    if not productos:
        return productos

    if persistir:
        pendientes = [
            p for p in productos
            if not _url_almacenada_o_none(p.get('imagen_url'))
        ]
        codigos = [
            normalizar_codigo_barras(p.get('codigo_barras'))
            for p in pendientes
        ]
        mapa_maestro = completar_mapa_imagenes(
            [c for c in codigos if c],
            buscar_oficial=True,
        )

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            for prod in pendientes:
                producto_id = prod.get('id')
                if not producto_id:
                    continue
                url_final = _resolver_url_escritura(
                    prod.get('imagen_url'),
                    prod.get('codigo_barras'),
                    mapa_maestro=mapa_maestro,
                )
                if not url_final:
                    continue
                cursor.execute(
                    'UPDATE productos SET imagen_url = ? WHERE id = ?',
                    (url_final, int(producto_id)),
                )
                prod['imagen_url'] = url_final
            conexion.commit()
        return productos

    return imagen_urls_para_catalogo(productos)


def asociar_imagenes_inventario(comercio_id):
    """Post-CSV: persiste URLs del catálogo maestro para productos sin imagen_url."""
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

    pendientes = [
        p for p in productos
        if not _url_almacenada_o_none(p.get('imagen_url'))
    ]
    if not pendientes:
        return 0

    aplicar_respaldo_imagenes(pendientes, persistir=True)
    return len(pendientes)

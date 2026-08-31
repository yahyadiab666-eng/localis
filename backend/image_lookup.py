"""Imágenes: catálogo maestro + image_manager en escritura; lectura instantánea."""

import os
import sqlite3
import threading
import time

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
    imagen_url_para_persistir,
    normalizar_codigo_barras,
    texto_campo_imagen,
)

EXPR_CODIGO_BARRAS = (
    "regexp_replace("
    "regexp_replace(TRIM(BOTH FROM CAST(codigo_barras AS TEXT)), '\\s+', '', 'g'), "
    "'\\.0+$', '', 'g')"
)
_LOG_CSV = '[Localis CSV]'
_PRESUPUESTO_OFF_SEG = int(os.getenv('IMPORT_OFF_BUDGET_SEC', '90'))


def _url_almacenada_o_none(valor):
    """URL ya persistida en PostgreSQL (Supabase, local o https explícita)."""
    return imagen_url_almacenada(valor)


def _resolver_url_escritura(
    imagen_url=None,
    codigo_barras=None,
    mapa_maestro=None,
):
    """Resolución al crear/importar: manual → catálogo maestro → OpenFoodFacts."""
    try:
        return resolver_imagen_escritura(
            imagen_manual=imagen_url,
            codigo_barras=codigo_barras,
            mapa_maestro=mapa_maestro,
        )
    except Exception as error:
        print(f'{_LOG_CSV} aviso resolver imagen: {type(error).__name__}')
        return None


def imagen_url_para_catalogo(imagen_url=None, codigo_barras=None):
    """URL para catálogo: PostgreSQL → catálogo maestro; None si no hay imagen."""
    url = resolver_imagen_catalogo(
        imagen_url=imagen_url,
        codigo_barras=codigo_barras,
    )
    return url or None


def imagen_url_para_guardar(imagen_manual=None, codigo_barras=None):
    """
    URL a persistir en productos.imagen_url:
    campo/archivo del formulario, o respaldo por código en catalogo_maestro_imagenes.
    """
    persistida = imagen_url_para_persistir(imagen_manual)
    if persistida:
        return persistida
    try:
        return imagen_maestro_por_codigo(codigo_barras) or None
    except Exception as error:
        print(f'{_LOG_CSV} aviso imagen para guardar: {type(error).__name__}')
        return None


def url_imagen_con_respaldo(imagen_url=None, codigo_barras=None):
    """Vista: imagen del producto o catálogo maestro por código de barras."""
    try:
        directa = _url_almacenada_o_none(imagen_url)
        if directa:
            return directa
        codigo = normalizar_codigo_barras(codigo_barras)
        if not codigo:
            return None
        respaldo = imagen_maestro_por_codigo(codigo)
        if respaldo:
            print(
                f'{_LOG_CSV} respaldo catalogo_maestro codigo={codigo!r} '
                f'url={respaldo!r}'
            )
            return respaldo
        print(
            f'{_LOG_CSV} sin imagen directa ni catalogo_maestro '
            f'codigo={codigo!r}'
        )
        return None
    except Exception as error:
        print(f'{_LOG_CSV} aviso respaldo imagen: {type(error).__name__}: {error}')
        return None


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
            codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
            url = mapa.get(codigo) if codigo else None
            if not url and codigo:
                url = imagen_maestro_por_codigo(codigo)
        prod['imagen_url'] = url or None

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
    try:
        return _resolver_url_escritura(
            imagen_url=imagen_url,
            codigo_barras=codigo_barras,
            mapa_maestro=mapa_maestro,
        )
    except Exception as error:
        print(f'{_LOG_CSV} aviso imagen definitiva: {type(error).__name__}')
        return None


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
            buscar_oficial=False,
        )

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            actualizaciones = []
            for prod in pendientes:
                producto_id = prod.get('id')
                if not producto_id:
                    continue
                url_final = resolver_imagen_escritura(
                    imagen_manual=prod.get('imagen_url'),
                    codigo_barras=prod.get('codigo_barras'),
                    mapa_maestro=mapa_maestro,
                    buscar_oficial=False,
                )
                if not url_final:
                    continue
                actualizaciones.append((url_final, int(producto_id)))
                prod['imagen_url'] = url_final
            if actualizaciones:
                cursor.executemany(
                    'UPDATE productos SET imagen_url = ? WHERE id = ?',
                    actualizaciones,
                )
            conexion.commit()
        return productos

    return imagen_urls_para_catalogo(productos)


def asociar_imagenes_inventario(comercio_id):
    """
    Completa imágenes faltantes tras el CSV (catálogo maestro + OpenFoodFacts).
    Cada producto va en try/except propio; un fallo de red no corta el lote.
    Pensado para correr en segundo plano, no dentro del request de importación.
    """
    try:
        return _asociar_imagenes_inventario(comercio_id)
    except Exception as error:
        print(f'{_LOG_CSV} aviso asociar_imagenes: {type(error).__name__}')
        return 0


def _asociar_imagenes_inventario(comercio_id):
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
        print(f'{_LOG_CSV} Error al leer productos para asociar imágenes: {error}')
        return 0

    pendientes = [
        p for p in productos
        if not _url_almacenada_o_none(p.get('imagen_url'))
    ]
    if not pendientes:
        return 0

    try:
        mapa_maestro = completar_mapa_imagenes(
            [
                normalizar_codigo_barras(p.get('codigo_barras'))
                for p in pendientes
            ],
            buscar_oficial=False,
        )
    except Exception as error:
        print(f'{_LOG_CSV} Error al leer catálogo maestro de imágenes: {error}')
        mapa_maestro = {}

    inicio = time.monotonic()
    actualizaciones = []
    for prod in pendientes:
        producto_id = prod.get('id')
        if not producto_id:
            continue
        if time.monotonic() - inicio > _PRESUPUESTO_OFF_SEG:
            print(
                f'{_LOG_CSV} presupuesto OFF agotado comercio={comercio_id} '
                f'actualizados={len(actualizaciones)} pendientes={len(pendientes)}'
            )
            break
        try:
            url_final = resolver_imagen_escritura(
                imagen_manual=prod.get('imagen_url'),
                codigo_barras=prod.get('codigo_barras'),
                mapa_maestro=mapa_maestro,
                buscar_oficial=True,
            )
            if not url_final:
                continue
            actualizaciones.append((url_final, int(producto_id)))
            prod['imagen_url'] = url_final
        except Exception as error:
            print(
                f'{_LOG_CSV} aviso imagen producto={producto_id}: '
                f'{type(error).__name__}'
            )
            continue

    if not actualizaciones:
        return 0

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.executemany(
                'UPDATE productos SET imagen_url = ? WHERE id = ?',
                actualizaciones,
            )
            conexion.commit()
    except Exception as error:
        print(f'{_LOG_CSV} Error al persistir imágenes post-importación: {error}')
        return 0
    return len(actualizaciones)


def programar_asociacion_imagenes_inventario(comercio_id):
    """Lanza la búsqueda de fotos oficiales en un hilo daemon (no bloquea HTTP)."""
    def _trabajo():
        try:
            print(f'{_LOG_CSV} OFF diferido inicio comercio={comercio_id}')
            actualizados = asociar_imagenes_inventario(comercio_id)
            print(
                f'{_LOG_CSV} OFF diferido fin comercio={comercio_id} '
                f'actualizados={actualizados}'
            )
        except Exception as error:
            print(
                f'{_LOG_CSV} aviso OFF diferido comercio={comercio_id}: '
                f'{type(error).__name__}'
            )

    hilo = threading.Thread(
        target=_trabajo,
        name=f'localis-csv-off-{comercio_id}',
        daemon=True,
    )
    hilo.start()
    return hilo

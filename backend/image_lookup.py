"""Imágenes: catálogo maestro + image_manager en escritura; lectura instantánea."""

import os
import sqlite3
import threading
import time

from backend.catalogo_maestro import (
    imagen_maestro_por_codigo,
)
from backend.db import get_db_connection
from backend.image_manager import (
    completar_mapa_imagenes,
    resolver_imagen_escritura,
)
from backend.utils import (
    EXPR_CODIGO_BARRAS,
    imagen_url_almacenada,
    imagen_url_para_persistir,
    normalizar_codigo_barras,
)

_LOG_CSV = '[Localis CSV]'
_LOG_IMAGEN = '[Localis Imagen]'
_PRESUPUESTO_OFF_SEG = int(os.getenv('IMPORT_OFF_BUDGET_SEC', '90'))


def _registrar_error_imagen(contexto, error):
    print(f'{_LOG_IMAGEN} ERROR {contexto}: {type(error).__name__}: {error}')


def _url_almacenada_o_none(valor):
    """URL de Supabase Storage ya persistida, o None."""
    try:
        return imagen_url_almacenada(valor)
    except Exception as error:
        _registrar_error_imagen('url almacenada', error)
        return None


def _respaldo_en_cascada(codigo_barras):
    """Catálogo maestro (Storage). Vacío si no hay foto en BD."""
    try:
        return imagen_maestro_por_codigo(codigo_barras) or None
    except Exception as error:
        _registrar_error_imagen(f'catalogo_maestro codigo={codigo_barras!r}', error)
        return None


def _resolver_url_escritura(
    imagen_url=None,
    codigo_barras=None,
    mapa_maestro=None,
):
    """Resolución al crear/importar: manual Storage → catálogo maestro."""
    try:
        return resolver_imagen_escritura(
            imagen_manual=imagen_url,
            codigo_barras=codigo_barras,
            mapa_maestro=mapa_maestro,
        )
    except Exception as error:
        _registrar_error_imagen('resolver escritura', error)
        return _respaldo_en_cascada(codigo_barras)


def imagen_url_para_catalogo(imagen_url=None, codigo_barras=None):
    """
    Lectura de catálogo: solo la URL ya persistida (Storage) o placeholder local.
    No consulta catálogo maestro ni red. codigo_barras se ignora en vistas.
    """
    del codigo_barras
    try:
        from utils.images import url_imagen_segura

        return url_imagen_segura(imagen_url)
    except Exception as error:
        _registrar_error_imagen('imagen_url_para_catalogo', error)
        return '/static/img/placeholder-producto.svg'


def imagen_url_para_guardar(imagen_manual=None, codigo_barras=None):
    """
    URL a persistir en productos.imagen_url (solo Storage).
    """
    try:
        persistida = imagen_url_para_persistir(imagen_manual)
        if persistida:
            return persistida
        return _respaldo_en_cascada(codigo_barras)
    except Exception as error:
        _registrar_error_imagen('imagen_url_para_guardar', error)
        return None


def url_imagen_con_respaldo(imagen_url=None, codigo_barras=None):
    """Vista Flask: Storage persistida o placeholder local. Sin red ni maestro."""
    del codigo_barras
    try:
        from utils.images import url_imagen_segura

        return url_imagen_segura(imagen_url)
    except Exception as error:
        _registrar_error_imagen('url_imagen_con_respaldo', error)
        return '/static/img/placeholder-producto.svg'


def imagen_urls_para_catalogo(productos):
    """
    No enriquece con catálogo maestro (eso bloquearía el listado).
    Deja las URLs persistidas; la vista aplica placeholder si faltan.
    """
    if not productos:
        return productos
    try:
        for prod in productos:
            try:
                directa = _url_almacenada_o_none(prod.get('imagen_url'))
                prod['imagen_url'] = directa or (prod.get('imagen_url') or '')
            except Exception as error:
                _registrar_error_imagen(
                    f"lote producto id={prod.get('id')}", error
                )
        return productos
    except Exception as error:
        _registrar_error_imagen('imagen_urls_para_catalogo', error)
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
        _registrar_error_imagen('resolver_imagen_url_definitiva', error)
        return _respaldo_en_cascada(codigo_barras)


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
                f"""
                SELECT COALESCE(
                    NULLIF(TRIM(BOTH FROM CAST(p.imagen_url AS TEXT)), ''),
                    NULLIF(TRIM(BOTH FROM CAST(m.url_imagen AS TEXT)), '')
                ) AS imagen_url,
                       p.codigo_barras
                FROM productos p
                LEFT JOIN LATERAL (
                    SELECT m1.url_imagen
                    FROM catalogo_maestro_imagenes m1
                    WHERE {EXPR_CODIGO_BARRAS.replace('codigo_barras', 'm1.codigo_barras')}
                        = {EXPR_CODIGO_BARRAS.replace('codigo_barras', 'p.codigo_barras')}
                      AND m1.url_imagen IS NOT NULL
                      AND TRIM(BOTH FROM CAST(m1.url_imagen AS TEXT)) <> ''
                    ORDER BY m1.updated_at DESC NULLS LAST
                    LIMIT 1
                ) m ON TRUE
                WHERE p.id = ?
                """,
                (int(producto_id),),
            )
            fila = cursor.fetchone()
            if not fila:
                return None
            registro = dict(fila)
            from utils.images import url_publica_producto_desde_bd

            url = url_publica_producto_desde_bd(registro.get('imagen_url'))
            return _url_almacenada_o_none(url) or ''
    except Exception as error:
        _registrar_error_imagen(f'obtener_imagen_url_producto({producto_id})', error)
        return ''


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

    try:
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
    except Exception as error:
        _registrar_error_imagen('resolver_imagen_producto', error)
        return None


def aplicar_respaldo_imagenes(productos, persistir=False):
    """
    persistir=False (catálogo): PostgreSQL → catálogo maestro (sin placeholder).
    persistir=True (post-import): guarda URLs del catálogo maestro en PostgreSQL.
    """
    if not productos:
        return productos

    try:
        if persistir:
            pendientes = [
                p for p in productos
                if not _url_almacenada_o_none(p.get('imagen_url'))
            ]
            codigos = [
                normalizar_codigo_barras(p.get('codigo_barras'))
                for p in pendientes
            ]
            try:
                mapa_maestro = completar_mapa_imagenes(
                    [c for c in codigos if c],
                    buscar_oficial=False,
                )
            except Exception as error:
                _registrar_error_imagen('completar_mapa_imagenes', error)
                mapa_maestro = {}

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
                        url_final = _respaldo_en_cascada(prod.get('codigo_barras'))
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
    except Exception as error:
        _registrar_error_imagen('aplicar_respaldo_imagenes', error)
        return imagen_urls_para_catalogo(productos)


def asociar_imagenes_inventario(comercio_id):
    """Completa imágenes faltantes tras el CSV (solo catálogo maestro / Storage)."""
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

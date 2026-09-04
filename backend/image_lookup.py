"""
Imágenes de producto: subida manual (Storage/local) y pipeline automático diferido.

- Foto del comerciante: cero llamadas a APIs de pago.
- Sin foto: hilo daemon consulta el pipeline (EAN → nombre) y persiste la URL
  oficial de la API, o un placeholder. El request HTTP no espera esa red.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time

from backend.db import get_db_connection
from backend.utils import (
    imagen_url_almacenada,
    imagen_url_para_persistir,
    normalizar_codigo_barras,
    url_imagen_api_oficial_valida,
    url_imagen_local_valida,
    url_imagen_subida_storage_valida,
)
from services.smart_image_pipeline import (
    PLACEHOLDER_PRODUCTO,
    resolver_imagen_automatica,
    url_catalogo_api_valida,
)

_LOG_CSV = '[Localis CSV]'
_LOG_IMAGEN = '[Localis Imagen]'
_PRESUPUESTO_API_SEG = int(os.getenv('IMPORT_API_IMAGEN_BUDGET_SEC', '60'))
_MAX_CSV_API = int(os.getenv('LOCALIS_CSV_API_MAX', '25'))
_descubrimiento_en_vuelo = set()
_descubrimiento_lock = threading.Lock()


def _registrar_error_imagen(contexto, error):
    print(f'{_LOG_IMAGEN} ERROR {contexto}: {type(error).__name__}: {error}')


def es_imagen_manual(valor):
    return bool(
        url_imagen_subida_storage_valida(valor) or url_imagen_local_valida(valor)
    )


def _url_mostrable_persistida(valor):
    return (
        imagen_url_almacenada(valor)
        or url_imagen_api_oficial_valida(valor)
        or url_catalogo_api_valida(valor)
    )


def imagen_url_para_catalogo(imagen_url=None, codigo_barras=None):
    try:
        from utils.images import url_imagen_producto

        return url_imagen_producto(
            imagen_url=imagen_url,
            codigo_barras=codigo_barras,
        )
    except Exception as error:
        _registrar_error_imagen('imagen_url_para_catalogo', error)
        return PLACEHOLDER_PRODUCTO


def imagen_url_para_guardar(
    imagen_manual=None,
    codigo_barras=None,
    nombre=None,
    categoria=None,
    descripcion=None,
):
    """Solo foto manual. El automático corre diferido, no en el INSERT."""
    del codigo_barras, nombre, categoria, descripcion
    try:
        return imagen_url_para_persistir(imagen_manual)
    except Exception as error:
        _registrar_error_imagen('imagen_url_para_guardar', error)
        return None


def persistir_imagen_producto_hibrida(
    file_storage=None,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
    comercio_id=None,
    imagen_url_form=None,
    existente=None,
):
    """
    Foto del dispositivo → Storage o /static/uploads. Costo de API = 0.
    Sin archivo: conserva Storage/local existente. No consulta APIs.
    """
    del codigo_barras, nombre, descripcion
    aviso = None
    hubo_archivo = bool(file_storage and getattr(file_storage, 'filename', ''))
    if hubo_archivo:
        try:
            from backend.supabase_storage import intentar_subir_imagen

            url_subida, aviso = intentar_subir_imagen(
                file_storage,
                prefijo=f'manual_{comercio_id or "prod"}',
                carpeta='productos',
                max_dimension=720,
            )
        except Exception as error:
            _registrar_error_imagen('hibrido subida producto', error)
            from backend.supabase_storage import AVISO_HIBRIDO_USUARIO

            url_subida = None
            aviso = AVISO_HIBRIDO_USUARIO
        persistida = imagen_url_para_persistir(url_subida)
        if persistida:
            return persistida, aviso
        respaldo = imagen_url_para_persistir(imagen_url_form) or (
            imagen_url_para_persistir(existente)
        )
        return respaldo, aviso

    persistida_form = imagen_url_para_persistir(imagen_url_form)
    if persistida_form:
        return persistida_form, aviso
    if es_imagen_manual(existente):
        return imagen_url_para_persistir(existente), aviso
    return None, aviso


def url_imagen_con_respaldo(imagen_url=None, codigo_barras=None):
    try:
        from utils.images import url_imagen_producto

        return url_imagen_producto(
            imagen_url=imagen_url,
            codigo_barras=codigo_barras,
        )
    except Exception as error:
        _registrar_error_imagen('url_imagen_con_respaldo', error)
        return PLACEHOLDER_PRODUCTO


def imagen_urls_para_catalogo(productos):
    """Lectura: no llama APIs. Enriquece en memoria con la URL persistida o catálogo maestro."""
    if not productos:
        return productos
    try:
        codigos_faltantes = set()
        for prod in productos:
            mostrable = _url_mostrable_persistida(prod.get('imagen_url'))
            if mostrable:
                prod['imagen_url'] = mostrable
            else:
                codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
                if codigo:
                    codigos_faltantes.add(codigo)

        if codigos_faltantes:
            from backend.catalogo_maestro import mapa_imagenes_maestro
            mapa_maestro = mapa_imagenes_maestro(list(codigos_faltantes))
            for prod in productos:
                if not prod.get('imagen_url'):
                    codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
                    if codigo and codigo in mapa_maestro:
                        prod['imagen_url'] = mapa_maestro[codigo]
                    else:
                        prod['imagen_url'] = PLACEHOLDER_PRODUCTO
        else:
            for prod in productos:
                if not prod.get('imagen_url'):
                    prod['imagen_url'] = PLACEHOLDER_PRODUCTO

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
    del codigo_barras, nombre, descripcion, mapa_codigos, mapa_nombres, mapa_maestro
    return imagen_url_para_persistir(imagen_url)


def normalizar_imagen_registro(
    imagen_url=None, codigo_barras=None, nombre=None, descripcion=None
):
    del nombre, descripcion
    return imagen_url_para_catalogo(imagen_url=imagen_url, codigo_barras=codigo_barras)


def obtener_imagen_url_producto(producto_id):
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
        from utils.images import url_publica_producto_desde_bd

        return (
            url_publica_producto_desde_bd(registro.get('imagen_url'))
            or PLACEHOLDER_PRODUCTO
        )
    except Exception as error:
        _registrar_error_imagen(f'obtener_imagen_url_producto({producto_id})', error)
        return PLACEHOLDER_PRODUCTO


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
    del buscar_web, persistir, nombre, descripcion
    try:
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


def preparar_mapa_imagenes_importacion(productos, snapshot_imagenes=None):
    """CSV: solo fotos ya persistidas (manual/Storage). Sin APIs de pago."""
    mapa = dict(snapshot_imagenes or {})
    for prod in productos or []:
        persistida = imagen_url_para_persistir(prod.get('imagen_url'))
        codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
        if persistida and codigo and codigo not in mapa:
            mapa[codigo] = persistida
    return mapa


def _persistir_resultado_pipeline(producto_id, resultado):
    if not producto_id or not resultado or not resultado.url:
        return False
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE productos
                    SET imagen_url = ?, imagen_fuente = ?
                    WHERE id = ?
                      AND (imagen_url IS NULL OR TRIM(CAST(imagen_url AS TEXT)) = '')
                    """,
                    (resultado.url, resultado.fuente, int(producto_id)),
                )
            except Exception:
                conexion.rollback()
                cursor.execute(
                    """
                    UPDATE productos
                    SET imagen_url = ?
                    WHERE id = ?
                      AND (imagen_url IS NULL OR TRIM(CAST(imagen_url AS TEXT)) = '')
                    """,
                    (resultado.url, int(producto_id)),
                )
            conexion.commit()
        return True
    except Exception as error:
        _registrar_error_imagen(f'persistir pipeline id={producto_id}', error)
        return False


def asociar_imagenes_inventario(comercio_id):
    """Tras CSV: rellena huecos con el pipeline de pago (tope de tiempo y de filas)."""
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT id, codigo_barras, imagen_url, nombre, descripcion
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
        if not (p.get('imagen_url') or '').strip()
    ]
    if not pendientes:
        return 0

    inicio = time.monotonic()
    actualizados = 0
    for prod in pendientes[:_MAX_CSV_API]:
        if time.monotonic() - inicio > _PRESUPUESTO_API_SEG:
            print(
                f'{_LOG_CSV} presupuesto API agotado comercio={comercio_id} '
                f'actualizados={actualizados}'
            )
            break
        if (prod.get('imagen_url') or '').strip():
            continue
        try:
            resultado = resolver_imagen_automatica(
                codigo_barras=prod.get('codigo_barras'),
                nombre=prod.get('nombre'),
                descripcion=prod.get('descripcion'),
            )
            if _persistir_resultado_pipeline(prod.get('id'), resultado):
                actualizados += 1
        except Exception as error:
            print(
                f'{_LOG_CSV} aviso imagen producto={prod.get("id")}: '
                f'{type(error).__name__}'
            )
    return actualizados


def programar_asociacion_imagenes_inventario(comercio_id):
    def _trabajo():
        try:
            print(f'{_LOG_CSV} pipeline diferido inicio comercio={comercio_id}')
            actualizados = asociar_imagenes_inventario(comercio_id)
            print(
                f'{_LOG_CSV} pipeline diferido fin comercio={comercio_id} '
                f'actualizados={actualizados}'
            )
        except Exception as error:
            print(
                f'{_LOG_CSV} aviso pipeline diferido comercio={comercio_id}: '
                f'{type(error).__name__}'
            )

    hilo = threading.Thread(
        target=_trabajo,
        name=f'localis-csv-img-{comercio_id}',
        daemon=True,
    )
    hilo.start()
    return hilo


def rellenar_imagenes_catalogo():
    """No-op: el relleno masivo no corre en Gunicorn. Usar alta/edición o CSV."""
    print(f'{_LOG_IMAGEN} relleno masivo desactivado (pago por consumo)')
    return 0


def programar_relleno_imagenes_catalogo():
    print(f'{_LOG_IMAGEN} relleno catalogo desactivado al arrancar')
    return None


def programar_descubrimiento_listado(productos, limite=None):
    """No dispara APIs de pago al listar el catálogo."""
    del productos, limite
    return 0


def programar_descubrimiento_producto(producto_id, categoria=None):
    """Tras el alta: si no hay foto manual, consulta la API en segundo plano."""
    if not producto_id:
        return False
    pid = int(producto_id)
    with _descubrimiento_lock:
        if pid in _descubrimiento_en_vuelo:
            return False
        _descubrimiento_en_vuelo.add(pid)

    def _trabajo():
        try:
            with get_db_connection(row_factory=sqlite3.Row) as conexion:
                cursor = conexion.cursor()
                cursor.execute(
                    """
                    SELECT id, nombre, descripcion, codigo_barras, imagen_url
                    FROM productos
                    WHERE id = ?
                    """,
                    (pid,),
                )
                prod = cursor.fetchone()
            if not prod:
                return
            prod = dict(prod)
            if (prod.get('imagen_url') or '').strip():
                return
            resultado = resolver_imagen_automatica(
                codigo_barras=prod.get('codigo_barras'),
                nombre=prod.get('nombre'),
                descripcion=prod.get('descripcion'),
                categoria=categoria,
            )
            _persistir_resultado_pipeline(pid, resultado)
            print(
                f'{_LOG_IMAGEN} pipeline producto={pid} fuente={resultado.fuente}'
            )
        except Exception as error:
            _registrar_error_imagen(f'descubrimiento producto={pid}', error)
        finally:
            with _descubrimiento_lock:
                _descubrimiento_en_vuelo.discard(pid)

    threading.Thread(
        target=_trabajo,
        name=f'localis-foto-{pid}',
        daemon=True,
    ).start()
    return True

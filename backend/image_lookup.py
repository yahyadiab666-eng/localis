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
    espejar_url_oficial_en_storage,
    resolver_imagen_escritura,
)
from backend.utils import (
    imagen_url_almacenada,
    imagen_url_para_persistir,
    normalizar_codigo_barras,
    url_imagen_local_valida,
    url_imagen_subida_storage_valida,
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
    """Catálogo maestro persistible (Storage/oficial). Vacío si no hay ficha limpia."""
    try:
        return imagen_url_almacenada(imagen_maestro_por_codigo(codigo_barras))
    except Exception as error:
        _registrar_error_imagen(f'catalogo_maestro codigo={codigo_barras!r}', error)
        return None


def _categoria_de_comercio(comercio_id):
    if not comercio_id:
        return None
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT cat.nombre
                FROM comercios c
                LEFT JOIN categorias cat ON cat.id = c.categoria_id
                WHERE c.id = ?
                """,
                (int(comercio_id),),
            )
            fila = cursor.fetchone()
        if not fila:
            return None
        if isinstance(fila, dict):
            return fila.get('nombre')
        return fila[0]
    except Exception as error:
        _registrar_error_imagen('categoria comercio', error)
        return None


def _resolver_url_escritura(
    imagen_url=None,
    codigo_barras=None,
    mapa_maestro=None,
    nombre=None,
    categoria=None,
    descripcion=None,
):
    """Resolución al crear/importar: manual Storage → catálogo maestro → cascada."""
    try:
        return resolver_imagen_escritura(
            imagen_manual=imagen_url,
            codigo_barras=codigo_barras,
            mapa_maestro=mapa_maestro,
            nombre=nombre,
            categoria=categoria,
            descripcion=descripcion,
            buscar_oficial=False,
        )
    except Exception as error:
        _registrar_error_imagen('resolver escritura', error)
        return _respaldo_en_cascada(codigo_barras)


def imagen_url_para_catalogo(imagen_url=None, codigo_barras=None):
    """
    Lectura de catálogo: URL persistida o maestro por EAN.
    Sin OpenFoodFacts. codigo_barras se usa si imagen_url está vacía.
    """
    try:
        from utils.images import url_imagen_producto

        return url_imagen_producto(
            imagen_url=imagen_url,
            codigo_barras=codigo_barras,
        )
    except Exception as error:
        _registrar_error_imagen('imagen_url_para_catalogo', error)
        return '/static/img/placeholder-producto.svg'


def imagen_url_para_guardar(
    imagen_manual=None,
    codigo_barras=None,
    nombre=None,
    categoria=None,
    descripcion=None,
):
    """URL a persistir: Storage, catálogo oficial o maestro. None si no hay foto."""
    try:
        persistida = imagen_url_para_persistir(imagen_manual)
        if persistida:
            return persistida
        return resolver_imagen_escritura(
            imagen_manual=imagen_manual,
            codigo_barras=codigo_barras,
            nombre=nombre,
            categoria=categoria,
            descripcion=descripcion,
            buscar_oficial=False,
        ) or _respaldo_en_cascada(codigo_barras)
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
    Foto del comerciante: comprime y deja URL mostrable de inmediato
    (disco local; Storage se sincroniza en segundo plano si hay service_role).
    Sin archivo: URL del formulario o maestro en BD. Sin OpenFoodFacts en el request.
    """
    del descripcion
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
        respaldo = imagen_url_para_persistir(imagen_url_form) or existente
        return respaldo, aviso

    persistida_form = imagen_url_para_persistir(imagen_url_form)
    if persistida_form:
        return persistida_form, aviso
    if existente:
        return existente, aviso
    maestro = _respaldo_en_cascada(codigo_barras)
    return maestro, aviso


def url_imagen_con_respaldo(imagen_url=None, codigo_barras=None):
    """Vista Flask: URL persistida o maestro por EAN. Sin OpenFoodFacts."""
    try:
        from utils.images import url_imagen_producto

        return url_imagen_producto(
            imagen_url=imagen_url,
            codigo_barras=codigo_barras,
        )
    except Exception as error:
        _registrar_error_imagen('url_imagen_con_respaldo', error)
        return '/static/img/placeholder-producto.svg'


def imagen_urls_para_catalogo(productos):
    """
    Lectura de catálogo: URL persistida o catálogo maestro por EAN (lote, sin red).
    No consulta OpenFoodFacts: eso sigue en el hilo de relleno.
    """
    if not productos:
        return productos
    try:
        from backend.image_manager import completar_mapa_imagenes

        faltantes = []
        vistos = set()
        for prod in productos:
            try:
                directa = _url_almacenada_o_none(prod.get('imagen_url'))
                if directa:
                    prod['imagen_url'] = directa
                    continue
                codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
                if codigo and codigo not in vistos:
                    vistos.add(codigo)
                    faltantes.append(codigo)
            except Exception as error:
                _registrar_error_imagen(
                    f"lote producto id={prod.get('id')}", error
                )
        mapa = {}
        if faltantes:
            try:
                mapa = completar_mapa_imagenes(faltantes, buscar_oficial=False) or {}
            except Exception as error:
                _registrar_error_imagen('lote maestro catalogo', error)
                mapa = {}

        persistir = []
        for prod in productos:
            if _url_almacenada_o_none(prod.get('imagen_url')):
                continue
            codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
            url_mae = mapa.get(codigo) if codigo else None
            if not url_mae:
                continue
            prod['imagen_url'] = url_mae
            producto_id = prod.get('id')
            if producto_id:
                persistir.append((url_mae, int(producto_id)))

        if persistir:
            try:
                with get_db_connection() as conexion:
                    cursor = conexion.cursor()
                    cursor.executemany(
                        """
                        UPDATE productos SET imagen_url = ?
                        WHERE id = ?
                          AND (imagen_url IS NULL OR TRIM(CAST(imagen_url AS TEXT)) = '')
                        """,
                        persistir,
                    )
                    conexion.commit()
            except Exception as error:
                _registrar_error_imagen('persistir maestro catalogo', error)
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
    try:
        return _resolver_url_escritura(
            imagen_url=imagen_url,
            codigo_barras=codigo_barras,
            mapa_maestro=mapa_maestro,
            nombre=nombre,
            descripcion=descripcion,
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
                """
                SELECT imagen_url, codigo_barras FROM productos WHERE id = ?
                """,
                (int(producto_id),),
            )
            fila = cursor.fetchone()
            if not fila:
                return None
            registro = dict(fila)
        from utils.images import url_publica_producto_desde_bd

        url = url_publica_producto_desde_bd(registro.get('imagen_url'))
        if url:
            return _url_almacenada_o_none(url) or url
        codigo = registro.get('codigo_barras')
        maestro = _respaldo_en_cascada(codigo)
        return _url_almacenada_o_none(maestro) or url_publica_producto_desde_bd(maestro) or ''
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
    del buscar_web

    try:
        if persistir:
            url = resolver_imagen_url_definitiva(
                imagen_url,
                codigo_barras,
                nombre=nombre,
                descripcion=descripcion,
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
                        nombre=prod.get('nombre'),
                        descripcion=prod.get('descripcion'),
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
                nombre=prod.get('nombre'),
                descripcion=prod.get('descripcion'),
                categoria=_categoria_de_comercio(comercio_id),
                buscar_oficial=True,
                reproceso_maestro=False,
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


def rellenar_imagenes_catalogo():
    """
    Rellena productos.imagen_url vacías: maestro → EAN → nombre (OFF).
    Devuelve cuántas filas se actualizaron. Pensado para tests e init.
    """
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT p.id, p.nombre, p.descripcion, p.codigo_barras, p.imagen_url,
                       cat.nombre AS categoria_nombre
                FROM productos p
                JOIN comercios c ON c.id = p.comercio_id
                LEFT JOIN categorias cat ON cat.id = c.categoria_id
                """
            )
            productos = [dict(fila) for fila in cursor.fetchall()]
    except Exception as error:
        _registrar_error_imagen('rellenar_imagenes_catalogo leer', error)
        return 0

    pendientes = []
    for prod in productos:
        if url_imagen_subida_storage_valida(prod.get('imagen_url')):
            continue
        if url_imagen_local_valida(prod.get('imagen_url')):
            continue
        pendientes.append(prod)
    if not pendientes:
        return 0

    actualizaciones = []
    for prod in pendientes:
        producto_id = prod.get('id')
        if not producto_id:
            continue
        try:
            actual = imagen_url_almacenada(prod.get('imagen_url'))
            if actual and not url_imagen_subida_storage_valida(actual):
                espejo = espejar_url_oficial_en_storage(
                    actual, prod.get('codigo_barras') or prod.get('nombre')
                )
                if espejo:
                    actualizaciones.append((espejo, int(producto_id)))
                    prod['imagen_url'] = espejo
                    time.sleep(0.15)
                    continue
                continue
            url_final = resolver_imagen_escritura(
                imagen_manual=prod.get('imagen_url'),
                codigo_barras=prod.get('codigo_barras'),
                nombre=prod.get('nombre'),
                descripcion=prod.get('descripcion'),
                categoria=prod.get('categoria_nombre'),
                buscar_oficial=True,
                reproceso_maestro=False,
            )
            if not url_final:
                continue
            actualizaciones.append((url_final, int(producto_id)))
            prod['imagen_url'] = url_final
            time.sleep(0.15)
        except Exception as error:
            _registrar_error_imagen(
                f"relleno id={producto_id}", error
            )

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
        _registrar_error_imagen('rellenar_imagenes_catalogo persistir', error)
        return 0
    print(f'{_LOG_IMAGEN} relleno catalogo actualizados={len(actualizaciones)}')
    return len(actualizaciones)


def programar_relleno_imagenes_catalogo():
    """Rellena fotos faltantes sin bloquear el arranque HTTP."""
    def _trabajo():
        try:
            print(f'{_LOG_IMAGEN} relleno catalogo inicio')
            n = rellenar_imagenes_catalogo()
            print(f'{_LOG_IMAGEN} relleno catalogo fin actualizados={n}')
        except Exception as error:
            _registrar_error_imagen('relleno catalogo hilo', error)

    hilo = threading.Thread(
        target=_trabajo,
        name='localis-relleno-imagenes',
        daemon=True,
    )
    hilo.start()
    return hilo


def programar_descubrimiento_producto(producto_id, categoria=None):
    """Busca foto oficial de un producto recien creado, sin bloquear el alta."""
    if not producto_id:
        return

    def _trabajo():
        try:
            with get_db_connection(row_factory=sqlite3.Row) as conexion:
                cursor = conexion.cursor()
                cursor.execute(
                    """
                    SELECT p.id, p.nombre, p.descripcion, p.codigo_barras, p.imagen_url,
                           cat.nombre AS categoria_nombre
                    FROM productos p
                    JOIN comercios c ON c.id = p.comercio_id
                    LEFT JOIN categorias cat ON cat.id = c.categoria_id
                    WHERE p.id = ?
                    """,
                    (int(producto_id),),
                )
                prod = cursor.fetchone()
            if not prod:
                return
            prod = dict(prod)
            if imagen_url_almacenada(prod.get('imagen_url')):
                return
            url_final = resolver_imagen_escritura(
                imagen_manual=prod.get('imagen_url'),
                codigo_barras=prod.get('codigo_barras'),
                nombre=prod.get('nombre'),
                descripcion=prod.get('descripcion'),
                categoria=categoria or prod.get('categoria_nombre'),
                buscar_oficial=True,
            )
            if not url_final:
                print(f'{_LOG_IMAGEN} descubrimiento producto={producto_id} sin foto')
                return
            with get_db_connection() as conexion:
                cursor = conexion.cursor()
                cursor.execute(
                    """
                    UPDATE productos SET imagen_url = ?
                    WHERE id = ?
                      AND (imagen_url IS NULL OR TRIM(CAST(imagen_url AS TEXT)) = '')
                    """,
                    (url_final, int(producto_id)),
                )
                conexion.commit()
            print(f'{_LOG_IMAGEN} descubrimiento producto={producto_id} ok')
        except Exception as error:
            _registrar_error_imagen(
                f'descubrimiento producto={producto_id}', error
            )

    threading.Thread(
        target=_trabajo,
        name=f'localis-foto-{producto_id}',
        daemon=True,
    ).start()

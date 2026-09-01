import os
import random
import sqlite3
import time
import traceback

import psycopg2

from backend.db import es_error_bd_transitorio, get_db_connection
from backend.runtime_cache import get_or_load, invalidate
from backend.comercio_schema import (
    normalizar_fila_comercio,
    normalizar_filas_comercio,
    sql_set_imagenes,
)
from backend.image_lookup import programar_asociacion_imagenes_inventario
from backend.utils import (
    EXPR_CODIGO_BARRAS,
    imagen_url_para_persistir,
    normalizar_codigo_barras,
    validar_ubicacion_comercio,
)
from backend.inventory_import import (
    LOG_PREFIX as CSV_LOG,
    cargar_archivo_inventario,
    detectar_mapeo_columnas,
    iter_lotes_productos,
    leer_encabezados_inventario,
    mensaje_error_importacion,
    persistir_importacion_por_lotes,
    recortar_mensaje_importacion,
    validar_inventario_previo,
)
from backend.plans import (
    MENSAJE_LIMITE_PRODUCTOS,
    PLAN_GRATIS_CODIGO,
    es_limite_ilimitado,
    limite_para_plan,
    mensaje_limite_importacion,
    obtener_plan_por_codigo,
)
from backend.subscriptions import (
    comercio_puede_gestionar_inventario,
    obtener_limite_productos_comercio,
)

_FILTRO_COMERCIO_PUBLICO = (
    " AND COALESCE(c.visible, 1) = 1"
    " AND LOWER(TRIM(c.estado_pago)) IN ('activo', 'gratis')"
)

_CONFIG_TTL_SEG = 120
_POOL_MUESTRA_ALEATORIA = 400


def _valor_fila(fila, clave, indice=0):
    """Lee columna de fila dict (PostgreSQL) o tupla (consultas sin row_factory)."""
    if isinstance(fila, dict):
        return fila.get(clave)
    return fila[indice]


def _cargar_tasa_dolar_db():
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                "SELECT valor FROM configuracion_sistema WHERE clave = 'tasa_dolar'"
            )
            fila = cursor.fetchone()
            return float(_valor_fila(fila, 'valor', 0)) if fila else 36.50
    except Exception:
        return 36.50


def obtener_tasa_dolar():
    return get_or_load('config:tasa_dolar', _cargar_tasa_dolar_db, _CONFIG_TTL_SEG)


def _cargar_config_map_db(claves):
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            placeholders = ', '.join('?' for _ in claves)
            cursor.execute(
                f'SELECT clave, valor FROM configuracion_sistema WHERE clave IN ({placeholders})',
                tuple(claves),
            )
            return {
                _valor_fila(fila, 'clave', 0): _valor_fila(fila, 'valor', 1)
                for fila in cursor.fetchall()
            }
    except Exception:
        return {}


def obtener_configs(claves_defaults):
    """
    Lee varias claves en una sola consulta.
    claves_defaults: dict clave → valor por defecto.
    """
    claves = list(claves_defaults.keys())
    cache_key = 'config:batch:' + ','.join(sorted(claves))

    def loader():
        encontrados = _cargar_config_map_db(claves)
        return {
            clave: encontrados.get(clave, default)
            for clave, default in claves_defaults.items()
        }

    return get_or_load(cache_key, loader, _CONFIG_TTL_SEG)


def obtener_config(clave, default=None):
    return obtener_configs({clave: default}).get(clave, default)


def invalidar_cache_configuracion():
    """Llamar tras actualizar configuracion_sistema desde admin."""
    invalidate('config:')


def _construir_filtro_palabras(palabra_clave):
    """Genera condiciones LIKE por cada palabra del término de búsqueda."""
    if not palabra_clave:
        return '', []

    palabras = [p.strip() for p in palabra_clave.split() if p.strip()]
    if not palabras:
        return '', []

    condiciones = []
    parametros = []
    campos = (
        'p.nombre', 'p.descripcion', 'c.nombre', 'c.ciudad', 'c.zona', 'c.direccion'
    )

    for palabra in palabras:
        like = f'%{palabra}%'
        sub = ' OR '.join(f'{campo} ILIKE ?' for campo in campos)
        codigo_palabra = normalizar_codigo_barras(palabra) or palabra
        sub += f' OR {EXPR_CODIGO_BARRAS} ILIKE ?'
        condiciones.append(f'({sub})')
        parametros.extend([like] * len(campos))
        parametros.append(f'%{codigo_palabra}%')

    filtro = ' AND '.join(condiciones)
    codigo_completo = normalizar_codigo_barras(palabra_clave)
    if codigo_completo:
        filtro = f'(({filtro}) OR {EXPR_CODIGO_BARRAS} = ?)'
        parametros.append(codigo_completo)
    return filtro, parametros


# ==========================================
# GESTIÓN DE COMERCIOS
# ==========================================


def registrar_comercio_completo(
    usuario_id,
    nombre,
    descripcion,
    telefono,
    direccion,
    categoria_id,
    logo_url=None,
    ciudad=None,
    zona=None,
    maps_url=None,
    documento_identidad=None,
):
    plan_gratis = obtener_plan_por_codigo(PLAN_GRATIS_CODIGO)
    plan_id = plan_gratis.get('id')
    limite = plan_gratis.get('limite_productos') or 50

    if not plan_id:
        return False, (
            'El plan gratuito no está configurado en el sistema. '
            'Contacta soporte o reinicia la aplicación para aplicar migraciones.'
        )

    ok_ubicacion, datos_ubicacion = validar_ubicacion_comercio(
        direccion, ciudad=ciudad, zona=zona, maps_url=maps_url
    )
    if not ok_ubicacion:
        return False, datos_ubicacion

    direccion = datos_ubicacion['direccion']
    ciudad = datos_ubicacion['ciudad']
    zona = datos_ubicacion['zona']
    maps_url = datos_ubicacion['maps_url']
    ubicacion_maps_url = datos_ubicacion['ubicacion_maps_url']

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            cursor.execute(
                'SELECT id FROM categorias WHERE id = ?',
                (int(categoria_id),),
            )
            if not cursor.fetchone():
                return False, 'La categoría seleccionada no es válida.'

            cursor.execute(
                """
                INSERT INTO comercios (
                    usuario_id, nombre, descripcion, telefono, direccion,
                    categoria_id, ciudad, zona, maps_url,
                    ubicacion_maps_url, documento_identidad, plan_id, plan_tipo,
                    limite_productos, estado_pago, fecha_inicio_suscripcion,
                    fecha_vencimiento
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'activo',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + INTERVAL '30 days')
                RETURNING id
                """,
                (
                    usuario_id,
                    nombre,
                    descripcion,
                    telefono,
                    direccion,
                    categoria_id,
                    ciudad,
                    zona,
                    maps_url,
                    ubicacion_maps_url,
                    documento_identidad,
                    plan_id,
                    PLAN_GRATIS_CODIGO,
                    limite,
                ),
            )
            fila_id = cursor.fetchone()
            if isinstance(fila_id, dict):
                nuevo_id = fila_id.get('id')
            else:
                nuevo_id = fila_id[0] if fila_id else None
            if logo_url and nuevo_id:
                fragmentos, extra = sql_set_imagenes(cursor, logo_url=logo_url)
                if fragmentos:
                    extra.append(nuevo_id)
                    cursor.execute(
                        f"UPDATE comercios SET {', '.join(fragmentos)} WHERE id = ?",
                        tuple(extra),
                    )

            conexion.commit()
            return True, 'Comercio registrado con éxito.'

    except psycopg2.IntegrityError as e:
        return False, f'Error: Integridad referencial violada. Detalle: {e}'
    except Exception as e:
        return False, f'Error al registrar: {str(e)}'


def actualizar_datos_comercio(
    comercio_id,
    nombre,
    telefono,
    direccion,
    descripcion=None,
    ciudad=None,
    zona=None,
    maps_url=None,
    logo_url=None,
    banner_url=None,
):
    ok_ubicacion, datos_ubicacion = validar_ubicacion_comercio(
        direccion, ciudad=ciudad, zona=zona, maps_url=maps_url
    )
    if not ok_ubicacion:
        return False, datos_ubicacion

    direccion = datos_ubicacion['direccion']
    ciudad = datos_ubicacion['ciudad']
    zona = datos_ubicacion['zona']
    maps_url = datos_ubicacion['maps_url']
    ubicacion_maps_url = datos_ubicacion['ubicacion_maps_url']

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            campos = [
                'nombre = ?',
                'telefono = ?',
                'direccion = ?',
                'descripcion = ?',
                'ciudad = ?',
                'zona = ?',
                'maps_url = ?',
                'ubicacion_maps_url = ?',
            ]
            valores = [
                nombre,
                telefono,
                direccion,
                descripcion,
                ciudad,
                zona,
                maps_url,
                ubicacion_maps_url,
            ]

            if logo_url:
                extra, extra_vals = sql_set_imagenes(cursor, logo_url=logo_url)
                campos.extend(extra)
                valores.extend(extra_vals)

            if banner_url:
                extra, extra_vals = sql_set_imagenes(cursor, banner_url=banner_url)
                campos.extend(extra)
                valores.extend(extra_vals)

            valores.append(comercio_id)
            cursor.execute(
                f"""
                UPDATE comercios
                SET {', '.join(campos)}
                WHERE id = ?
                """,
                tuple(valores),
            )

            conexion.commit()
            return cursor.rowcount > 0, 'Datos del comercio actualizados.'
    except Exception as e:
        return False, f'Error al actualizar comercio: {str(e)}'


def obtener_comercio_por_id(comercio_id, solo_visible=True):
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()

            query = """
                SELECT c.*, cat.nombre AS categoria_nombre
                FROM comercios c
                LEFT JOIN categorias cat ON c.categoria_id = cat.id
                WHERE c.id = ?
            """
            if solo_visible:
                query += _FILTRO_COMERCIO_PUBLICO

            cursor.execute(query, (comercio_id,))
            fila = cursor.fetchone()
            return normalizar_fila_comercio(dict(fila)) if fila else None
    except Exception as e:
        print(f'Error al obtener comercio: {e}')
        return None


def listar_comercios_por_usuario(usuario_id):
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT c.*, cat.nombre AS categoria
                FROM comercios c
                LEFT JOIN categorias cat ON c.categoria_id = cat.id
                WHERE c.usuario_id = ?
                ORDER BY c.nombre ASC
                """,
                (usuario_id,),
            )
            return normalizar_filas_comercio([dict(fila) for fila in cursor.fetchall()])
    except psycopg2.Error:
        raise
    except Exception as error:
        print(f'Error al listar comercios del usuario {usuario_id}: {error}')
        raise


def usuario_posee_comercio(usuario_id, comercio_id):
    if not usuario_id or not comercio_id:
        return False
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT 1 FROM comercios WHERE id = ? AND usuario_id = ? LIMIT 1',
                (int(comercio_id), int(usuario_id)),
            )
            return cursor.fetchone() is not None
    except psycopg2.Error:
        raise
    except Exception:
        return False


def obtener_comercio_por_usuario(usuario_id, comercio_id=None):
    """Obtiene un comercio del usuario; si comercio_id se omite, retorna el primero."""
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            if comercio_id is not None:
                cursor.execute(
                    'SELECT * FROM comercios WHERE id = ? AND usuario_id = ?',
                    (int(comercio_id), int(usuario_id)),
                )
            else:
                cursor.execute(
                    'SELECT * FROM comercios WHERE usuario_id = ? ORDER BY id ASC LIMIT 1',
                    (usuario_id,),
                )
            fila = cursor.fetchone()
            return normalizar_fila_comercio(dict(fila)) if fila else None
    except psycopg2.Error:
        raise
    except Exception as error:
        print(f'Error al obtener comercio por usuario {usuario_id}: {error}')
        raise


def buscar_y_filtrar_comercios(
    palabra_clave=None, categoria_id=None
):
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()

            query = """
                SELECT c.*, cat.nombre AS categoria_nombre
                FROM comercios c
                LEFT JOIN categorias cat ON c.categoria_id = cat.id
                WHERE 1=1
            """ + _FILTRO_COMERCIO_PUBLICO
            parametros = []

            if palabra_clave:
                query += ' AND (c.nombre ILIKE ? OR c.descripcion ILIKE ? OR c.ciudad ILIKE ? OR c.zona ILIKE ?)'
                like = f'%{palabra_clave}%'
                parametros.extend([like, like, like, like])

            if categoria_id:
                query += ' AND c.categoria_id = ?'
                parametros.append(categoria_id)

            cursor.execute(query, parametros)
            return normalizar_filas_comercio([dict(f) for f in cursor.fetchall()])
    except Exception as e:
        print(f'Error al filtrar comercios: {e}')
        return []


# ==========================================
# GESTIÓN DE PRODUCTOS
# ==========================================


_SQL_IMAGEN_URL = 'p.imagen_url AS imagen_url'


def _aplicar_url_imagen_producto(fila):
    """Convierte ruta/URL de Storage o catálogo oficial; deja None si no hay foto."""
    from utils.images import url_publica_producto_desde_bd

    crudo = fila.get('imagen_url')
    if crudo is None:
        crudo = fila.get('url_imagen')
    url = url_publica_producto_desde_bd(crudo)
    fila['imagen_url'] = url or None
    return fila


def _completar_imagenes_productos(productos):
    """
    Rellena imagen_url desde el catálogo maestro en una consulta aparte
    (no comparte transacción con el JOIN comercios/productos) y persiste
    las URLs encontradas para que url_bd deje de ser None.
    """
    if not productos:
        return productos
    for fila in productos:
        _aplicar_url_imagen_producto(fila)

    faltantes = []
    for fila in productos:
        if fila.get('imagen_url'):
            continue
        faltantes.append(fila)
    if not faltantes:
        return productos

    try:
        from backend.catalogo_maestro import mapa_imagenes_maestro

        codigos = [
            normalizar_codigo_barras(fila.get('codigo_barras'))
            for fila in faltantes
        ]
        mapa = mapa_imagenes_maestro([c for c in codigos if c]) or {}
    except Exception as error:
        print(f'[Localis Imagen] Lote maestro omitido: {type(error).__name__}: {error}')
        traceback.print_exc()
        mapa = {}

    from backend.utils import url_imagen_catalogo_valida
    from utils.images import url_publica_producto_desde_bd

    persistir = []
    for fila in faltantes:
        codigo = normalizar_codigo_barras(fila.get('codigo_barras'))
        url = url_publica_producto_desde_bd(mapa.get(codigo)) if codigo else ''
        if not url:
            url = url_imagen_catalogo_valida(mapa.get(codigo)) if codigo else None
        if url:
            fila['imagen_url'] = url
            producto_id = fila.get('id')
            if producto_id:
                persistir.append((url, int(producto_id)))

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
            print(
                f'[Localis Imagen] No se persistieron URLs de maestro: '
                f'{type(error).__name__}: {error}'
            )
            traceback.print_exc()
    return productos


def _base_query_productos_publicos(con_maestro=True):
    del con_maestro
    # Padre (comercios) primero, luego productos: mismo orden de bloqueo que
    # las escrituras con FK. El maestro NO entra en este SELECT.
    return f"""
        SELECT
            p.id,
            p.comercio_id,
            p.nombre,
            p.descripcion,
            p.precio_usd,
            p.codigo_barras,
            {_SQL_IMAGEN_URL},
            c.nombre AS comercio_nombre,
            c.telefono AS comercio_telefono,
            c.id AS comercio_id,
            cat.nombre AS categoria_nombre
        FROM comercios c
        JOIN productos p ON p.comercio_id = c.id
        LEFT JOIN categorias cat ON c.categoria_id = cat.id
        WHERE 1=1
    """ + _FILTRO_COMERCIO_PUBLICO


def _es_columna_inexistente(error):
    orig = getattr(error, 'orig', error)
    texto = str(error).lower()
    return (
        'UndefinedColumn' in type(orig).__name__
        or 'undefinedcolumn' in type(error).__name__.lower()
        or 'does not exist' in texto
    )


def _ejecutar_listado_productos(cursor, conexion, query, parametros):
    """Ejecuta el SELECT; si falta una columna del maestro, reintenta sin ese JOIN."""
    try:
        cursor.execute(query, parametros)
        return cursor.fetchall()
    except Exception as error:
        if es_error_bd_transitorio(error) or _es_deadlock(error):
            try:
                conexion.rollback()
            except Exception:
                pass
            raise
        if not _es_columna_inexistente(error):
            try:
                conexion.rollback()
            except Exception:
                pass
            raise
        print(
            f'[Localis Imagen] Consulta con catalogo_maestro_imagenes falló '
            f'({type(error).__name__}: {error}). Se reintenta con p.imagen_url.'
        )
        try:
            conexion.rollback()
        except Exception:
            pass
        query_simple = query.replace(_SQL_IMAGEN_URL, 'p.imagen_url AS imagen_url')
        cursor.execute(query_simple, parametros)
        return cursor.fetchall()


def _es_deadlock(error):
    orig = getattr(error, 'orig', error)
    pgcode = getattr(orig, 'pgcode', None) or getattr(error, 'pgcode', None)
    if pgcode in ('40P01', '40001'):
        return True
    texto = f'{type(orig).__name__} {error}'.lower()
    return 'deadlock' in texto


def _aplicar_filtros_productos(query, parametros, palabra_clave, categoria_nombre, comercio_id):
    if comercio_id:
        query += ' AND p.comercio_id = ?'
        parametros.append(comercio_id)

    filtro_palabras, params_palabras = _construir_filtro_palabras(palabra_clave)
    if filtro_palabras:
        query += f' AND {filtro_palabras}'
        parametros.extend(params_palabras)

    if categoria_nombre:
        query += ' AND cat.nombre = ?'
        parametros.append(categoria_nombre)

    return query, parametros


_MAX_REINTENTOS_LISTADO = 3


def buscar_y_filtrar_productos(
    palabra_clave=None,
    categoria_nombre=None,
    comercio_id=None,
    limit=None,
    orden_aleatorio=False,
):
    ultimo_error = None
    for intento in range(_MAX_REINTENTOS_LISTADO):
        try:
            return _completar_imagenes_productos(
                _buscar_y_filtrar_productos_once(
                    palabra_clave=palabra_clave,
                    categoria_nombre=categoria_nombre,
                    comercio_id=comercio_id,
                    limit=limit,
                    orden_aleatorio=orden_aleatorio,
                )
            )
        except Exception as e:
            ultimo_error = e
            if intento < _MAX_REINTENTOS_LISTADO - 1 and (
                es_error_bd_transitorio(e) or _es_deadlock(e)
            ):
                print(
                    f'[Localis] Deadlock/transitorio en listado de productos '
                    f'(intento {intento + 1}/{_MAX_REINTENTOS_LISTADO}): {e}'
                )
                time.sleep(0.05 * (intento + 1))
                continue
            print(f'Error en la búsqueda de productos: {e}')
            traceback.print_exc()
            return []
    print(f'Error en la búsqueda de productos: {ultimo_error}')
    return []


def _buscar_y_filtrar_productos_once(
    palabra_clave=None,
    categoria_nombre=None,
    comercio_id=None,
    limit=None,
    orden_aleatorio=False,
):
    tasa = obtener_tasa_dolar() or 1.0
    with get_db_connection(row_factory=sqlite3.Row) as conexion:
        cursor = conexion.cursor()

        parametros = []
        if orden_aleatorio and limit and not palabra_clave and not categoria_nombre:
            query_ids = """
                SELECT p.id
                FROM comercios c
                JOIN productos p ON p.comercio_id = c.id
                LEFT JOIN categorias cat ON c.categoria_id = cat.id
                WHERE 1=1
            """ + _FILTRO_COMERCIO_PUBLICO
            query_ids, params_ids = _aplicar_filtros_productos(
                query_ids, [], palabra_clave, categoria_nombre, comercio_id
            )
            pool_size = max(int(limit) * 10, min(_POOL_MUESTRA_ALEATORIA, 400))
            query_ids += ' ORDER BY p.id DESC LIMIT ?'
            params_ids.append(pool_size)
            cursor.execute(query_ids, params_ids)
            ids = [
                _valor_fila(fila, 'id', 0)
                for fila in cursor.fetchall()
                if _valor_fila(fila, 'id', 0) is not None
            ]
            conexion.commit()
            if not ids:
                return []
            ids = random.sample(ids, min(int(limit), len(ids)))
            placeholders = ', '.join('?' for _ in ids)
            query = _base_query_productos_publicos()
            query += f' AND p.id IN ({placeholders})'
            filas = _ejecutar_listado_productos(cursor, conexion, query, ids)
        else:
            query = _base_query_productos_publicos()
            query, parametros = _aplicar_filtros_productos(
                query, parametros, palabra_clave, categoria_nombre, comercio_id
            )
            query += ' ORDER BY p.id DESC'
            if limit:
                query += ' LIMIT ?'
                parametros.append(int(limit))
            filas = _ejecutar_listado_productos(cursor, conexion, query, parametros)

        productos = []
        for fila in filas:
            d = dict(fila) if not isinstance(fila, dict) else fila
            try:
                precio = float(d.get('precio_usd') or 0)
            except (TypeError, ValueError):
                precio = 0.0
            d['precio_usd'] = precio
            d['precio_bs'] = round(precio * tasa, 2)
            productos.append(d)

        return productos


def obtener_producto_publico(producto_id):
    """Producto con datos de tienda para modal/vista pública."""
    try:
        tasa = obtener_tasa_dolar() or 1.0
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                f"""
                SELECT
                    p.id,
                    p.comercio_id,
                    p.nombre,
                    p.descripcion,
                    p.precio_usd,
                    p.codigo_barras,
                    {_SQL_IMAGEN_URL},
                    c.nombre AS comercio_nombre,
                    c.id AS comercio_id,
                    c.telefono AS comercio_telefono
                FROM comercios c
                JOIN productos p ON p.comercio_id = c.id
                WHERE p.id = ? AND COALESCE(c.visible, 1) = 1
                  AND LOWER(TRIM(c.estado_pago)) IN ('activo', 'gratis')
                """,
                (producto_id,),
            )
            fila = cursor.fetchone()
            if not fila:
                return None
            d = dict(fila)
            try:
                precio = float(d.get('precio_usd') or 0)
            except (TypeError, ValueError):
                precio = 0.0
            d['precio_usd'] = precio
            d['precio_bs'] = round(precio * tasa, 2)
        _completar_imagenes_productos([d])
        return d
    except Exception as e:
        print(f'Error al obtener producto público: {e}')
        return None


def obtener_producto_por_id(producto_id, comercio_id=None):
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()

            if comercio_id:
                cursor.execute(
                    f"""
                    SELECT
                        p.id,
                        p.comercio_id,
                        p.nombre,
                        p.descripcion,
                        p.precio_usd,
                        p.codigo_barras,
                        {_SQL_IMAGEN_URL}
                    FROM productos p
                    WHERE p.id = ? AND p.comercio_id = ?
                    """,
                    (producto_id, comercio_id),
                )
            else:
                cursor.execute(
                    f"""
                    SELECT
                        p.id,
                        p.comercio_id,
                        p.nombre,
                        p.descripcion,
                        p.precio_usd,
                        p.codigo_barras,
                        {_SQL_IMAGEN_URL}
                    FROM productos p
                    WHERE p.id = ?
                    """,
                    (producto_id,),
                )

            fila = cursor.fetchone()
            if not fila:
                return None
            producto = dict(fila)
        _completar_imagenes_productos([producto])
        return producto
    except Exception as e:
        print(f'Error al obtener producto: {e}')
        return None


def obtener_productos_comercio(comercio_id):
    """Inventario del panel comerciante: imagen_url explícita + catálogo maestro."""
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                f"""
                SELECT
                    p.id,
                    p.nombre,
                    p.descripcion,
                    p.precio_usd,
                    p.codigo_barras,
                    {_SQL_IMAGEN_URL}
                FROM productos p
                WHERE p.comercio_id = ?
                ORDER BY p.id DESC
                """,
                (comercio_id,),
            )
            productos = []
            for fila in cursor.fetchall():
                d = dict(fila) if not isinstance(fila, dict) else fila
                productos.append(d)
        _completar_imagenes_productos(productos)
        return productos
    except Exception as e:
        print(f'Error al listar productos del comercio: {e}')
        traceback.print_exc()
        return []


def actualizar_producto(
    producto_id,
    comercio_id,
    nombre,
    descripcion,
    precio_usd,
    codigo_barras=None,
    imagen_url=None,
    *,
    incluir_imagen=False,
):
    """
    Actualización parcial segura: imagen_url solo se incluye en el UPDATE
    cuando incluir_imagen=True y hay una URL explícita nueva.
    """
    try:
        codigo_normalizado = normalizar_codigo_barras(codigo_barras)
        campos = {
            'nombre': nombre,
            'descripcion': descripcion,
            'precio_usd': float(precio_usd),
            'codigo_barras': codigo_normalizado,
        }
        if incluir_imagen:
            imagen_nueva = imagen_url_para_persistir(imagen_url)
            if imagen_url and not imagen_nueva:
                return (
                    False,
                    'La imagen se procesó pero la URL no es válida para guardar en la base de datos.',
                )
            if not imagen_nueva:
                return (
                    False,
                    'No se pudo subir la imagen del producto a Supabase Storage.',
                )
            campos['imagen_url'] = imagen_nueva

        set_sql = ', '.join(f'{columna} = ?' for columna in campos)
        valores = list(campos.values()) + [producto_id, comercio_id]

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                f"""
                UPDATE productos
                SET {set_sql}
                WHERE id = ? AND comercio_id = ?
                """,
                tuple(valores),
            )
            filas_afectadas = cursor.rowcount
            conexion.commit()
            if filas_afectadas > 0:
                return True, 'Producto actualizado correctamente.'
            return (
                False,
                'No se encontró el producto o no tienes permiso para modificarlo.',
            )
    except Exception as e:
        return False, f'Error al actualizar producto: {str(e)}'


def eliminar_producto(producto_id, comercio_id):
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'DELETE FROM productos WHERE id = ? AND comercio_id = ?',
                (producto_id, comercio_id),
            )
            filas_afectadas = cursor.rowcount
            conexion.commit()
            if filas_afectadas > 0:
                return True, 'Producto eliminado con éxito.'
            return (
                False,
                'No se encontró el producto o no tienes permiso para eliminarlo.',
            )
    except Exception as e:
        return False, f'Error al eliminar el producto: {str(e)}'


def procesar_csv_productos(comercio_id, archivo_csv):
    """
    Importador inteligente con reemplazo total del inventario.
    Procesa por lotes en memoria acotada y transacciones PostgreSQL seguras.
    """
    etapa = 'inicio'
    nombre_archivo = getattr(archivo_csv, 'filename', None)
    print(f'{CSV_LOG} inicio comercio={comercio_id} archivo={nombre_archivo!r}')
    try:
        etapa = 'leer_archivo'
        data, extension, error_lectura = cargar_archivo_inventario(archivo_csv)
        if error_lectura:
            print(f'{CSV_LOG} rechazo etapa={etapa}: {error_lectura}')
            return False, recortar_mensaje_importacion(error_lectura), None

        etapa = 'encabezados'
        encabezados, error_enc = leer_encabezados_inventario(data, extension)
        if error_enc:
            print(f'{CSV_LOG} rechazo etapa={etapa}: {error_enc}')
            return False, recortar_mensaje_importacion(error_enc), None

        etapa = 'mapeo_columnas'
        mapeo, meta, error_mapeo = detectar_mapeo_columnas(encabezados)
        if error_mapeo:
            print(f'{CSV_LOG} rechazo etapa={etapa}: {error_mapeo}')
            return False, recortar_mensaje_importacion(error_mapeo), None

        etapa = 'permiso_inventario'
        ok, msg = comercio_puede_gestionar_inventario(comercio_id)
        if not ok:
            print(f'{CSV_LOG} rechazo etapa={etapa}: {msg}')
            return False, recortar_mensaje_importacion(msg), None

        etapa = 'tasa_dolar'
        tasa = float(obtener_tasa_dolar() or 1.0)

        etapa = 'validacion'
        valido, error_validacion, meta_validacion = validar_inventario_previo(
            data,
            extension,
            encabezados,
            mapeo,
            meta,
            tasa_dolar=tasa,
        )
        if not valido:
            print(f'{CSV_LOG} rechazo etapa={etapa}: {error_validacion}')
            return False, recortar_mensaje_importacion(error_validacion), None

        etapa = 'limite_plan'
        total_validos = (meta_validacion or {}).get('filas_validas', 0)
        limite = obtener_limite_productos_comercio(comercio_id)
        if not es_limite_ilimitado(limite) and total_validos > limite:
            with get_db_connection(row_factory=sqlite3.Row) as conexion:
                cursor = conexion.cursor()
                cursor.execute(
                    'SELECT plan_tipo FROM comercios WHERE id = ?',
                    (int(comercio_id),),
                )
                fila = cursor.fetchone()
            plan_tipo = (fila['plan_tipo'] if fila else 'gratis') or 'gratis'
            mensaje, plan_sugerido = mensaje_limite_importacion(
                plan_tipo, total_validos, limite
            )
            return False, recortar_mensaje_importacion(mensaje), {
                'plan_sugerido': plan_sugerido
            }

        def _generador_lotes():
            return iter_lotes_productos(
                data,
                extension,
                encabezados,
                mapeo,
                meta,
                tasa_dolar=tasa,
                imagen_default=None,
            )

        etapa = 'persistir'
        insertados = persistir_importacion_por_lotes(comercio_id, _generador_lotes)

        etapa = 'asociar_imagenes'
        try:
            programar_asociacion_imagenes_inventario(comercio_id)
        except Exception as exc_img:
            print(
                f'{CSV_LOG} aviso etapa={etapa} {type(exc_img).__name__}: {exc_img} '
                '(el inventario ya se guardó; las fotos oficiales se omiten)'
            )
            traceback.print_exc()

        print(f'{CSV_LOG} ok comercio={comercio_id} insertados={insertados}')
        return (
            True,
            f'Importación completada: {insertados} productos cargados. '
            'Columnas reconocidas automáticamente desde la primera fila. '
            'Las fotos oficiales se completarán en segundo plano.',
            None,
        )

    except Exception as exc:
        print(f'{CSV_LOG} FALLO etapa={etapa} {type(exc).__name__}: {exc}')
        traceback.print_exc()
        return False, recortar_mensaje_importacion(mensaje_error_importacion(exc)), None

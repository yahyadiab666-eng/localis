import os
import sqlite3

import psycopg2

from backend.db import get_db_connection
from backend.image_lookup import EXPR_CODIGO_BARRAS, aplicar_respaldo_imagenes, asociar_imagenes_inventario
from backend.utils import normalizar_codigo_barras
from backend.inventory_import import (
    cargar_archivo_inventario,
    detectar_mapeo_columnas,
    iter_lotes_productos,
    contar_productos_validos,
    leer_encabezados_inventario,
    mensaje_error_importacion,
    persistir_importacion_por_lotes,
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
    " AND c.visible = 1 AND c.estado_pago IN ('activo', 'gratis')"
)
def obtener_tasa_dolar():
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                "SELECT valor FROM configuracion_sistema WHERE clave = 'tasa_dolar'"
            )
            fila = cursor.fetchone()
            return float(fila[0]) if fila else 36.50
    except Exception:
        return 36.50


def obtener_config(clave, default=None):
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                "SELECT valor FROM configuracion_sistema WHERE clave = ?", (clave,)
            )
            fila = cursor.fetchone()
            return fila[0] if fila else default
    except Exception:
        return default


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

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            cursor.execute(
                'SELECT id FROM comercios WHERE usuario_id = ?', (usuario_id,)
            )
            if cursor.fetchone():
                return False, 'Error: El usuario ya tiene un comercio registrado.'

            cursor.execute(
                """
                INSERT INTO comercios (
                    usuario_id, nombre, descripcion, telefono, direccion,
                    logo_url, categoria_id, ciudad, zona, maps_url,
                    documento_identidad, plan_id, plan_tipo, limite_productos,
                    estado_pago, fecha_inicio_suscripcion, fecha_vencimiento
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'activo',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + INTERVAL '30 days')
                """,
                (
                    usuario_id,
                    nombre,
                    descripcion,
                    telefono,
                    direccion,
                    logo_url,
                    categoria_id,
                    ciudad,
                    zona,
                    maps_url,
                    documento_identidad,
                    plan_id,
                    PLAN_GRATIS_CODIGO,
                    limite,
                ),
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
            ]
            valores = [
                nombre,
                telefono,
                direccion,
                descripcion,
                ciudad,
                zona,
                maps_url,
            ]

            if logo_url:
                campos.append('logo_url = ?')
                valores.append(logo_url)

            if banner_url:
                campos.append('banner_url = ?')
                campos.append('imagen_portada = ?')
                valores.extend([banner_url, banner_url])

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
            return dict(fila) if fila else None
    except Exception as e:
        print(f'Error al obtener comercio: {e}')
        return None


def obtener_comercio_por_usuario(usuario_id):
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT * FROM comercios WHERE usuario_id = ?', (usuario_id,)
            )
            fila = cursor.fetchone()
            return dict(fila) if fila else None
    except Exception:
        return None


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
            return [dict(f) for f in cursor.fetchall()]
    except Exception as e:
        print(f'Error al filtrar comercios: {e}')
        return []


# ==========================================
# GESTIÓN DE PRODUCTOS
# ==========================================


def buscar_y_filtrar_productos(
    palabra_clave=None,
    categoria_nombre=None,
    comercio_id=None,
    limit=None,
    orden_aleatorio=False,
):
    try:
        tasa = obtener_tasa_dolar() or 1.0
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()

            query = """
                SELECT p.*, c.nombre AS comercio_nombre, c.telefono AS comercio_telefono,
                       c.id AS comercio_id,
                       cat.nombre AS categoria_nombre
                FROM productos p
                JOIN comercios c ON p.comercio_id = c.id
                LEFT JOIN categorias cat ON c.categoria_id = cat.id
                WHERE 1=1
            """ + _FILTRO_COMERCIO_PUBLICO
            parametros = []

            if comercio_id:
                query += ' AND p.comercio_id = ?'
                parametros.append(comercio_id)

            filtro_palabras, params_palabras = _construir_filtro_palabras(
                palabra_clave
            )
            if filtro_palabras:
                query += f' AND {filtro_palabras}'
                parametros.extend(params_palabras)

            if categoria_nombre:
                query += ' AND cat.nombre = ?'
                parametros.append(categoria_nombre)

            if orden_aleatorio:
                query += ' ORDER BY RANDOM()'
            else:
                query += ' ORDER BY p.id DESC'

            if limit:
                query += ' LIMIT ?'
                parametros.append(int(limit))

            cursor.execute(query, parametros)
            filas = cursor.fetchall()

            productos = []
            for fila in filas:
                d = dict(fila)
                try:
                    precio = float(d.get('precio_usd') or 0)
                except (TypeError, ValueError):
                    precio = 0.0
                d['precio_usd'] = precio
                d['precio_bs'] = round(precio * tasa, 2)
                productos.append(d)

            return aplicar_respaldo_imagenes(productos)
    except Exception as e:
        print(f'Error en la búsqueda de productos: {str(e)}')
        return []


def obtener_producto_publico(producto_id):
    """Producto con datos de tienda para modal/vista pública."""
    try:
        tasa = obtener_tasa_dolar() or 1.0
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT p.*, c.nombre AS comercio_nombre, c.id AS comercio_id,
                       c.telefono AS comercio_telefono
                FROM productos p
                JOIN comercios c ON p.comercio_id = c.id
                WHERE p.id = ? AND c.visible = 1
                  AND c.estado_pago IN ('activo', 'gratis')
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
            aplicar_respaldo_imagenes([d])
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
                    'SELECT * FROM productos WHERE id = ? AND comercio_id = ?',
                    (producto_id, comercio_id),
                )
            else:
                cursor.execute(
                    'SELECT * FROM productos WHERE id = ?', (producto_id,)
                )

            fila = cursor.fetchone()
            if not fila:
                return None
            producto = dict(fila)
            aplicar_respaldo_imagenes([producto])
            return producto
    except Exception as e:
        print(f'Error al obtener producto: {e}')
        return None


def actualizar_producto(
    producto_id,
    comercio_id,
    nombre,
    descripcion,
    precio_usd,
    codigo_barras=None,
    imagen_url=None,
):
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                UPDATE productos
                SET nombre = ?, descripcion = ?, precio_usd = ?, codigo_barras = ?,
                    imagen_url = COALESCE(?, imagen_url)
                WHERE id = ? AND comercio_id = ?
                """,
                (
                    nombre,
                    descripcion,
                    float(precio_usd),
                    codigo_barras,
                    imagen_url,
                    producto_id,
                    comercio_id,
                ),
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
    try:
        data, extension, error_lectura = cargar_archivo_inventario(archivo_csv)
        if error_lectura:
            return False, error_lectura, None

        encabezados, error_enc = leer_encabezados_inventario(data, extension)
        if error_enc:
            return False, error_enc, None

        mapeo, meta, error_mapeo = detectar_mapeo_columnas(encabezados)
        if error_mapeo:
            return False, error_mapeo, None

        ok, msg = comercio_puede_gestionar_inventario(comercio_id)
        if not ok:
            return False, msg, None

        tasa = float(obtener_tasa_dolar() or 1.0)
        total_validos = contar_productos_validos(
            data,
            extension,
            encabezados,
            mapeo,
            meta,
            tasa_dolar=tasa,
            imagen_default=None,
        )
        if total_validos == 0:
            return False, (
                'No se encontraron filas válidas con nombre y precio. '
                'Revisa que los datos no estén vacíos y que el precio use formato numérico.'
            ), None

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
            return False, mensaje, {'plan_sugerido': plan_sugerido}

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

        insertados = persistir_importacion_por_lotes(comercio_id, _generador_lotes)
        asociar_imagenes_inventario(comercio_id)

        return (
            True,
            f'Importación completada: {insertados} productos cargados. '
            'Columnas reconocidas automáticamente desde la primera fila.',
            None,
        )

    except Exception as exc:
        return False, mensaje_error_importacion(exc), None

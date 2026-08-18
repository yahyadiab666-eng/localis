import csv
import io
import os
import sqlite3

import openpyxl
import psycopg2

from backend.db import get_db_connection
from backend.image_batch import DEFAULT_IMAGEN
from backend.plans import PLAN_GRATIS_CODIGO, limite_para_plan, obtener_plan_por_codigo, validar_cantidad_productos
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
MAX_FILE_SIZE = 5 * 1024 * 1024


def archivo_permitido(filename):
    return (
        '.' in filename
        and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
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


def _normalizar_imagen_url(img):
    if not img or img == '__PENDING__':
        return '/static/images/default-product.webp'
    if img.startswith('http://') or img.startswith('https://'):
        return img
    if img.startswith('/static/'):
        return img
    return f'/static/uploads/{img}'


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
        sub = ' OR '.join(f'{campo} LIKE ?' for campo in campos)
        condiciones.append(f'({sub})')
        parametros.extend([like] * len(campos))

    return ' AND '.join(condiciones), parametros


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
    delivery,
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
                    logo_url, delivery, categoria_id, ciudad, zona, maps_url,
                    documento_identidad, plan_id, plan_tipo, limite_productos,
                    estado_pago, fecha_inicio_suscripcion, fecha_vencimiento
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'activo',
                        CURRENT_TIMESTAMP, date('now', '+30 days'))
                """,
                (
                    usuario_id,
                    nombre,
                    descripcion,
                    telefono,
                    direccion,
                    logo_url,
                    delivery,
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
):
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            if logo_url:
                cursor.execute(
                    """
                    UPDATE comercios
                    SET nombre = ?, telefono = ?, direccion = ?, descripcion = ?,
                        ciudad = ?, zona = ?, maps_url = ?, logo_url = ?
                    WHERE id = ?
                    """,
                    (
                        nombre,
                        telefono,
                        direccion,
                        descripcion,
                        ciudad,
                        zona,
                        maps_url,
                        logo_url,
                        comercio_id,
                    ),
                )
            else:
                cursor.execute(
                    """
                    UPDATE comercios
                    SET nombre = ?, telefono = ?, direccion = ?, descripcion = ?,
                        ciudad = ?, zona = ?, maps_url = ?
                    WHERE id = ?
                    """,
                    (
                        nombre,
                        telefono,
                        direccion,
                        descripcion,
                        ciudad,
                        zona,
                        maps_url,
                        comercio_id,
                    ),
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
                query += ' AND c.visible = 1'

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
    palabra_clave=None, categoria_id=None, delivery=None
):
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()

            query = """
                SELECT c.*, cat.nombre AS categoria_nombre
                FROM comercios c
                LEFT JOIN categorias cat ON c.categoria_id = cat.id
                WHERE c.visible = 1
            """
            parametros = []

            if palabra_clave:
                query += ' AND (c.nombre LIKE ? OR c.descripcion LIKE ? OR c.ciudad LIKE ? OR c.zona LIKE ?)'
                like = f'%{palabra_clave}%'
                parametros.extend([like, like, like, like])

            if categoria_id:
                query += ' AND c.categoria_id = ?'
                parametros.append(categoria_id)

            if delivery is not None and delivery != '':
                query += ' AND c.delivery = ?'
                parametros.append(int(delivery))

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
    delivery=None,
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
                       c.delivery AS comercio_delivery, c.id AS comercio_id,
                       cat.nombre AS categoria_nombre
                FROM productos p
                JOIN comercios c ON p.comercio_id = c.id
                LEFT JOIN categorias cat ON c.categoria_id = cat.id
                WHERE c.visible = 1
            """
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

            if delivery is not None and delivery != '':
                query += ' AND c.delivery = ?'
                parametros.append(int(delivery))

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
                d['precio_bs'] = (
                    round(d['precio_usd'] * tasa, 2) if d.get('precio_usd') else 0.0
                )
                productos.append(d)

            return productos
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
                """,
                (producto_id,),
            )
            fila = cursor.fetchone()
            if not fila:
                return None
            d = dict(fila)
            d['precio_bs'] = round(d['precio_usd'] * tasa, 2)
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
            return dict(fila) if fila else None
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


def _leer_filas_archivo(archivo):
    extension = archivo.filename.rsplit('.', 1)[-1].lower()
    filas = []

    if extension == 'xlsx':
        wb = openpyxl.load_workbook(
            filename=io.BytesIO(archivo.read()), data_only=True
        )
        hoja = wb.active
        encabezados = [
            str(cell.value or '').strip().lower() for cell in hoja[1]
        ]
        for row in hoja.iter_rows(min_row=2, values_only=True):
            if not any(row):
                continue
            filas.append(dict(zip(encabezados, row)))
    elif extension == 'csv':
        contenido = archivo.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(contenido))
        filas = [
            {
                (clave or '').strip().lower(): valor
                for clave, valor in fila.items()
            }
            for fila in reader
        ]
    else:
        return None, 'El archivo debe tener extensión .csv o .xlsx.'

    return filas, None


def _parsear_fila_producto(fila):
    nombre = str(fila.get('nombre') or '').strip()
    precio_usd_raw = str(fila.get('precio_usd') or '0').strip().replace(',', '.')

    if not nombre or nombre == 'None' or not precio_usd_raw:
        return None

    try:
        precio_usd = float(precio_usd_raw)
    except ValueError:
        return None

    descripcion = str(fila.get('descripcion') or '').strip()
    if descripcion == 'None':
        descripcion = ''

    codigo_barras = str(fila.get('codigo_barras') or '').strip()
    if not codigo_barras or codigo_barras == 'None':
        codigo_barras = None

    imagen_url = str(fila.get('imagen_url') or fila.get('imagen') or '').strip()
    if not imagen_url or imagen_url == 'None':
        imagen_url = DEFAULT_IMAGEN

    return {
        'nombre': nombre,
        'descripcion': descripcion,
        'precio_usd': precio_usd,
        'codigo_barras': codigo_barras,
        'imagen_url': imagen_url,
    }


def procesar_csv_productos(comercio_id, archivo_csv, upload_folder=None):
    """
    Carga masiva con reemplazo total.
    Guarda la URL de imagen del CSV/Excel tal cual en imagen_url (sin descargas).
    """
    del upload_folder  # Conservado por compatibilidad con llamadas existentes.

    if not archivo_csv or archivo_csv.filename == '':
        return False, 'No se adjuntó ningún archivo.'

    extension = archivo_csv.filename.rsplit('.', 1)[-1].lower()
    if extension not in ['csv', 'xlsx']:
        return False, 'El archivo debe tener extensión .csv o .xlsx.'

    try:
        filas, error = _leer_filas_archivo(archivo_csv)
        if error:
            return False, error

        productos_validos = []

        for fila in filas:
            parsed = _parsear_fila_producto(fila)
            if parsed:
                productos_validos.append(parsed)

        if not productos_validos:
            return False, 'El archivo está vacío o no contiene filas válidas.'

        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()

            cursor.execute(
                'SELECT plan_tipo, limite_productos FROM comercios WHERE id = ?',
                (comercio_id,),
            )
            comercio = cursor.fetchone()
            plan_tipo = comercio['plan_tipo'] if comercio else 'basica'

            ok, msg = validar_cantidad_productos(plan_tipo, len(productos_validos))
            if not ok:
                return False, msg

            cursor.execute(
                'DELETE FROM productos WHERE comercio_id = ?', (comercio_id,)
            )

            insertados = 0
            for prod in productos_validos:
                cursor.execute(
                    """
                    INSERT INTO productos (
                        comercio_id, nombre, descripcion, precio_usd,
                        codigo_barras, imagen_url
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        comercio_id,
                        prod['nombre'],
                        prod['descripcion'],
                        prod['precio_usd'],
                        prod['codigo_barras'],
                        prod['imagen_url'],
                    ),
                )
                insertados += 1

            conexion.commit()

        return (
            True,
            f'Carga completada: {insertados} productos importados con sus URLs de imagen.',
        )

    except Exception as e:
        return False, f'Error al procesar el archivo: {str(e)}'

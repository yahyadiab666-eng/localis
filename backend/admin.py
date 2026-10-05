import re
import sqlite3

from backend.comercio_schema import sql_set_imagenes
from backend.db import get_db_connection
from backend.plans import PLANES, limite_para_plan, obtener_plan_por_codigo
from backend.utils import parsear_visible_form, url_banner_principal

TIPOS_REPORTES_PERMITIDOS = {'soporte', 'reportar_tienda', 'reportar_articulo'}


def validar_correo(correo):
    patron = r'^[\w\.-]+@[\w\.-]+\.\.\w+$'
    return re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', correo) is not None


# ==========================================
# SOPORTE, MENSAJERÍA Y REPORTES
# ==========================================


def crear_ticket_soporte_o_reporte(
    usuario_id, tipo, correo, mensaje, referencia_id=None
):
    if not tipo or not correo or not mensaje:
        return (
            False,
            'Error: Todos los campos obligatorios (tipo, correo, mensaje) deben ser completados.',
        )

    tipo = tipo.strip()
    correo = correo.strip()
    mensaje = mensaje.strip()

    if tipo not in TIPOS_REPORTES_PERMITIDOS:
        return False, f"Error: El tipo de reporte '{tipo}' no es válido."

    if not validar_correo(correo):
        return False, 'Error: El formato del correo electrónico no es válido.'

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                INSERT INTO soporte_y_reportes (usuario_id, tipo, correo, mensaje, referencia_id, estado)
                VALUES (?, ?, ?, ?, ?, 'pendiente')
                """,
                (usuario_id, tipo, correo, mensaje, referencia_id),
            )
            conexion.commit()
        return True, 'Reporte o mensaje enviado al equipo técnico con éxito.'
    except Exception as e:
        return False, f'Error al procesar el reporte: {str(e)}'


def obtener_bandeja_tecnica(estado_filtro=None):
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()

            query = 'SELECT * FROM soporte_y_reportes'
            parametros = []

            if estado_filtro in ['pendiente', 'resuelto']:
                query += ' WHERE estado = ?'
                parametros.append(estado_filtro)

            query += ' ORDER BY fecha DESC'
            cursor.execute(query, parametros)
            return [dict(fila) for fila in cursor.fetchall()]
    except Exception as e:
        print(f'Error al obtener la bandeja técnica: {str(e)}')
        return []


def resolver_ticket_soporte(ticket_id):
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                "UPDATE soporte_y_reportes SET estado = 'resuelto' WHERE id = ?",
                (int(ticket_id),),
            )
            conexion.commit()
        return True, 'El ticket fue marcado como resuelto con éxito.'
    except Exception as e:
        return False, f'Error al resolver el ticket: {str(e)}'


# ==========================================
# CONFIGURACIÓN ECONÓMICA Y CONTROL FISCAL
# ==========================================


def actualizar_tasa_dolar(admin_id, nueva_tasa):
    try:
        tasa_limpia = float(nueva_tasa)
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                "SELECT valor FROM configuracion_sistema WHERE clave = 'tasa_dolar'"
            )
            fila = cursor.fetchone()
            tasa_anterior = fila[0] if fila else '36.50'

            cursor.execute(
                """
                INSERT INTO configuracion_sistema (clave, valor)
                VALUES ('tasa_dolar', ?)
                ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor
                """,
                (str(tasa_limpia),),
            )

            detalles_log = (
                f'Cambio de tasa de cambio. Anterior: {tasa_anterior} Bs.'
                f' Nueva: {tasa_limpia} Bs.'
            )
            cursor.execute(
                """
                INSERT INTO logs_auditoria (usuario_id, accion, detalles)
                VALUES (?, 'Cambio de tasa de cambio', ?)
                """,
                (admin_id, detalles_log),
            )

            conexion.commit()
        from backend.stores import invalidar_cache_configuracion

        invalidar_cache_configuracion()
        return True, f'Tasa del día actualizada a {tasa_limpia} Bs. Grabado en auditoría.'
    except ValueError:
        return False, 'Error: El valor de la tasa debe ser un número válido.'
    except Exception as e:
        return False, f'Error en la base de datos: {str(e)}'


def obtener_banner_principal():
    from config import url_banner_por_defecto
    from backend.stores import obtener_config

    fallback = url_banner_por_defecto()
    valor = obtener_config('banner_principal', fallback)
    return url_banner_principal(valor, default=fallback)


def actualizar_banner_principal(admin_id, banner_url):
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                INSERT INTO configuracion_sistema (clave, valor)
                VALUES ('banner_principal', ?)
                ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor
                """,
                (banner_url,),
            )
            cursor.execute(
                """
                INSERT INTO logs_auditoria (usuario_id, accion, detalles)
                VALUES (?, 'Cambio banner principal', ?)
                """,
                (admin_id, f'Nuevo banner: {banner_url}'),
            )
            conexion.commit()
        from backend.stores import invalidar_cache_configuracion

        invalidar_cache_configuracion()
        return True, 'Banner promocional actualizado correctamente.'
    except Exception as e:
        return False, f'Error al actualizar banner: {str(e)}'


def cambiar_visibilidad_comercio(admin_id, comercio_id, visible, estado_pago):
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT nombre FROM comercios WHERE id = ?', (int(comercio_id),)
            )
            fila = cursor.fetchone()
            nombre_comercio = fila[0] if fila else f'ID {comercio_id}'

            cursor.execute(
                """
                UPDATE comercios
                SET visible = ?, estado_pago = ?
                WHERE id = ?
                """,
                (int(parsear_visible_form(visible)), estado_pago, int(comercio_id)),
            )

            detalles_log = (
                f"Modificación de comercio '{nombre_comercio}'."
                f' Visibilidad: {visible}, Estado de pago: {estado_pago}.'
            )
            cursor.execute(
                """
                INSERT INTO logs_auditoria (usuario_id, accion, detalles)
                VALUES (?, 'Control de Comercio', ?)
                """,
                (admin_id, detalles_log),
            )

            conexion.commit()
        return (
            True,
            'Estado de visibilidad y pago del comercio actualizado con registro de auditoría.',
        )
    except Exception as e:
        return False, f'Error al cambiar visibilidad: {str(e)}'


def suspender_comercio_temporal(admin_id, comercio_id):
    """Suspensión temporal: oculta la tienda y marca estado suspendido."""
    return cambiar_visibilidad_comercio(admin_id, comercio_id, 0, 'suspendido')


def reactivar_comercio(admin_id, comercio_id):
    """Reactiva un comercio suspendido u oculto."""
    return cambiar_visibilidad_comercio(admin_id, comercio_id, 1, 'activo')


def eliminar_comercio_definitivo(admin_id, comercio_id):
    """Elimina un comercio y sus datos asociados de forma permanente."""
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            cursor.execute(
                'SELECT nombre FROM comercios WHERE id = ?',
                (int(comercio_id),),
            )
            fila = cursor.fetchone()
            if not fila:
                return False, 'Comercio no encontrado.'
            nombre_comercio = fila[0]

            cursor.execute(
                'DELETE FROM pagos WHERE tienda_id = ?', (int(comercio_id),)
            )
            cursor.execute(
                'DELETE FROM solicitudes_pago WHERE comercio_id = ?',
                (int(comercio_id),),
            )
            cursor.execute(
                'DELETE FROM productos WHERE comercio_id = ?', (int(comercio_id),)
            )
            cursor.execute(
                'DELETE FROM comercios WHERE id = ?', (int(comercio_id),)
            )

            if cursor.rowcount == 0:
                conexion.rollback()
                return False, 'No se pudo eliminar el comercio.'

            cursor.execute(
                """
                INSERT INTO logs_auditoria (usuario_id, accion, detalles)
                VALUES (?, 'Eliminación de comercio', ?)
                """,
                (
                    admin_id,
                    f'Comercio eliminado permanentemente: {nombre_comercio} (ID {comercio_id})',
                ),
            )
            conexion.commit()

        return True, f'El comercio "{nombre_comercio}" fue eliminado definitivamente.'
    except Exception as e:
        return False, f'Error al eliminar comercio: {str(e)}'


def cambiar_plan_comercio(admin_id, comercio_id, plan_tipo, estado_pago=None):
    """Cambia el plan de suscripción de una tienda."""
    plan_tipo = (plan_tipo or 'basica').lower()
    if plan_tipo not in PLANES:
        return False, f"Plan '{plan_tipo}' no válido."

    limite = limite_para_plan(plan_tipo)

    plan_db = obtener_plan_por_codigo(plan_tipo)
    plan_id = plan_db.get('id') if plan_db else None

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            if estado_pago:
                cursor.execute(
                    """
                    UPDATE comercios
                    SET plan_id = ?, plan_tipo = ?, limite_productos = ?, estado_pago = ?,
                        fecha_inicio_suscripcion = CURRENT_TIMESTAMP,
                        fecha_vencimiento = CURRENT_TIMESTAMP + (
                            COALESCE(
                                (SELECT dias_duracion FROM planes WHERE id = ?), 30
                            ) * INTERVAL '1 day'
                        )
                    WHERE id = ?
                    """,
                    (plan_id, plan_tipo, limite, estado_pago, plan_id, int(comercio_id)),
                )
            else:
                cursor.execute(
                    """
                    UPDATE comercios
                    SET plan_id = ?, plan_tipo = ?, limite_productos = ?,
                        fecha_inicio_suscripcion = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (plan_id, plan_tipo, limite, int(comercio_id)),
                )

            cursor.execute(
                """
                INSERT INTO logs_auditoria (usuario_id, accion, detalles)
                VALUES (?, 'Cambio de plan', ?)
                """,
                (
                    admin_id,
                    f'Comercio ID {comercio_id} -> plan {plan_tipo}, estado {estado_pago or "sin cambio"}',
                ),
            )
            conexion.commit()
        return True, f'Plan actualizado a {PLANES[plan_tipo]["nombre"]}.'
    except Exception as e:
        return False, f'Error al cambiar plan: {str(e)}'


def obtener_todos_comercios_admin(busqueda=None):
    """Lista comercios para el panel admin, con filtro opcional por texto."""
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            query = """
                SELECT c.id, c.nombre, c.telefono, c.visible, c.estado_pago,
                       c.plan_tipo, c.limite_productos, c.fecha_vencimiento,
                       c.documento_identidad, cat.nombre AS categoria,
                       u.correo AS correo_dueno,
                       COALESCE(p.nombre, c.plan_tipo) AS plan_nombre
                FROM comercios c
                LEFT JOIN categorias cat ON c.categoria_id = cat.id
                LEFT JOIN usuarios u ON c.usuario_id = u.id
                LEFT JOIN planes p ON c.plan_id = p.id
            """
            parametros = []

            if busqueda:
                termino = f'%{busqueda.strip()}%'
                query += """
                    WHERE c.nombre ILIKE ?
                       OR u.correo ILIKE ?
                       OR c.documento_identidad ILIKE ?
                       OR c.telefono ILIKE ?
                """
                parametros.extend([termino, termino, termino, termino])

            query += ' ORDER BY c.id DESC'
            cursor.execute(query, parametros)
            return [dict(f) for f in cursor.fetchall()]
    except Exception as e:
        print(f'Error al listar comercios: {e}')
        return []


def confirmar_pago_suscripcion(comercio_id, plan_tipo, meses=1):
    """
    Confirma pago admin y activa suscripción en una transacción atómica.
    Retorna (exito, mensaje).
    """
    from backend.payments import activar_suscripcion_con_pago
    from backend.subscriptions import _calcular_fecha_vencimiento_meses

    plan_tipo = (plan_tipo or 'basica').lower()
    if plan_tipo not in PLANES:
        return False, 'Plan no válido.'

    limite = limite_para_plan(plan_tipo)
    plan_db = obtener_plan_por_codigo(plan_tipo)
    plan_id = plan_db.get('id') if plan_db else None
    if not plan_id:
        return False, 'Plan no configurado en el sistema.'

    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT fecha_vencimiento FROM comercios WHERE id = ?',
                (int(comercio_id),),
            )
            fila = cursor.fetchone()
            if not fila:
                return False, 'Comercio no encontrado.'

        nueva_fecha = _calcular_fecha_vencimiento_meses(fila['fecha_vencimiento'], meses)
        exito, mensaje, _ = activar_suscripcion_con_pago(
            comercio_id,
            plan_id,
            plan_tipo,
            limite,
            nueva_fecha,
            pago_registro={
                'columnas': ('tienda_id', 'plan_id', 'monto', 'metodo', 'estado'),
                'valores': (
                    int(comercio_id),
                    plan_id,
                    plan_db.get('precio', 0) if plan_db else 0,
                    'admin',
                    'aprobado',
                ),
            },
        )
        if exito:
            return True, f'Suscripción {plan_tipo} activada por {meses} mes(es).'
        return False, mensaje
    except Exception as e:
        return False, f'Error al confirmar pago: {str(e)}'


# ==========================================
# ACCESO TOTAL DEL ADMINISTRADOR
# ==========================================
# El administrador puede visualizar, auditar y gestionar directamente cualquier
# tienda, perfil y catálogo sin depender del comerciante. Todo es de solo
# lectura salvo las acciones explícitas que ya registran auditoría.


def obtener_comercio_admin(comercio_id):
    """Ficha completa de un comercio (tienda + propietario + otros comercios).

    Devuelve un dict plano o ``None`` si el comercio no existe. Nunca lanza.
    """
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT c.*,
                       cat.nombre AS categoria,
                       u.id AS propietario_id,
                       u.nombre AS propietario_nombre,
                       u.correo AS propietario_correo,
                       u.foto_url AS propietario_foto,
                       u.rol AS propietario_rol,
                       COALESCE(p.nombre, c.plan_tipo) AS plan_nombre,
                       (
                           SELECT COUNT(*) FROM productos
                           WHERE comercio_id = c.id
                       ) AS total_productos
                FROM comercios c
                LEFT JOIN categorias cat ON c.categoria_id = cat.id
                LEFT JOIN usuarios u ON c.usuario_id = u.id
                LEFT JOIN planes p ON c.plan_id = p.id
                WHERE c.id = ?
                """,
                (int(comercio_id),),
            )
            fila = cursor.fetchone()
            if not fila:
                return None
            comercio = dict(fila)

            propietario_id = comercio.get('propietario_id')
            if propietario_id:
                cursor.execute(
                    """
                    SELECT id, nombre, visible, estado_pago, plan_tipo
                    FROM comercios
                    WHERE usuario_id = ? AND id <> ?
                    ORDER BY id DESC
                    """,
                    (propietario_id, int(comercio_id)),
                )
                comercio['otros_comercios'] = [dict(f) for f in cursor.fetchall()]
                cursor.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM comercios WHERE usuario_id = ?
                    """,
                    (propietario_id,),
                )
                conteo = cursor.fetchone()
                comercio['total_comercios_dueno'] = int(
                    (conteo['n'] if isinstance(conteo, dict) else conteo[0]) or 0
                )
            else:
                comercio['otros_comercios'] = []
                comercio['total_comercios_dueno'] = 0
            return comercio
    except Exception as e:
        print(f'Error al obtener comercio admin (id={comercio_id}): {e}')
        return None


def obtener_productos_admin(comercio_id):
    """Catálogo completo de un comercio para auditoría del administrador."""
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT id, nombre, descripcion, precio_usd, codigo_barras, stock,
                       imagen_url, imagen_estado, activo
                FROM productos
                WHERE comercio_id = ?
                ORDER BY id DESC
                """,
                (int(comercio_id),),
            )
            return [dict(f) for f in cursor.fetchall()]
    except Exception as e:
        print(f'Error al listar productos admin (comercio={comercio_id}): {e}')
        return []


def eliminar_producto_admin(producto_id, admin_id, comercio_id=None):
    """Elimina un producto desde el panel admin y registra auditoría."""
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            # Desvincula el destacado del producto (por ID) antes de borrarlo.
            try:
                cursor.execute('SAVEPOINT boost_off_admin')
                cursor.execute(
                    """
                    UPDATE boosts
                    SET estado = 'expirado'
                    WHERE tipo = 'producto' AND objetivo_id = ?
                    """,
                    (int(producto_id),),
                )
                cursor.execute('RELEASE SAVEPOINT boost_off_admin')
            except Exception as error_boost:
                try:
                    cursor.execute('ROLLBACK TO SAVEPOINT boost_off_admin')
                except Exception:
                    pass
                print(
                    f'[Localis Boost] admin: destacado no desvinculado '
                    f'(id={producto_id}): {error_boost}',
                    flush=True,
                )
            if comercio_id:
                cursor.execute(
                    'DELETE FROM productos WHERE id = ? AND comercio_id = ?',
                    (int(producto_id), int(comercio_id)),
                )
            else:
                cursor.execute(
                    'DELETE FROM productos WHERE id = ?', (int(producto_id),)
                )
            if cursor.rowcount == 0:
                conexion.rollback()
                return False, 'Producto no encontrado.'

            cursor.execute(
                """
                INSERT INTO logs_auditoria (usuario_id, accion, detalles)
                VALUES (?, 'Eliminación de producto (admin)', ?)
                """,
                (
                    admin_id,
                    f'Producto ID {int(producto_id)} eliminado por el administrador '
                    f'(comercio {comercio_id or "desconocido"}).',
                ),
            )
            conexion.commit()
        return True, 'Producto eliminado por el administrador.'
    except Exception as e:
        return False, f'Error al eliminar producto: {str(e)}'


def obtener_usuarios_admin(busqueda=None):
    """Lista todos los usuarios registrados con el conteo de sus comercios.

    Permite al administrador auditar cualquier perfil, tenga o no una tienda.
    """
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            query = """
                SELECT u.id, u.nombre, u.correo, u.rol, u.foto_url,
                       (
                           SELECT COUNT(*) FROM comercios c WHERE c.usuario_id = u.id
                       ) AS total_comercios
                FROM usuarios u
            """
            parametros = []
            if busqueda:
                termino = f'%{busqueda.strip()}%'
                query += ' WHERE u.nombre ILIKE ? OR u.correo ILIKE ?'
                parametros.extend([termino, termino])
            query += ' ORDER BY u.id DESC'
            cursor.execute(query, parametros)
            usuarios = [dict(f) for f in cursor.fetchall()]

            # Se adjuntan los comercios de cada usuario (para enlazar a su ficha).
            for usuario in usuarios:
                cursor.execute(
                    """
                    SELECT id, nombre, visible, estado_pago
                    FROM comercios
                    WHERE usuario_id = ?
                    ORDER BY id DESC
                    """,
                    (usuario['id'],),
                )
                usuario['comercios'] = [dict(f) for f in cursor.fetchall()]
            return usuarios
    except Exception as e:
        print(f'Error al listar usuarios admin: {e}')
        return []


# ==========================================
# ONBOARDING ASISTIDO (ADMIN)
# ==========================================
# El administrador opera la tienda como si fuera el dueño para la carga inicial.
# Solo se apoya en las tablas/columnas existentes; no altera planes ni pagos.

ESTADOS_PAGO_VALIDOS = ('activo', 'vencido', 'suspendido', 'gratis')


def _registrar_auditoria_admin(cursor, admin_id, accion, detalles):
    """Inserta una fila de auditoría reutilizando el cursor de la transacción."""
    cursor.execute(
        """
        INSERT INTO logs_auditoria (usuario_id, accion, detalles)
        VALUES (?, ?, ?)
        """,
        (admin_id, accion, detalles),
    )


def obtener_categorias_admin():
    """Categorías disponibles para el formulario de edición de tienda."""
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute('SELECT id, nombre FROM categorias ORDER BY nombre ASC')
            return [dict(f) for f in cursor.fetchall()]
    except Exception as e:
        print(f'Error al listar categorias admin: {e}')
        return []


def actualizar_comercio_admin(
    admin_id, comercio_id, datos, logo_url=None, banner_url=None
):
    """Edita el perfil de cualquier tienda desde el panel admin.

    ``datos`` admite: nombre, descripcion, telefono, documento_identidad,
    direccion, ciudad, zona, maps_url, categoria_id, visible, estado_pago,
    banner_color. Registra auditoría. Retorna ``(exito, mensaje)``.
    """
    datos = datos or {}
    nombre = (datos.get('nombre') or '').strip()
    if not nombre:
        return False, 'El nombre del comercio es obligatorio.'

    categoria_id = datos.get('categoria_id')
    estado_pago = (datos.get('estado_pago') or '').strip().lower()

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            if categoria_id:
                cursor.execute(
                    'SELECT 1 FROM categorias WHERE id = ?', (int(categoria_id),)
                )
                if not cursor.fetchone():
                    return False, 'La categoría seleccionada no es válida.'

            campos = []
            valores = []
            columnas_simples = (
                ('nombre', nombre),
                ('descripcion', datos.get('descripcion')),
                ('telefono', datos.get('telefono')),
                ('documento_identidad', datos.get('documento_identidad')),
                ('direccion', datos.get('direccion')),
                ('ciudad', datos.get('ciudad')),
                ('zona', datos.get('zona')),
                ('maps_url', datos.get('maps_url')),
                ('ubicacion_maps_url', datos.get('maps_url')),
            )
            for columna, valor in columnas_simples:
                if valor is None:
                    continue
                valor_limpio = str(valor).strip()
                campos.append(f'{columna} = ?')
                valores.append(valor_limpio or None)

            if categoria_id:
                campos.append('categoria_id = ?')
                valores.append(int(categoria_id))

            if datos.get('visible') is not None:
                campos.append('visible = ?')
                valores.append(int(parsear_visible_form(datos.get('visible'))))

            if estado_pago in ESTADOS_PAGO_VALIDOS:
                campos.append('estado_pago = ?')
                valores.append(estado_pago)

            if datos.get('banner_color') is not None:
                from backend.apariencia import normalizar_color_banner

                campos.append('banner_color = ?')
                valores.append(normalizar_color_banner(datos.get('banner_color')))

            if logo_url:
                frag, vals = sql_set_imagenes(cursor, logo_url=logo_url)
                campos.extend(frag)
                valores.extend(vals)
            if banner_url:
                frag, vals = sql_set_imagenes(cursor, banner_url=banner_url)
                campos.extend(frag)
                valores.extend(vals)

            if not campos:
                return False, 'No hay cambios para guardar.'

            valores.append(int(comercio_id))
            cursor.execute(
                f"UPDATE comercios SET {', '.join(campos)} WHERE id = ?",
                tuple(valores),
            )
            if cursor.rowcount == 0:
                conexion.rollback()
                return False, 'Comercio no encontrado.'

            _registrar_auditoria_admin(
                cursor,
                admin_id,
                'Edición de tienda (admin)',
                f'Comercio ID {int(comercio_id)} editado por el administrador.',
            )
            conexion.commit()
        return True, 'Datos de la tienda actualizados por el administrador.'
    except Exception as e:
        return False, f'Error al actualizar la tienda: {str(e)}'


def obtener_producto_admin(producto_id, comercio_id=None):
    """Producto de cualquier comercio para el formulario de edición admin."""
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            if comercio_id:
                cursor.execute(
                    'SELECT * FROM productos WHERE id = ? AND comercio_id = ?',
                    (int(producto_id), int(comercio_id)),
                )
            else:
                cursor.execute(
                    'SELECT * FROM productos WHERE id = ?', (int(producto_id),)
                )
            fila = cursor.fetchone()
            return dict(fila) if fila else None
    except Exception as e:
        print(f'Error al obtener producto admin: {e}')
        return None


def crear_producto_admin(
    admin_id,
    comercio_id,
    nombre,
    descripcion,
    precio_usd,
    codigo_barras=None,
    imagen_url=None,
):
    """Crea un producto en cualquier comercio desde el panel admin.

    Retorna ``(exito, mensaje, producto_id)``.
    """
    if not (nombre or '').strip():
        return False, 'El nombre es obligatorio.', None
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT 1 FROM comercios WHERE id = ?', (int(comercio_id),)
            )
            if not cursor.fetchone():
                return False, 'Comercio no encontrado.', None

            cursor.execute(
                """
                INSERT INTO productos
                    (comercio_id, nombre, descripcion, precio_usd,
                     codigo_barras, imagen_url)
                VALUES (?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    int(comercio_id),
                    nombre.strip(),
                    descripcion,
                    float(precio_usd),
                    codigo_barras,
                    imagen_url,
                ),
            )
            fila = cursor.fetchone()
            if isinstance(fila, dict):
                producto_id = fila.get('id')
            else:
                producto_id = fila[0] if fila else None

            _registrar_auditoria_admin(
                cursor,
                admin_id,
                'Alta de producto (admin)',
                f'Producto {producto_id} creado en comercio {int(comercio_id)}.',
            )
            conexion.commit()
        return True, 'Producto creado por el administrador.', producto_id
    except Exception as e:
        return False, f'Error al crear producto: {str(e)}', None


def actualizar_producto_admin(
    admin_id,
    producto_id,
    comercio_id,
    nombre,
    descripcion,
    precio_usd,
    codigo_barras=None,
    imagen_url=None,
    incluir_imagen=False,
):
    """Edita un producto de cualquier comercio desde el panel admin.

    Reutiliza ``backend.stores.actualizar_producto`` para la actualización
    parcial segura y agrega la trazabilidad en ``logs_auditoria``.
    """
    from backend.stores import actualizar_producto

    exito, mensaje = actualizar_producto(
        producto_id,
        comercio_id,
        nombre,
        descripcion,
        precio_usd,
        codigo_barras=codigo_barras,
        imagen_url=imagen_url,
        incluir_imagen=incluir_imagen,
    )
    if not exito:
        return False, mensaje

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            _registrar_auditoria_admin(
                cursor,
                admin_id,
                'Edición de producto (admin)',
                f'Producto {int(producto_id)} del comercio {int(comercio_id)} editado.',
            )
            conexion.commit()
    except Exception as e:
        print(f'[Localis Admin] aviso auditoría edición producto: {e}')

    return True, 'Producto actualizado por el administrador.'

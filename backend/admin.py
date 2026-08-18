import re
import sqlite3

from backend.db import get_db_connection
from backend.plans import PLANES, limite_para_plan, obtener_plan_por_codigo

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
        return True, f'Tasa del día actualizada a {tasa_limpia} Bs. Grabado en auditoría.'
    except ValueError:
        return False, 'Error: El valor de la tasa debe ser un número válido.'
    except Exception as e:
        return False, f'Error en la base de datos: {str(e)}'


def obtener_banner_principal():
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                "SELECT valor FROM configuracion_sistema WHERE clave = 'banner_principal'"
            )
            fila = cursor.fetchone()
            return (
                fila[0]
                if fila
                else 'https://images.pexels.com/photos/18618233/pexels-photo-18618233.jpeg?auto=compress&cs=tinysrgb&w=1920'
            )
    except Exception:
        return 'https://images.pexels.com/photos/18618233/pexels-photo-18618233.jpeg?auto=compress&cs=tinysrgb&w=1920'


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
                (int(visible), estado_pago, int(comercio_id)),
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
                    WHERE c.nombre LIKE ?
                       OR u.correo LIKE ?
                       OR c.documento_identidad LIKE ?
                       OR c.telefono LIKE ?
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
    Endpoint preparado para confirmar pago y activar suscripción.
    Retorna (exito, mensaje).
    """
    plan_tipo = (plan_tipo or 'basica').lower()
    if plan_tipo not in PLANES:
        return False, 'Plan no válido.'

    limite = limite_para_plan(plan_tipo)
    plan_db = obtener_plan_por_codigo(plan_tipo)
    plan_id = plan_db.get('id') if plan_db else None

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                UPDATE comercios
                SET plan_id = ?, plan_tipo = ?, limite_productos = ?, estado_pago = 'activo',
                    visible = 1,
                    fecha_inicio_suscripcion = CURRENT_TIMESTAMP,
                    fecha_vencimiento = CURRENT_TIMESTAMP + (? * INTERVAL '1 month')
                WHERE id = ?
                """,
                (plan_id, plan_tipo, limite, int(meses), int(comercio_id)),
            )
            if cursor.rowcount == 0:
                conexion.commit()
                return False, 'Comercio no encontrado.'

            if plan_id:
                cursor.execute(
                    """
                    INSERT INTO pagos (tienda_id, plan_id, monto, metodo, estado)
                    VALUES (?, ?, ?, 'admin', 'aprobado')
                    """,
                    (
                        int(comercio_id),
                        plan_id,
                        plan_db.get('precio', 0) if plan_db else 0,
                    ),
                )
            conexion.commit()
        return True, f'Suscripción {plan_tipo} activada por {meses} mes(es).'
    except Exception as e:
        return False, f'Error al confirmar pago: {str(e)}'

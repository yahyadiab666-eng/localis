"""Confirmación atómica de pagos y activación de suscripciones."""

import sqlite3

from backend.db import get_db_connection


def activar_suscripcion_con_pago(
    comercio_id,
    plan_id,
    plan_tipo,
    limite_productos,
    fecha_vencimiento,
    pago_registro,
    solicitud_registro=None,
):
    """
    Actualiza comercio + inserta pago (+ solicitud opcional) en una sola transacción.
    Retorna (exito, mensaje, datos) con RETURNING para evitar registros huérfanos.
    """
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE comercios
                    SET plan_id = ?, plan_tipo = ?, limite_productos = ?,
                        estado_pago = 'activo', visible = 1,
                        fecha_inicio_suscripcion = CURRENT_TIMESTAMP,
                        fecha_vencimiento = ?
                    WHERE id = ?
                    RETURNING id, plan_tipo, estado_pago, fecha_vencimiento
                    """,
                    (
                        plan_id,
                        plan_tipo,
                        limite_productos,
                        fecha_vencimiento,
                        int(comercio_id),
                    ),
                )
                comercio_row = cursor.fetchone()
                if not comercio_row:
                    conexion.rollback()
                    return False, 'Comercio no encontrado.', None

                cursor.execute(
                    f"""
                    INSERT INTO pagos ({', '.join(pago_registro['columnas'])})
                    VALUES ({', '.join('?' for _ in pago_registro['valores'])})
                    RETURNING id
                    """,
                    tuple(pago_registro['valores']),
                )
                pago_row = cursor.fetchone()
                if not pago_row:
                    conexion.rollback()
                    return False, 'No se pudo registrar el pago.', None

                solicitud_id = None
                if solicitud_registro:
                    cursor.execute(
                        f"""
                        INSERT INTO solicitudes_pago ({', '.join(solicitud_registro['columnas'])})
                        VALUES ({', '.join('?' for _ in solicitud_registro['valores'])})
                        RETURNING id
                        """,
                        tuple(solicitud_registro['valores']),
                    )
                    solicitud_row = cursor.fetchone()
                    if not solicitud_row:
                        conexion.rollback()
                        return False, 'No se pudo registrar la solicitud de pago.', None
                    solicitud_id = solicitud_row['id']

                conexion.commit()
                return True, 'Suscripción activada correctamente.', {
                    'comercio_id': comercio_row['id'],
                    'plan_tipo': comercio_row['plan_tipo'],
                    'estado_pago': comercio_row['estado_pago'],
                    'fecha_vencimiento': comercio_row['fecha_vencimiento'],
                    'pago_id': pago_row['id'],
                    'solicitud_id': solicitud_id,
                }
            except Exception:
                conexion.rollback()
                raise
    except Exception as error:
        return False, f'Error al confirmar pago: {error}', None

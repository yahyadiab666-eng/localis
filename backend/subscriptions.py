"""Ciclo de vida automático de suscripciones, avisos y pagos."""

import re
import sqlite3
from datetime import datetime, timedelta

from backend.db import get_db_connection
from backend.plans import (
    PLANES,
    obtener_plan_por_codigo,
    limite_desde_plan_id,
    limite_para_plan,
)

def verificar_vencimientos_comercios():
    """
    Marca como vencidos los comercios cuya fecha de vencimiento ya pasó.
    Se ejecuta al arranque de la aplicación.
    """
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                UPDATE comercios
                SET estado_pago = 'vencido'
                WHERE date(fecha_vencimiento) < date('now')
                  AND estado_pago IN ('activo', 'gratis')
                """
            )
            conexion.commit()
            return cursor.rowcount
    except Exception as e:
        print(f'Aviso verificación vencimientos: {e}')
        return 0


def contar_productos_comercio(comercio_id):
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            'SELECT COUNT(*) FROM productos WHERE comercio_id = ?',
            (comercio_id,),
        )
        return cursor.fetchone()[0]


def obtener_limite_productos_comercio(comercio_id):
    with get_db_connection(row_factory=sqlite3.Row) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT c.plan_id, c.plan_tipo, c.limite_productos, c.estado_pago,
                   p.limite_productos AS plan_limite
            FROM comercios c
            LEFT JOIN planes p ON c.plan_id = p.id
            WHERE c.id = ?
            """,
            (comercio_id,),
        )
        fila = cursor.fetchone()

    if not fila:
        return 50
    if fila['plan_limite'] is not None:
        return fila['plan_limite']
    if fila['limite_productos'] is not None:
        return fila['limite_productos']
    if fila['plan_id']:
        return limite_desde_plan_id(fila['plan_id'])
    return limite_para_plan(fila['plan_tipo'])


def puede_agregar_producto(comercio_id, cantidad_nueva=1):
    with get_db_connection(row_factory=sqlite3.Row) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT c.estado_pago, c.plan_tipo, c.nombre, c.visible,
                   COALESCE(p.nombre, c.plan_tipo) AS plan_nombre
            FROM comercios c
            LEFT JOIN planes p ON c.plan_id = p.id
            WHERE c.id = ?
            """,
            (comercio_id,),
        )
        comercio = cursor.fetchone()

    if not comercio:
        return False, 'Comercio no encontrado.'
    if comercio['estado_pago'] == 'vencido':
        return False, 'Tu suscripción ha vencido. Renueva tu plan para seguir agregando productos.'
    if comercio['estado_pago'] == 'suspendido':
        return False, 'Tu comercio está suspendido. Contacta a soporte técnico.'
    if comercio['visible'] == 0 and comercio['estado_pago'] == 'vencido':
        return False, 'Tu tienda está oculta por vencimiento de suscripción.'

    limite = obtener_limite_productos_comercio(comercio_id)
    if limite is None:
        return True, None

    actual = contar_productos_comercio(comercio_id)
    if actual + cantidad_nueva > limite:
        return (
            False,
            f'Has alcanzado el límite de {limite} productos de tu plan '
            f'{comercio["plan_nombre"]}. Actualiza tu plan para agregar más.',
        )
    return True, None


def _fecha_vencida(fecha_vencimiento):
    if not fecha_vencimiento:
        return False
    try:
        venc = datetime.strptime(str(fecha_vencimiento)[:10], '%Y-%m-%d').date()
        return venc < datetime.now().date()
    except ValueError:
        return False


def _calcular_fecha_fin_prueba(comercio):
    """Fecha de vencimiento del periodo de prueba (30 días)."""
    fecha_venc = comercio.get('fecha_vencimiento')
    if fecha_venc:
        return str(fecha_venc)[:10]

    fecha_reg = comercio.get('fecha_registro')
    if fecha_reg:
        try:
            reg = datetime.strptime(str(fecha_reg)[:10], '%Y-%m-%d').date()
            return (reg + timedelta(days=30)).strftime('%Y-%m-%d')
        except ValueError:
            pass

    return (datetime.now().date() + timedelta(days=30)).strftime('%Y-%m-%d')


def _en_periodo_prueba(comercio):
    plan = (comercio.get('plan_tipo') or 'gratis').lower()
    estado = (comercio.get('estado_pago') or 'activo').lower()

    if estado in ('vencido', 'suspendido'):
        return False

    if plan == 'gratis' or estado == 'gratis':
        return not _fecha_vencida(_calcular_fecha_fin_prueba(comercio))

    return False


def marcar_bienvenida_vista(comercio_id):
    """Marca el aviso de bienvenida como visto (solo se muestra la primera vez)."""
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                UPDATE comercios
                SET aviso_bienvenida_visto = 1
                WHERE id = ?
                """,
                (int(comercio_id),),
            )
            conexion.commit()
            return cursor.rowcount > 0
    except Exception as e:
        print(f'Error al marcar bienvenida vista: {e}')
        return False


def obtener_avisos_suscripcion(comercio):
    """
    Determina qué modales push mostrar al comerciante.
    Retorna dict con flags: bienvenida_prueba, suscripcion_vencida, fecha_vencimiento, plan_actual.
    """
    if not comercio:
        return {}

    plan_tipo = (comercio.get('plan_tipo') or 'gratis').lower()
    estado = comercio.get('estado_pago') or 'activo'
    fecha_venc = comercio.get('fecha_vencimiento')
    fecha_fmt = _calcular_fecha_fin_prueba(comercio)
    visto = int(comercio.get('aviso_bienvenida_visto') or 0)

    avisos = {
        'plan_actual': plan_tipo,
        'fecha_vencimiento': fecha_fmt,
        'bienvenida_prueba': False,
        'suscripcion_vencida': False,
    }

    if estado == 'vencido' or _fecha_vencida(fecha_venc or fecha_fmt):
        avisos['suscripcion_vencida'] = True
    elif (
        not visto
        and estado not in ('vencido', 'suspendido')
        and not _fecha_vencida(fecha_venc or fecha_fmt)
        and (_en_periodo_prueba(comercio) or plan_tipo == 'gratis')
    ):
        avisos['bienvenida_prueba'] = True

    return avisos


def rechazar_renovacion_vencida(comercio_id):
    """Oculta la tienda cuando el comerciante rechaza renovar tras vencimiento."""
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                UPDATE comercios
                SET visible = 0, estado_pago = 'vencido'
                WHERE id = ?
                """,
                (int(comercio_id),),
            )
            conexion.commit()
            if cursor.rowcount == 0:
                return False, 'Comercio no encontrado.'
        return True, 'Tu tienda quedó oculta en el catálogo público hasta que renueves tu plan.'
    except Exception as e:
        return False, f'Error al actualizar estado: {e}'


def _calcular_fecha_vencimiento_prorrateo(fecha_vencimiento_actual, dias=30):
    """
    Si aún hay días activos, suma 30 días a la fecha de vencimiento actual.
    Si ya venció, suma 30 días desde hoy.
    """
    hoy = datetime.now().date()
    if fecha_vencimiento_actual:
        try:
            base = datetime.strptime(str(fecha_vencimiento_actual)[:10], '%Y-%m-%d').date()
            if base >= hoy:
                from datetime import timedelta
                return (base + timedelta(days=dias)).strftime('%Y-%m-%d')
        except ValueError:
            pass
    from datetime import timedelta
    return (hoy + timedelta(days=dias)).strftime('%Y-%m-%d')


def registrar_pago_movil_plan(comercio_id, plan_tipo, referencia, fecha_transferencia):
    """
    Registra solicitud de pago móvil y activa el plan con prorrateo de días.
    """
    plan_tipo = (plan_tipo or 'basica').lower()
    if plan_tipo not in PLANES or plan_tipo == 'gratis':
        return False, 'Plan no válido para pago.'

    referencia = (referencia or '').strip()
    if not re.match(r'^\d{6}$', referencia):
        return False, 'La referencia de pago móvil debe tener exactamente 6 dígitos.'

    if not fecha_transferencia:
        return False, 'Indica la fecha de la transferencia.'

    plan = obtener_plan_por_codigo(plan_tipo)
    if not plan:
        return False, 'Plan no encontrado.'

    limite = limite_para_plan(plan_tipo)
    plan_id = plan.get('id')
    dias = plan.get('dias_duracion') or 30

    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT plan_tipo, fecha_vencimiento FROM comercios WHERE id = ?',
                (int(comercio_id),),
            )
            comercio = cursor.fetchone()
            if not comercio:
                return False, 'Comercio no encontrado.'

            nueva_fecha = _calcular_fecha_vencimiento_prorrateo(
                comercio['fecha_vencimiento'], dias
            )

            cursor.execute(
                """
                UPDATE comercios
                SET plan_id = ?, plan_tipo = ?, limite_productos = ?,
                    estado_pago = 'activo', visible = 1,
                    fecha_inicio_suscripcion = CURRENT_TIMESTAMP,
                    fecha_vencimiento = ?
                WHERE id = ?
                """,
                (plan_id, plan_tipo, limite, nueva_fecha, int(comercio_id)),
            )

            cursor.execute(
                """
                INSERT INTO solicitudes_pago (
                    comercio_id, plan_tipo, referencia, fecha_transferencia, estado
                )
                VALUES (?, ?, ?, ?, 'pendiente')
                """,
                (int(comercio_id), plan_tipo, referencia, fecha_transferencia),
            )

            cursor.execute(
                """
                INSERT INTO pagos (
                    tienda_id, plan_id, monto, metodo, referencia, estado
                )
                VALUES (?, ?, ?, 'pago_movil_manual', ?, 'pendiente')
                """,
                (
                    int(comercio_id),
                    plan_id,
                    plan.get('precio', 0),
                    referencia,
                ),
            )
            conexion.commit()

        return (
            True,
            f'Plan {plan["nombre"]} activado hasta {nueva_fecha}. '
            f'Referencia {referencia} registrada para verificación.',
        )
    except Exception as e:
        return False, f'Error al registrar pago: {e}'


def obtener_datos_pago_movil():
    """Lee configuración de pago móvil del sistema."""
    from backend.stores import obtener_config
    from config import PAGO_MOVIL_DEFAULT

    return {
        'banco': obtener_config('pago_movil_banco', PAGO_MOVIL_DEFAULT['banco']),
        'cedula_rif': obtener_config(
            'pago_movil_cedula', PAGO_MOVIL_DEFAULT['cedula_rif']
        ),
        'telefono': obtener_config(
            'pago_movil_telefono', PAGO_MOVIL_DEFAULT['telefono']
        ),
    }

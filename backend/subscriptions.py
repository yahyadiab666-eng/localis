"""Ciclo de vida automático de suscripciones, avisos y pagos."""

import re
import sqlite3
from datetime import datetime, timedelta

from backend.db import get_db_connection
from backend.plans import (
    PLANES,
    MENSAJE_LIMITE_PRODUCTOS,
    es_limite_ilimitado,
    obtener_plan_por_codigo,
    limite_desde_plan_id,
    limite_para_plan,
)
from backend.utils import formatear_fecha

def verificar_vencimientos_comercios():
    """
    Marca como vencidos y oculta del catálogo los comercios cuya fecha expiró.
    Se ejecuta al arranque y periódicamente en requests autenticados.
    """
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                UPDATE comercios
                SET estado_pago = 'vencido', visible = 0
                WHERE CAST(fecha_vencimiento AS DATE) < CURRENT_DATE
                  AND estado_pago IN ('activo', 'gratis')
                """
            )
            conexion.commit()
            return cursor.rowcount
    except Exception as e:
        print(f'Aviso verificación vencimientos: {e}')
        return 0


def verificar_vencimiento_comercio(comercio_id):
    """Sincroniza vencimiento de un comercio concreto. Retorna True si quedó vencido."""
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                UPDATE comercios
                SET estado_pago = 'vencido', visible = 0
                WHERE id = ?
                  AND CAST(fecha_vencimiento AS DATE) < CURRENT_DATE
                  AND estado_pago IN ('activo', 'gratis')
                """,
                (int(comercio_id),),
            )
            conexion.commit()
            return cursor.rowcount > 0
    except Exception as e:
        print(f'Aviso verificación vencimiento comercio {comercio_id}: {e}')
        return False


def comercio_puede_gestionar_inventario(comercio_id):
    """
    Verifica si el comercio puede editar, eliminar o importar inventario.
    Retorna (True, None) o (False, mensaje).
    """
    verificar_vencimiento_comercio(comercio_id)

    with get_db_connection(row_factory=sqlite3.Row) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            'SELECT estado_pago FROM comercios WHERE id = ?',
            (int(comercio_id),),
        )
        fila = cursor.fetchone()

    if not fila:
        return False, 'Comercio no encontrado.'
    estado = fila['estado_pago']
    if estado == 'vencido':
        return (
            False,
            'Tu suscripción ha vencido. Renueva tu plan para gestionar inventario.',
        )
    if estado == 'suspendido':
        return False, 'Tu comercio está suspendido. Contacta a soporte técnico.'
    return True, None


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
        if es_limite_ilimitado(fila['plan_limite']):
            return -1
        return fila['plan_limite']
    if fila['limite_productos'] is not None:
        return fila['limite_productos']
    if fila['plan_id']:
        return limite_desde_plan_id(fila['plan_id'])
    return limite_para_plan(fila['plan_tipo'])


def puede_agregar_producto(comercio_id, cantidad_nueva=1):
    verificar_vencimiento_comercio(comercio_id)

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

    limite = obtener_limite_productos_comercio(comercio_id)
    if es_limite_ilimitado(limite):
        return True, None

    actual = contar_productos_comercio(comercio_id)
    if actual + cantidad_nueva > limite:
        return False, MENSAJE_LIMITE_PRODUCTOS
    return True, None


def _fecha_vencida(fecha_vencimiento):
    if not fecha_vencimiento:
        return False
    try:
        texto = formatear_fecha(fecha_vencimiento)
        if not texto:
            return False
        venc = datetime.strptime(texto, '%Y-%m-%d').date()
        return venc < datetime.now().date()
    except (TypeError, ValueError):
        return False


def _calcular_fecha_fin_prueba(comercio):
    """Fecha de vencimiento del periodo de prueba (30 días)."""
    fecha_venc = formatear_fecha(comercio.get('fecha_vencimiento'))
    if fecha_venc:
        return fecha_venc

    fecha_reg = formatear_fecha(comercio.get('fecha_registro'))
    if fecha_reg:
        try:
            reg = datetime.strptime(fecha_reg, '%Y-%m-%d').date()
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


def _calcular_nueva_fecha_vencimiento(fecha_vencimiento_actual, dias=30):
    """max(fecha_actual, fecha_vencimiento_actual) + dias."""
    hoy = datetime.now().date()
    base = hoy
    if fecha_vencimiento_actual:
        try:
            vencimiento = datetime.strptime(
                formatear_fecha(fecha_vencimiento_actual), '%Y-%m-%d'
            ).date()
            base = max(hoy, vencimiento)
        except (TypeError, ValueError):
            pass
    return (base + timedelta(days=dias)).strftime('%Y-%m-%d')


def _calcular_fecha_vencimiento_prorrateo(fecha_vencimiento_actual, dias=30):
    """Compatibilidad: delega en la regla max(hoy, vencimiento) + dias."""
    return _calcular_nueva_fecha_vencimiento(fecha_vencimiento_actual, dias)


def _calcular_fecha_vencimiento_meses(fecha_vencimiento_actual, meses=1):
    """Extiende suscripción N meses desde max(hoy, vencimiento actual)."""
    hoy = datetime.now().date()
    base = hoy
    if fecha_vencimiento_actual:
        try:
            vencimiento = datetime.strptime(
                formatear_fecha(fecha_vencimiento_actual), '%Y-%m-%d'
            ).date()
            base = max(hoy, vencimiento)
        except (TypeError, ValueError):
            pass
    return (base + timedelta(days=30 * max(1, int(meses)))).strftime('%Y-%m-%d')


def _precio_usd_plan(plan):
    return float(plan.get('precio') or plan.get('precio_usd') or 0)


def calcular_monto_pago_plan(plan_tipo):
    """Calcula montos USD y Bs según plan y tasa oficial del sistema."""
    from backend.stores import obtener_tasa_dolar

    plan = obtener_plan_por_codigo(plan_tipo)
    if not plan:
        return None

    tasa = float(obtener_tasa_dolar() or 1.0)
    monto_usd = _precio_usd_plan(plan)
    return {
        'plan_tipo': (plan_tipo or '').lower(),
        'plan_nombre': plan.get('nombre', plan_tipo),
        'monto_usd': monto_usd,
        'tasa': tasa,
        'monto_bs': round(monto_usd * tasa, 2),
    }


def _referencia_ya_usada(referencia, excluir_comercio_id=None):
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT 1 FROM pagos
            WHERE referencia = ? AND estado IN ('pendiente', 'aprobado')
            LIMIT 1
            """,
            (referencia,),
        )
        if cursor.fetchone():
            return True
        cursor.execute(
            """
            SELECT 1 FROM solicitudes_pago
            WHERE referencia = ? AND estado IN ('pendiente', 'aprobado')
            LIMIT 1
            """,
            (referencia,),
        )
        return cursor.fetchone() is not None


def registrar_pago_movil_plan(
    comercio_id,
    plan_tipo,
    referencia,
    fecha_transferencia,
    banco_emisor=None,
    telefono_pagador=None,
):
    """
    Registra pago móvil reportado por el comercio, valida datos y activa el plan.
    """
    plan_tipo = (plan_tipo or 'basica').lower()
    if plan_tipo not in PLANES or plan_tipo == 'gratis':
        return False, 'Plan no válido para pago.', None

    referencia = (referencia or '').strip()
    if not re.match(r'^\d{6}$', referencia):
        return False, 'La referencia de pago móvil debe tener exactamente 6 dígitos.', None

    banco_emisor = (banco_emisor or '').strip()
    if not banco_emisor:
        return False, 'Indica el banco emisor del pago móvil.', None

    telefono_pagador = (telefono_pagador or '').strip()
    if len(re.sub(r'\D', '', telefono_pagador)) < 10:
        return False, 'Indica un teléfono válido desde el cual realizaste el pago.', None

    if not fecha_transferencia:
        return False, 'Indica la fecha de la transferencia.', None

    if _referencia_ya_usada(referencia):
        return False, 'Esta referencia ya fue registrada en el sistema.', None

    montos = calcular_monto_pago_plan(plan_tipo)
    if not montos:
        return False, 'Plan no encontrado.', None

    plan = obtener_plan_por_codigo(plan_tipo)
    limite = limite_para_plan(plan_tipo)
    if es_limite_ilimitado(limite):
        limite = None
    plan_id = plan.get('id')
    dias = plan.get('dias_duracion') or 30
    monto_usd = montos['monto_usd']
    monto_bs = montos['monto_bs']

    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT fecha_vencimiento FROM comercios WHERE id = ?',
                (int(comercio_id),),
            )
            comercio = cursor.fetchone()
            if not comercio:
                return False, 'Comercio no encontrado.', None

            nueva_fecha = _calcular_fecha_vencimiento_prorrateo(
                comercio['fecha_vencimiento'], dias
            )

        from backend.payments import activar_suscripcion_con_pago

        exito, mensaje, datos_tx = activar_suscripcion_con_pago(
            comercio_id,
            plan_id,
            plan_tipo,
            limite,
            nueva_fecha,
            pago_registro={
                'columnas': (
                    'tienda_id',
                    'plan_id',
                    'monto',
                    'metodo',
                    'referencia',
                    'banco_origen',
                    'telefono_pagador',
                    'estado',
                ),
                'valores': (
                    int(comercio_id),
                    plan_id,
                    monto_bs,
                    'pago_movil',
                    referencia,
                    banco_emisor,
                    telefono_pagador,
                    'aprobado',
                ),
            },
            solicitud_registro={
                'columnas': (
                    'comercio_id',
                    'plan_tipo',
                    'referencia',
                    'fecha_transferencia',
                    'estado',
                ),
                'valores': (
                    int(comercio_id),
                    plan_tipo,
                    referencia,
                    fecha_transferencia,
                    'aprobado',
                ),
            },
        )
        if not exito:
            return False, mensaje, None

        return (
            True,
            (
                f'Pago registrado. Plan {plan["nombre"]} activo hasta {nueva_fecha}. '
                f'Monto: ${monto_usd:.2f} USD ({monto_bs:.2f} Bs).'
            ),
            {
                'referencia': referencia,
                'plan_tipo': plan_tipo,
                'fecha_vencimiento': nueva_fecha,
                'estado': 'activo',
                'monto_usd': monto_usd,
                'monto_bs': monto_bs,
                'tasa': montos['tasa'],
                'pago_id': (datos_tx or {}).get('pago_id'),
            },
        )
    except Exception as e:
        return False, f'Error al registrar pago: {e}', None


def activar_suscripcion_por_comprobante(
    comercio_id,
    plan_tipo,
    referencia,
    comprobante_url=None,
    monto_ocr_bs=None,
):
    """
    Valida referencia OCR, registra pago aprobado y renueva suscripción automáticamente.
    """
    plan_tipo = (plan_tipo or 'basica').lower()
    if plan_tipo not in PLANES or plan_tipo == 'gratis':
        return False, 'Plan no válido para pago.', None

    referencia = (referencia or '').strip()
    if not re.match(r'^\d{6}$', referencia):
        return False, 'No se detectó una referencia válida de 6 dígitos.', None

    if _referencia_ya_usada(referencia):
        return False, 'Esta referencia ya fue registrada en el sistema.', None

    montos = calcular_monto_pago_plan(plan_tipo)
    if not montos:
        return False, 'Plan no encontrado.', None

    plan = obtener_plan_por_codigo(plan_tipo)
    if not plan:
        return False, 'Plan no encontrado.', None

    limite = limite_para_plan(plan_tipo)
    if es_limite_ilimitado(limite):
        limite = None
    plan_id = plan.get('id')
    dias = plan.get('dias_duracion') or 30
    monto_bs = float(monto_ocr_bs or montos['monto_bs'])

    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT fecha_vencimiento FROM comercios WHERE id = ?',
                (int(comercio_id),),
            )
            comercio = cursor.fetchone()
            if not comercio:
                return False, 'Comercio no encontrado.', None

            nueva_fecha = _calcular_nueva_fecha_vencimiento(
                comercio['fecha_vencimiento'], dias
            )

        from backend.payments import activar_suscripcion_con_pago

        exito, mensaje, datos_tx = activar_suscripcion_con_pago(
            comercio_id,
            plan_id,
            plan_tipo,
            limite,
            nueva_fecha,
            pago_registro={
                'columnas': (
                    'tienda_id',
                    'plan_id',
                    'monto',
                    'metodo',
                    'referencia',
                    'banco_origen',
                    'estado',
                ),
                'valores': (
                    int(comercio_id),
                    plan_id,
                    monto_bs,
                    'pago_movil_ocr',
                    referencia,
                    'Banco Caribe',
                    'aprobado',
                ),
            },
            solicitud_registro={
                'columnas': (
                    'comercio_id',
                    'plan_tipo',
                    'referencia',
                    'fecha_transferencia',
                    'estado',
                ),
                'valores': (
                    int(comercio_id),
                    plan_tipo,
                    referencia,
                    datetime.now().date(),
                    'aprobado',
                ),
            },
        )
        if not exito:
            return False, mensaje, None

        return (
            True,
            f'Pago verificado. Plan {plan["nombre"]} activo hasta {nueva_fecha}.',
            {
                'referencia': referencia,
                'plan_tipo': plan_tipo,
                'fecha_vencimiento': nueva_fecha,
                'estado': 'activo',
                'monto_usd': montos['monto_usd'],
                'monto_bs': monto_bs,
                'tasa': montos['tasa'],
                'comprobante_url': comprobante_url,
                'pago_id': (datos_tx or {}).get('pago_id'),
            },
        )
    except Exception as error:
        return False, f'Error al activar suscripción: {error}', None


def obtener_datos_pago_movil(plan_tipo=None):
    """Lee configuración de pago móvil y montos calculados para un plan."""
    from backend.stores import obtener_config, obtener_tasa_dolar
    from config import PAGO_MOVIL_DEFAULT

    tasa = float(obtener_tasa_dolar() or 1.0)
    datos = {
        'banco': obtener_config('pago_movil_banco', PAGO_MOVIL_DEFAULT['banco']),
        'cedula_rif': obtener_config(
            'pago_movil_cedula', PAGO_MOVIL_DEFAULT['cedula_rif']
        ),
        'telefono': obtener_config(
            'pago_movil_telefono', PAGO_MOVIL_DEFAULT['telefono']
        ),
        'tasa': tasa,
    }
    if plan_tipo:
        montos = calcular_monto_pago_plan(plan_tipo)
        if montos:
            datos.update(montos)
    return datos

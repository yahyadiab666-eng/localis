"""Ciclo de vida automático de suscripciones, avisos y pagos."""

import re
import sqlite3
from datetime import datetime, timedelta

from backend.db import get_db_connection
from backend.plans import (
    PLANES,
    MENSAJE_LIMITE_PRODUCTOS,
    clasificar_cambio_plan,
    es_downgrade,
    es_limite_ilimitado,
    obtener_plan_por_codigo,
    limite_desde_plan_id,
    limite_para_plan,
)
from backend.utils import formatear_fecha

def verificar_vencimientos_comercios():
    """
    Aplica downgrades programados y marca como vencidos los comercios expirados.
    Se ejecuta al arranque y periódicamente en requests autenticados.
    """
    aplicados = aplicar_planes_pendientes()
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                UPDATE comercios
                SET estado_pago = 'vencido', visible = 0
                WHERE fecha_vencimiento < CURRENT_DATE
                  AND estado_pago IN ('activo', 'gratis')
                """
            )
            conexion.commit()
            return cursor.rowcount + aplicados
    except Exception as e:
        print(f'Aviso verificación vencimientos: {e}')
        return aplicados


def aplicar_planes_pendientes():
    """
    Al iniciar un nuevo ciclo, aplica el plan inferior programado (downgrade).
    """
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                UPDATE comercios c
                SET plan_tipo = c.plan_pendiente,
                    plan_id = COALESCE(
                        c.plan_id_pendiente,
                        (SELECT p.id FROM planes p
                         WHERE p.codigo = c.plan_pendiente AND p.activo = 1
                         LIMIT 1)
                    ),
                    limite_productos = COALESCE(
                        (SELECT p.limite_productos FROM planes p
                         WHERE p.codigo = c.plan_pendiente AND p.activo = 1
                         LIMIT 1),
                        c.limite_productos
                    ),
                    plan_pendiente = NULL,
                    plan_id_pendiente = NULL
                WHERE c.plan_pendiente IS NOT NULL
                  AND c.fecha_vencimiento <= CURRENT_DATE
                """
            )
            conexion.commit()
            return cursor.rowcount
    except Exception as e:
        print(f'Aviso aplicar planes pendientes: {e}')
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
                  AND fecha_vencimiento < CURRENT_DATE
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
    with get_db_connection(row_factory=sqlite3.Row) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT c.estado_pago, c.plan_tipo, c.limite_productos,
                   p.limite_productos AS plan_limite,
                   (SELECT COUNT(*) FROM productos WHERE comercio_id = c.id) AS total_productos
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

    limite = comercio['limite_productos']
    if comercio['plan_limite'] is not None:
        limite = comercio['plan_limite']
    if limite is None:
        limite = limite_para_plan(comercio['plan_tipo'])
    if es_limite_ilimitado(limite):
        return True, None

    actual = int(comercio['total_productos'] or 0)
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


def _dias_restantes_suscripcion(fecha_vencimiento):
    """Días hasta la fecha de vencimiento (0 si ya venció o no hay fecha)."""
    if not fecha_vencimiento:
        return 0
    try:
        venc = datetime.strptime(
            formatear_fecha(fecha_vencimiento), '%Y-%m-%d'
        ).date()
        return max(0, (venc - datetime.now().date()).days)
    except (TypeError, ValueError):
        return 0


def _calcular_fecha_vencimiento_desde_hoy(dias=30):
    """Nuevo ciclo desde la fecha actual del cambio (upgrade)."""
    return (datetime.now().date() + timedelta(days=dias)).strftime('%Y-%m-%d')


def _calcular_nueva_fecha_vencimiento(fecha_vencimiento_actual, dias=30):
    """max(fecha_actual, fecha_vencimiento_actual) + dias (renovación)."""
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


def _calcular_monto_upgrade(comercio, plan_destino):
    """
    Ajuste por upgrade: precio del plan nuevo menos crédito del periodo no usado.
    """
    plan_actual_codigo = (comercio.get('plan_tipo') or 'gratis').lower()
    plan_nuevo = obtener_plan_por_codigo(plan_destino)
    plan_actual = obtener_plan_por_codigo(plan_actual_codigo)
    if not plan_nuevo:
        return None

    precio_nuevo = _precio_usd_plan(plan_nuevo)
    precio_actual = _precio_usd_plan(plan_actual or {})
    dias_ciclo = int(plan_nuevo.get('dias_duracion') or 30)

    estado = (comercio.get('estado_pago') or 'activo').lower()
    if plan_actual_codigo == 'gratis' or estado == 'vencido' or precio_actual <= 0:
        return round(precio_nuevo, 2)

    dias_restantes = _dias_restantes_suscripcion(comercio.get('fecha_vencimiento'))
    if dias_restantes <= 0:
        return round(precio_nuevo, 2)

    credito = precio_actual * dias_restantes / max(1, dias_ciclo)
    return round(max(0.0, precio_nuevo - credito), 2)


def calcular_cotizacion_cambio_plan(comercio, plan_tipo_destino, tasa=None):
    """
    Cotiza un cambio de plan según upgrade, downgrade o renovación.
    Retorna dict con tipo_cambio, montos, fechas estimadas y mensaje UI.
    """
    from backend.stores import obtener_tasa_dolar

    plan_tipo_destino = (plan_tipo_destino or 'basica').lower()
    if plan_tipo_destino not in PLANES or plan_tipo_destino == 'gratis':
        return None

    plan = obtener_plan_por_codigo(plan_tipo_destino)
    if not plan:
        return None

    comercio = comercio or {}
    plan_actual = (comercio.get('plan_tipo') or 'gratis').lower()
    tipo_cambio = clasificar_cambio_plan(plan_actual, plan_tipo_destino)
    dias = int(plan.get('dias_duracion') or 30)
    if tasa is None:
        tasa = float(obtener_tasa_dolar() or 1.0)
    else:
        tasa = float(tasa)
    precio_completo = _precio_usd_plan(plan)

    resultado = {
        'plan_tipo': plan_tipo_destino,
        'plan_nombre': plan.get('nombre', plan_tipo_destino),
        'plan_actual': plan_actual,
        'tipo_cambio': tipo_cambio,
        'tasa': tasa,
        'precio_usd_completo': precio_completo,
        'requiere_pago': True,
        'plan_pendiente_actual': comercio.get('plan_pendiente'),
    }

    if tipo_cambio == 'downgrade':
        fecha_aplicacion = formatear_fecha(comercio.get('fecha_vencimiento'))
        if not fecha_aplicacion:
            fecha_aplicacion = _calcular_fecha_fin_prueba(comercio)
        resultado.update({
            'monto_usd': 0.0,
            'monto_bs': 0.0,
            'requiere_pago': False,
            'fecha_vencimiento_estimada': fecha_aplicacion,
            'fecha_aplicacion_downgrade': fecha_aplicacion,
            'mensaje': (
                f'Mantendrás tu plan actual hasta el {fecha_aplicacion}. '
                f'El plan {plan.get("nombre")} se aplicará automáticamente '
                f' al iniciar el próximo ciclo.'
            ),
        })
        return resultado

    if tipo_cambio == 'upgrade':
        monto_usd = _calcular_monto_upgrade(comercio, plan_tipo_destino)
        nueva_fecha = _calcular_fecha_vencimiento_desde_hoy(dias)
        resultado.update({
            'monto_usd': monto_usd,
            'monto_bs': round(monto_usd * tasa, 2),
            'fecha_vencimiento_estimada': nueva_fecha,
            'mensaje': (
                f'Upgrade inmediato: pagas el ajuste de ${monto_usd:.2f} USD '
                f'({round(monto_usd * tasa, 2):.2f} Bs) y tu nuevo ciclo vence el '
                f'{nueva_fecha}.'
            ),
        })
        return resultado

    # Renovación (mismo plan)
    nueva_fecha = _calcular_nueva_fecha_vencimiento(comercio.get('fecha_vencimiento'), dias)
    resultado.update({
        'monto_usd': precio_completo,
        'monto_bs': round(precio_completo * tasa, 2),
        'fecha_vencimiento_estimada': nueva_fecha,
        'mensaje': (
            f'Renovación: ${precio_completo:.2f} USD ({round(precio_completo * tasa, 2):.2f} Bs). '
            f'Se suman {dias} días a tu vencimiento actual (hasta {nueva_fecha}).'
        ),
    })
    return resultado


def calcular_monto_pago_plan(plan_tipo, comercio=None, tasa=None):
    """Calcula montos USD y Bs según plan, tasa y contexto del comercio."""
    if comercio:
        cotizacion = calcular_cotizacion_cambio_plan(comercio, plan_tipo, tasa=tasa)
        if cotizacion:
            return {
                'plan_tipo': cotizacion['plan_tipo'],
                'plan_nombre': cotizacion['plan_nombre'],
                'monto_usd': cotizacion['monto_usd'],
                'tasa': cotizacion['tasa'],
                'monto_bs': cotizacion['monto_bs'],
                'tipo_cambio': cotizacion['tipo_cambio'],
                'requiere_pago': cotizacion['requiere_pago'],
            }

    from backend.stores import obtener_tasa_dolar

    plan = obtener_plan_por_codigo(plan_tipo)
    if not plan:
        return None

    if tasa is None:
        tasa = float(obtener_tasa_dolar() or 1.0)
    else:
        tasa = float(tasa)
    monto_usd = _precio_usd_plan(plan)
    return {
        'plan_tipo': (plan_tipo or '').lower(),
        'plan_nombre': plan.get('nombre', plan_tipo),
        'monto_usd': monto_usd,
        'tasa': tasa,
        'monto_bs': round(monto_usd * tasa, 2),
        'tipo_cambio': 'renovacion',
        'requiere_pago': True,
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


def _resolver_fecha_y_montos_activacion(comercio, plan_tipo):
    """Calcula fecha de vencimiento y montos según tipo de cambio de plan."""
    cotizacion = calcular_cotizacion_cambio_plan(comercio, plan_tipo)
    if not cotizacion:
        return None

    if cotizacion['tipo_cambio'] == 'downgrade':
        return cotizacion

    plan = obtener_plan_por_codigo(plan_tipo)
    dias = int(plan.get('dias_duracion') or 30) if plan else 30

    if cotizacion['tipo_cambio'] == 'upgrade':
        nueva_fecha = _calcular_fecha_vencimiento_desde_hoy(dias)
    else:
        nueva_fecha = _calcular_nueva_fecha_vencimiento(
            comercio.get('fecha_vencimiento'), dias
        )

    cotizacion['fecha_vencimiento'] = nueva_fecha
    return cotizacion


def programar_downgrade_plan(comercio_id, plan_tipo):
    """Programa un downgrade para el próximo ciclo de facturación."""
    plan_tipo = (plan_tipo or '').lower()
    if plan_tipo not in PLANES or plan_tipo == 'gratis':
        return False, 'Plan no válido.', None

    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT id, plan_tipo, plan_id, fecha_vencimiento, estado_pago
                FROM comercios WHERE id = ?
                """,
                (int(comercio_id),),
            )
            comercio = cursor.fetchone()
            if not comercio:
                return False, 'Comercio no encontrado.', None

            comercio_dict = dict(comercio)
            if not es_downgrade(comercio_dict.get('plan_tipo'), plan_tipo):
                return False, 'Este cambio no es un downgrade. Usa el flujo de pago.', None

            plan = obtener_plan_por_codigo(plan_tipo)
            if not plan:
                return False, 'Plan no encontrado.', None

            plan_id_pendiente = plan.get('id')
            cursor.execute(
                """
                UPDATE comercios
                SET plan_pendiente = ?, plan_id_pendiente = ?
                WHERE id = ?
                """,
                (plan_tipo, plan_id_pendiente, int(comercio_id)),
            )
            conexion.commit()

        cotizacion = calcular_cotizacion_cambio_plan(comercio_dict, plan_tipo)
        fecha_aplicacion = (cotizacion or {}).get('fecha_aplicacion_downgrade')
        return (
            True,
            (
                f'Cambio programado al plan {plan.get("nombre")}. '
                f'Seguirás con tu plan actual hasta el {fecha_aplicacion or "fin del periodo pagado"}.'
            ),
            {
                'plan_tipo': plan_tipo,
                'plan_pendiente': plan_tipo,
                'fecha_aplicacion': fecha_aplicacion,
                'tipo_cambio': 'downgrade',
            },
        )
    except Exception as error:
        return False, f'Error al programar downgrade: {error}', None


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

    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT id, plan_tipo, plan_id, fecha_vencimiento, estado_pago
                FROM comercios WHERE id = ?
                """,
                (int(comercio_id),),
            )
            comercio = cursor.fetchone()
            if not comercio:
                return False, 'Comercio no encontrado.', None

            comercio_dict = dict(comercio)
            if es_downgrade(comercio_dict.get('plan_tipo'), plan_tipo):
                return programar_downgrade_plan(comercio_id, plan_tipo)

            resolucion = _resolver_fecha_y_montos_activacion(comercio_dict, plan_tipo)
            if not resolucion:
                return False, 'Plan no encontrado.', None

    except Exception as e:
        return False, f'Error al validar comercio: {e}', None

    montos = calcular_monto_pago_plan(plan_tipo, comercio_dict)
    if not montos or montos.get('requiere_pago') is False:
        return False, 'No se pudo calcular el monto del plan.', None

    plan = obtener_plan_por_codigo(plan_tipo)
    limite = limite_para_plan(plan_tipo)
    if es_limite_ilimitado(limite):
        limite = None
    plan_id = plan.get('id')
    monto_usd = montos['monto_usd']
    monto_bs = montos['monto_bs']
    nueva_fecha = resolucion['fecha_vencimiento']

    try:
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
                'tipo_cambio': resolucion['tipo_cambio'],
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

    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT id, plan_tipo, plan_id, fecha_vencimiento, estado_pago
                FROM comercios WHERE id = ?
                """,
                (int(comercio_id),),
            )
            comercio = cursor.fetchone()
            if not comercio:
                return False, 'Comercio no encontrado.', None

            comercio_dict = dict(comercio)
            if es_downgrade(comercio_dict.get('plan_tipo'), plan_tipo):
                return programar_downgrade_plan(comercio_id, plan_tipo)

            resolucion = _resolver_fecha_y_montos_activacion(comercio_dict, plan_tipo)
            if not resolucion:
                return False, 'Plan no encontrado.', None

    except Exception as e:
        return False, f'Error al validar comercio: {e}', None

    montos = calcular_monto_pago_plan(plan_tipo, comercio_dict)
    if not montos:
        return False, 'Plan no encontrado.', None

    plan = obtener_plan_por_codigo(plan_tipo)
    if not plan:
        return False, 'Plan no encontrado.', None

    limite = limite_para_plan(plan_tipo)
    if es_limite_ilimitado(limite):
        limite = None
    plan_id = plan.get('id')
    monto_bs = float(monto_ocr_bs or montos['monto_bs'])
    nueva_fecha = resolucion['fecha_vencimiento']

    try:
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
                'tipo_cambio': resolucion['tipo_cambio'],
                'comprobante_url': comprobante_url,
                'pago_id': (datos_tx or {}).get('pago_id'),
            },
        )
    except Exception as error:
        return False, f'Error al activar suscripción: {error}', None


def obtener_datos_pago_movil(plan_tipo=None):
    """Lee configuración de pago móvil y montos calculados para un plan."""
    from backend.stores import obtener_configs, obtener_tasa_dolar
    from config import PAGO_MOVIL_DEFAULT

    tasa = float(obtener_tasa_dolar() or 1.0)
    configs = obtener_configs({
        'pago_movil_banco': PAGO_MOVIL_DEFAULT['banco'],
        'pago_movil_cedula': PAGO_MOVIL_DEFAULT['cedula_rif'],
        'pago_movil_telefono': PAGO_MOVIL_DEFAULT['telefono'],
    })
    datos = {
        'banco': configs['pago_movil_banco'],
        'cedula_rif': configs['pago_movil_cedula'],
        'telefono': configs['pago_movil_telefono'],
        'tasa': tasa,
    }
    if plan_tipo:
        montos = calcular_monto_pago_plan(plan_tipo, tasa=tasa)
        if montos:
            datos.update(montos)
    return datos

"""Constantes y utilidades de planes de suscripción."""

import sqlite3

from backend.db import get_db_connection

PLANES = {
    'gratis': {
        'nombre': 'Plan Gratis / Prueba',
        'precio_usd': 0,
        'limite_productos': 50,
        'dias_duracion': 30,
    },
    'basica': {
        'nombre': 'Plan Básico',
        'precio_usd': 10,
        'limite_productos': 100,
        'dias_duracion': 30,
    },
    'pro': {
        'nombre': 'Pro',
        'precio_usd': 15,
        'limite_productos': 300,
        'dias_duracion': 30,
    },
    'business': {
        'nombre': 'Business',
        'precio_usd': 35,
        'limite_productos': -1,
        'dias_duracion': 30,
    },
}

LIMITE_ILIMITADO = -1
MENSAJE_LIMITE_PRODUCTOS = (
    'Has alcanzado el límite de productos de tu plan actual. '
    'Actualiza tu suscripción para seguir publicando.'
)

PLAN_GRATIS_CODIGO = 'gratis'

ORDEN_PLANES = ['gratis', 'basica', 'pro', 'business']

PLAN_BENEFICIOS = {
    'gratis': {
        'productos': 'Hasta 50 productos',
        'visibilidad': 'Visible en catálogo Localis',
        'soporte': 'Soporte por WhatsApp',
        'duracion': '30 días de prueba gratuita',
    },
    'basica': {
        'productos': 'Hasta 100 productos',
        'visibilidad': 'Presencia en catálogo público',
        'soporte': 'Soporte estándar',
        'duracion': 'Renovación mensual (30 días)',
    },
    'pro': {
        'productos': 'Hasta 300 productos',
        'visibilidad': 'Mayor visibilidad en búsquedas',
        'soporte': 'Soporte prioritario',
        'duracion': 'Renovación mensual (30 días)',
    },
    'business': {
        'productos': 'Productos ilimitados',
        'visibilidad': 'Máxima visibilidad y destacado',
        'soporte': 'Soporte prioritario dedicado',
        'duracion': 'Renovación mensual (30 días)',
    },
}


def es_limite_ilimitado(limite):
    return limite is None or limite < 0


def obtener_beneficios_plan(codigo):
    plan = PLANES.get(codigo, {})
    extras = PLAN_BENEFICIOS.get(codigo, {})
    limite = plan.get('limite_productos')
    return {
        'codigo': codigo,
        'nombre': plan.get('nombre', codigo),
        'precio_usd': plan.get('precio_usd', 0),
        'limite_productos': limite,
        'limite_texto': 'Ilimitados' if es_limite_ilimitado(limite) else str(limite),
        'beneficios': list(extras.values()),
        **extras,
    }


def obtener_plan_por_codigo(codigo):
    """Busca un plan activo en la tabla planes; fallback a constantes."""
    codigo = (codigo or 'basica').lower()
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT id, codigo, nombre, precio, limite_productos,
                       soporte_prioritario, dias_duracion, destacado, activo
                FROM planes
                WHERE codigo = ? AND activo = 1
                """,
                (codigo,),
            )
            fila = cursor.fetchone()
            if fila:
                return dict(fila)
    except Exception:
        pass

    plan = PLANES.get(codigo)
    if not plan:
        return None
    return {
        'id': None,
        'codigo': codigo,
        'nombre': plan['nombre'],
        'precio': plan['precio_usd'],
        'limite_productos': plan['limite_productos'],
        'dias_duracion': plan.get('dias_duracion', 30),
        'soporte_prioritario': 0,
        'destacado': 0,
        'activo': 1,
    }


def limite_desde_plan_id(plan_id):
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT limite_productos FROM planes WHERE id = ? AND activo = 1',
                (plan_id,),
            )
            fila = cursor.fetchone()
            if fila:
                return fila[0]
    except Exception:
        pass
    return 50


def plan_recomendado_para_cantidad(plan_tipo_actual, cantidad_productos):
    """Plan mínimo (superior al actual) que cubre la cantidad indicada."""
    plan_tipo_actual = (plan_tipo_actual or 'gratis').lower()
    if plan_tipo_actual not in ORDEN_PLANES:
        plan_tipo_actual = 'gratis'

    idx_actual = ORDEN_PLANES.index(plan_tipo_actual)
    for codigo in ORDEN_PLANES[idx_actual + 1 :]:
        limite = limite_para_plan(codigo)
        if es_limite_ilimitado(limite) or cantidad_productos <= limite:
            return codigo
    return 'business'


def mensaje_limite_importacion(plan_tipo_actual, cantidad_archivo, limite_actual):
    """
    Mensaje contextual cuando un CSV/Excel supera el límite del plan.
    Retorna (mensaje, codigo_plan_sugerido).
    """
    plan_tipo_actual = (plan_tipo_actual or 'gratis').lower()
    plan_actual = obtener_plan_por_codigo(plan_tipo_actual) or {}
    nombre_actual = plan_actual.get('nombre', plan_tipo_actual)

    plan_sugerido_codigo = plan_recomendado_para_cantidad(
        plan_tipo_actual, cantidad_archivo
    )
    plan_sugerido = obtener_plan_por_codigo(plan_sugerido_codigo) or {}
    nombre_sugerido = plan_sugerido.get('nombre', plan_sugerido_codigo)
    precio_sugerido = float(
        plan_sugerido.get('precio') or plan_sugerido.get('precio_usd') or 0
    )
    limite_sugerido = plan_sugerido.get('limite_productos')
    if es_limite_ilimitado(limite_sugerido):
        capacidad_sugerida = 'productos ilimitados'
    else:
        capacidad_sugerida = f'hasta {limite_sugerido} productos'

    mensaje = (
        f'Tu archivo contiene {cantidad_archivo} productos, pero tu plan '
        f'{nombre_actual} permite hasta {limite_actual}. '
        f'Actualiza al plan {nombre_sugerido} (${precio_sugerido:.0f}/mes, '
        f'{capacidad_sugerida}) para importar todo tu inventario. '
        f'La importación fue cancelada y no se modificó ningún producto.'
    )
    return mensaje, plan_sugerido_codigo


def limite_para_plan(plan_tipo):
    """Retorna el límite de productos; -1 = ilimitado."""
    plan = obtener_plan_por_codigo(plan_tipo or 'basica')
    if not plan:
        return 50
    limite = plan.get('limite_productos')
    if limite is None:
        return LIMITE_ILIMITADO
    return limite


def validar_cantidad_productos(plan_tipo, cantidad):
    """
    Verifica si la cantidad de productos está dentro del límite del plan.
    Retorna (True, None) o (False, mensaje_error).
    """
    limite = limite_para_plan(plan_tipo)
    if es_limite_ilimitado(limite):
        return True, None
    if cantidad > limite:
        plan_nombre = obtener_plan_por_codigo(plan_tipo).get('nombre', plan_tipo)
        return (
            False,
            f'El archivo contiene {cantidad} productos, pero tu plan {plan_nombre}'
            f' permite un máximo de {limite}. Actualiza tu plan o reduce el archivo.',
        )
    return True, None

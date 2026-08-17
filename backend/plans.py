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
    'basica': {'nombre': 'Básica', 'precio_usd': 10, 'limite_productos': 50, 'dias_duracion': 30},
    'pro': {'nombre': 'Pro', 'precio_usd': 17, 'limite_productos': 200, 'dias_duracion': 30},
    'business': {
        'nombre': 'Business',
        'precio_usd': 35,
        'limite_productos': None,
        'dias_duracion': 30,
    },
}

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
        'productos': 'Hasta 50 productos',
        'visibilidad': 'Presencia en catálogo público',
        'soporte': 'Soporte estándar',
        'duracion': 'Renovación mensual (30 días)',
    },
    'pro': {
        'productos': 'Hasta 200 productos',
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


def obtener_beneficios_plan(codigo):
    plan = PLANES.get(codigo, {})
    extras = PLAN_BENEFICIOS.get(codigo, {})
    limite = plan.get('limite_productos')
    return {
        'codigo': codigo,
        'nombre': plan.get('nombre', codigo),
        'precio_usd': plan.get('precio_usd', 0),
        'limite_productos': limite,
        'limite_texto': 'Ilimitados' if limite is None else str(limite),
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


def limite_para_plan(plan_tipo):
    """Retorna el límite de productos; None = ilimitado."""
    plan = obtener_plan_por_codigo(plan_tipo or 'basica')
    if not plan:
        return 50
    return plan.get('limite_productos')


def validar_cantidad_productos(plan_tipo, cantidad):
    """
    Verifica si la cantidad de productos está dentro del límite del plan.
    Retorna (True, None) o (False, mensaje_error).
    """
    limite = limite_para_plan(plan_tipo)
    if limite is None:
        return True, None
    if cantidad > limite:
        plan_nombre = obtener_plan_por_codigo(plan_tipo).get('nombre', plan_tipo)
        return (
            False,
            f'El archivo contiene {cantidad} productos, pero tu plan {plan_nombre}'
            f' permite un máximo de {limite}. Actualiza tu plan o reduce el archivo.',
        )
    return True, None

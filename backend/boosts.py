"""Módulo de "Destacados / Boosts" (micro-pago de visibilidad).

Modelo accesible de micro-pago:
  - PRECIO_BOOST_USD   = 3.00 USD
  - DURACION_BOOST_DIAS = 7 días  (equivalente a ~0.43 USD/día)

Al contratar un boost se registra la fecha de inicio y de fin en la entidad
objetivo (``comercios`` o ``productos``) y se guarda una fila de auditoría en la
tabla ``boosts``. La portada prioriza los elementos destacados mediante un ORDER
BY ligero (ver ``backend.stores``) sin afectar el resto de la lógica del
catálogo público.

Nota: este módulo define el MODELO de micro-pago (precio, duración y registro de
fechas). El cobro real puede conectarse después al flujo de pago móvil existente
sin modificar este contrato ni la lógica de planes/suscripciones.
"""

import sqlite3
from datetime import datetime, timedelta

from backend.db import get_db_connection

PRECIO_BOOST_USD = 3.0
DURACION_BOOST_DIAS = 7

TIPOS_BOOST_VALIDOS = ('tienda', 'producto')


def _fecha_fin_desde_hoy(dias=None):
    dias = int(dias) if dias else DURACION_BOOST_DIAS
    return datetime.now() + timedelta(days=dias)


def boost_activo(fecha_fin):
    """True si ``fecha_fin`` (str/datetime/None) sigue vigente."""
    if not fecha_fin:
        return False
    try:
        if isinstance(fecha_fin, datetime):
            fin = fecha_fin
        else:
            fin = datetime.strptime(str(fecha_fin)[:19], '%Y-%m-%d %H:%M:%S')
        return fin > datetime.now()
    except (TypeError, ValueError):
        return False


def _registrar_boost(cursor, comercio_id, tipo, objetivo_id, fecha_inicio, fecha_fin, dias):
    cursor.execute(
        """
        INSERT INTO boosts
            (comercio_id, tipo, objetivo_id, precio_usd, dias_duracion,
             estado, fecha_inicio, fecha_fin)
        VALUES (?, ?, ?, ?, ?, 'activo', ?, ?)
        """,
        (
            int(comercio_id),
            tipo,
            int(objetivo_id) if objetivo_id is not None else None,
            PRECIO_BOOST_USD,
            int(dias or DURACION_BOOST_DIAS),
            fecha_inicio,
            fecha_fin,
        ),
    )


def contratar_boost_tienda(comercio_id, dias=None):
    """Activa el destaque de una tienda. Retorna ``(exito, mensaje)``."""
    try:
        dias = int(dias or DURACION_BOOST_DIAS)
        inicio = datetime.now()
        fin = _fecha_fin_desde_hoy(dias)

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT 1 FROM comercios WHERE id = ?', (int(comercio_id),)
            )
            if not cursor.fetchone():
                return False, 'Comercio no encontrado.'

            cursor.execute(
                """
                UPDATE comercios
                SET boost_inicio = ?, boost_fin = ?
                WHERE id = ?
                """,
                (inicio, fin, int(comercio_id)),
            )
            _registrar_boost(
                cursor, comercio_id, 'tienda', None, inicio, fin, dias
            )
            conexion.commit()
        return True, f'Tu tienda quedó destacada por {dias} día(s).'
    except Exception as error:
        return False, f'Error al destacar la tienda: {error}'


def contratar_boost_producto(comercio_id, producto_id, dias=None):
    """Activa el destaque de un producto. Retorna ``(exito, mensaje)``."""
    try:
        dias = int(dias or DURACION_BOOST_DIAS)
        inicio = datetime.now()
        fin = _fecha_fin_desde_hoy(dias)

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT 1 FROM productos WHERE id = ? AND comercio_id = ?',
                (int(producto_id), int(comercio_id)),
            )
            if not cursor.fetchone():
                return False, 'Producto no encontrado.'

            cursor.execute(
                """
                UPDATE productos
                SET boost_inicio = ?, boost_fin = ?
                WHERE id = ?
                """,
                (inicio, fin, int(producto_id)),
            )
            _registrar_boost(
                cursor, comercio_id, 'producto', producto_id, inicio, fin, dias
            )
            conexion.commit()
        return True, f'Producto destacado por {dias} día(s).'
    except Exception as error:
        return False, f'Error al destacar el producto: {error}'


def estado_boosts_comercio(comercio_id):
    """Resumen de boosts vigentes de un comercio (para el panel).

    Retorna::
        {
            'tienda_boost_fin': 'YYYY-MM-DD HH:MM:SS' | None,
            'tienda_activo': bool,
            'productos': {producto_id: {'boost_fin': ..., 'activo': bool}},
        }
    """
    resultado = {'tienda_boost_fin': None, 'tienda_activo': False, 'productos': {}}
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT boost_inicio, boost_fin FROM comercios WHERE id = ?',
                (int(comercio_id),),
            )
            fila = cursor.fetchone()
            if fila:
                boost_fin = fila['boost_fin'] if isinstance(fila, dict) else None
                resultado['tienda_boost_fin'] = boost_fin
                resultado['tienda_activo'] = boost_activo(boost_fin)

            cursor.execute(
                """
                SELECT id, boost_fin
                FROM productos
                WHERE comercio_id = ? AND boost_fin IS NOT NULL
                """,
                (int(comercio_id),),
            )
            for fila in cursor.fetchall():
                pid = fila['id'] if isinstance(fila, dict) else None
                boost_fin = fila['boost_fin'] if isinstance(fila, dict) else None
                if pid is None:
                    continue
                resultado['productos'][int(pid)] = {
                    'boost_fin': boost_fin,
                    'activo': boost_activo(boost_fin),
                }
    except Exception as error:
        print(f'[Localis Boost] estado no disponible: {error}')
    return resultado

"""Recompensas y referidos B2B por puntos (Localis).

Reglas de negocio:
  - 1 punto por cada comercio referido válido (nombre exacto del referente).
  - 5 puntos = 1 destacado de producto GRATIS por 7 días.

Todas las operaciones son defensivas (``try-except``) y transaccionales para
no romper el flujo de registro ni el panel del comerciante.
"""

import sqlite3

from backend.boosts import PRECIOS_BOOST_USD, aplicar_boost_en_cursor
from backend.db import get_db_connection

PUNTOS_POR_REFERIDO = 1
PUNTOS_POR_DESTACADO = 5
DIAS_DESTACADO_PUNTOS = 7


def _a_int(valor, campo='id'):
    if valor is None or isinstance(valor, bool):
        raise ValueError(f'{campo} inválido.')
    try:
        return int(str(valor).strip())
    except (TypeError, ValueError):
        raise ValueError(f'{campo} inválido.')


def _valor(fila, clave, indice=0):
    if fila is None:
        return None
    if isinstance(fila, dict):
        return fila.get(clave)
    return fila[indice]


# ==========================================
# Resolución de referente y otorgamiento
# ==========================================


def resolver_referente_cursor(cursor, nombre, excluir_id=None):
    """Busca un comercio por nombre exacto (case-insensitive). Nunca lanza."""
    nombre = (nombre or '').strip()
    if not nombre:
        return None
    try:
        query = 'SELECT id, nombre FROM comercios WHERE LOWER(TRIM(nombre)) = LOWER(?)'
        params = [nombre]
        if excluir_id:
            query += ' AND id <> ?'
            params.append(int(excluir_id))
        query += ' ORDER BY id ASC LIMIT 1'
        cursor.execute(query, tuple(params))
        fila = cursor.fetchone()
        if not fila:
            return None
        return {'id': _valor(fila, 'id', 0), 'nombre': _valor(fila, 'nombre', 1)}
    except Exception as error:
        print(f'[Localis Referidos] no se pudo resolver referente: {error}', flush=True)
        return None


def otorgar_referido_cursor(cursor, referente_id, referido_comercio_id, nombre_referente):
    """Suma 1 punto al referente y registra el referido. No hace commit."""
    cursor.execute(
        'UPDATE comercios SET puntos = COALESCE(puntos, 0) + ? WHERE id = ?',
        (PUNTOS_POR_REFERIDO, int(referente_id)),
    )
    cursor.execute(
        """
        INSERT INTO referidos
            (referente_comercio_id, referido_comercio_id, nombre_referente, puntos_otorgados)
        VALUES (?, ?, ?, ?)
        """,
        (
            int(referente_id),
            int(referido_comercio_id),
            (nombre_referente or '').strip() or None,
            PUNTOS_POR_REFERIDO,
        ),
    )


# ==========================================
# Consultas para el panel
# ==========================================


def saldo_puntos(comercio_id):
    """Puntos actuales del comercio (0 si no existe o hay error)."""
    try:
        comercio_id = _a_int(comercio_id, 'comercio_id')
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT COALESCE(puntos, 0) FROM comercios WHERE id = ?',
                (comercio_id,),
            )
            fila = cursor.fetchone()
            if not fila:
                return 0
            return int(_valor(fila, 'puntos', 0) or 0)
    except Exception as error:
        print(f'[Localis Referidos] saldo no disponible: {error}', flush=True)
        return 0


def resumen_premios(comercio_id):
    """Balance, referidos y canjes del comercio (defensivo)."""
    resumen = {
        'puntos': 0,
        'referidos_total': 0,
        'referidos': [],
        'canjes_total': 0,
        'puntos_por_destacado': PUNTOS_POR_DESTACADO,
        'dias_destacado': DIAS_DESTACADO_PUNTOS,
    }
    try:
        comercio_id = _a_int(comercio_id, 'comercio_id')
    except ValueError:
        return resumen
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT COALESCE(puntos, 0) AS puntos FROM comercios WHERE id = ?',
                (comercio_id,),
            )
            fila = cursor.fetchone()
            if fila:
                resumen['puntos'] = int(fila['puntos'] or 0)

            cursor.execute(
                'SELECT COUNT(*) AS n FROM referidos WHERE referente_comercio_id = ?',
                (comercio_id,),
            )
            fila = cursor.fetchone()
            resumen['referidos_total'] = int(fila['n'] or 0) if fila else 0

            cursor.execute(
                """
                SELECT nombre_referente, puntos_otorgados, fecha_registro
                FROM referidos
                WHERE referente_comercio_id = ?
                ORDER BY id DESC
                LIMIT 50
                """,
                (comercio_id,),
            )
            resumen['referidos'] = [dict(f) for f in cursor.fetchall()]

            cursor.execute(
                'SELECT COUNT(*) AS n FROM canjes_puntos WHERE comercio_id = ?',
                (comercio_id,),
            )
            fila = cursor.fetchone()
            resumen['canjes_total'] = int(fila['n'] or 0) if fila else 0
    except Exception as error:
        print(f'[Localis Referidos] resumen no disponible: {error}', flush=True)
    return resumen


# ==========================================
# Canje de puntos por destacado
# ==========================================


def canjear_destacado(comercio_id, producto_id, dias=DIAS_DESTACADO_PUNTOS):
    """Canjea ``PUNTOS_POR_DESTACADO`` por un destacado gratis del producto.

    Descuento atómico (solo si hay saldo) + activación del boost en una única
    transacción. Retorna ``(exito, mensaje)``.
    """
    try:
        comercio_id = _a_int(comercio_id, 'comercio_id')
        producto_id = _a_int(producto_id, 'producto_id')
        dias = _a_int(dias, 'dias')
    except ValueError as error:
        return False, str(error)
    if dias not in PRECIOS_BOOST_USD:
        dias = DIAS_DESTACADO_PUNTOS

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            # Descuento atómico: solo si el saldo alcanza. Evita doble gasto.
            cursor.execute(
                """
                UPDATE comercios
                SET puntos = COALESCE(puntos, 0) - ?
                WHERE id = ? AND COALESCE(puntos, 0) >= ?
                RETURNING puntos
                """,
                (PUNTOS_POR_DESTACADO, comercio_id, PUNTOS_POR_DESTACADO),
            )
            if not cursor.fetchone():
                conexion.rollback()
                cursor.execute(
                    'SELECT COALESCE(puntos, 0) FROM comercios WHERE id = ?',
                    (comercio_id,),
                )
                fila = cursor.fetchone()
                saldo = int(_valor(fila, 'puntos', 0) or 0) if fila else 0
                return (
                    False,
                    f'Necesitas {PUNTOS_POR_DESTACADO} puntos para canjear. '
                    f'Tu saldo actual es {saldo}.',
                )

            ok, msg, datos = aplicar_boost_en_cursor(
                cursor,
                comercio_id,
                producto_id,
                dias,
                metodo='puntos',
                referencia=None,
                precio_usd=0.0,
                registrar_solicitud=False,
            )
            if not ok:
                conexion.rollback()
                return False, msg

            cursor.execute(
                """
                INSERT INTO canjes_puntos
                    (comercio_id, producto_id, puntos_usados, dias_duracion,
                     estado, fecha_inicio, fecha_fin)
                VALUES (?, ?, ?, ?, 'activo', ?, ?)
                """,
                (
                    comercio_id,
                    producto_id,
                    PUNTOS_POR_DESTACADO,
                    dias,
                    datos['inicio'],
                    datos['fin'],
                ),
            )
            conexion.commit()

        return (
            True,
            f'¡Destacado gratis por {dias} días canjeado con {PUNTOS_POR_DESTACADO} puntos!',
        )
    except Exception as error:
        print(
            f'[Localis Referidos] canje falló: {type(error).__name__}: {error}',
            flush=True,
        )
        return False, 'No se pudo canjear el destacado. Intenta de nuevo.'

"""Módulo de "Destacados / Boosts" de PRODUCTO (pago móvil y Binance Pay).

Reutiliza EXACTAMENTE el sistema de pagos existente en ``Localis``:

  - Anti-doble-gasto de referencias: ``backend.subscriptions._referencia_ya_usada``
    (consulta las tablas ``pagos`` y ``solicitudes_pago``).
  - Datos de pago móvil: ``obtener_datos_pago_movil`` (banco, cédula, teléfono,
    tasa) desde ``configuracion_sistema``.
  - Registro del pago: tabla ``solicitudes_pago`` (la misma que usa el pago móvil).
  - Formato de referencia: 6 dígitos para pago móvil; 6-24 dígitos para el ID de
    orden de Binance Pay (idéntico a ``activar_suscripcion_por_comprobante``).

Precios fijos por duración (USD), pagando con Pago Móvil:
  - 3 días -> $2.00
  - 5 días -> $3.00
  - 7 días -> $4.00

Con **Binance Pay (USDT)** el destaque tiene un descuento fijo de 1 USD sobre el
precio original (``DESCUENTO_BINANCE_USD``), así que queda en $1.00 / $2.00 / $3.00.
El precio que se cobra y el que valida el OCR salen SIEMPRE de
:func:`precio_boost` según el método de pago.

Blindaje (fail-safe):
  - Verifica que el ``producto_id`` pertenezca al ``comercio_id`` de la sesión.
  - Verifica que la ``referencia_pago`` no haya sido usada (chequeo previo +
    índice ÚNICO ``idx_boosts_referencia`` como garantía atómica).
  - Sanitiza IDs (enteros estrictos) y referencia (solo dígitos).
  - Fechas en UTC estricto (columnas ``TIMESTAMPTZ``).
  - Pago + auditoría + activación en una única transacción (rollback total).
"""
import logging
logger = logging.getLogger('localis.boosts')

import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

import psycopg2

from backend.db import get_db_connection
from backend.subscriptions import _referencia_ya_usada

# Duración (días) -> precio fijo en USD (pagando con Pago Móvil).
PRECIOS_BOOST_USD = {3: 2.0, 5: 3.0, 7: 4.0}
DIAS_BOOST_VALIDOS = tuple(sorted(PRECIOS_BOOST_USD))

# Descuento fijo (USD) al pagar el destaque con Binance Pay (USDT).
DESCUENTO_BINANCE_USD = 1.0
# Suelo de seguridad: nunca se cobra menos que esto, aunque cambien los precios.
PRECIO_BOOST_MINIMO_USD = 0.5

# Compatibilidad con el contrato anterior del módulo.
DURACION_BOOST_DIAS = 7
PRECIO_BOOST_USD = PRECIOS_BOOST_USD[DURACION_BOOST_DIAS]


def es_metodo_binance(metodo):
    """True si el método de pago es Binance Pay (acepta ``binance*``)."""
    return str(metodo or '').strip().lower().startswith('binance')


def precio_con_descuento_binance(precio_usd, metodo='pago_movil'):
    """Aplica (o no) el descuento de Binance Pay a un precio base en USD.

    Función pura: nunca lanza; ante un precio no numérico devuelve 0.0.
    """
    try:
        base = float(precio_usd or 0)
    except (TypeError, ValueError):
        base = 0.0
    if not es_metodo_binance(metodo):
        return round(base, 2)
    return round(max(PRECIO_BOOST_MINIMO_USD, base - DESCUENTO_BINANCE_USD), 2)


# ==========================================
# Utilidades de validación / sanitización
# ==========================================


def _a_int(valor, campo='id'):
    """Entero limpio y estricto. Rechaza ``bool``, ``None`` y texto no numérico."""
    if valor is None or isinstance(valor, bool):
        raise ValueError(f'{campo} inválido.')
    texto = str(valor).strip()
    if not texto:
        raise ValueError(f'{campo} inválido.')
    try:
        return int(texto)
    except (TypeError, ValueError):
        raise ValueError(f'{campo} inválido.')


def sanitizar_referencia(referencia):
    """Deja solo los dígitos de la referencia (sin espacios ni caracteres raros)."""
    return re.sub(r'\D', '', str(referencia or ''))


def referencia_valida(referencia, metodo='pago_movil_ocr'):
    """True si la referencia cumple el formato del método de pago.

    Pago móvil: exactamente 6 dígitos. Binance Pay: 6-24 dígitos (ID de orden),
    igual que ``activar_suscripcion_por_comprobante``.
    """
    ref = sanitizar_referencia(referencia)
    patron = r'^\d{6,24}$' if str(metodo or '').startswith('binance') else r'^\d{6}$'
    return bool(re.match(patron, ref))


def _ahora_utc():
    return datetime.now(timezone.utc)


def opciones_boost():
    """Opciones de duración/precio para la interfaz del comerciante.

    Cada opción trae el precio de Pago Móvil (``precio_usd``) y el de Binance
    Pay con el descuento ya aplicado (``precio_binance_usd``), para que la
    plantilla muestre ambos sin recalcular nada en JS.
    """
    opciones = []
    for dias in DIAS_BOOST_VALIDOS:
        base = PRECIOS_BOOST_USD[dias]
        binance = precio_con_descuento_binance(base, 'binance')
        opciones.append(
            {
                'dias': dias,
                'precio_usd': base,
                'precio_binance_usd': binance,
                'descuento_binance_usd': round(base - binance, 2),
            }
        )
    return opciones


def precio_boost(dias, metodo='pago_movil'):
    """Precio USD de una duración válida según el método, o ``None``.

    Con Binance Pay se descuenta ``DESCUENTO_BINANCE_USD`` del precio original.
    Sin ``metodo`` devuelve el precio de Pago Móvil (contrato anterior).
    """
    try:
        base = PRECIOS_BOOST_USD.get(_a_int(dias, 'dias'))
    except ValueError:
        return None
    if base is None:
        return None
    return precio_con_descuento_binance(base, metodo)


def boost_activo(fecha_fin):
    """True si ``fecha_fin`` (ISO/UTC, datetime o date) sigue vigente.

    Tolerante a ``None`` y a formatos variados de Supabase: ante cualquier
    valor no interpretable degrada a ``False`` (nunca lanza).
    """
    if not fecha_fin:
        return False
    try:
        if isinstance(fecha_fin, datetime):
            fin = fecha_fin
        elif isinstance(fecha_fin, date):
            fin = datetime(
                fecha_fin.year, fecha_fin.month, fecha_fin.day, tzinfo=timezone.utc
            )
        else:
            fin = datetime.fromisoformat(str(fecha_fin).replace('Z', '+00:00'))
        if fin.tzinfo is None:
            fin = fin.replace(tzinfo=timezone.utc)
        return fin > _ahora_utc()
    except (TypeError, ValueError, OverflowError, OSError):
        return False


def _es_referencia_duplicada(error):
    """True si el error de BD es una violación de unicidad (código 23505)."""
    try:
        if isinstance(error, psycopg2.errors.UniqueViolation):
            return True
    except Exception:
        pass
    return getattr(error, 'pgcode', None) == '23505'


def referencia_ya_usada_boost(referencia):
    """True si la referencia ya existe en la tabla ``boosts`` (auditoría)."""
    ref = sanitizar_referencia(referencia)
    if not ref:
        return False
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT 1 FROM boosts WHERE referencia_pago = ? LIMIT 1', (ref,)
            )
            return cursor.fetchone() is not None
    except Exception as error:
        logger.error(f'[Localis Boost] no se pudo auditar la referencia: {error}')
        return False


def _registrar_intento_rechazado(comercio_id, producto_id, referencia, motivo):
    """Audita un intento de boost rechazado. Nunca lanza (aislado)."""
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                INSERT INTO logs_auditoria (usuario_id, accion, detalles)
                VALUES (?, 'Boost rechazado', ?)
                """,
                (
                    None,
                    f'comercio={comercio_id} producto={producto_id} '
                    f'ref={referencia or "-"} motivo={motivo}',
                ),
            )
            conexion.commit()
    except Exception as error:
        logger.error(f'[Localis Boost] no se pudo auditar rechazo: {error}')


# ==========================================
# Activación del boost de producto
# ==========================================


def activar_boost_producto(
    comercio_id,
    producto_id,
    dias,
    referencia,
    comprobante_url=None,
    comprobante_hash=None,
    metodo='pago_movil_ocr',
):
    """Activa el destaque de un producto tras validar el pago.

    ``referencia``/``comprobante_*`` provienen del OCR del comprobante (igual
    que el flujo de mensualidades). ``metodo`` es ``pago_movil_ocr`` (por
    defecto) o ``binance``; cada uno usa su propio formato de referencia y su
    propio registro de auditoría. Retorna ``(exito, mensaje)``. Ante cualquier
    fallo no deja estado inconsistente ni realiza cargos.
    """
    # 1) Sanitización y control de tipos.
    try:
        comercio_id = _a_int(comercio_id, 'comercio_id')
        producto_id = _a_int(producto_id, 'producto_id')
        dias = _a_int(dias, 'dias')
    except ValueError as error:
        return False, str(error)

    if dias not in PRECIOS_BOOST_USD:
        return False, 'Duración no válida. Elige 3, 5 o 7 días.'

    es_binance = es_metodo_binance(metodo)
    metodo = 'binance' if es_binance else 'pago_movil_ocr'
    referencia = sanitizar_referencia(referencia)
    if not referencia_valida(referencia, metodo):
        _registrar_intento_rechazado(
            comercio_id, producto_id, referencia, 'referencia_invalida'
        )
        return (
            False,
            'No se detectó un ID de orden válido de Binance.'
            if es_binance
            else 'La referencia de pago móvil debe tener exactamente 6 dígitos.',
        )

    # 2) Anti fraude: la referencia no puede haberse usado nunca.
    #    Se audita en el sistema de pagos (pagos/solicitudes_pago = mensualidades)
    #    y en la tabla propia de boosts.
    try:
        if _referencia_ya_usada(referencia) or referencia_ya_usada_boost(referencia):
            _registrar_intento_rechazado(
                comercio_id, producto_id, referencia, 'referencia_reutilizada'
            )
            return False, 'Esta referencia de pago ya fue registrada en el sistema.'
    except Exception as error:
        logger.error(f'[Localis Boost] no se pudo validar la referencia: {error}')
        return False, 'No se pudo validar la referencia de pago. Intenta de nuevo.'

    # Precio real del método elegido (Binance Pay aplica el descuento fijo).
    precio_usd = precio_boost(dias, metodo)
    inicio = _ahora_utc()
    fin = inicio + timedelta(days=dias)
    plan_tipo_boost = f'boost_{dias}d'

    # 3) Transacción atómica: pago + auditoría + activación del producto.
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            # 3a) Propiedad inequívoca producto -> comercio de la sesión.
            cursor.execute(
                'SELECT id FROM productos WHERE id = ? AND comercio_id = ?',
                (producto_id, comercio_id),
            )
            if not cursor.fetchone():
                conexion.rollback()
                _registrar_intento_rechazado(
                    comercio_id, producto_id, referencia, 'producto_no_pertenece'
                )
                return False, 'El producto no pertenece a tu comercio.'

            # 3b) Registro del pago en la tabla de pago móvil existente
            #     (idéntico al flujo de mensualidades: trazabilidad + hash).
            cursor.execute(
                """
                INSERT INTO solicitudes_pago
                    (comercio_id, plan_tipo, referencia, fecha_transferencia, estado,
                     comprobante_url, comprobante_hash)
                VALUES (?, ?, ?, ?, 'aprobado', ?, ?)
                """,
                (
                    comercio_id,
                    plan_tipo_boost,
                    referencia,
                    inicio.strftime('%Y-%m-%d'),
                    comprobante_url,
                    comprobante_hash,
                ),
            )

            # 3c) Auditoría del boost. La referencia es ÚNICA (anti doble-gasto).
            cursor.execute(
                """
                INSERT INTO boosts
                    (comercio_id, tipo, objetivo_id, precio_usd, dias_duracion,
                     referencia_pago, metodo, estado, fecha_inicio, fecha_fin)
                VALUES (?, 'producto', ?, ?, ?, ?, ?, 'activo', ?, ?)
                """,
                (
                    comercio_id,
                    producto_id,
                    precio_usd,
                    dias,
                    referencia,
                    metodo,
                    inicio,
                    fin,
                ),
            )

            # 3d) Activación en el producto (propiedad re-verificada en el WHERE).
            cursor.execute(
                """
                UPDATE productos
                SET boost_inicio = ?, boost_fin = ?
                WHERE id = ? AND comercio_id = ?
                """,
                (inicio, fin, producto_id, comercio_id),
            )
            if cursor.rowcount == 0:
                conexion.rollback()
                _registrar_intento_rechazado(
                    comercio_id, producto_id, referencia, 'producto_no_actualizado'
                )
                return False, 'No se pudo activar el destacado del producto.'

            conexion.commit()

        sufijo = ' · Binance Pay' if es_binance else ''
        return (
            True,
            f'¡Producto destacado por {dias} días! (${precio_usd:.2f} USD{sufijo})',
        )
    except Exception as error:
        # El context manager ya ejecutó rollback al propagarse la excepción.
        if _es_referencia_duplicada(error):
            _registrar_intento_rechazado(
                comercio_id, producto_id, referencia, 'referencia_duplicada'
            )
            return False, 'Esta referencia de pago ya fue utilizada en un destacado.'
        print(
            f'[Localis Boost] fallo al activar: {type(error).__name__}: {error}',
            flush=True,
        )
        return False, 'No se pudo activar el destacado. No se realizó ningún cargo.'


# ==========================================
# Estado de boosts (panel del comerciante)
# ==========================================


def aplicar_boost_en_cursor(
    cursor,
    comercio_id,
    producto_id,
    dias,
    metodo='pago_movil_ocr',
    referencia=None,
    comprobante_url=None,
    comprobante_hash=None,
    precio_usd=0.0,
    registrar_solicitud=True,
):
    """Aplica el boost (auditoría + activación) usando un cursor existente.

    No hace commit: el llamador controla la transacción. Retorna
    ``(exito, mensaje, datos)`` donde ``datos`` incluye ``inicio``/``fin``.
    """
    try:
        comercio_id = _a_int(comercio_id, 'comercio_id')
        producto_id = _a_int(producto_id, 'producto_id')
        dias = _a_int(dias, 'dias')
    except ValueError as error:
        return False, str(error), None

    if dias not in PRECIOS_BOOST_USD:
        return False, 'Duración no válida.', None

    inicio = _ahora_utc()
    fin = inicio + timedelta(days=dias)

    # Propiedad inequívoca producto -> comercio.
    cursor.execute(
        'SELECT id FROM productos WHERE id = ? AND comercio_id = ?',
        (producto_id, comercio_id),
    )
    if not cursor.fetchone():
        return False, 'El producto no pertenece a tu comercio.', None

    if registrar_solicitud:
        cursor.execute(
            """
            INSERT INTO solicitudes_pago
                (comercio_id, plan_tipo, referencia, fecha_transferencia, estado,
                 comprobante_url, comprobante_hash)
            VALUES (?, ?, ?, ?, 'aprobado', ?, ?)
            """,
            (
                comercio_id,
                f'boost_{dias}d',
                referencia,
                inicio.strftime('%Y-%m-%d'),
                comprobante_url,
                comprobante_hash,
            ),
        )

    cursor.execute(
        """
        INSERT INTO boosts
            (comercio_id, tipo, objetivo_id, precio_usd, dias_duracion,
             referencia_pago, metodo, estado, fecha_inicio, fecha_fin)
        VALUES (?, 'producto', ?, ?, ?, ?, ?, 'activo', ?, ?)
        """,
        (comercio_id, producto_id, precio_usd, dias, referencia, metodo, inicio, fin),
    )

    cursor.execute(
        """
        UPDATE productos
        SET boost_inicio = ?, boost_fin = ?
        WHERE id = ? AND comercio_id = ?
        """,
        (inicio, fin, producto_id, comercio_id),
    )
    if cursor.rowcount == 0:
        return False, 'No se pudo activar el destacado.', None

    return True, 'ok', {'inicio': inicio, 'fin': fin}


def estado_boosts_productos(comercio_id):
    """Boosts de producto de un comercio: ``{'productos': {id: {...}}}``."""
    resultado = {'productos': {}}
    try:
        comercio_id = _a_int(comercio_id, 'comercio_id')
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT id, boost_fin
                FROM productos
                WHERE comercio_id = ? AND boost_fin IS NOT NULL
                """,
                (comercio_id,),
            )
            for fila in cursor.fetchall():
                if not isinstance(fila, dict):
                    continue
                pid = fila.get('id')
                if pid is None:
                    continue
                resultado['productos'][int(pid)] = {
                    'boost_fin': fila.get('boost_fin'),
                    'activo': boost_activo(fila.get('boost_fin')),
                }
    except Exception as error:
        logger.error(f'[Localis Boost] estado no disponible: {error}')
    return resultado

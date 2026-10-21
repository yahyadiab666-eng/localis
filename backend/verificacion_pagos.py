"""Auditoría de la verificación automática de pagos.

Registra en ``solicitudes_pago`` **cómo** se validó cada pago: si la API de
Binance confirmó la orden, si no la encontró, si no se pudo verificar o si solo
pasó el filtro OCR del pago móvil. Permite que el admin vea el detalle sin
depender de los logs del worker.

Todo es best-effort: nunca lanza ni bloquea el flujo de pago.
"""

from __future__ import annotations

import logging

from backend.db import get_db_connection

logger = logging.getLogger('localis.verificacion_pagos')

# Códigos de verificación (columna ``solicitudes_pago.verificacion``).
VERIF_BINANCE_CONFIRMADO = 'binance_confirmado'
VERIF_BINANCE_NO_ENCONTRADO = 'binance_no_encontrado'
VERIF_BINANCE_NO_VERIFICADO = 'binance_no_verificado'
VERIF_PAGO_MOVIL_OCR = 'pago_movil_ocr'


def describir_verificacion(metodo, confirmado=None, estado_monto=None, monto=None):
    """Traduce el resultado de la verificación a ``(codigo, detalle)``.

    ``metodo``: ``binance`` o ``pago_movil*``. ``confirmado``: resultado de la
    API de Binance (``True``/``False``/``None``). ``estado_monto``: salida de
    :func:`backend.payment_ocr.clasificar_monto`.
    """
    es_binance = str(metodo or '').startswith('binance')
    if es_binance:
        if confirmado is True:
            codigo = VERIF_BINANCE_CONFIRMADO
        elif confirmado is False:
            codigo = VERIF_BINANCE_NO_ENCONTRADO
        else:
            codigo = VERIF_BINANCE_NO_VERIFICADO
    else:
        codigo = VERIF_PAGO_MOVIL_OCR

    partes = [f'metodo={metodo or "pago_movil_ocr"}']
    if es_binance:
        api = {True: 'confirmado', False: 'no_encontrado'}.get(
            confirmado, 'no_verificado'
        )
        partes.append(f'api_binance={api}')
    if monto is not None:
        partes.append(f'monto={monto}')
    if estado_monto:
        partes.append(f'estado_monto={estado_monto}')
    return codigo, '; '.join(partes)


def guardar_verificacion(
    comercio_id,
    referencia,
    metodo,
    confirmado=None,
    estado_monto=None,
    monto=None,
):
    """Guarda el resultado en ``solicitudes_pago``. Nunca lanza.

    Si las columnas aún no existen (migración pendiente) se omite con un aviso:
    la trazabilidad es secundaria y no debe afectar al pago ya aprobado.
    """
    try:
        referencia = (str(referencia) if referencia is not None else '').strip()
        if not referencia:
            return False
        codigo, detalle = describir_verificacion(
            metodo, confirmado=confirmado, estado_monto=estado_monto, monto=monto
        )
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                UPDATE solicitudes_pago
                SET verificacion = ?, verificacion_detalle = ?
                WHERE referencia = ? AND comercio_id = ?
                """,
                (codigo, detalle, referencia, int(comercio_id)),
            )
            conexion.commit()
        return True
    except Exception as error:
        logger.warning(
            '[Localis Verificación] no se pudo guardar la auditoría '
            f'(ref={referencia}): {type(error).__name__}: {error}'
        )
        return False

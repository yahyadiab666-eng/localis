"""Auditoría de la verificación automática de pagos.

Registra en ``solicitudes_pago`` **cómo** se validó cada pago: si la API de
Binance confirmó la orden, si no la encontró, si no se pudo verificar o si solo
pasó el filtro OCR del pago móvil. Permite que el admin vea el detalle sin
depender de los logs del worker.

Además redacta el mensaje de la **bandeja de revisión** del panel admin
(``soporte_y_reportes`` con ``tipo = 'revision_pago'``), que es donde aterrizan
los pagos que no quedaron confirmados automáticamente.

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

# Tipo de ticket interno de la bandeja administrativa.
TIPO_REVISION_PAGO = 'revision_pago'
# Tope de revisiones pendientes por comercio: evita inundar la bandeja.
MAX_REVISIONES_PENDIENTES = 20


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


def concepto_plan(plan_tipo):
    """Concepto para una mensualidad: ``plan:pro``."""
    return f'plan:{str(plan_tipo or "").strip().lower()}'


def concepto_boost(dias, producto_id):
    """Concepto para un destacado: ``boost:7:prod:123``."""
    return f'boost:{int(dias)}:prod:{int(producto_id)}'


def concepto_banner(dias):
    """Concepto para un alquiler de banner B2B: ``banner:30``."""
    return f'banner:{int(dias)}'


def parsear_concepto(concepto):
    """Interpreta el concepto guardado en la alerta.

    Devuelve ``{'tipo': 'plan', 'plan_tipo': ...}``,
    ``{'tipo': 'boost', 'dias': n, 'producto_id': n}`` o
    ``{'tipo': 'banner', 'dias': n}``, o ``None`` si el formato no es
    reconocible. Función pura: no toca la BD.
    """
    texto = str(concepto or '').strip().lower()
    if not texto:
        return None
    partes = texto.split(':')
    if partes[0] == 'plan' and len(partes) == 2 and partes[1]:
        return {'tipo': 'plan', 'plan_tipo': partes[1]}
    if partes[0] == 'boost' and len(partes) == 4 and partes[2] == 'prod':
        try:
            return {
                'tipo': 'boost',
                'dias': int(partes[1]),
                'producto_id': int(partes[3]),
            }
        except (TypeError, ValueError):
            return None
    if partes[0] == 'banner' and len(partes) == 2:
        try:
            return {'tipo': 'banner', 'dias': int(partes[1])}
        except (TypeError, ValueError):
            return None
    return None


def motivo_revision(confirmado):
    """Motivo de revisión para un pago Binance no confirmado.

    ``'no_encontrado'`` si la API respondió que la orden no está (o el monto no
    cuadra); ``'no_verificado'`` si no se pudo comprobar (API caída, sin monto,
    volumen anómalo). ``None`` cuando la orden sí quedó confirmada.
    """
    if confirmado is True:
        return None
    return 'no_encontrado' if confirmado is False else 'no_verificado'


def describir_revision_pago(
    metodo,
    confirmado,
    referencia=None,
    monto=None,
    moneda='USDT',
    fecha_comprobante=None,
    nombre_comercio=None,
    comercio_id=None,
):
    """Redacta el mensaje de la bandeja para un pago no confirmado.

    Devuelve ``(motivo, mensaje)`` o ``(None, '')`` si no requiere revisión.
    Función pura: se prueba sin BD ni red.
    """
    motivo = motivo_revision(confirmado)
    if motivo is None:
        return None, ''

    partes = []
    if motivo == 'no_encontrado':
        partes.append(
            'RECHAZADO: la captura pasó el OCR, pero Binance NO confirma que la '
            'orden exista (posible ID equivocado o comprobante falsificado).'
        )
    else:
        partes.append(
            'ACEPTADO SIN VERIFICAR: el pago se activó con el OCR, pero la API '
            'de Binance no pudo confirmar la orden. Revisa manualmente.'
        )

    if nombre_comercio:
        etiqueta = f'{nombre_comercio} (ID {comercio_id})' if comercio_id else nombre_comercio
        partes.append(f'Comercio: {etiqueta}')
    if referencia:
        partes.append(f'ID de orden: {referencia}')
    if monto is not None:
        partes.append(f'Monto esperado: {monto} {moneda}')
    if fecha_comprobante:
        partes.append(f'Fecha del comprobante: {fecha_comprobante}')
    partes.append(f'Metodo: {metodo or "binance"}')
    return motivo, ' — '.join(partes)


def registrar_revision_pago(
    comercio_id,
    referencia,
    motivo,
    mensaje,
    correo=None,
    usuario_id=None,
    comprobante_url=None,
    monto=None,
    concepto=None,
):
    """Crea el ticket de revisión en la bandeja administrativa. Nunca lanza.

    Evita duplicados (misma referencia pendiente) y limita el número de
    revisiones pendientes por comercio para no inundar la bandeja si alguien
    reintenta o introduce referencias inventadas.
    """
    try:
        referencia = (str(referencia) if referencia is not None else '').strip()
        if not referencia or not mensaje:
            return False

        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            cursor.execute(
                """
                SELECT 1 FROM soporte_y_reportes
                WHERE tipo = ? AND referencia_pago = ? AND estado = 'pendiente'
                LIMIT 1
                """,
                (TIPO_REVISION_PAGO, referencia),
            )
            if cursor.fetchone():
                return False

            cursor.execute(
                """
                SELECT COUNT(*) FROM soporte_y_reportes
                WHERE tipo = ? AND referencia_id = ? AND estado = 'pendiente'
                """,
                (TIPO_REVISION_PAGO, int(comercio_id)),
            )
            fila = cursor.fetchone()
            pendientes = fila[0] if fila else 0
            if pendientes is not None and int(pendientes) >= MAX_REVISIONES_PENDIENTES:
                logger.warning(
                    f'[Localis Verificación] bandeja llena para el comercio '
                    f'{comercio_id} ({pendientes} pendientes); no se crea revisión.'
                )
                return False

            cursor.execute(
                """
                INSERT INTO soporte_y_reportes
                    (usuario_id, tipo, correo, mensaje, referencia_id, estado,
                     comprobante_url, referencia_pago, concepto)
                VALUES (?, ?, ?, ?, ?, 'pendiente', ?, ?, ?)
                """,
                (
                    usuario_id,
                    TIPO_REVISION_PAGO,
                    correo or 'sistema@localis.app',
                    mensaje,
                    int(comercio_id),
                    comprobante_url,
                    referencia,
                    concepto,
                ),
            )
            conexion.commit()

        logger.info(
            f'[Localis Verificación] revisión registrada ({motivo}) '
            f'ref={referencia} comercio={comercio_id}'
        )
        return True
    except Exception as error:
        logger.warning(
            '[Localis Verificación] no se pudo registrar la revisión '
            f'(ref={referencia}): {type(error).__name__}: {error}'
        )
        return False

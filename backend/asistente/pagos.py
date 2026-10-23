"""Soporte de pagos del asistente (aislado y con salvaguardas).

Objetivo: cubrir sin humanos las dudas y la **revalidacion** de pagos reportados.

Lo que hace:
  - ``estado_pago(comercio_id)``: estado legible del ultimo pago y de las
    revisiones pendientes. **Solo lectura.**
  - ``hay_revision_reverificable(comercio_id)``: True si hay una revision
    pendiente de un pago Binance que quedo **"sin verificar"** (fallo transitorio
    de la API, no un rechazo).
  - ``reverificar(usuario_id, comercio_id)``: vuelve a consultar Binance (solo
    lectura) para ESA revision del PROPIO comercio. Si ahora SI se confirma,
    cierra el ticket en la bandeja (``estado='resuelto'``). Si no, informa.

Lo que NO hace (a proposito):
  - NO cambia el plan, el vencimiento, el monto ni ninguna tabla de dinero.
  - NO toca revisiones de otros comercios ni pagos rechazados (``no_encontrado``).
  - NO inventa: si no hay revision elegible, no propone ni ejecuta nada.

Reutiliza la logica ya probada del proyecto (``verificar_orden_binance``,
``calcular_monto_pago_plan``) y queda bajo el interruptor
``LOCALIS_ASISTENTE_ACCIONES``.
"""

from __future__ import annotations

import logging
import os
import secrets
import threading
import time

from backend.asistente.acciones import habilitado

_log = logging.getLogger('localis.asistente.pagos')

TIPO_REVISION = 'revision_pago'
VERIF_BINANCE_NO_VERIFICADO = 'binance_no_verificado'

# Frases que indican que el comerciante quiere que se revise su pago.
_CLAVES_REVERIFICAR = (
    'reverific', 'revalid', 'volver a verificar', 'volver a revisar',
    'revisar mi pago', 'verificar mi pago', 'revisen mi pago', 'verifiquen mi pago',
    'mi pago', 'el pago', 'pago movil', 'pago móvil', 'binance',
)


def _revisiones_pendientes(comercio_id, limite=5):
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT id, referencia_pago, concepto, mensaje, fecha '
                'FROM soporte_y_reportes '
                "WHERE tipo = ? AND estado = 'pendiente' AND referencia_id = ? "
                'ORDER BY fecha DESC LIMIT ?',
                (TIPO_REVISION, int(comercio_id), int(limite)),
            )
            return [dict(f) if not isinstance(f, dict) else f for f in cursor.fetchall()]
    except Exception as error:
        _log.warning('no se pudieron leer las revisiones: %s', error)
        return []


def _ultimos_pagos(comercio_id, limite=3):
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT referencia, plan_tipo, estado, verificacion, '
                'verificacion_detalle, fecha_registro FROM solicitudes_pago '
                'WHERE comercio_id = ? ORDER BY id DESC LIMIT ?',
                (int(comercio_id), int(limite)),
            )
            return [dict(f) if not isinstance(f, dict) else f for f in cursor.fetchall()]
    except Exception as error:
        _log.warning('no se pudieron leer los pagos: %s', error)
        return []


def _verificacion_de(comercio_id, referencia):
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT verificacion FROM solicitudes_pago '
                'WHERE comercio_id = ? AND referencia = ? ORDER BY id DESC LIMIT 1',
                (int(comercio_id), str(referencia)),
            )
            fila = cursor.fetchone()
        if not fila:
            return None
        return fila.get('verificacion') if isinstance(fila, dict) else fila[0]
    except Exception as error:
        _log.warning('no se pudo leer la verificacion: %s', error)
        return None


def _paso_sugerido(verificacion):
    if verificacion == VERIF_BINANCE_NO_VERIFICADO:
        return (
            'Binance no pudo confirmar la orden en ese momento. Puedes pedirle al '
            'asistente que la vuelva a verificar; si sigue sin aparecer, revisa el '
            'ID de la orden y el monto exacto.'
        )
    if verificacion == 'binance_no_encontrado':
        return (
            'La captura paso el OCR pero Binance no encuentra esa orden: revisa que '
            'el ID de la orden y el monto sean exactos y vuelve a subir el comprobante.'
        )
    if verificacion == 'binance_confirmado':
        return 'El pago quedo confirmado en Binance.'
    return (
        'Revisa que el monto sea el exacto, que la fecha del comprobante sea '
        'reciente y que no hayas usado el mismo comprobante antes.'
    )


def estado_pago(comercio_id):
    """Texto legible con el estado del pago del comercio. Solo lectura."""
    if not comercio_id:
        return ''
    pagos = _ultimos_pagos(comercio_id)
    revisiones = _revisiones_pendientes(comercio_id)
    lineas = []
    if pagos:
        ultimo = pagos[0]
        verificacion = ultimo.get('verificacion')
        lineas.append(
            f'Ultimo pago reportado: referencia {ultimo.get("referencia") or "?"} '
            f'(plan {ultimo.get("plan_tipo") or "?"}, estado {ultimo.get("estado") or "?"})'
        )
        if verificacion:
            lineas.append(f'Verificacion: {verificacion}')
        lineas.append(f'Paso sugerido: {_paso_sugerido(verificacion)}')
    else:
        lineas.append('No hay pagos reportados por este comercio.')
    if revisiones:
        lineas.append(f'Revisiones pendientes en la bandeja: {len(revisiones)}')
        motivo = str(revisiones[0].get('mensaje') or '')[:200]
        if motivo:
            lineas.append(f'Motivo mas reciente: {motivo}')
    else:
        lineas.append('No hay revisiones pendientes.')
    return '\n'.join(lineas)


def _revision_reverificable(comercio_id):
    """Primera revision pendiente de un pago Binance 'sin verificar'."""
    for revision in _revisiones_pendientes(comercio_id):
        referencia = str(revision.get('referencia_pago') or '').strip()
        if not referencia:
            continue
        verificacion = _verificacion_de(comercio_id, referencia)
        if verificacion == VERIF_BINANCE_NO_VERIFICADO:
            return revision
    return None


def hay_revision_reverificable(comercio_id):
    try:
        return _revision_reverificable(comercio_id) is not None
    except Exception as error:
        _log.warning('no se pudo comprobar la revision: %s', error)
        return False


def _monto_esperado(comercio_id, revision):
    """Monto en USD del plan de la revision (o None). Nunca lanza."""
    try:
        from backend.plans import obtener_plan_por_codigo
        from backend.stores import obtener_comercio_por_usuario
        from backend.subscriptions import calcular_monto_pago_plan
        from backend.verificacion_pagos import parsear_concepto

        concepto = parsear_concepto(revision.get('concepto'))
        if not concepto or concepto.get('tipo') != 'plan':
            return None
        comercio = obtener_comercio_por_usuario(None, comercio_id)
        if not comercio:
            return None
        plan_tipo = concepto.get('plan_tipo')
        obtener_plan_por_codigo(plan_tipo)  # valida que el plan exista
        montos = calcular_monto_pago_plan(plan_tipo, comercio)
        if montos:
            return montos.get('monto_usd')
    except Exception as error:
        _log.warning('no se pudo calcular el monto esperado: %s', error)
    return None


def proponer(comercio_id, texto):
    """Propuesta de revalidacion de pago (no cambia nada) o None. Nunca lanza."""
    if not habilitado() or not comercio_id or not texto:
        return None
    try:
        bajo = str(texto).lower()
        if not any(clave in bajo for clave in _CLAVES_REVERIFICAR):
            return None
        revision = _revision_reverificable(comercio_id)
        if not revision:
            return None
        referencia = str(revision.get('referencia_pago') or '').strip()
        return {
            'id': secrets.token_urlsafe(16),
            'accion': 'pago_reverificar',
            'resumen': f'Revalidar en Binance el pago «{referencia}»',
            'mensaje': (
                f'Puedo **volver a consultar Binance** para tu pago con ID '
                f'«{referencia}». Si la orden ya aparece, cierro la revision; si no, '
                f'te digo que revisar. Confirma para intentarlo.'
            ),
            'parametros': {},
        }
    except Exception as error:
        _log.warning('propuesta de pago fallo: %s', error)
        return None


def _reverificar_revision(comercio_id, revision, usuario_id=None):
    """Revalida ESA revision en Binance y, si se confirma, cierra el ticket.

    NO toca el plan, el vencimiento ni el monto: el pago ya se activo con el OCR.
    Devuelve ``(ok, mensaje)``. Nunca lanza.
    """
    referencia = str(revision.get('referencia_pago') or '').strip()
    if not referencia:
        return False, 'La revision no tiene referencia.'
    monto = _monto_esperado(comercio_id, revision)

    try:
        from backend.binance_api import verificar_orden_binance

        confirmado = verificar_orden_binance(referencia, monto)
    except Exception as error:
        _log.warning('revalidacion fallo: %s', error)
        confirmado = None

    if confirmado is not True:
        if confirmado is False:
            return False, (
                f'Binance no encuentra la orden «{referencia}». Revisa que el ID sea '
                f'exacto y que el monto coincida, y vuelve a subir el comprobante.'
            )
        return False, (
            'Binance no responde en este momento. Intenta de nuevo mas tarde o '
            'escribenos por WhatsApp con el boton "Escribir a soporte".'
        )

    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                "UPDATE soporte_y_reportes SET estado = 'resuelto', "
                "mensaje = COALESCE(mensaje, '') || ' | Revalidado: orden confirmada "
                "en Binance.' "
                "WHERE id = ? AND tipo = ? AND estado = 'pendiente' AND referencia_id = ?",
                (int(revision.get('id')), TIPO_REVISION, int(comercio_id)),
            )
            cambiadas = int(getattr(cursor, 'rowcount', 0) or 0)
            conexion.commit()
    except Exception as error:
        _log.warning('no se pudo cerrar la revision: %s', error)
        return False, 'Binance confirmo el pago, pero no pude cerrar la revision. Escribenos.'

    if cambiadas <= 0:
        return False, 'La revision ya estaba cerrada.'
    if usuario_id:
        try:
            from backend.asistente.acciones import _auditar

            _auditar(
                usuario_id,
                'pago_reverificar',
                f'revision={revision.get("id")} ref={referencia}',
            )
        except Exception:
            pass
    return True, (
        f'Confirmado: Binance ya tiene la orden «{referencia}». Cerre la revision y '
        f'tu pago quedo verificado.'
    )


def reverificar(usuario_id, comercio_id):
    """Revalidacion pedida por el comerciante. Nunca cambia el plan."""
    if not habilitado():
        return False, 'Las acciones no estan disponibles en este momento.'
    if not usuario_id or not comercio_id:
        return False, 'Necesitas iniciar sesion como comerciante.'
    revision = _revision_reverificable(comercio_id)
    if not revision:
        return False, 'No encontre un pago de Binance pendiente de revisar.'
    return _reverificar_revision(comercio_id, revision, usuario_id=usuario_id)


# ==========================================================
# Reintento automatico en segundo plano (sin humanos)
# ==========================================================
# Revalida SOLO las revisiones de Binance que quedaron "sin verificar" por un
# fallo transitorio de la API. Cierra el ticket si ahora se confirma. Nunca toca
# dinero. Acotado: N por ciclo y cada _REINTENTO_SEG segundos.
_REINTENTO_SEG = max(120, int(os.environ.get('LOCALIS_PAGOS_REINTENTO_SEG', '900') or 900))
_REINTENTO_LOTE = max(1, min(5, int(os.environ.get('LOCALIS_PAGOS_REINTENTO_LOTE', '1') or 1)))
_ultimo_reintento = 0.0
_reintento_iniciado = False
_reintento_lock = threading.Lock()
_ultimo_arranque = 0.0


def _reintento_habilitado():
    valor = str(os.environ.get('LOCALIS_PAGOS_REINTENTO', '1')).strip().lower()
    return valor not in ('0', 'false', 'no', 'off')


def _revisiones_reverificables(limite):
    """Revisiones pendientes de Binance 'sin verificar' (cualquier comercio)."""
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT r.id, r.referencia_id AS comercio_id, r.referencia_pago, '
                'r.concepto, r.mensaje, r.fecha '
                'FROM soporte_y_reportes r '
                'JOIN solicitudes_pago s ON s.referencia = r.referencia_pago '
                'AND s.comercio_id = r.referencia_id '
                "WHERE r.tipo = ? AND r.estado = 'pendiente' AND s.verificacion = ? "
                'ORDER BY r.fecha ASC LIMIT ?',
                (TIPO_REVISION, VERIF_BINANCE_NO_VERIFICADO, int(limite)),
            )
            return [dict(f) if not isinstance(f, dict) else f for f in cursor.fetchall()]
    except Exception as error:
        _log.warning('no se pudieron listar revisiones para reintento: %s', error)
        return []


def reintento_oportunista():
    """Revalida algunas revisiones 'sin verificar'. Acotado y sin lanzar."""
    global _ultimo_reintento
    if not habilitado() or not _reintento_habilitado():
        return
    ahora = time.time()
    if ahora - _ultimo_reintento < _REINTENTO_SEG:
        return
    _ultimo_reintento = ahora
    for revision in _revisiones_reverificables(_REINTENTO_LOTE):
        try:
            comercio_id = revision.get('comercio_id') or revision.get('referencia_id')
            if comercio_id:
                _reverificar_revision(int(comercio_id), revision)
        except Exception as error:
            _log.warning('reintento de pago fallo: %s', error)


def _bucle_reintento():
    while True:
        try:
            reintento_oportunista()
        except Exception:
            pass
        time.sleep(_REINTENTO_SEG)


def iniciar_reintento_periodico():
    """Hilo daemon de reintento. Idempotente. Nunca lanza. No llamar al importar."""
    global _reintento_iniciado
    if not _reintento_habilitado():
        return None
    with _reintento_lock:
        if _reintento_iniciado:
            return None
        try:
            hilo = threading.Thread(
                target=_bucle_reintento, name='localis-pagos-reintento', daemon=True
            )
            hilo.start()
        except Exception as error:
            _log.warning('no se pudo iniciar el reintento de pagos: %s', error)
            return None
        _reintento_iniciado = True
        return hilo


def asegurar_reintento_activo():
    """Arranca el reintento desde el request (post-fork). Throttle 30 s."""
    global _ultimo_arranque
    try:
        if _reintento_iniciado or not _reintento_habilitado():
            return
        ahora = time.time()
        if ahora - _ultimo_arranque < 30:
            return
        _ultimo_arranque = ahora
        iniciar_reintento_periodico()
    except Exception:
        pass
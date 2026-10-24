"""Recordatorio de vencimiento de suscripción por correo (convierte prueba → pago).

Cada día busca tiendas cuya suscripción **vence en ≤5 días** (o venció hace poco)
y les envía un correo recordatorio con el enlace de pago. Es la palanca de
**conversión a pago**: el primer mes es gratis, así que el negocio se decide al
renovar.

No molesta: envía a lo sumo un recordatorio cada 6 días por tienda y no escribe a
tiendas vencidas hace más de 30 días ni a pre-cuentas. El hilo se arranca de forma
perezosa desde el request (post-fork). Nunca lanza.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from backend.correo import _base_url, correo_configurado, enviar_correo

logger = logging.getLogger('localis.recordatorios')

_LOG = '[Localis Recordatorios]'
# Candado de BD para que solo una instancia del web envíe correos a la vez.
_LOCK_ENVIOS = 774412001
_HORA_UTC = 13          # a partir de las 09:00 VET
_DIAS_AVISO = 5         # avisa cuando faltan ≤ 5 días
_DIAS_TOLERANCIA = 30   # no insiste a vencidos hace más de 30 días
_DIAS_REINTENTO = 6     # como mínimo 6 días entre recordatorios por tienda
_INTERVALO = 3600       # revisa cada hora

_iniciado = False
_ultimo_intento_arranque = 0.0
_lock = threading.Lock()


def _ahora_utc():
    return datetime.now(timezone.utc)


def _tiendas_por_vencer():
    """Tiendas a las que corresponde recordar hoy. Nunca lanza (devuelve [])."""
    try:
        from backend.db import get_db_connection

        ahora = _ahora_utc()
        hasta = ahora + timedelta(days=_DIAS_AVISO)
        desde = ahora - timedelta(days=_DIAS_TOLERANCIA)
        reintento = ahora - timedelta(days=_DIAS_REINTENTO)
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT id, nombre, owner_email, fecha_vencimiento
                FROM comercios
                WHERE owner_email IS NOT NULL AND TRIM(owner_email) <> ''
                  AND COALESCE(visible, 1) = 1
                  AND COALESCE(estado_tienda, 'activa') = 'activa'
                  AND fecha_vencimiento IS NOT NULL
                  AND fecha_vencimiento <= ?
                  AND fecha_vencimiento >= ?
                  AND (recordatorio_vencimiento_en IS NULL
                       OR recordatorio_vencimiento_en < ?)
                ORDER BY fecha_vencimiento ASC
                """,
                (hasta, desde, reintento),
            )
            filas = cursor.fetchall()
        return [dict(fila) for fila in filas]
    except Exception as error:
        logger.error(f'{_LOG} no se pudieron listar tiendas: {type(error).__name__}: {error}')
        return []


def _dias_restantes(fecha):
    try:
        if isinstance(fecha, str):
            fecha = datetime.strptime(fecha[:10], '%Y-%m-%d')
        if fecha.tzinfo is None:
            fecha = fecha.replace(tzinfo=timezone.utc)
        return (fecha.date() - _ahora_utc().date()).days
    except Exception:
        return None


def _cuerpo(tienda):
    nombre = (tienda.get('nombre') or 'tu tienda').strip()
    fecha = tienda.get('fecha_vencimiento')
    dias = _dias_restantes(fecha)
    fecha_txt = ''
    try:
        if not isinstance(fecha, str):
            fecha_txt = fecha.strftime('%d/%m/%Y')
        else:
            fecha_txt = fecha[:10]
    except Exception:
        fecha_txt = ''
    link = f'{_base_url()}/comercio/planes'
    if dias is not None and dias < 0:
        estado = f'venció el {fecha_txt}' if fecha_txt else 'está vencida'
        enganche = (
            'Reactívala en un minuto para no dejar de recibir visitas ni perder '
            'tu posición en Google.'
        )
        asunto = f'Tu tienda "{nombre}" en Localis está vencida'
    else:
        cuando = f'vence en {dias} día(s)' if dias is not None else 'vence pronto'
        estado = f'{cuando} ({fecha_txt})' if fecha_txt else cuando
        enganche = (
            'Renuévala para seguir apareciendo en las búsquedas y recibiendo '
            'clientes por WhatsApp.'
        )
        asunto = f'Tu tienda "{nombre}" en Localis vence pronto'
    cuerpo = (
        f'Hola 👋\n\n'
        f'Tu tienda "{nombre}" en Localis {estado}.\n\n'
        f'{enganche}\n\n'
        f'Renueva aquí (plan Básico, $10/mes, hasta 100 productos, sin comisión '
        f'por tus ventas):\n{link}\n\n'
        f'Si tienes dudas, responde a este correo.\n\n'
        f'— Equipo Localis'
    )
    return asunto, cuerpo


def _marcar_enviado(comercio_id):
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'UPDATE comercios SET recordatorio_vencimiento_en = ? WHERE id = ?',
                (_ahora_utc(), int(comercio_id)),
            )
            conexion.commit()
        return True
    except Exception as error:
        logger.error(f'{_LOG} no se pudo marcar {comercio_id}: {error}')
        return False


def _tomar_candado_envios(cursor):
    """Candado de BD para que solo una instancia envíe correos a la vez.

    Con 2+ instancias del web, dos procesos pueden listar la misma tienda y
    mandar el recordatorio dos veces (o el reporte diario). Un candado por
    transacción lo evita; se libera solo al hacer commit/rollback.

    Si el motor no soporta el candado (p. ej. SQLite en pruebas) devuelve True
    para conservar el comportamiento anterior.
    """
    try:
        cursor.execute('SELECT pg_try_advisory_xact_lock(?)', (_LOCK_ENVIOS,))
        fila = cursor.fetchone()
        if isinstance(fila, dict):
            return bool(list(fila.values())[0])
        return bool(fila[0]) if fila else True
    except Exception:
        return True


def enviar_recordatorios_vencimiento():
    """Envía los recordatorios correspondientes. Devuelve cuántos envió. Nunca lanza."""
    try:
        if not correo_configurado():
            return 0
        if _ahora_utc().hour < _HORA_UTC:
            return 0
        from backend.db import get_db_connection

        # El candado se mantiene mientras dura el envío: otra instancia que
        # llegue a la vez sale por aquí sin enviar nada.
        with get_db_connection() as conexion:
            curso = conexion.cursor()
            if not _tomar_candado_envios(curso):
                return 0
            return _enviar_recordatorios()
    except Exception as error:
        logger.error(f'{_LOG} fallo inesperado: {type(error).__name__}: {error}')
        return 0


def _enviar_recordatorios():
    """Recorre las tiendas por vencer y envía (ya con el candado tomado)."""
    try:
        enviados = 0
        for tienda in _tiendas_por_vencer():
            destino = (tienda.get('owner_email') or '').strip()
            if not destino:
                continue
            asunto, cuerpo = _cuerpo(tienda)
            try:
                ok, detalle = enviar_correo(destino, asunto, cuerpo)
            except Exception as error:
                ok, detalle = False, str(error)
            if ok:
                _marcar_enviado(tienda.get('id'))
                enviados += 1
            else:
                logger.error(f'{_LOG} fallo a {destino}: {detalle}')
        if enviados:
            logger.info(f'{_LOG} {enviados} recordatorio(s) de vencimiento enviado(s)')
        return enviados
    except Exception as error:
        logger.error(f'{_LOG} fallo inesperado: {type(error).__name__}: {error}')
        return 0


def _bucle():
    while True:
        try:
            enviar_recordatorios_vencimiento()
        except Exception as error:
            logger.error(f'{_LOG} ciclo falló: {type(error).__name__}: {error}')
        time.sleep(max(300, _INTERVALO))


def asegurar_recordatorios_activos():
    """Arranca el planificador (hilo post-fork). Idempotente. Nunca lanza."""
    global _iniciado, _ultimo_intento_arranque
    try:
        if _iniciado:
            return
        ahora = time.time()
        if ahora - _ultimo_intento_arranque < 30:
            return
        _ultimo_intento_arranque = ahora
        with _lock:
            if _iniciado:
                return
            _iniciado = True
        threading.Thread(
            target=_bucle, name='localis-recordatorios', daemon=True
        ).start()
    except Exception:
        pass

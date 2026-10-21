"""Reintento periódico en segundo plano de imágenes pendientes/rechazadas.

Sin falsos positivos: los productos marcados ``pendiente``/``rechazada`` se
reintentan de forma continua (ritmo acotado para no saturar 1 CPU), ampliando
la búsqueda (multi-fuente + site:host locales). Cuando se obtiene una foto real,
el pipeline actualiza ``imagen_estado='real'``.

Config (opcional)::

    LOCALIS_IMG_BACKFILL=1              # 0 desactiva el reintento periódico
    LOCALIS_IMG_BACKFILL_LOTE=20        # productos por comercio y ciclo
    LOCALIS_IMG_BACKFILL_COMERCIOS=2    # comercios por ciclo
    LOCALIS_IMG_BACKFILL_PRESUPUESTO=50 # segundos de trabajo por ciclo
    LOCALIS_IMG_BACKFILL_INTERVALO=90   # segundos entre ciclos
"""

from __future__ import annotations
import logging
logger = logging.getLogger('localis.image_backfill')

import os
import threading
import time

_LOG = '[Localis Backfill]'


def _env_int(nombre, defecto):
    try:
        return int(str(os.getenv(nombre, '')).strip() or defecto)
    except (TypeError, ValueError):
        return defecto


_LOTE = max(1, _env_int('LOCALIS_IMG_BACKFILL_LOTE', 20))
_COMERCIOS = max(1, _env_int('LOCALIS_IMG_BACKFILL_COMERCIOS', 2))
_PRESUPUESTO = max(10, _env_int('LOCALIS_IMG_BACKFILL_PRESUPUESTO', 50))
_INTERVALO = max(30, _env_int('LOCALIS_IMG_BACKFILL_INTERVALO', 90))

_iniciado = False
_ultimo_intento_arranque = 0.0
_lock = threading.Lock()


def _habilitado():
    valor = str(os.getenv('LOCALIS_IMG_BACKFILL', '1')).strip().lower()
    return valor not in ('0', 'false', 'no', 'off')


def _comercios_con_pendientes(limite):
    from backend.db import get_db_connection

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT DISTINCT comercio_id
                FROM productos
                WHERE COALESCE(imagen_estado, 'pendiente') IN ('pendiente', 'rechazada')
                  AND comercio_id IS NOT NULL
                ORDER BY comercio_id
                LIMIT ?
                """,
                (int(limite),),
            )
            filas = cursor.fetchall()
    except Exception as error:
        logger.error(f'{_LOG} no se pudieron listar pendientes: {type(error).__name__}: {error}')
        return []

    comercios = []
    for fila in filas:
        valor = fila.get('comercio_id') if isinstance(fila, dict) else fila[0]
        if valor is not None:
            comercios.append(int(valor))
    return comercios


def ejecutar_ciclo():
    """Procesa un lote acotado de pendientes. Retorna cuántos comercios atendió."""
    from services.professional_image_pipeline import pipeline_habilitado, procesar_inventario

    if not pipeline_habilitado():
        return 0
    atendidos = 0
    for comercio_id in _comercios_con_pendientes(_COMERCIOS):
        try:
            procesar_inventario(
                comercio_id, limite=_LOTE, presupuesto_seg=_PRESUPUESTO
            )
            atendidos += 1
        except Exception as error:
            logger.error(f'{_LOG} comercio={comercio_id} fallo: {type(error).__name__}: {error}')
    if atendidos:
        logger.info(f'{_LOG} ciclo completado comercios={atendidos} lote={_LOTE}')
    return atendidos


def _bucle():
    while True:
        time.sleep(_INTERVALO)
        try:
            ejecutar_ciclo()
        except Exception as error:
            logger.error(f'{_LOG} ciclo fallo inesperado: {type(error).__name__}: {error}')


def iniciar_backfill_periodico():
    """Arranca el reintento periódico (hilo daemon). Idempotente. Nunca lanza.

    OJO: NO se debe llamar al importar el módulo. Un hilo creado antes del fork
    de gunicorn NO sobrevive al fork en producción (por eso el reintento estaba
    muerto, igual que el volcado de tráfico). Se arranca de forma perezosa desde
    el primer request, vía ``asegurar_backfill_activo``.
    """
    global _iniciado
    if not _habilitado():
        logger.info(f'{_LOG} desactivado (LOCALIS_IMG_BACKFILL=0)')
        return None
    with _lock:
        if _iniciado:
            return None
        try:
            hilo = threading.Thread(
                target=_bucle, name='localis-img-backfill', daemon=True
            )
            hilo.start()
        except Exception as error:
            # No marcar como iniciado: se reintenta en el próximo request.
            logger.error(
                f'{_LOG} no se pudo iniciar el hilo: {type(error).__name__}: {error}'
            )
            return None
        _iniciado = True
        logger.info(
            f'{_LOG} activo: cada {_INTERVALO}s, {_LOTE} producto(s) x '
            f'{_COMERCIOS} comercio(s), presupuesto {_PRESUPUESTO}s'
        )
        return hilo


def asegurar_backfill_activo():
    """Arranca el backfill la 1.ª vez que se atiende un request (post-fork).

    Un hilo creado al importar el módulo nace ANTES del fork de gunicorn y no
    sobrevive en producción (por eso el reintento estaba muerto). Arrancarlo
    desde el request garantiza que viva en el worker. Se intenta a lo sumo cada
    30 s. Nunca lanza.
    """
    global _ultimo_intento_arranque
    try:
        if _iniciado or not _habilitado():
            return
        ahora = time.time()
        if ahora - _ultimo_intento_arranque < 30:
            return
        _ultimo_intento_arranque = ahora
        iniciar_backfill_periodico()
    except Exception:
        pass

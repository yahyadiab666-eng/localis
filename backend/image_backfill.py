"""Reintento periódico en segundo plano de imágenes pendientes/rechazadas.

Sin falsos positivos: los productos marcados ``pendiente``/``rechazada`` se
reintentan de forma continua (ritmo acotado para no saturar 1 CPU), ampliando
la búsqueda (multi-fuente + site:host locales). Cuando se obtiene una foto real,
el pipeline actualiza ``imagen_estado='real'``.

Config (opcional)::

    LOCALIS_IMG_BACKFILL=1              # 0 desactiva el reintento periódico
    LOCALIS_IMG_BACKFILL_LOTE=2         # productos por comercio y ciclo
    LOCALIS_IMG_BACKFILL_COMERCIOS=2    # comercios por ciclo
    LOCALIS_IMG_BACKFILL_INTERVALO=300  # segundos entre ciclos
"""

from __future__ import annotations

import os
import threading
import time

_LOG = '[Localis Backfill]'


def _env_int(nombre, defecto):
    try:
        return int(str(os.getenv(nombre, '')).strip() or defecto)
    except (TypeError, ValueError):
        return defecto


_LOTE = max(1, _env_int('LOCALIS_IMG_BACKFILL_LOTE', 2))
_COMERCIOS = max(1, _env_int('LOCALIS_IMG_BACKFILL_COMERCIOS', 2))
_INTERVALO = max(30, _env_int('LOCALIS_IMG_BACKFILL_INTERVALO', 300))

_iniciado = False
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
        print(f'{_LOG} no se pudieron listar pendientes: {type(error).__name__}: {error}')
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
            procesar_inventario(comercio_id, limite=_LOTE)
            atendidos += 1
        except Exception as error:
            print(f'{_LOG} comercio={comercio_id} fallo: {type(error).__name__}: {error}')
    if atendidos:
        print(f'{_LOG} ciclo completado comercios={atendidos} lote={_LOTE}')
    return atendidos


def _bucle():
    while True:
        time.sleep(_INTERVALO)
        try:
            ejecutar_ciclo()
        except Exception as error:
            print(f'{_LOG} ciclo fallo inesperado: {type(error).__name__}: {error}')


def iniciar_backfill_periodico():
    """Arranca el reintento periódico (hilo daemon). Idempotente."""
    global _iniciado
    if not _habilitado():
        print(f'{_LOG} desactivado (LOCALIS_IMG_BACKFILL=0)')
        return None
    with _lock:
        if _iniciado:
            return None
        _iniciado = True
        hilo = threading.Thread(target=_bucle, name='localis-img-backfill', daemon=True)
        hilo.start()
        print(
            f'{_LOG} activo: cada {_INTERVALO}s, {_LOTE} producto(s) x '
            f'{_COMERCIOS} comercio(s)'
        )
        return hilo

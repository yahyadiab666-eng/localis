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
_REPARAR = str(os.getenv('LOCALIS_IMG_REPARAR', '1')).strip().lower() not in (
    '0', 'false', 'no', 'off',
)
_REPARAR_INTERVALO = max(600, _env_int('LOCALIS_IMG_REPARAR_INTERVALO', 1800))
_REPARAR_LOTE = max(50, _env_int('LOCALIS_IMG_REPARAR_LOTE', 400))
_LIMPIAR = str(os.getenv('LOCALIS_LIMPIAR_HUERFANOS', '1')).strip().lower() not in (
    '0', 'false', 'no', 'off',
)
_LIMPIAR_INTERVALO = max(1800, _env_int('LOCALIS_LIMPIAR_HUERFANOS_INTERVALO', 21600))
_LIMPIAR_LOTE = max(50, _env_int('LOCALIS_LIMPIAR_HUERFANOS_LOTE', 400))

_iniciado = False
_lock = threading.Lock()
_ultima_reparacion = 0.0
_ultima_limpieza = 0.0


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


def _reparar_si_toca(forzar=False):
    """Repara (throttled) imágenes 'reales' cuyo asset ya no existe."""
    global _ultima_reparacion
    if not _REPARAR:
        return 0
    ahora = time.monotonic()
    if not forzar and (ahora - _ultima_reparacion) < _REPARAR_INTERVALO:
        return 0
    _ultima_reparacion = ahora
    try:
        from backend.cobertura_visual import reparar_imagenes_rotas

        resultado = reparar_imagenes_rotas(limite=_REPARAR_LOTE)
    except Exception as error:
        print(f'{_LOG} reparación fallo: {type(error).__name__}: {error}')
        return 0
    if resultado.get('reparadas'):
        print(
            f'{_LOG} incoherencias reparadas: {resultado["reparadas"]}/'
            f'{resultado["revisadas"]} imágenes rotas re-cubiertas'
        )
    return int(resultado.get('reparadas') or 0)


def _limpiar_si_toca(forzar=False):
    """Purga (throttled) assets manuales huérfanos del bucket/local."""
    global _ultima_limpieza
    if not _LIMPIAR:
        return 0
    ahora = time.monotonic()
    if not forzar and (ahora - _ultima_limpieza) < _LIMPIAR_INTERVALO:
        return 0
    _ultima_limpieza = ahora
    try:
        from backend.storage_cleanup import limpiar_huerfanos

        resultado = limpiar_huerfanos(max_por_carpeta=_LIMPIAR_LOTE)
        generados = 0
        try:
            from backend.storage_cleanup import eliminar_assets_generados

            generados = int(eliminar_assets_generados(limite=_LIMPIAR_LOTE * 5).get('limpiados') or 0)
        except Exception as error_generados:
            print(f'{_LOG} limpieza de fabricados fallo: {type(error_generados).__name__}')
    except Exception as error:
        print(f'{_LOG} limpieza de huérfanos fallo: {type(error).__name__}: {error}')
        return 0
    if resultado.get('borrados') or generados:
        print(
            f'{_LOG} mantenimiento: huérfanos={resultado["borrados"]}/'
            f'{resultado["revisados"]} fabricados_limpiados={generados}'
        )
    return int(resultado.get('borrados') or 0)


def ejecutar_ciclo():
    """Procesa un lote acotado de pendientes. Retorna cuántos comercios atendió."""
    from services.professional_image_pipeline import pipeline_habilitado, procesar_inventario

    if not pipeline_habilitado():
        return 0
    _reparar_si_toca()
    _limpiar_si_toca()
    atendidos = 0
    for comercio_id in _comercios_con_pendientes(_COMERCIOS):
        try:
            procesar_inventario(
                comercio_id, limite=_LOTE, presupuesto_seg=_PRESUPUESTO
            )
            atendidos += 1
        except Exception as error:
            print(f'{_LOG} comercio={comercio_id} fallo: {type(error).__name__}: {error}')
    if atendidos:
        print(f'{_LOG} ciclo completado comercios={atendidos} lote={_LOTE}')
    return atendidos


def _bucle():
    _reparar_si_toca(forzar=True)
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
            f'{_COMERCIOS} comercio(s), presupuesto {_PRESUPUESTO}s'
        )
        return hilo

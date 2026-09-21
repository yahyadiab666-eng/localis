"""Cola de importación de inventario en segundo plano (Localis).

Objetivo: que subir un CSV/Excel **nunca bloquee** el hilo de la petición HTTP.
El endpoint responde ``HTTP 202 Accepted`` de inmediato con un ``job_id`` y el
catálogo se procesa en workers propios.

Diseño para alta concurrencia en un VPS:

- ``queue.Queue`` acotada (``LOCALIS_IMPORT_QUEUE_MAX``): si se llena, se
  rechaza con ``ColaImportacionLlena`` en lugar de acumular memoria sin límite.
- Pool fijo de hilos daemon (``LOCALIS_IMPORT_WORKERS``): evita saturar la CPU
  cuando muchos comercios suben catálogos a la vez.
- Cada job se ejecuta dentro de ``app.app_context()`` (contexto de Flask) para
  que el código de negocio tenga ``current_app``/configuración disponibles.
- Los lotes de filas siguen usando transacciones y el *advisory lock* por
  comercio, de modo que dos importaciones del mismo comercio se serializan y
  las de comercios distintos pueden avanzar en paralelo.
- Los archivos se leen una vez en la petición (con tope de tamaño) y viajan en
  memoria; el worker libera los bytes al terminar.

API pública::

    from backend.import_queue import configurar_cola, encolar_importacion, obtener_estado_job

    configurar_cola(app)                       # una vez al arrancar
    job = encolar_importacion(comercio_id, filename, data)
    estado = obtener_estado_job(job['job_id'], comercio_id=comercio_id)
"""

from __future__ import annotations

import io
import os
import queue
import threading
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

_LOG = '[Localis Cola]'

_QUEUE_MAX = max(1, int(os.getenv('LOCALIS_IMPORT_QUEUE_MAX', '200')))
_WORKERS = max(1, int(os.getenv('LOCALIS_IMPORT_WORKERS', '2')))
_JOB_TTL_SEG = max(60, int(os.getenv('LOCALIS_IMPORT_JOB_TTL_SEC', '3600')))
_MAX_JOBS = max(50, int(os.getenv('LOCALIS_IMPORT_MAX_JOBS', '500')))

_ESTADOS_FINALES = frozenset({'completado', 'parcial', 'error'})


class ColaImportacionLlena(Exception):
    """No hay capacidad inmediata: el servidor ya procesa muchos catálogos."""


class ArchivoEnMemoria:
    """Adaptador compatible con ``werkzeug.FileStorage`` para el worker."""

    def __init__(self, filename, data):
        self.filename = filename
        self.stream = io.BytesIO(data or b'')
        self.content_length = len(data or b'')


_cola: queue.Queue | None = None
_app = None
_lock = threading.Lock()
_trabajadores_iniciados = False
_jobs: dict = {}
_jobs_lock = threading.Lock()
_procesador = None  # inyección para pruebas


def _log(mensaje):
    print(f'{_LOG} {mensaje}', flush=True)


def _carpeta_cola():
    from config import RUTA_RAIZ

    destino = Path(RUTA_RAIZ) / 'instance' / 'cola_import'
    destino.mkdir(parents=True, exist_ok=True)
    return destino


def _guardar_spool(job_id, data):
    """Guarda el archivo en disco (no en RAM) para soportar cientos en cola."""
    ruta = _carpeta_cola() / f'{job_id}.bin'
    ruta.write_bytes(data)
    return str(ruta)


def _leer_spool(ruta):
    try:
        with open(ruta, 'rb') as archivo:
            return archivo.read()
    except Exception:
        return None


def _borrar_spool(ruta):
    try:
        if ruta and os.path.exists(ruta):
            os.remove(ruta)
    except Exception:
        pass


def configurar_cola(app=None, *, workers=None, maxsize=None):
    """Guarda la app Flask y prepara la cola. Idempotente.

    Los trabajadores se crean de forma perezosa en la primera importación para
    no arrancar hilos en procesos que solo importan el módulo (tests, CLI).
    """
    global _app
    if app is not None:
        _app = app
    del workers, maxsize  # los límites se leen de variables de entorno
    return True


def _contexto_app():
    if _app is None:
        return nullcontext()
    return _app.app_context()


def _procesar_por_defecto(comercio_id, filename, data):
    from backend.stores import procesar_csv_productos

    archivo = ArchivoEnMemoria(filename, data)
    return procesar_csv_productos(comercio_id, archivo)


def _obtener_procesador():
    return _procesador or _procesar_por_defecto


def _asegurar_cola():
    global _cola, _trabajadores_iniciados
    if _trabajadores_iniciados and _cola is not None:
        return _cola
    with _lock:
        if _trabajadores_iniciados and _cola is not None:
            return _cola
        _cola = queue.Queue(maxsize=_QUEUE_MAX)
        for indice in range(_WORKERS):
            hilo = threading.Thread(
                target=_bucle_trabajador,
                name=f'localis-import-{indice}',
                daemon=True,
            )
            hilo.start()
        _trabajadores_iniciados = True
        _log(f'trabajadores iniciados={_WORKERS} capacidad={_QUEUE_MAX} hilos=daemon')
    return _cola


def _bucle_trabajador():
    while True:
        job_id = _cola.get()
        try:
            _ejecutar_job(job_id)
        except Exception as error:  # nunca debe morir un worker
            _log(f'job={job_id} fallo inesperado: {type(error).__name__}: {error}')
            _marcar_error(job_id, f'Error inesperado al procesar el catálogo ({type(error).__name__}).')
        finally:
            _cola.task_done()


def _ejecutar_job(job_id):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job['estado'] = 'procesando'
        job['mensaje'] = 'Procesando catálogo…'
        job['actualizado'] = time.time()
        comercio_id = job['comercio_id']
        filename = job['filename']
        ruta_spool = job.get('ruta')
        data = _leer_spool(ruta_spool) if ruta_spool else (job.get('data') or b'')

    inicio = time.monotonic()
    _log(f'job={job_id} inicio comercio={comercio_id} archivo={filename!r} bytes={len(data)}')
    try:
        with _contexto_app():
            exito, mensaje, meta = _obtener_procesador()(comercio_id, filename, data)
    except Exception as error:
        _log(f'job={job_id} excepcion: {type(error).__name__}: {error}')
        _marcar_error(job_id, 'No se pudo completar la importación. Tu inventario no fue modificado.')
        return
    finally:
        _borrar_spool(ruta_spool)
        with _jobs_lock:
            job_actual = _jobs.get(job_id)
            if job_actual is not None:
                job_actual.pop('ruta', None)
                job_actual.pop('data', None)

    duracion = time.monotonic() - inicio
    _finalizar_job(
        job_id,
        exito=bool(exito),
        mensaje=mensaje,
        meta=meta,
        duracion=duracion,
    )
    _log(
        f'job={job_id} fin exito={exito} duracion={duracion:.1f}s '
        f'estado={"completado" if exito else "error"}'
    )


def _finalizar_job(job_id, *, exito, mensaje, meta=None, duracion=None):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        meta_limpio = _serializar_meta(meta)
        estado = 'error' if not exito else 'completado'
        # El éxito "real" solo cuando todas las imágenes son reales; si quedan
        # pendientes, el trabajo se reporta como PARCIAL (sin falsos positivos).
        if exito and meta_limpio.get('estado_imagenes') in ('parcial', 'sin_reales'):
            estado = 'parcial'
        job['estado'] = estado
        job['exito'] = bool(exito)
        job['mensaje'] = str(mensaje or '')[:1400]
        job['meta'] = meta_limpio
        job['duracion'] = round(duracion, 2) if duracion is not None else None
        job['actualizado'] = time.time()
        job.pop('ruta', None)
        job.pop('data', None)  # liberar memoria


def _marcar_error(job_id, mensaje):
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return
        job['estado'] = 'error'
        job['exito'] = False
        job['mensaje'] = str(mensaje or 'Error al procesar el catálogo.')[:1400]
        job['actualizado'] = time.time()
        job.pop('ruta', None)
        job.pop('data', None)


def _serializar_meta(meta):
    if not meta or not isinstance(meta, dict):
        return {}
    limpio = {}
    for clave, valor in meta.items():
        if isinstance(valor, (str, int, float, bool)) or valor is None:
            limpio[str(clave)] = valor
    return limpio


def _limpiar_jobs_antiguos():
    ahora = time.time()
    with _jobs_lock:
        vencidos = [
            jid
            for jid, job in _jobs.items()
            if job.get('estado') in _ESTADOS_FINALES
            and (ahora - job.get('actualizado', ahora)) > _JOB_TTL_SEG
        ]
        for jid in vencidos:
            job = _jobs.pop(jid, None)
            if job:
                _borrar_spool(job.get('ruta'))
        if len(_jobs) > _MAX_JOBS:
            ordenados = sorted(
                _jobs.items(),
                key=lambda item: item[1].get('creado', 0),
            )
            sobrantes = len(_jobs) - _MAX_JOBS
            for jid, job in ordenados:
                if sobrantes <= 0:
                    break
                if job.get('estado') in _ESTADOS_FINALES:
                    _jobs.pop(jid, None)
                    _borrar_spool(job.get('ruta'))
                    sobrantes -= 1


def encolar_importacion(comercio_id, filename, data, usuario_id=None):
    """Encola un catálogo. Retorna el resumen del job o lanza ColaImportacionLlena."""
    if not data:
        raise ValueError('El archivo está vacío.')
    cola = _asegurar_cola()
    _limpiar_jobs_antiguos()

    job_id = uuid.uuid4().hex
    ahora = time.time()
    try:
        ruta_spool = _guardar_spool(job_id, bytes(data))
    except Exception as error:
        raise ValueError(f'No se pudo guardar el catálogo temporalmente: {error}') from error
    job = {
        'job_id': job_id,
        'comercio_id': int(comercio_id) if comercio_id is not None else None,
        'usuario_id': usuario_id,
        'filename': str(filename or 'inventario.csv'),
        'ruta': ruta_spool,
        'estado': 'encolado',
        'exito': None,
        'mensaje': 'En cola para procesar.',
        'meta': {},
        'creado': ahora,
        'actualizado': ahora,
    }
    try:
        cola.put_nowait(job_id)
    except queue.Full as error:
        _borrar_spool(ruta_spool)
        raise ColaImportacionLlena(
            'El servidor está procesando varios catálogos a la vez. '
            'Espera unos segundos y vuelve a intentarlo.'
        ) from error

    with _jobs_lock:
        _jobs[job_id] = job

    _log(f'job={job_id} encolado comercio={comercio_id} archivo={filename!r}')
    return {
        'job_id': job_id,
        'estado': 'encolado',
        'posicion': cola.qsize(),
        'mensaje': 'Tu catálogo se está procesando en segundo plano.',
    }


def obtener_estado_job(job_id, *, comercio_id=None, usuario_id=None):
    """Estado de un job, validando pertenencia. ``None`` si no existe o no es suyo."""
    if not job_id:
        return None
    _limpiar_jobs_antiguos()
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        if comercio_id is not None and job.get('comercio_id') != int(comercio_id):
            return None
        if usuario_id is not None and job.get('usuario_id') not in (None, usuario_id):
            return None
        return {
            'job_id': job['job_id'],
            'estado': job['estado'],
            'exito': job.get('exito'),
            'mensaje': job.get('mensaje'),
            'meta': dict(job.get('meta') or {}),
            'creado': job.get('creado'),
            'actualizado': job.get('actualizado'),
            'duracion': job.get('duracion'),
        }


def estadisticas_cola():
    """Métricas simples para /health o diagnóstico."""
    with _jobs_lock:
        por_estado = {}
        for job in _jobs.values():
            estado = job.get('estado') or 'desconocido'
            por_estado[estado] = por_estado.get(estado, 0) + 1
    return {
        'trabajadores': _WORKERS,
        'capacidad': _QUEUE_MAX,
        'en_cola': _cola.qsize() if _cola is not None else 0,
        'jobs': len(_jobs),
        'por_estado': por_estado,
    }


def reiniciar_para_pruebas():
    """Solo para tests: vacía el registro de jobs (no detiene los hilos)."""
    with _jobs_lock:
        _jobs.clear()

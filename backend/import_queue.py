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
import logging
logger = logging.getLogger('localis.import_queue')

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
    logger.info(f'{_LOG} {mensaje}')


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
        # Otro worker lo encoló: el estado está en la base de datos.
        job = _job_leer(job_id)
    if not job:
        _log(f'job={job_id} no encontrado (ni en memoria ni en BD)')
        return
    with _jobs_lock:
        _jobs[job_id] = job
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
    meta_limpio = _serializar_meta(meta)
    estado = 'error' if not exito else 'completado'
    # El éxito "real" solo cuando todas las imágenes son reales; si quedan
    # pendientes, el trabajo se reporta como PARCIAL (sin falsos positivos).
    if exito and meta_limpio.get('estado_imagenes') in ('parcial', 'sin_reales'):
        estado = 'parcial'
    duracion_redondeada = round(duracion, 2) if duracion is not None else None
    mensaje_corto = str(mensaje or '')[:1400]
    # El estado compartido va PRIMERO: así, en cuanto alguien ve el estado final
    # (aunque sea desde otro worker), la base de datos ya lo refleja.
    _job_actualizar(
        job_id,
        estado=estado,
        exito=bool(exito),
        mensaje=mensaje_corto,
        meta=meta_limpio,
        duracion=duracion_redondeada,
        ruta=None,
        actualizado=time.time(),
    )
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job['estado'] = estado
            job['exito'] = bool(exito)
            job['mensaje'] = mensaje_corto
            job['meta'] = meta_limpio
            job['duracion'] = duracion_redondeada
            job['actualizado'] = time.time()
            job.pop('ruta', None)
            job.pop('data', None)  # liberar memoria


def _marcar_error(job_id, mensaje):
    mensaje_corto = str(mensaje or 'Error al procesar el catálogo.')[:1400]
    _job_actualizar(
        job_id,
        estado='error',
        exito=False,
        mensaje=mensaje_corto,
        ruta=None,
        actualizado=time.time(),
    )
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job:
            job['estado'] = 'error'
            job['exito'] = False
            job['mensaje'] = mensaje_corto
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


def _meta_a_texto(meta):
    import json

    try:
        return json.dumps(meta or {}, ensure_ascii=False)
    except Exception:
        return '{}'


def _meta_desde_texto(texto):
    import json

    try:
        valor = json.loads(texto or '{}')
        return valor if isinstance(valor, dict) else {}
    except Exception:
        return {}


def _job_guardar(job):
    """Persiste el job (best-effort). Nunca lanza: si falla, queda solo en memoria."""
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                INSERT INTO import_jobs (
                    job_id, comercio_id, usuario_id, filename, estado,
                    exito, mensaje, meta, duracion, ruta, creado, actualizado
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (job_id) DO UPDATE SET
                    estado = EXCLUDED.estado,
                    exito = EXCLUDED.exito,
                    mensaje = EXCLUDED.mensaje,
                    meta = EXCLUDED.meta,
                    duracion = EXCLUDED.duracion,
                    ruta = EXCLUDED.ruta,
                    actualizado = EXCLUDED.actualizado
                """,
                (
                    job['job_id'],
                    job.get('comercio_id'),
                    job.get('usuario_id'),
                    job.get('filename'),
                    job.get('estado') or 'encolado',
                    job.get('exito'),
                    job.get('mensaje'),
                    _meta_a_texto(job.get('meta')),
                    job.get('duracion'),
                    job.get('ruta'),
                    job.get('creado'),
                    job.get('actualizado'),
                ),
            )
            conexion.commit()
        return True
    except Exception as error:
        logger.debug(f'{_LOG} estado de job no persistido ({type(error).__name__}): {error}')
        return False


def _job_actualizar(job_id, **campos):
    """Actualiza campos del job en BD (best-effort)."""
    if not campos:
        return False
    columnas = []
    valores = []
    for clave in ('estado', 'exito', 'mensaje', 'duracion', 'ruta', 'actualizado'):
        if clave in campos:
            columnas.append(f'{clave} = ?')
            valores.append(campos[clave])
    if 'meta' in campos:
        columnas.append('meta = ?')
        valores.append(_meta_a_texto(campos['meta']))
    if not columnas:
        return False
    valores.append(job_id)
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                f"UPDATE import_jobs SET {', '.join(columnas)} WHERE job_id = ?",
                tuple(valores),
            )
            conexion.commit()
        return True
    except Exception as error:
        logger.debug(f'{_LOG} job no actualizado ({type(error).__name__}): {error}')
        return False


def _job_leer(job_id):
    """Lee el job de BD. ``None`` si no existe o si la BD no responde."""
    try:
        from backend.db import get_db_connection

        with get_db_connection(row_factory=True) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT job_id, comercio_id, usuario_id, filename, estado, exito,
                       mensaje, meta, duracion, creado, actualizado
                FROM import_jobs WHERE job_id = ?
                """,
                (job_id,),
            )
            fila = cursor.fetchone()
        if not fila:
            return None
        job = dict(fila)
        job['meta'] = _meta_desde_texto(job.get('meta'))
        return job
    except Exception as error:
        logger.debug(f'{_LOG} job no leído ({type(error).__name__}): {error}')
        return None


def _job_borrar(job_id):
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute('DELETE FROM import_jobs WHERE job_id = ?', (job_id,))
            conexion.commit()
        return True
    except Exception:
        return False


_PURGA_MIN_INTERVALO = max(30, int(os.getenv('LOCALIS_IMPORT_PURGA_SEC', '120')))
_ultima_purga = [0.0]


def _job_purgar():
    """Borra en BD los jobs terminados hace rato y recorta el total.

    Con freno: el polling llama aquí cada pocos segundos y un ``DELETE`` por
    petición castigaría la base de datos sin necesidad.
    """
    if time.monotonic() - _ultima_purga[0] < _PURGA_MIN_INTERVALO:
        return False
    _ultima_purga[0] = time.monotonic()
    try:
        from backend.db import get_db_connection

        ahora = time.time()
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'DELETE FROM import_jobs WHERE estado IN (?, ?, ?) AND actualizado < ?',
                ('completado', 'parcial', 'error', ahora - _JOB_TTL_SEG),
            )
            cursor.execute(
                """
                DELETE FROM import_jobs WHERE job_id IN (
                    SELECT job_id FROM import_jobs
                    ORDER BY actualizado ASC
                    OFFSET ?
                )
                """,
                (_MAX_JOBS,),
            )
            conexion.commit()
        return True
    except Exception:
        return False


def _jobs_por_estado():
    """Conteo por estado desde BD (vacío si no hay conexión)."""
    try:
        from backend.db import get_db_connection

        with get_db_connection(row_factory=True) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT estado, COUNT(*) AS n FROM import_jobs GROUP BY estado'
            )
            return {
                str(fila['estado']): int(fila['n'])
                for fila in (cursor.fetchall() or [])
                if fila.get('estado') is not None
            }
    except Exception:
        return {}


def _limpiar_jobs_antiguos():
    ahora = time.time()
    # También en BD (best-effort): es la fuente compartida entre workers.
    _job_purgar()
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
    # Registrar el job ANTES de encolarlo: un worker puede despertar en cuanto
    # ``put_nowait`` suelta el ítem y buscar el job en ``_jobs``. Si aún no está
    # registrado, lo descartaba en silencio y el catálogo nunca se insertaba
    # (job eternamente en 'encolado'). Este orden elimina la carrera.
    with _jobs_lock:
        _jobs[job_id] = job
    # Estado compartido: cualquier worker puede responder al polling.
    _job_guardar(job)
    try:
        cola.put_nowait(job_id)
    except queue.Full as error:
        with _jobs_lock:
            _jobs.pop(job_id, None)
        _job_borrar(job_id)
        _borrar_spool(ruta_spool)
        raise ColaImportacionLlena(
            'El servidor está procesando varios catálogos a la vez. '
            'Espera unos segundos y vuelve a intentarlo.'
        ) from error

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
        # Con varios workers, el job puede haberlo encolado otro: se lee de BD.
        job = _job_leer(job_id)
    if not job:
        return None
    if comercio_id is not None and job.get('comercio_id') != int(comercio_id):
        return None
    if usuario_id is not None and job.get('usuario_id') not in (None, usuario_id):
        return None
    return {
        'job_id': job.get('job_id') or job_id,
        'estado': job.get('estado'),
        'exito': job.get('exito'),
        'mensaje': job.get('mensaje'),
        'meta': dict(job.get('meta') or {}),
        'creado': job.get('creado'),
        'actualizado': job.get('actualizado'),
        'duracion': job.get('duracion'),
    }


def estadisticas_cola():
    """Métricas simples para /health o diagnóstico.

    El estado vive en la base de datos (compartido entre workers); la cola y los
    trabajadores son de este proceso.
    """
    with _jobs_lock:
        locales = {}
        for job in _jobs.values():
            estado = job.get('estado') or 'desconocido'
            locales[estado] = locales.get(estado, 0) + 1
    compartidos = _jobs_por_estado()
    por_estado = compartidos or locales
    return {
        'trabajadores': _WORKERS,
        'capacidad': _QUEUE_MAX,
        'en_cola': _cola.qsize() if _cola is not None else 0,
        'jobs': sum(por_estado.values()),
        'por_estado': por_estado,
        'en_memoria': len(_jobs),
    }


def reiniciar_para_pruebas():
    """Solo para tests: vacía el registro de jobs (no detiene los hilos).

    Borra de la BD **solo los jobs que este proceso tiene registrados** (los de
    la prueba). Vaciar la tabla entera eliminaría trabajos reales de comercios.
    """
    with _jobs_lock:
        ids = list(_jobs.keys())
        _jobs.clear()
    for job_id in ids:
        _job_borrar(job_id)

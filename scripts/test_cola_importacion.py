#!/usr/bin/env python3
"""Pruebas del sistema de importación asíncrona (sin base de datos real).

Cubre:
  - Mecánica de la cola: encolar → procesar → completado / error.
  - Rechazo controlado cuando la cola está llena (HTTP 503 esperado).
  - Aislamiento por comercio (un comercio no ve trabajos de otro).
  - Contrato HTTP 202 y polling en la ruta/flujo del panel (auditoría de fuente).
  - Barcode Spider y APIs globales eliminadas del pipeline de imágenes.
"""

from __future__ import annotations

import queue
import sys
import time
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass

from backend import import_queue  # noqa: E402  (ruta del proyecto)


def _hay_bd():
    """True si hay base de datos (la parte de estado compartido la necesita)."""
    import os

    return bool((os.environ.get('DATABASE_URL') or '').strip())

_ERRORES = []


def _ok(condicion, mensaje):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    _ERRORES.append(mensaje)
    return False


def _leer(relativo):
    return (RAIZ / relativo).read_text(encoding='utf-8')


def _esperar(job_id, objetivos, comercio_id=None, timeout=12.0):
    fin = time.time() + timeout
    while time.time() < fin:
        estado = import_queue.obtener_estado_job(job_id, comercio_id=comercio_id)
        if estado and estado['estado'] in objetivos:
            return estado
        time.sleep(0.05)
    return None


def _probar_estado_compartido():
    """El estado del trabajo debe sobrevivir a 'otro worker' (se lee de la BD).

    Es lo que permite ``WEB_CONCURRENCY > 1``: el polling puede caer en un
    proceso distinto del que procesó el archivo.
    """
    print('\n=== Estado compartido entre workers (base de datos) ===')
    if not _hay_bd():
        print('  SKIP  sin DATABASE_URL: no se puede probar el estado compartido')
        return

    def procesador(comercio_id, filename, data):
        return True, 'Importación completada.', {'filas': 2}

    import_queue._procesador = procesador
    import_queue.configurar_cola(None)
    import_queue.reiniciar_para_pruebas()

    job = import_queue.encolar_importacion(103, 'compartido.csv', b'a,b\n1,2\n')
    job_id = job['job_id']
    estado = _esperar(job_id, {'completado', 'parcial', 'error'}, comercio_id=103)
    _ok(estado is not None, f"el job termina ({estado and estado['estado']})")

    # Simula OTRO worker: se vacía la memoria local del proceso.
    with import_queue._jobs_lock:
        import_queue._jobs.clear()

    desde_bd = import_queue.obtener_estado_job(job_id, comercio_id=103)
    _ok(desde_bd is not None, 'otro worker encuentra el job (estado leído de la BD)')
    _ok(
        desde_bd and desde_bd['estado'] in ('completado', 'parcial'),
        f"estado compartido = {desde_bd and desde_bd['estado']}",
    )
    _ok(
        desde_bd and desde_bd['meta'].get('filas') == 2,
        'la meta también viaja por la BD',
    )
    _ok(
        import_queue.obtener_estado_job(job_id, comercio_id=999) is None,
        'otro comercio sigue sin verlo (aislamiento)',
    )

    # El test vació la memoria a propósito (simula otro worker), así que el borrado
    # se hace por id, que es justo lo que usa la limpieza real.
    import_queue._job_borrar(job_id)
    _ok(
        import_queue.obtener_estado_job(job_id, comercio_id=103) is None,
        'la limpieza borra también la fila en BD',
    )


def _probar_mecanica():
    print('\n=== Mecánica de la cola (procesador simulado) ===')
    registros = []

    def procesador(comercio_id, filename, data):
        registros.append((comercio_id, filename, len(data)))
        return True, 'Importación completada: 3 productos cargados.', {'filas': 3}

    import_queue._procesador = procesador
    import_queue.configurar_cola(None)
    import_queue.reiniciar_para_pruebas()

    job = import_queue.encolar_importacion(101, 'catalogo.csv', b'nombre,precio\nHarina,1.5\n')
    _ok(job['job_id'] and job['estado'] == 'encolado', 'encola y devuelve job_id')
    _ok(
        not import_queue.obtener_estado_job(job['job_id'], comercio_id=999),
        'no filtra trabajos de otro comercio',
    )

    estado = _esperar(job['job_id'], {'completado', 'error'}, comercio_id=101)
    _ok(bool(estado) and estado['estado'] == 'completado', 'el worker completa el trabajo')
    _ok(estado and estado.get('exito') is True, 'marca exito=True')
    _ok(
        registros and registros[0][0] == 101 and registros[0][2] > 0,
        'procesa el comercio y los bytes correctos',
    )

    def procesador_error(_c, _f, _d):
        return False, 'No se pudo completar la importación.', {'plan_sugerido': 'pro'}

    import_queue._procesador = procesador_error
    job2 = import_queue.encolar_importacion(102, 'catalogo.xlsx', b'datos')
    estado2 = _esperar(job2['job_id'], {'completado', 'error'}, comercio_id=102)
    _ok(bool(estado2) and estado2['estado'] == 'error', 'el worker reporta error')
    _ok(estado2 and estado2['meta'].get('plan_sugerido') == 'pro', 'propaga meta/plan sugerido')


def _probar_cola_llena():
    print('\n=== Cola llena (control de concurrencia) ===')

    class _ColaLlena:
        def put_nowait(self, _item):
            raise queue.Full()

        def qsize(self):
            return 1

    with patch.object(import_queue, '_asegurar_cola', return_value=_ColaLlena()):
        try:
            import_queue.encolar_importacion(1, 'x.csv', b'data')
            _ok(False, 'lanza ColaImportacionLlena')
        except import_queue.ColaImportacionLlena:
            _ok(True, 'lanza ColaImportacionLlena (-> HTTP 503)')


def _auditar_fuente():
    print('\n=== Auditoría de fuente (contrato HTTP 202 + polling) ===')
    # Las rutas viven ahora en el blueprint de comercio (antes estaban en main.py);
    # se auditan en el módulo donde están de verdad.
    rutas = _leer('backend/blueprints/comercio.py')
    _ok('202,' in rutas, 'la ruta responde HTTP 202')
    _ok('estado_importacion' in rutas, 'existe la ruta de estado del trabajo')
    _ok('encolar_importacion(' in rutas, 'la ruta encola (no procesa en línea)')
    _ok(
        'procesar_csv_productos(comercio' not in rutas,
        'la ruta ya NO procesa el CSV en línea',
    )

    cola = _leer('backend/import_queue.py')
    _ok('queue.Queue' in cola and 'maxsize=' in cola, 'cola acotada por maxsize')
    _ok('daemon=True' in cola, 'workers daemon (no bloquean el apagado)')
    _ok('app_context()' in cola, 'los workers corren con contexto de Flask')
    _ok('task_done' in cola, 'libera la cola con task_done')

    plantilla = _leer('templates/comercio.html')
    _ok('panel-comercio-csv-form' in plantilla, 'el formulario conserva el gancho JS')
    js = _leer('static/js/localis.js')
    _ok('inicializarFormularioCsv' in js, 'el panel intercepta el submit')
    _ok("X-Requested-With': 'fetch'" in js, 'la subida pide JSON (202)')
    _ok('/comercio/productos/importacion/' in js, 'el panel hace polling del estado')

    print('\n=== Código legacy de Barcode Spider purgado ===')
    _ok(
        not (RAIZ / 'services' / 'smart_image_pipeline.py').exists(),
        'services/smart_image_pipeline.py eliminado',
    )


def main() -> int:
    _probar_mecanica()
    _probar_cola_llena()
    _auditar_fuente()
    _probar_estado_compartido()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK importación asíncrona y pipeline sin Barcode Spider')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

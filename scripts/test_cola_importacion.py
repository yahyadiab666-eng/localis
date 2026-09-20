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

from backend import import_queue  # noqa: E402  (ruta del proyecto)

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
    main = _leer('main.py')
    _ok('202,' in main, 'la ruta responde HTTP 202')
    _ok('estado_importacion' in main, 'existe la ruta de estado del trabajo')
    _ok('encolar_importacion(' in main, 'la ruta encola (no procesa en línea)')
    _ok('procesar_csv_productos(comercio' not in main, 'la ruta ya NO procesa el CSV en línea')

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

    print('\n=== Barcode Spider y APIs globales eliminadas ===')
    from services import smart_image_pipeline as legacy

    for nombre in (
        '_buscar_barcode_spider_ean',
        '_buscar_barcode_spider_nombre',
        '_buscar_upcitemdb_ean',
        '_buscar_barcodelookup_ean',
        '_clave_spider',
    ):
        _ok(not hasattr(legacy, nombre), f'eliminada {nombre}')
    _ok(legacy.buscar_por_ean('7702084137520') is None, 'buscar_por_ean ya no sale a red')
    _ok(legacy.buscar_por_nombre('Harina PAN') is None, 'buscar_por_nombre ya no sale a red')


def main() -> int:
    _probar_mecanica()
    _probar_cola_llena()
    _auditar_fuente()

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

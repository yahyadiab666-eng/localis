#!/usr/bin/env python3
"""Pruebas de concurrencia y carga (sin BD ni red).

Valida la arquitectura para cientos de usuarios simultáneos:

  1. Cola de importación: N trabajos concurrentes, cola acotada, sin pérdidas.
  2. Asignación instantánea de imágenes en paralelo (hilos), sin inventar assets.
  3. Semáforo de CPU: ``rembg`` nunca corre en paralelo (LOCALIS_IMG_MAX_CONCURRENT).
"""

from __future__ import annotations

import io
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

_ERRORES = []


def _ok(condicion, mensaje):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    _ERRORES.append(mensaje)
    return False


def _probar_cola_concurrente():
    print('\n=== Cola de importación bajo concurrencia ===')
    from backend import import_queue

    completados = []
    lock = threading.Lock()

    def procesador(comercio_id, filename, data):
        time.sleep(0.03)
        with lock:
            completados.append(comercio_id)
        return True, f'ok {filename}', {'estado_imagenes': 'parcial'}

    import_queue._procesador = procesador
    import_queue.configurar_cola(None)
    import_queue.reiniciar_para_pruebas()

    total = 120

    def enviar(i):
        try:
            return import_queue.encolar_importacion(1000 + i, f'cat_{i}.csv', b'x,y\n1,2\n')
        except import_queue.ColaImportacionLlena:
            return {'estado': 'rechazado'}

    inicio = time.perf_counter()
    with ThreadPoolExecutor(max_workers=30) as ejecutor:
        jobs = list(ejecutor.map(enviar, range(total)))
    aceptados = [j for j in jobs if j.get('job_id')]
    rechazados = [j for j in jobs if not j.get('job_id')]
    _ok(len(aceptados) + len(rechazados) == total, f'{total} solicitudes concurrentes atendidas')

    # Espera a que se completen los aceptados.
    fin = time.perf_counter() + 30
    while fin > time.perf_counter():
        pendientes = [j for j in aceptados if (import_queue.obtener_estado_job(j['job_id']) or {}).get('estado') in ('encolado', 'procesando')]
        if not pendientes:
            break
        time.sleep(0.05)
    duracion = time.perf_counter() - inicio

    estados = {}
    for j in aceptados:
        est = (import_queue.obtener_estado_job(j['job_id']) or {}).get('estado')
        estados[est] = estados.get(est, 0) + 1
    _ok(len(completados) == len(aceptados), f'procesados {len(completados)}/{len(aceptados)} sin pérdidas')
    _ok(all(e in ('completado', 'parcial') for e in estados), f'estados finales: {estados}')
    _ok(duracion < 25, f'{len(aceptados)} trabajos en {duracion:.1f}s')
    print(f'     (aceptados={len(aceptados)}, rechazados_por_cola_llena={len(rechazados)})')


def _probar_asignacion_paralela():
    print('\n=== Asignación instantánea en paralelo (2.000 productos) ===')
    from unittest.mock import patch

    from backend.catalogo_maestro_index import IndiceMaestro
    from backend.inventory_import import asignar_imagenes_instantaneas

    def lote(base):
        return [
            {
                'nombre': f'Producto {base + i}',
                'descripcion': '',
                'marca': '',
                'categoria': '',
                'codigo_barras': None,
                'imagen_url': None,
            }
            for i in range(250)
        ]

    lotes = [lote(n * 250) for n in range(8)]
    inicio = time.perf_counter()
    with patch('backend.catalogo_maestro_index.obtener_indice', return_value=IndiceMaestro()):
        with ThreadPoolExecutor(max_workers=8) as ejecutor:
            list(ejecutor.map(lambda l: asignar_imagenes_instantaneas(l, {}, None), lotes))
    duracion = time.perf_counter() - inicio

    productos = [p for lote_ in lotes for p in lote_]
    vacios = [p for p in productos if not p.get('imagen_url')]
    _ok(len(productos) == 2000, '2.000 productos procesados en 8 hilos')
    _ok(not [p for p in productos if p.get('imagen_url')], 'cero imágenes inventadas (estado neutro)')
    _ok(all(p.get('imagen_estado') == 'pendiente' for p in productos), 'todos pendientes de foto verificada')
    _ok(len(vacios) == len(productos), 'sin foto verificada => imagen nula, no placeholder')
    _ok(duracion < 5, f'asignación paralela en {duracion:.2f}s')


def _probar_semaforo_cpu():
    print('\n=== Semáforo de CPU (rembg serializado) ===')
    from PIL import Image, ImageDraw

    import services.professional_image_pipeline as P

    # Imagen con fondo NO blanco para forzar la ruta de rembg.
    img = Image.new('RGB', (500, 500), (30, 90, 200))
    d = ImageDraw.Draw(img)
    d.ellipse([150, 150, 350, 350], fill=(200, 40, 40))
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    data = buf.getvalue()

    activos = {'max': 0, 'actual': 0}
    lock = threading.Lock()
    original = P._quitar_fondo_rembg

    def rembg_lento(_data):
        with lock:
            activos['actual'] += 1
            activos['max'] = max(activos['max'], activos['actual'])
        time.sleep(0.08)
        with lock:
            activos['actual'] -= 1
        return Image.new('RGBA', (500, 500), (255, 0, 0, 255))

    P._quitar_fondo_rembg = rembg_lento
    try:
        with ThreadPoolExecutor(max_workers=6) as ejecutor:
            list(ejecutor.map(lambda _i: P.procesar_fondo_blanco(data), range(6)))
    finally:
        P._quitar_fondo_rembg = original

    _ok(activos['max'] <= 1, f'rembg nunca en paralelo (max concurrente={activos["max"]})')


def main() -> int:
    _probar_cola_concurrente()
    _probar_asignacion_paralela()
    _probar_semaforo_cpu()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK concurrencia: cola, paralelismo y límite de CPU verificados')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

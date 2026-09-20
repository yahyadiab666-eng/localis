#!/usr/bin/env python3
"""
Diagnóstico del pipeline profesional de imágenes y de la cola asíncrona.

Verifica, SIN modificar nada y SIN generar claves:
  1. Carga de variables de entorno (.env y os.getenv).
  2. Que Barcode Spider / UPCitemdb / Barcode Lookup estén ELIMINADOS.
  3. Disponibilidad del pipeline profesional y de rembg (modelo local).
  4. Configuración de la cola asíncrona de importación (workers, capacidad).
  5. (Opcional) Una prueba de búsqueda real con UN producto:
     ejecuta con LOCALIS_DIAG_RED=1.

No persiste nada en la base de datos.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

MODULOS_PROHIBIDOS = (
    '_clave_spider',
    '_clave_upcitemdb',
    '_clave_barcodelookup',
    '_buscar_barcode_spider_ean',
    '_buscar_upcitemdb_ean',
    '_buscar_barcodelookup_ean',
)

EAN_PRUEBA = '7702084137520'
NOMBRE_PRUEBA = 'Harina P.A.N. 1kg'


def _separador(titulo):
    print('\n' + '=' * 60)
    print(titulo)
    print('=' * 60)


def main() -> int:
    fallos = []

    _separador('PASO 1 - Carga de variables de entorno')
    from dotenv import load_dotenv

    archivo_env = RAIZ / '.env'
    print(f'Ruta raíz del proyecto : {RAIZ}')
    print(f'¿Existe ".env"?        : {archivo_env.exists()}')
    if archivo_env.exists():
        load_dotenv(archivo_env, override=False)
    print(f'LOCALIS_IMG_PIPELINE   : {os.getenv("LOCALIS_IMG_PIPELINE", "(por defecto) 1")}')
    print(f'LOCALIS_IMG_WORKERS    : {os.getenv("LOCALIS_IMPORT_WORKERS", "(por defecto) 2")}')
    print(f'LOCALIS_IMG_QUEUE_MAX  : {os.getenv("LOCALIS_IMPORT_QUEUE_MAX", "(por defecto) 8")}')

    _separador('PASO 2 - Barcode Spider / APIs globales eliminadas')
    from services import smart_image_pipeline as legado

    for nombre in MODULOS_PROHIBIDOS:
        presente = hasattr(legado, nombre)
        print(f'  {nombre:32s} -> {"PRESENTE (mal)" if presente else "eliminada (ok)"}')
        if presente:
            fallos.append(f'{nombre} sigue presente en smart_image_pipeline')
    print(f'  buscar_por_ean(EAN)      -> {legado.buscar_por_ean(EAN_PRUEBA)!r} (siempre None)')
    print(f'  buscar_por_nombre(...)   -> {legado.buscar_por_nombre(NOMBRE_PRUEBA)!r} (siempre None)')

    _separador('PASO 3 - Pipeline profesional y rembg')
    from services import professional_image_pipeline as pro

    print(f'  pipeline_habilitado()  -> {pro.pipeline_habilitado()}')
    rembg_ok = importlib.util.find_spec('rembg') is not None
    onnx_ok = importlib.util.find_spec('onnxruntime') is not None
    print(f'  rembg instalado        -> {rembg_ok}')
    print(f'  onnxruntime instalado  -> {onnx_ok}')
    if not rembg_ok:
        fallos.append('rembg no está instalado (pip install rembg)')
    try:
        modelo = os.getenv('LOCALIS_REMBG_MODEL', 'u2net')
        ruta_modelo = Path.home() / '.rembg' / 'models' / modelo / f'{modelo}.onnx'
        print(f'  modelo {modelo:15s} -> {"descargado" if ruta_modelo.exists() else "se descargará en la 1a ejecución"}')
    except Exception as error:
        print(f'  modelo                 -> no verificable ({type(error).__name__})')

    _separador('PASO 4 - Cola asíncrona de importación')
    from backend import import_queue as cola

    stats = cola.estadisticas_cola()
    print(f'  trabajadores -> {stats["trabajadores"]}')
    print(f'  capacidad    -> {stats["capacidad"]}')
    print(f'  en cola      -> {stats["en_cola"]}')
    print('  contrato     -> POST cargar-csv responde HTTP 202 + polling de estado')

    _separador('PASO 5 - Búsqueda real (opcional)')
    if os.getenv('LOCALIS_DIAG_RED') == '1':
        candidatos = pro.buscar_candidatos(
            codigo_barras=EAN_PRUEBA,
            nombre=NOMBRE_PRUEBA,
            descripcion='harina de maíz blanco 1kg',
        )
        print(f'  candidatos encontrados -> {len(candidatos)}')
        for candidato in candidatos[:5]:
            print(f'    score={candidato.score:6.1f} {candidato.fuente:14s} {candidato.url[:90]}')
    else:
        print('  omitida (exporta LOCALIS_DIAG_RED=1 para probar la búsqueda real)')

    _separador('REPORTE')
    if fallos:
        print('Puntos a corregir:')
        for item in fallos:
            print(f'  - {item}')
        return 1
    print('OK: pipeline profesional activo, sin Barcode Spider y cola lista.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

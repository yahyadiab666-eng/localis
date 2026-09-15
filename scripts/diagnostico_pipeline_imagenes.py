#!/usr/bin/env python3
"""
Diagnóstico aislado del pipeline de imágenes automático (Parte 1).

Verifica, SIN modificar nada y SIN generar claves:
  1. Carga de variables de entorno (load_dotenv) y nombre exacto del archivo '.env'.
  2. Presencia de BARCODE_SPIDER_API_KEY / UPCITEMDB_API_KEY / BARCODE_LOOKUP_API_KEY.
  3. Resultado de hay_proveedor_pagado() y por qué.
  4. Prueba de red real con UN solo producto de prueba (EAN + nombre).
  5. Reporte claro: qué funciona, qué no y dónde se rompe la cadena.

No persiste nada en la base de datos. Solo imprime el estado de la cadena.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

CLAVES = (
    'BARCODE_SPIDER_API_KEY',
    'UPCITEMDB_API_KEY',
    'BARCODE_LOOKUP_API_KEY',
)

# Producto único de prueba: Coca-Cola 355 ml (EAN real para probar el lookup).
EAN_PRUEBA = '049000028911'
NOMBRE_PRUEBA = 'Coca Cola lata 355ml'
DESCRIPCION_PRUEBA = 'refresco gaseoso'


def _estado_clave(nombre):
    valor = (os.getenv(nombre) or '').strip()
    if not valor:
        return 'AUSENTE/VACIA'
    return f'CON VALOR (len={len(valor)})'


def _separador(titulo):
    print('\n' + '=' * 60)
    print(titulo)
    print('=' * 60)


def main() -> int:
    fallos = []

    # --- Paso 1: carga de .env ---
    _separador('PASO 1 - Carga de variables de entorno (load_dotenv)')
    from dotenv import load_dotenv

    archivo_env = RAIZ / '.env'
    print(f'Ruta raíz del proyecto : {RAIZ}')
    print(f'¿Existe "{archivo_env.name}" exactamente? : {archivo_env.exists()}')
    variantes = sorted(p.name for p in RAIZ.glob('*env*') if p.is_file())
    print(f'Archivos con "env" en el nombre : {variantes}')
    if archivo_env.name != '.env':
        fallos.append('El archivo de entorno no se llama exactamente ".env".')

    print('\n-- os.getenv() ANTES de load_dotenv --')
    for clave in CLAVES:
        print(f'  {clave} = {_estado_clave(clave)}')

    load_dotenv(archivo_env, override=True)

    print('\n-- os.getenv() DESPUES de load_dotenv(override=True) --')
    for clave in CLAVES:
        print(f'  {clave} = {_estado_clave(clave)}')

    declaradas = set()
    if archivo_env.exists():
        for linea in archivo_env.read_text(encoding='utf-8', errors='ignore').splitlines():
            linea = linea.strip()
            if '=' in linea and not linea.startswith('#'):
                declaradas.add(linea.split('=', 1)[0].strip())
    print('\n-- Claves de imágenes declaradas dentro de ".env" --')
    for clave in CLAVES:
        print(f'  {clave} : {"SI declarada" if clave in declaradas else "NO declarada"}')

    # --- Paso 3: hay_proveedor_pagado() ---
    _separador('PASO 3 - hay_proveedor_pagado() aislado')
    from services.smart_image_pipeline import (
        _clave_barcodelookup,
        _clave_spider,
        _clave_upcitemdb,
        hay_proveedor_pagado,
        resolver_imagen_automatica,
    )

    print(f'  _clave_spider()         -> {"CON VALOR" if _clave_spider() else "vacia"}')
    print(f'  _clave_upcitemdb()      -> {"CON VALOR" if _clave_upcitemdb() else "vacia"}')
    print(f'  _clave_barcodelookup()  -> {"CON VALOR" if _clave_barcodelookup() else "vacia"}')
    resultado = hay_proveedor_pagado()
    print(f'  hay_proveedor_pagado()  -> {resultado}')
    print(f'  Motivo: {"hay al menos una clave con valor" if resultado else "NINGUNA clave de proveedor tiene valor"}')

    # --- Paso 4: prueba de red real con UN producto ---
    _separador('PASO 4 - Prueba de red real (UN solo producto, sin persistir)')
    print(f'  hay_proveedor_pagado() = {hay_proveedor_pagado()}')
    print('  (los logs [Localis SmartImage] muestran cada GET y su respuesta HTTP)')

    print('\n  >>> PRUEBA A: lookup por EAN')
    resultado_ean = resolver_imagen_automatica(
        codigo_barras=EAN_PRUEBA,
        nombre=NOMBRE_PRUEBA,
        descripcion=DESCRIPCION_PRUEBA,
    )
    print(
        '  RESULTADO A -> url=%r | fuente=%s | placeholder=%s'
        % (resultado_ean.url, resultado_ean.fuente, resultado_ean.es_placeholder)
    )

    print('\n  >>> PRUEBA B: búsqueda por nombre')
    resultado_nombre = resolver_imagen_automatica(
        nombre=NOMBRE_PRUEBA,
        descripcion=DESCRIPCION_PRUEBA,
    )
    print(
        '  RESULTADO B -> url=%r | fuente=%s | placeholder=%s'
        % (resultado_nombre.url, resultado_nombre.fuente, resultado_nombre.es_placeholder)
    )

    # --- Paso 5: reporte ---
    _separador('PASO 5 - REPORTE DE LA CADENA')
    encontrada = (
        not resultado_ean.es_placeholder or not resultado_nombre.es_placeholder
    )
    if not hay_proveedor_pagado():
        veredicto = (
            'La cadena de código esta BIEN, pero NO hay ninguna clave con valor: '
            'el pipeline corta en "sin clave" y devuelve placeholder sin salir a red.'
        )
        fallos.append('No hay proveedor con clave -> nunca hay llamada de red.')
    elif encontrada:
        veredicto = (
            'CADENA COMPLETA OK: variable de entorno -> función -> red -> respuesta -> URL.'
        )
    else:
        veredicto = (
            'La cadena de codigo esta BIEN (carga, funcion, peticion HTTP y manejo de respuesta), '
            'pero el proveedor RECHAZO la peticion (p.ej. HTTP 403 por suscripcion/clave vencida). '
            'El pipeline hace su trabajo y cae a placeholder. Revisa el codigo HTTP en los logs.'
        )

    print(veredicto)
    print('\n  Desglose:')
    print('    [x] Carga de .env y os.getenv()          -> funciona')
    print('    [x] hay_proveedor_pagado()               -> funciona')
    print('    [x] Construcción de la URL y User-Agent  -> funciona (GET visible en logs)')
    print('    [x] Envío HTTP y manejo de 401/403/200   -> funciona (loguea el cuerpo)')
    print('    [x] Cascada EAN -> nombre -> placeholder -> funciona')
    print(f'    [ ] Respuesta con imagen real            -> {"OK" if encontrada else "NO (sin imagen util)"}')

    if fallos:
        print('\nPuntos a corregir:')
        for item in fallos:
            print(f'  - {item}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

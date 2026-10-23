"""Copia las variables de entorno de un servicio de Render a otro.

¿Para qué? Render **no permite cambiar la región** de un servicio ya creado: hay
que crear uno nuevo en la región correcta. Y volver a teclear todas las claves es
un infierno. Este script las copia por la API, sin tocarlas.

Uso (Windows PowerShell):

    $env:RENDER_API_KEY = 'rnd_tu_clave'      # Dashboard -> Account Settings -> API Keys
    python scripts/copiar_env_render.py --lista
    python scripts/copiar_env_render.py --desde localis --hacia localis-us-east
    python scripts/copiar_env_render.py --desde localis --hacia localis-us-east --apply

Sin ``--apply`` es **dry-run**: solo muestra qué haría (y oculta los valores).
``--lista`` imprime los servicios con su **región**, que es el dato que decide si
merece la pena mover algo.

La clave se lee de ``RENDER_API_KEY`` (variable de entorno o el ``.env`` local,
que está ignorado por git). El script **nunca imprime la clave**: en el dry-run
solo muestra los 4 primeros caracteres de cada valor.
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

# Igual que el resto de scripts: se puede poner RENDER_API_KEY en el .env local
# (ignorado por git) y el script la lee sin que haya que exportarla a mano.
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'), override=False)
except Exception:
    pass

API = 'https://api.render.com/v1'


def _cabeceras():
    clave = (os.environ.get('RENDER_API_KEY') or '').strip()
    if not clave:
        print('Falta RENDER_API_KEY en el entorno.')
        print('  PowerShell:  $env:RENDER_API_KEY = "rnd_..."')
        sys.exit(2)
    return {'Authorization': f'Bearer {clave}', 'Accept': 'application/json'}


def listar_servicios(cabeceras):
    respuesta = requests.get(f'{API}/services', headers=cabeceras, params={'limit': 100}, timeout=30)
    respuesta.raise_for_status()
    filas = respuesta.json() or []
    servicios = []
    for fila in filas:
        datos = fila.get('service') or fila
        servicios.append(
            {
                'id': datos.get('id'),
                'name': datos.get('name'),
                'type': datos.get('type'),
                'region': datos.get('region') or datos.get('serviceDetails', {}).get('region'),
                'url': datos.get('serviceDetails', {}).get('url'),
                'suspended': datos.get('suspended'),
            }
        )
    return servicios


def _resolver(servicios, referencia):
    ref = str(referencia or '').strip().lower()
    for s in servicios:
        if str(s.get('id', '')).lower() == ref:
            return s
        if str(s.get('name', '')).lower() == ref:
            return s
    return None


def leer_env(cabeceras, service_id):
    respuesta = requests.get(
        f'{API}/services/{service_id}/env-vars', headers=cabeceras, params={'limit': 100}, timeout=30
    )
    respuesta.raise_for_status()
    filas = respuesta.json() or []
    salida = []
    for fila in filas:
        datos = fila.get('envVar') or fila
        if isinstance(datos, dict) and datos.get('key'):
            salida.append({'key': datos['key'], 'value': datos.get('value', '')})
    return salida


def escribir_env(cabeceras, service_id, variables, reemplazar=True):
    respuesta = requests.put(
        f'{API}/services/{service_id}/env-vars',
        headers={**cabeceras, 'Content-Type': 'application/json'},
        params={'replace': 'true' if reemplazar else 'false'},
        json=[{'key': v['key'], 'value': v['value']} for v in variables],
        timeout=60,
    )
    respuesta.raise_for_status()
    return respuesta.json()


def main():
    parser = argparse.ArgumentParser(description='Copia variables de entorno entre servicios Render')
    parser.add_argument('--lista', action='store_true', help='lista servicios con su región')
    parser.add_argument('--desde', help='servicio origen (nombre o id)')
    parser.add_argument('--hacia', help='servicio destino (nombre o id)')
    parser.add_argument('--apply', action='store_true', help='escribe de verdad (si no, dry-run)')
    parser.add_argument(
        '--export',
        metavar='RUTA',
        help='guarda las variables del origen en un JSON (¡contiene secretos!)',
    )
    parser.add_argument(
        '--import',
        dest='importar',
        metavar='RUTA',
        help='escribe en --hacia las variables de un JSON exportado antes',
    )
    args = parser.parse_args()

    cabeceras = _cabeceras()

    # --- Importar desde un JSON exportado (ruta "borrar y recrear con el mismo
    # nombre" para conservar la URL y no tocar el APK). -----------------------
    if args.importar:
        import json

        if not args.hacia:
            print('Falta --hacia (servicio destino).')
            return 2
        with open(args.importar, encoding='utf-8') as fichero:
            variables = json.load(fichero)
        servicios = listar_servicios(cabeceras)
        destino = _resolver(servicios, args.hacia)
        if not destino:
            print(f'No encontrado el servicio destino: {args.hacia}')
            return 2
        print(f"Destino: {destino['name']} (region={destino['region']})")
        print(f'Variables a escribir: {len(variables)}')
        if not args.apply:
            print('DRY-RUN: repite con --apply para escribirlas.')
            return 0
        escribir_env(cabeceras, destino['id'], variables, reemplazar=True)
        print(f'Listo: {len(variables)} variables escritas en {destino["name"]}.')
        return 0

    if args.lista or not args.desde:
        print('Servicios en tu cuenta de Render:')
        for s in listar_servicios(cabeceras):
            print(
                f"  {s['name']:<28} region={str(s['region']):<12} tipo={str(s['type']):<14} "
                f"id={s['id']}"
            )
        if not args.desde:
            print('\nPara copiar: --desde <origen> --hacia <destino> [--apply]')
        return 0

    servicios = listar_servicios(cabeceras)
    origen = _resolver(servicios, args.desde)
    if not origen:
        print(f'No encontrado el servicio origen: {args.desde}')
        return 2

    print(f"Origen : {origen['name']} (region={origen['region']}, id={origen['id']})")

    variables = leer_env(cabeceras, origen['id'])
    print(f'\nVariables en el origen: {len(variables)}')
    for v in variables:
        valor = v['value'] or ''
        muestra = '(vacío)' if not valor else f'{valor[:4]}…{valor[-2:]} ({len(valor)} caracteres)'
        print(f"  {v['key']:<38} {muestra}")

    if args.export:
        import json

        with open(args.export, 'w', encoding='utf-8') as fichero:
            json.dump(variables, fichero, ensure_ascii=False, indent=2)
        print(f'\nExportadas {len(variables)} variables a {args.export}')
        print('OJO: ese fichero tiene SECRETOS. Guárdalo fuera del repo y bórralo al terminar.')

    if not args.hacia:
        print(
            '\nFalta --hacia (servicio destino) para copiarlas directamente.'
            if not args.export
            else '\nYa están guardadas: crea el servicio nuevo y usa --import.'
        )
        return 0 if args.export else 2

    destino = _resolver(servicios, args.hacia)
    if not destino:
        print(f'No encontrado el servicio destino: {args.hacia}')
        return 2
    print(f"Destino: {destino['name']} (region={destino['region']}, id={destino['id']})")

    if not args.apply:
        print(f'\nDRY-RUN: se copiarían {len(variables)} variables al destino.')
        print('Repite con --apply para escribirlas (reemplaza las del destino).')
        return 0

    escribir_env(cabeceras, destino['id'], variables, reemplazar=True)
    print(f"\nListo: {len(variables)} variables copiadas a {destino['name']}.")
    print('Recuerda: si el servicio cambia de región, la BD debe estar en la misma o cercana.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

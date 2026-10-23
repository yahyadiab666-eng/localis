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

La clave NUNCA se escribe en ficheros: se lee de ``RENDER_API_KEY``.
"""

from __future__ import annotations

import argparse
import os
import sys

import requests

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
    args = parser.parse_args()

    cabeceras = _cabeceras()

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

    if not args.hacia:
        print('Falta --hacia (servicio destino).')
        return 2

    servicios = listar_servicios(cabeceras)
    origen = _resolver(servicios, args.desde)
    destino = _resolver(servicios, args.hacia)
    if not origen:
        print(f'No encontrado el servicio origen: {args.desde}')
        return 2
    if not destino:
        print(f'No encontrado el servicio destino: {args.hacia}')
        return 2

    print(f"Origen : {origen['name']} (region={origen['region']}, id={origen['id']})")
    print(f"Destino: {destino['name']} (region={destino['region']}, id={destino['id']})")

    variables = leer_env(cabeceras, origen['id'])
    print(f'\nVariables en el origen: {len(variables)}')
    for v in variables:
        valor = v['value'] or ''
        muestra = '(vacío)' if not valor else f'{valor[:4]}…{valor[-2:]} ({len(valor)} caracteres)'
        print(f"  {v['key']:<38} {muestra}")

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

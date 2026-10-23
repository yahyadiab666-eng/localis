"""Recrea un servicio de Render en otra región replicando su configuración.

Render **no permite cambiar la región** de un servicio existente: hay que crear
uno nuevo y mover el tráfico. Este script automatiza la parte delicada:

    --ver        muestra la configuración actual y el JSON que se enviaría
    --crear      crea el servicio nuevo (misma config, otra región y otro nombre)
    --comparar   compara el servicio nuevo con el viejo campo a campo
    --renombrar  renombra el nuevo al nombre del viejo (conserva la URL)
    --borrar     borra un servicio (pide confirmación por texto)

Lee ``RENDER_API_KEY`` del entorno o del ``.env`` local (ignorado por git).

Uso típico (migrar de oregon a virginia conservando la URL):

    python scripts/render_migrar_region.py --ver
    python scripts/render_migrar_region.py --crear --region virginia --nombre-nuevo localis-us
    python scripts/copiar_env_render.py --hacia localis-us --import <fichero> --apply
    python scripts/render_migrar_region.py --comparar --nombre-nuevo localis-us
    # una vez verificado:
    python scripts/render_migrar_region.py --borrar --servicio localis --si-borrar
    python scripts/render_migrar_region.py --renombrar --nombre-nuevo localis-us --nuevo-nombre localis

Nada destructivo ocurre sin un flag explícito.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import requests

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(RAIZ, '.env'), override=False)
except Exception:
    pass

API = 'https://api.render.com/v1'

# Campos del servicio que copiamos tal cual al crear el nuevo.
CAMPOS_CLAVE = ('type', 'repo', 'branch', 'autoDeploy', 'autoDeployTrigger', 'rootDir')
DETALLES_CLAVE = (
    'env',
    'plan',
    'healthCheckPath',
    'numInstances',
    'renderSubdomainPolicy',
    'pullRequestPreviewsEnabled',
)


def _cabeceras():
    clave = (os.environ.get('RENDER_API_KEY') or '').strip()
    if not clave:
        print('Falta RENDER_API_KEY (variable de entorno o .env).')
        sys.exit(2)
    return {
        'Authorization': f'Bearer {clave}',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
    }


def _get(url, cabeceras):
    respuesta = requests.get(url, headers=cabeceras, timeout=30)
    respuesta.raise_for_status()
    return respuesta.json()


def listar(cabeceras):
    filas = _get(f'{API}/services?limit=100', cabeceras) or []
    return [f.get('service') or f for f in filas]


def buscar(cabeceras, referencia):
    ref = str(referencia or '').strip().lower()
    for s in listar(cabeceras):
        if str(s.get('id', '')).lower() == ref or str(s.get('name', '')).lower() == ref:
            return s
    return None


def _config_actual(svc):
    """Config relevante del servicio (sin secretos; los env van aparte)."""
    detalles = svc.get('serviceDetails') or {}
    return {
        'name': svc.get('name'),
        'region': detalles.get('region'),
        'plan': detalles.get('plan'),
        'tipo': svc.get('type'),
        'repo': svc.get('repo'),
        'branch': svc.get('branch'),
        'autoDeploy': svc.get('autoDeploy'),
        'autoDeployTrigger': svc.get('autoDeployTrigger'),
        'rootDir': svc.get('rootDir'),
        'healthCheckPath': detalles.get('healthCheckPath'),
        'numInstances': detalles.get('numInstances'),
        'buildCommand': (detalles.get('envSpecificDetails') or {}).get('buildCommand'),
        'startCommand': (detalles.get('envSpecificDetails') or {}).get('startCommand'),
        'url': detalles.get('url'),
    }


def _payload_nuevo(svc, region, nombre, start_command=None, variables=None):
    """JSON para POST /services replicando el servicio dado.

    OJO: según la API de Render, ``region`` va **dentro de** ``serviceDetails``
    (a nivel superior se ignora y el servicio cae en la región por defecto,
    ``oregon``).
    """
    detalles = svc.get('serviceDetails') or {}
    especificos = detalles.get('envSpecificDetails') or {}
    cuerpo = {
        'type': svc.get('type') or 'web_service',
        'name': nombre,
        'ownerId': svc.get('ownerId'),
        'repo': svc.get('repo'),
        'branch': svc.get('branch'),
        'autoDeploy': svc.get('autoDeploy') or 'yes',
        'serviceDetails': {
            'env': detalles.get('env') or 'python',
            'runtime': detalles.get('runtime') or 'python',
            'region': region,
            'plan': detalles.get('plan'),
            'healthCheckPath': detalles.get('healthCheckPath') or '/health/live',
            'envSpecificDetails': {
                'buildCommand': especificos.get('buildCommand')
                or 'pip install -r requirements.txt',
                'startCommand': start_command or especificos.get('startCommand'),
            },
        },
    }
    if svc.get('rootDir'):
        cuerpo['rootDir'] = svc['rootDir']
    if variables:
        cuerpo['envVars'] = [
            {'key': v['key'], 'value': v.get('value', '')}
            for v in variables
            if v.get('key')
        ]
    return cuerpo


def _config_para_replicar(svc):
    """Subconjunto del servicio necesario para recrearlo (sin secretos).

    Se guarda en JSON para poder recrear el servicio **aunque ya no exista** el
    original (si te quedas sin origen, el script no puede leer su configuración).
    """
    detalles = svc.get('serviceDetails') or {}
    return {
        'type': svc.get('type') or 'web_service',
        'ownerId': svc.get('ownerId'),
        'repo': svc.get('repo'),
        'branch': svc.get('branch'),
        'autoDeploy': svc.get('autoDeploy') or 'yes',
        'rootDir': svc.get('rootDir') or '',
        'serviceDetails': {
            'env': detalles.get('env') or 'python',
            'runtime': detalles.get('runtime') or 'python',
            'plan': detalles.get('plan'),
            'healthCheckPath': detalles.get('healthCheckPath') or '/health/live',
            'envSpecificDetails': {
                'buildCommand': (detalles.get('envSpecificDetails') or {}).get('buildCommand')
                or 'pip install -r requirements.txt',
                'startCommand': (detalles.get('envSpecificDetails') or {}).get('startCommand')
                or 'gunicorn main:app',
            },
        },
    }


def _comparar(viejo, nuevo):
    a, b = _config_actual(viejo), _config_actual(nuevo)
    diferencias = []
    for clave in sorted(set(a) | set(b)):
        if clave in ('name', 'region', 'url'):
            continue  # cambian a propósito
        if a.get(clave) != b.get(clave):
            diferencias.append(f'  {clave}: viejo={a.get(clave)!r}  nuevo={b.get(clave)!r}')
    return a, b, diferencias


def main():
    parser = argparse.ArgumentParser(description='Migrar un servicio Render de región')
    parser.add_argument('--ver', action='store_true', help='mostrar config y JSON propuesto')
    parser.add_argument('--crear', action='store_true', help='crear el servicio nuevo')
    parser.add_argument('--comparar', action='store_true', help='comparar nuevo vs viejo')
    parser.add_argument('--renombrar', action='store_true', help='renombrar un servicio')
    parser.add_argument('--borrar', action='store_true', help='borrar un servicio')
    parser.add_argument('--servicio', default='localis', help='servicio origen (o el que se borra)')
    parser.add_argument('--nombre-nuevo', default='localis-us', help='nombre del servicio nuevo')
    parser.add_argument('--region', default='virginia', help='región destino (virginia, ohio, oregon...)')
    parser.add_argument('--nuevo-nombre', help='nombre final al renombrar')
    parser.add_argument('--start-command', help='sobrescribe el comando de arranque')
    parser.add_argument(
        '--env-json',
        metavar='RUTA',
        help='JSON con las variables (de copiar_env_render.py --export) para crearlas de una vez',
    )
    parser.add_argument(
        '--export-config',
        metavar='RUTA',
        help='guardar la configuración del origen para poder recrearlo luego (sin secretos)',
    )
    parser.add_argument(
        '--config-json',
        metavar='RUTA',
        help='configuración guardada con --export-config (permite crear sin origen vivo)',
    )
    parser.add_argument('--si-borrar', action='store_true', help='confirmación explícita para borrar')
    args = parser.parse_args()

    variables = None
    if args.env_json:
        with open(args.env_json, encoding='utf-8') as fichero:
            variables = json.load(fichero)

    cabeceras = _cabeceras()

    if args.borrar:
        svc = buscar(cabeceras, args.servicio)
        if not svc:
            print(f'No encontrado: {args.servicio}')
            return 2
        print(f"Se va a BORRAR: {svc['name']} ({svc['id']})  url={(svc.get('serviceDetails') or {}).get('url')}")
        if not args.si_borrar:
            print('Falta --si-borrar para confirmar.')
            return 2
        r = requests.delete(f"{API}/services/{svc['id']}", headers=cabeceras, timeout=60)
        print('DELETE ->', r.status_code)
        return 0 if r.status_code in (200, 202, 204) else 1

    if args.renombrar:
        svc = buscar(cabeceras, args.nombre_nuevo)
        if not svc:
            print(f'No encontrado: {args.nombre_nuevo}')
            return 2
        if not args.nuevo_nombre:
            print('Falta --nuevo-nombre.')
            return 2
        r = requests.patch(
            f"{API}/services/{svc['id']}",
            headers=cabeceras,
            json={'name': args.nuevo_nombre},
            timeout=60,
        )
        print('PATCH ->', r.status_code)
        if r.status_code == 200:
            print('Nuevo nombre:', r.json().get('name'))
        else:
            print(r.text[:400])
        return 0 if r.status_code == 200 else 1

    viejo = buscar(cabeceras, args.servicio)
    if not viejo and not args.config_json:
        print(f'No encontrado el servicio origen: {args.servicio}')
        print(
            'Si ya lo borraste, pasa --config-json con la configuración que guardaste '
            'antes con --export-config.'
        )
        return 2

    if args.export_config:
        if not viejo:
            print('Para --export-config hace falta el servicio origen vivo.')
            return 2
        with open(args.export_config, 'w', encoding='utf-8') as fichero:
            json.dump(_config_para_replicar(viejo), fichero, ensure_ascii=False, indent=2)
        print(f'Configuración guardada en {args.export_config} (sin secretos).')
        print('Guárdala junto al JSON de variables: permite recrear sin el origen vivo.')
        return 0

    if args.ver:
        print('=== Configuración ACTUAL ===')
        print(json.dumps(_config_actual(viejo), indent=2, ensure_ascii=False))
        print(f'\n=== JSON que se enviaría para crear "{args.nombre_nuevo}" en {args.region} ===')
        print(
            json.dumps(
                _payload_nuevo(viejo, args.region, args.nombre_nuevo, args.start_command),
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    if args.comparar:
        if not viejo:
            print('Para --comparar hace falta el servicio origen vivo.')
            return 2
        nuevo = buscar(cabeceras, args.nombre_nuevo)
        if not nuevo:
            print(f'Aún no existe: {args.nombre_nuevo}')
            return 2
        a, b, diferencias = _comparar(viejo, nuevo)
        print('Viejo :', json.dumps(a, ensure_ascii=False))
        print('Nuevo :', json.dumps(b, ensure_ascii=False))
        if diferencias:
            print('\nDIFERENCIAS (fuera de nombre/region/url):')
            print('\n'.join(diferencias))
            return 1
        print('\nSin diferencias relevantes: configuración replicada.')
        return 0

    if args.crear:
        existente = buscar(cabeceras, args.nombre_nuevo)
        if existente:
            print(f'Ya existe "{args.nombre_nuevo}" ({existente["id"]}); no se crea nada.')
            return 0
        base = viejo
        if base is None:
            with open(args.config_json, encoding='utf-8') as fichero:
                base = json.load(fichero)
            print(f'Usando la configuración guardada en {args.config_json}.')
        cuerpo = _payload_nuevo(
            base, args.region, args.nombre_nuevo, args.start_command, variables
        )
        # Nunca imprimir los valores de las variables: solo el conteo.
        visible = {clave: valor for clave, valor in cuerpo.items() if clave != 'envVars'}
        if variables:
            visible['envVars'] = f'[{len(variables)} variables, valores ocultos]'
        print('Creando con:')
        print(json.dumps(visible, indent=2, ensure_ascii=False))
        r = requests.post(f'{API}/services', headers=cabeceras, json=cuerpo, timeout=60)
        print('\nPOST ->', r.status_code)
        if r.status_code in (200, 201):
            datos = (r.json() or {}).get('service') or r.json()
            print('Creado:', datos.get('id'), datos.get('name'))
            print('URL:', ((datos.get('serviceDetails') or {}).get('url')))
            print('\nSiguiente paso: copiar las variables de entorno.')
            return 0
        print(r.text[:800])
        return 1

    parser.print_help()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

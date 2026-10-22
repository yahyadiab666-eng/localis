"""Ubicaciones de comercio (sedes con nombre) — verificación SIN red ni BD.

Comprueba ``backend.utils.derivar_datos_maps`` y ``backend.ubicaciones``:
la derivación de datos desde enlaces de Google Maps y la validación de la
lista de sedes que alimenta el repeater de los formularios.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend import ubicaciones  # noqa: E402
from backend.utils import derivar_datos_maps  # noqa: E402

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def _probar_derivar():
    print('=== derivar_datos_maps: coordenadas en la ruta (@lat,lng) ===')
    d = derivar_datos_maps('https://www.google.com/maps/@10.6543,-63.1234,15z')
    _ok(d['latitud'] == 10.6543 and d['longitud'] == -63.1234, f"coords {d['coordinates_maps']}")
    _ok(d['coordinates_maps'] == '10.6543,-63.1234', 'coordinates_maps formateado')
    _ok(d['valido'] is True, 'enlace válido')

    print('\n=== derivar_datos_maps: nombre del lugar en la ruta ===')
    d = derivar_datos_maps(
        'https://www.google.com/maps/place/Panader%C3%ADa+Central/@10.5,-63.2,17z'
    )
    _ok(d['nombre'] == 'Panadería Central', f"nombre {d['nombre']!r}")
    _ok(d['direccion'] == 'Panadería Central', 'dirección derivada del nombre')
    _ok(d['coordinates_maps'] == '10.5,-63.2', f"coords {d['coordinates_maps']}")

    print('\n=== derivar_datos_maps: coordenadas en el query (?q=lat,lng) ===')
    d = derivar_datos_maps('https://maps.google.com/?q=10.6543,-63.1234')
    _ok(d['coordinates_maps'] == '10.6543,-63.1234', f"coords {d['coordinates_maps']}")
    _ok(d['nombre'] is None, 'no confunde coordenadas con nombre')

    print('\n=== derivar_datos_maps: texto de búsqueda en el query ===')
    d = derivar_datos_maps('https://maps.google.com/?q=Porlamar,+Nueva+Esparta')
    _ok(d['nombre'] == 'Porlamar, Nueva Esparta', f"nombre {d['nombre']!r}")
    _ok(d['coordinates_maps'] is None, 'sin coordenadas')
    _ok(d['valido'] is True, 'enlace válido por texto')

    print('\n=== derivar_datos_maps: coordenadas !3d..!4d.. ===')
    d = derivar_datos_maps(
        'https://www.google.com/maps/place/Sede/data=!3d10.55!4d-63.25'
    )
    _ok(d['coordinates_maps'] == '10.55,-63.25', f"coords {d['coordinates_maps']}")
    _ok(d['nombre'] == 'Sede', f"nombre {d['nombre']!r}")

    print('\n=== derivar_datos_maps: enlace corto y entradas inválidas ===')
    d = derivar_datos_maps('https://maps.app.goo.gl/AbCdEf123')
    _ok(d['valido'] is True, 'enlace corto aceptado como válido')
    _ok(d['coordinates_maps'] is None and d['nombre'] is None, 'sin datos derivables')

    for vacio in ('', None, '   '):
        d = derivar_datos_maps(vacio)
        _ok(d['valido'] is False, f'vacío {vacio!r} -> inválido')

    d = derivar_datos_maps('https://example.com/lo-que-sea')
    _ok(d['valido'] is False, 'dominio ajeno -> inválido')

    print('\n=== derivar_datos_maps: nunca lanza ===')
    crash = False
    for raro in (123, [], {}, 'http://[', 'https://google.com/maps/@abc,def,15z'):
        try:
            derivar_datos_maps(raro)
        except Exception as error:
            crash = True
            print(f'    lanzó con {raro!r}: {error}')
    _ok(not crash, 'entradas raras -> no lanza')


def _probar_normalizar():
    print('\n=== normalizar_ubicaciones: lista válida ===')
    ok, lista = ubicaciones.normalizar_ubicaciones([
        {'nombre': 'Sede Centro', 'maps_url': 'https://maps.google.com/?q=10.5,-63.2'},
        {'nombre': 'Depósito', 'maps_url': 'https://www.google.com/maps/@10.6,-63.3,17z'},
    ])
    _ok(ok, 'lista aceptada')
    _ok(len(lista) == 2, f'dos sedes ({len(lista)})')
    _ok(lista[0]['es_principal'] == 1 and lista[0]['orden'] == 0, 'la primera es la principal')
    _ok(lista[1]['es_principal'] == 0 and lista[1]['orden'] == 1, 'la segunda queda ordenada')
    _ok(lista[0]['direccion'] == 'Sede Centro', 'dirección derivada del nombre si no hay texto')
    _ok(lista[1]['coordinates_maps'] == '10.6,-63.3', 'coordenadas derivadas')

    print('\n=== normalizar_ubicaciones: filas vacías se ignoran ===')
    ok, lista = ubicaciones.normalizar_ubicaciones([
        {'nombre': '', 'maps_url': ''},
        {'nombre': 'Única', 'maps_url': 'https://maps.google.com/?q=10.5,-63.2'},
        {'nombre': '   ', 'maps_url': '  '},
    ])
    _ok(ok and len(lista) == 1, f'se conserva solo la válida ({len(lista) if ok else "err"})')

    print('\n=== normalizar_ubicaciones: errores de validación ===')
    ok, msg = ubicaciones.normalizar_ubicaciones([
        {'nombre': '', 'maps_url': 'https://maps.google.com/?q=10.5,-63.2'},
    ])
    _ok(not ok and 'nombre' in msg.lower(), f'sin nombre -> {msg}')

    ok, msg = ubicaciones.normalizar_ubicaciones([
        {'nombre': 'Sede', 'maps_url': ''},
    ])
    _ok(not ok and 'google maps' in msg.lower(), f'sin enlace -> {msg}')

    ok, msg = ubicaciones.normalizar_ubicaciones([
        {'nombre': 'Sede', 'maps_url': 'https://example.com/no-es-maps'},
    ])
    _ok(not ok and 'no parece' in msg.lower(), f'enlace inválido -> {msg}')

    ok, msg = ubicaciones.normalizar_ubicaciones([
        {'nombre': f'Sede {i}', 'maps_url': 'https://maps.google.com/?q=10.5,-63.2'}
        for i in range(11)
    ])
    _ok(not ok and '10' in msg, f'más de 10 -> {msg}')

    print('\n=== normalizar_ubicaciones: lista vacía (se permite a nivel servicio) ===')
    ok, lista = ubicaciones.normalizar_ubicaciones([])
    _ok(ok and lista == [], 'vacío -> ok con lista vacía')


def _probar_principal():
    print('\n=== datos_ubicacion_principal ===')
    ok, lista = ubicaciones.normalizar_ubicaciones([
        {'nombre': 'Centro', 'maps_url': 'https://maps.google.com/?q=10.5,-63.2'},
        {'nombre': 'Anexo', 'maps_url': 'https://www.google.com/maps/@10.6,-63.3,17z'},
    ])
    datos = ubicaciones.datos_ubicacion_principal(lista)
    _ok(datos['direccion'] == 'Centro', f"direccion {datos['direccion']!r}")
    _ok(datos['maps_url'] == 'https://maps.google.com/?q=10.5,-63.2', 'maps_url de la principal')
    _ok(datos['ubicacion_maps_url'] == datos['maps_url'], 'alias ubicacion_maps_url')

    vacio = ubicaciones.datos_ubicacion_principal([])
    _ok(
        all(v is None for v in vacio.values()),
        'sin sedes -> todas las columnas en None',
    )


def main() -> int:
    _probar_derivar()
    _probar_normalizar()
    _probar_principal()

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK ubicaciones: derivación de Maps y validación de sedes')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

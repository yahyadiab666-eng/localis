"""Relleno automático de ciudad/zona desde Google Maps — SIN red.

Prueba la parte pura y determinista:
  * :func:`backend.utils.derivar_datos_maps` sobre la URL a la que Google resuelve
    un enlace corto (``/maps/search/11.08,+-63.97``), que antes no se detectaba.
  * :func:`backend.geo_ubicacion.datos_desde_respuesta_nominatim` con una
    respuesta REAL de Nominatim (fixture) y con entradas incompletas.
  * El interruptor ``LOCALIS_GEOCODIFICACION=0`` deja las sedes intactas.

La llamada real a la red es opcional: define ``LOCALIS_GEOCODIFICACION_TEST=1``
para verificarla (se omite en CI para no depender de terceros).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend import geo_ubicacion  # noqa: E402
from backend.utils import derivar_datos_maps  # noqa: E402

FALLOS = []

# Respuesta REAL de Nominatim (recortada) para 11.080405,-63.969985 (Juan Griego).
RESPUESTA_NOMINATIM = {
    'place_id': 297212461,
    'lat': '11.0804355',
    'lon': '-63.9700775',
    'name': 'Avenida Jesús Rafael Leandro',
    'display_name': (
        'Avenida Jesús Rafael Leandro, Casco Central, Urbanización Francisco Adrián, '
        'Valparaíso, Juan Griego, Parroquia Juan Griego, Municipio Marcano, '
        'Estado Nueva Esparta, 6309, Venezuela'
    ),
    'address': {
        'road': 'Avenida Jesús Rafael Leandro',
        'neighbourhood': 'Casco Central',
        'quarter': 'Urbanización Francisco Adrián',
        'suburb': 'Valparaíso',
        'city': 'Juan Griego',
        'municipality': 'Parroquia Juan Griego',
        'county': 'Municipio Marcano',
        'state': 'Estado Nueva Esparta',
        'postcode': '6309',
        'country': 'Venezuela',
        'country_code': 've',
    },
}


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def _probar_coordenadas_de_enlace_resuelto():
    print('=== Coordenadas del enlace corto resuelto (/maps/search/lat,lng) ===')
    d = derivar_datos_maps(
        'https://www.google.com/maps/search/11.080405,+-63.969985'
        '?entry=tts&g_ep=xyz'
    )
    _ok(
        d['coordinates_maps'] == '11.080405,-63.969985',
        f"coordenadas detectadas ({d['coordinates_maps']})",
    )
    _ok(d['valido'] is True, 'enlace válido')

    d2 = derivar_datos_maps('https://www.google.com/maps/search/11.08,%20-63.97,15z')
    _ok(d2['coordinates_maps'] == '11.08,-63.97', f"con espacio ({d2['coordinates_maps']})")


def _probar_parser_nominatim():
    print('\n=== Interpretación de la respuesta de Nominatim (pura) ===')
    datos = geo_ubicacion.datos_desde_respuesta_nominatim(RESPUESTA_NOMINATIM)
    _ok(datos['ciudad'] == 'Juan Griego', f"ciudad {datos['ciudad']!r}")
    _ok(datos['zona'] == 'Casco Central', f"zona {datos['zona']!r}")
    _ok(datos['estado'] == 'Nueva Esparta', f"estado sin prefijo {datos['estado']!r}")
    _ok(datos['pais'] == 'Venezuela', f"país {datos['pais']!r}")
    _ok(
        datos['direccion'] == 'Avenida Jesús Rafael Leandro, Casco Central, Juan Griego, Nueva Esparta',
        f"dirección compuesta {datos['direccion']!r}",
    )

    print('\n=== Tolerancia: respuestas incompletas (nunca lanza) ===')
    _ok(geo_ubicacion.datos_desde_respuesta_nominatim(None) == {}, 'None -> {}')
    _ok(geo_ubicacion.datos_desde_respuesta_nominatim([]) == {}, 'lista -> {}')
    vacio = geo_ubicacion.datos_desde_respuesta_nominatim({'address': {}})
    _ok(
        vacio['ciudad'] is None and vacio['zona'] is None,
        'sin address -> ciudad/zona None',
    )

    print('\n=== Zona cae al estado si no hay barrio ===')
    sin_barrio = {
        'address': {'city': 'Porlamar', 'state': 'Estado Nueva Esparta', 'country': 'Venezuela'},
    }
    datos = geo_ubicacion.datos_desde_respuesta_nominatim(sin_barrio)
    _ok(datos['zona'] == 'Nueva Esparta', f"zona=estado ({datos['zona']!r})")
    _ok(datos['ciudad'] == 'Porlamar', f"ciudad {datos['ciudad']!r}")


def _probar_interruptor_apagado():
    print('\n=== Con LOCALIS_GEOCODIFICACION=0 las sedes no se tocan ===')
    anterior = os.environ.get('LOCALIS_GEOCODIFICACION')
    os.environ['LOCALIS_GEOCODIFICACION'] = '0'
    try:
        entrada = [{
            'nombre': 'Sede',
            'maps_url': 'https://www.google.com/maps/search/11.08,-63.97',
            'direccion': 'Sede',
            'ciudad': None,
            'zona': None,
        }]
        salida = geo_ubicacion.enriquecer_ubicaciones_geo(entrada)
        _ok(salida[0]['ciudad'] is None, 'ciudad sigue en None')
        _ok(salida[0]['zona'] is None, 'zona sigue en None')
        _ok(salida is not entrada, 'devuelve una lista nueva (no muta la entrada)')
    finally:
        if anterior is None:
            os.environ.pop('LOCALIS_GEOCODIFICACION', None)
        else:
            os.environ['LOCALIS_GEOCODIFICACION'] = anterior


def _probar_live():
    print('\n=== Geocodificación REAL (red, opcional) ===')
    if str(os.getenv('LOCALIS_GEOCODIFICACION_TEST', '')).strip() != '1':
        print('  SKIP  define LOCALIS_GEOCODIFICACION_TEST=1 para probar la red')
        return
    datos = geo_ubicacion.geocodificar_coordenadas(11.080405, -63.969985)
    _ok(bool(datos), 'la red respondió')
    _ok(
        bool(datos) and 'juan griego' in (datos.get('ciudad') or '').lower(),
        f"ciudad real ({(datos or {}).get('ciudad')!r})",
    )


def main() -> int:
    _probar_coordenadas_de_enlace_resuelto()
    _probar_parser_nominatim()
    _probar_interruptor_apagado()
    _probar_live()

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK geocodificación: coordenadas del enlace y ciudad/zona/dirección')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

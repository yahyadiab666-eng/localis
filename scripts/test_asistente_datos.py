#!/usr/bin/env python3
"""Datos en vivo del asistente: funciones puras y saltos seguros. Sin red ni BD."""

from __future__ import annotations

import sys
from pathlib import Path

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


def _probar_keywords():
    print('\n=== Palabras con contenido ===')
    from backend.asistente.datos_vivos import _keywords

    _ok(
        _keywords('¿Cuanto cuesta el Arroz Mary 1kg?') == ['arroz', 'mary', '1kg'],
        'quita stopwords (cuanto, cuesta, el) y acentos',
    )
    _ok(_keywords('') == [], 'vacio -> lista vacia')
    _ok(_keywords(None) == [], 'None -> lista vacia')


def _probar_menciones():
    print('\n=== Deteccion de tema ===')
    from backend.asistente.datos_vivos import (
        menciona_categoria,
        menciona_comercio,
        menciona_imagenes,
        menciona_importacion,
        menciona_pago,
        menciona_tienda,
    )

    _ok(menciona_comercio('Cuantos productos permite mi plan?'), 'plan -> comercio')
    _ok(not menciona_comercio('cuanto cuesta el arroz'), 'arroz -> NO comercio')
    _ok(menciona_imagenes('por que mi producto no tiene foto'), 'foto -> imagenes')
    _ok(not menciona_imagenes('cuanto cuesta el arroz'), 'arroz -> NO imagenes')
    _ok(menciona_importacion('mi importacion quedo pendiente'), 'importacion')
    _ok(menciona_pago('necesito revisar mi pago movil'), 'pago')
    _ok(menciona_tienda('donde queda la tienda?'), 'tienda')
    _ok(menciona_categoria('que categorias hay?'), 'categoria')


def _probar_tiendas():
    print('\n=== Formato de tiendas ===')
    from backend.asistente.datos_vivos import formatear_tiendas

    texto = formatear_tiendas(
        [
            {'nombre': 'Abastos El Sol', 'ciudad': 'Porlamar', 'zona': 'Centro',
             'telefono': '04121234567'},
            {'nombre': '', 'ciudad': 'X'},
        ]
    )
    _ok('Abastos El Sol - Centro, Porlamar | WhatsApp 04121234567' in texto, 'tienda con ubicacion y contacto')
    _ok(texto.count('\n') == 0, 'ignora tiendas sin nombre')
    _ok(formatear_tiendas([]) == '', 'lista vacia -> cadena vacia')


def _probar_presupuesto():
    print('\n=== Presupuesto diario (desactivado por defecto) ===')
    from backend.asistente import presupuesto

    _ok(presupuesto.disponible() is True, 'desactivado -> siempre disponible')
    presupuesto.registrar(1000)
    _ok(presupuesto.estado()['limite'] == 0, 'limite 0 (desactivado)')


def _probar_formato():
    print('\n=== Formato de precios ===')
    from backend.asistente.datos_vivos import formatear_productos

    texto = formatear_productos(
        [
            {'nombre': 'Arroz Mary 1kg', 'precio_usd': 1.2, 'precio_bs': 66.0,
             'comercio_nombre': 'Abastos El Sol'},
            {'nombre': 'Sin precio', 'precio_usd': 0, 'precio_bs': 0,
             'comercio_nombre': ''},
            {'nombre': '', 'precio_usd': 5, 'comercio_nombre': 'X'},
        ]
    )
    _ok('Arroz Mary 1kg: 1.20 USD (Bs 66.00) - en Abastos El Sol' in texto, 'linea con USD y Bs')
    _ok('Sin precio: 0.00 USD' in texto, 'precio 0 no muestra Bs')
    _ok(texto.count('\n') == 1, 'ignora productos sin nombre')
    _ok(formatear_productos([]) == '', 'lista vacia -> cadena vacia')


def _probar_saltos_seguros():
    print('\n=== No busca catalogo en temas de documentacion ===')
    from backend.asistente.datos_vivos import HECHOS_CLAVE, buscar_precios

    _ok(buscar_precios('cuanto cuesta el plan Pro?') == '', 'plan -> no busca catalogo')
    _ok(buscar_precios('   ') == '', 'vacio -> cadena vacia')
    _ok(buscar_precios('a b c') == '', 'sin palabras utiles -> cadena vacia')
    _ok('10 USD' in HECHOS_CLAVE and '15 USD' in HECHOS_CLAVE, 'hechos clave con precios')
    _ok('35 USD' in HECHOS_CLAVE and 'ilimitados' in HECHOS_CLAVE, 'hechos clave con Business')


def main() -> int:
    _probar_keywords()
    _probar_menciones()
    _probar_formato()
    _probar_tiendas()
    _probar_saltos_seguros()
    _probar_presupuesto()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK datos en vivo del asistente')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

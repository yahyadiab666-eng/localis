#!/usr/bin/env python3
"""Regresión del clasificador universal de categorías de producto.

Cubre el fallo detectado en producción: la coincidencia por subcadena y el
desempate por orden fijo asignaban categorías incorrectas
(``'salchichas'`` → bebidas por ``'chicha'``, ``'carnet'`` → alimentos por
``'carne'``). Se verifica que:

- El comportamiento correcto se mantiene (casos base).
- Los falsos positivos por subcadena ya no ocurren.
- Ante ambigüedad o falta de pistas se cae a ``otros`` (NUNCA se adivina).
- El resultado siempre pertenece al enum estricto ``CLAVES_CATEGORIA``.
- ``clasificar_categoria`` nunca lanza.
"""

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


def _probar_casos_base():
    print('\n=== Casos base (no deben romperse) ===')
    from backend.categorias_producto import clasificar_categoria

    casos = {
        'Refresco Pepsi 1.5L': 'bebidas',
        'Celular Samsung Galaxy A15': 'tecnologia',
        'Taladro percutor 650W': 'ferreteria',
        'Harina de maíz P.A.N. 1kg': 'alimentos',
        'Shampoo anticaspa 375ml': 'belleza',
        'Camisa manga larga': 'ropa',
        'Cuaderno universitario 100 hojas': 'papeleria',
        'Pañal desechable talla M': 'bebes',
        'Llanta radial 195/65': 'automotriz',
        'Balón de fútbol No. 5': 'deportes',
        'Alimento para perro 2kg': 'mascotas',
        'Rompecabezas infantil 100 piezas': 'juguetes',
        'Tabletas de ibuprofeno 400mg': 'salud',
        'Detergente en polvo 1kg': 'hogar',
        'Producto raro sin pistas': 'otros',
    }
    for nombre, esperado in casos.items():
        obtenido = clasificar_categoria(nombre=nombre)
        _ok(obtenido == esperado, f'{nombre!r} -> {obtenido} (esperado {esperado})')


def _probar_falsos_positivos():
    print('\n=== Falsos positivos por subcadena (regresión) ===')
    from backend.categorias_producto import clasificar_categoria

    casos = {
        'Salchichas': 'alimentos',          # antes: bebidas ('chicha')
        'Carnet de identidad': 'otros',     # antes: alimentos ('carne')
        'Goma de mascar Trident': 'alimentos',  # antes: papeleria ('goma')
        'Pasta de dientes': 'belleza',      # antes: alimentos ('pasta')
        'Filtro de agua': 'hogar',          # antes: automotriz (orden fijo)
        'Agua oxigenada 500ml': 'salud',    # antes: bebidas ('agua')
        'Batería de carro': 'automotriz',   # antes: tecnologia ('bateria')
        'Ratón inalámbrico': 'tecnologia',  # antes: otros (faltaba término)
        'Jabón de baño': 'belleza',
        'Jabón de lavar': 'hogar',
        'Motocicleta': 'automotriz',
        'Computadora portátil': 'tecnologia',
        'Sombra para ventana': 'otros',     # 'sombra' de maquillaje no aplica
    }
    for nombre, esperado in casos.items():
        obtenido = clasificar_categoria(nombre=nombre)
        _ok(obtenido == esperado, f'{nombre!r} -> {obtenido} (esperado {esperado})')


def _probar_ambiguedad():
    print('\n=== Sin certeza -> categoría por defecto (nunca adivinar) ===')
    from backend.categorias_producto import (
        CATEGORIA_POR_DEFECTO,
        _categoria_desde_texto,
        clasificar_categoria,
    )

    _ok(CATEGORIA_POR_DEFECTO == 'otros', 'la categoría por defecto es "otros"')
    _ok(
        _categoria_desde_texto('xyz qwerty') == ('otros', 0),
        'texto sin pistas -> (otros, 0)',
    )
    # Empate exacto entre dos categorías -> no se arriesga ninguna.
    _ok(
        _categoria_desde_texto('llanta bicicleta') == ('otros', 0),
        'empate automotriz/deportes -> otros',
    )
    _ok(
        clasificar_categoria(nombre='') == 'otros'
        and clasificar_categoria() == 'otros',
        'entrada vacía -> otros',
    )


def _probar_enum_estricto():
    print('\n=== El resultado siempre es una clave válida (enum estricto) ===')
    from backend.categorias_producto import (
        CLAVES_CATEGORIA,
        clasificar_categoria,
        imagen_para_categoria,
    )

    entradas = [
        None, '', '   ', 123, 'producto', 'ALIMENTOS', 'Cuidado Personal',
        'Tecnología', 'Víveres', 'xyz', 'Salchichas',
    ]
    _ok(
        all(clasificar_categoria(nombre=e) in CLAVES_CATEGORIA for e in entradas),
        'clasificar_categoria devuelve claves del enum',
    )
    _ok(
        all(
            imagen_para_categoria(e).startswith('/static/img/placeholder-')
            for e in entradas
        ),
        'imagen_para_categoria siempre devuelve un placeholder',
    )


def _probar_hints():
    print('\n=== Categoría declarada en el archivo (hint) ===')
    from backend.categorias_producto import clasificar_categoria

    casos = {
        'Bebidas': 'bebidas',
        'Tecnología': 'tecnologia',
        'Ferretería': 'ferreteria',
        'Alimentos': 'alimentos',
        'Cuidado Personal': 'belleza',
        'Ropa': 'ropa',
        'Papelería': 'papeleria',
        'Bebés': 'bebes',
        'Automotriz': 'automotriz',
        'Deportes': 'deportes',
        'Víveres': 'alimentos',
    }
    for hint, esperado in casos.items():
        obtenido = clasificar_categoria(categoria_hint=hint)
        _ok(obtenido == esperado, f'hint {hint!r} -> {obtenido} (esperado {esperado})')


def _probar_nunca_lanza():
    print('\n=== Robusteza: nunca lanza ===')
    from backend.categorias_producto import clasificar_categoria

    class Veneno:
        def __str__(self):
            raise RuntimeError('boom')

    try:
        resultado = clasificar_categoria(nombre=Veneno())
        _ok(resultado == 'otros', 'un valor que revienta al convertir -> otros')
    except Exception as error:  # pragma: no cover
        _ok(False, f'clasificar_categoria lanzó: {type(error).__name__}: {error}')


def main() -> int:
    _probar_casos_base()
    _probar_falsos_positivos()
    _probar_ambiguedad()
    _probar_enum_estricto()
    _probar_hints()
    _probar_nunca_lanza()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK clasificador de categorías: sin falsos positivos, fallback seguro')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Riesgos residuales de imágenes: históricos, modelos numéricos y pegados.

Invariantes:
  1. `fuente_verificada` separa fuentes fuertes de las débiles/históricas, y
     `puede_reemplazar` permite reevaluar un 'real' de fuente débil.
  2. El índice maestro y las consultas conservan el NÚMERO de modelo
     ("Redmi Note 12") sin colapsar ni confundirlo con capacidad (128 GB).
  3. El matching de modelo tolera variantes pegadas/separadas ("note12" ~ "note 12").

Sin BD ni red.
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


def _probar_fuente_verificada():
    print('\n=== Fuente verificada y reevaluación de reales ===')
    from backend.activos_verificados import fuente_verificada
    from backend.motor_imagenes import puede_reemplazar

    _ok(not fuente_verificada('serper'), "fuente histórica 'serper' = DÉBIL")
    _ok(not fuente_verificada('serper_sin_verificar'), 'sin_verificar = DÉBIL')
    _ok(not fuente_verificada(None), 'sin fuente = DÉBIL')
    _ok(fuente_verificada('serper_verificado'), 'serper_verificado = FUERTE')
    _ok(fuente_verificada('catalogo_maestro'), 'catalogo_maestro (EAN) = FUERTE')
    _ok(fuente_verificada('profesional_bing-web_supabase'), 'profesional_* = FUERTE')
    _ok(fuente_verificada('comercio'), 'comercio (subida) = FUERTE')

    _ok(
        puede_reemplazar('https://x/prod.webp', 'real', 'serper'),
        "un 'real' de fuente débil es reevaluable",
    )
    _ok(
        not puede_reemplazar('https://x/prod.webp', 'real', 'profesional_bing-web_supabase'),
        "un 'real' de fuente fuerte se protege",
    )
    _ok(
        not puede_reemplazar('/static/uploads/productos/manual_1_a.webp', 'real', 'manual'),
        'una subida manual nunca se reemplaza',
    )


def _probar_indice_numerico():
    print('\n=== Índice maestro conserva el número de modelo ===')
    from backend.catalogo_maestro_index import normalizar_clave_producto

    k12 = normalizar_clave_producto('Redmi Note 12')
    k13 = normalizar_clave_producto('Redmi Note 13')
    _ok('12' in k12.split(), f'Note 12 conserva el 12 ({k12})')
    _ok(k12 != k13, f'Note 12 y Note 13 NO colapsan ({k12} != {k13})')
    ka15 = normalizar_clave_producto('Samsung Galaxy A15 128 GB')
    _ok('a15' in ka15.split() and '128' not in ka15.split(), f'capacidad excluida ({ka15})')


def _probar_consultas_numericas():
    print('\n=== Consultas conservan el número de modelo ===')
    from services.professional_image_pipeline import _tokens_consulta

    c = _tokens_consulta('Redmi Note 12', None)
    _ok('12' in c, f'"Redmi Note 12" conserva el 12 ({c})')
    c2 = _tokens_consulta('Samsung Galaxy A15 128 GB', None)
    _ok('a15' in c2 and '128' not in c2 and 'gb' not in c2, f'capacidad excluida ({c2})')


def _probar_modelo_pegado():
    print('\n=== Tolerancia a nombres pegados/separados ===')
    from backend.imagenes_producto import (
        _candidato_ean_valido,
        _modelo_presente,
        _tokens_identidad,
    )

    _ok(_modelo_presente('note12', 'redmi note 12 128gb'), 'note12 ~ "note 12"')
    _ok(_modelo_presente('note12', 'note12'), 'note12 exacto')
    _ok(not _modelo_presente('note12', 'redmi note 13'), 'note12 no casa con note 13')

    tokens = _tokens_identidad('Samsung Note12')
    candidato = {
        'url': 'https://tienda.com/p/samsung-note-12',
        'dominio': 'tienda.com',
        'titulo': 'Samsung Note 12 128GB',
        'contexto': '',
    }
    _ok(
        _candidato_ean_valido(candidato, '7591234567890', tokens),
        'un modelo pegado casa con el separado (sin falso negativo)',
    )


def main() -> int:
    _probar_fuente_verificada()
    _probar_indice_numerico()
    _probar_consultas_numericas()
    _probar_modelo_pegado()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK residuales: históricos reevaluables, modelos numéricos conservados y matching tolerante')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Base de conocimiento del asistente: indice y recuperacion lexica (BM25).

Sin red ni BD. Comprueba que el indice se construye, que las preguntas tipicas
encuentran documentacion relevante y que las consultas raras no lanzan.
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


def _probar_indice():
    print('\n=== Indice de conocimiento ===')
    from backend.asistente.conocimiento import obtener_indice

    indice = obtener_indice()
    _ok(indice['n'] > 0, f'el indice tiene fragmentos ({indice["n"]})')
    _ok(indice['avgdl'] > 0, 'la longitud media de documento es valida')
    fuentes = {doc['fuente'] for doc in indice['docs']}
    _ok(
        'docs/asistente-faq.md' in fuentes,
        'la FAQ curada esta indexada',
    )


def _probar_busqueda():
    print('\n=== Recuperacion ===')
    from backend.asistente.conocimiento import buscar, contexto_para

    resultados = buscar('como importo mi inventario CSV Excel')
    _ok(len(resultados) > 0, 'encuentra resultados para importar inventario')
    _ok(
        any('import' in (r['texto'] or '').lower() for r in resultados),
        'alguno de los fragmentos menciona importar',
    )

    foto = buscar('por que mi producto no tiene foto imagen')
    _ok(len(foto) > 0, 'encuentra resultados sobre imagenes')
    _ok(
        any('foto' in (r['texto'] or '').lower() or 'imagen' in (r['texto'] or '').lower()
            for r in foto),
        'alguno de los fragmentos habla de foto o imagen',
    )

    _ok(buscar('zzzxxx qqqwwweee vvvbbb') == [], 'sin coincidencias -> lista vacia')
    _ok(buscar(None) == [], 'None no lanza y devuelve lista vacia')
    _ok(buscar('') == [], 'cadena vacia -> lista vacia')
    _ok(buscar(123) == [], 'entrada no textual no lanza')

    contexto = contexto_para('foto de producto', max_chars=2000)
    _ok(isinstance(contexto, str) and len(contexto) > 0, 'el contexto RAG no esta vacio')
    _ok('---' in contexto, 'el contexto separa los fragmentos')


def main() -> int:
    _probar_indice()
    _probar_busqueda()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK base de conocimiento del asistente')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

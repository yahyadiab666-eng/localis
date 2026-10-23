#!/usr/bin/env python3
"""Guardarrailes del asistente: saneado de entrada y redaccion de secretos.

Sin red ni BD. Verifica que los secretos evidentes se tachan, que la entrada se
acota y que el interruptor de habilitacion respeta las variables de entorno.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

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


def _probar_redaccion():
    print('\n=== Redaccion de secretos ===')
    from backend.asistente.seguridad import redactar

    casos = [
        'DATABASE_URL=postgresql://user:pass@host:5432/db',
        'mi clave es sk-or-v1-abcdefghijklmnop123456',
        'usa sk-ABCDEFGHIJKLMNOPQRSTUV para la api',
        'SUPABASE_SERVICE_ROLE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'
        '.eyJyb2xlIjoic2VydmljZV9yb2xlIn0.abcdef123456',
    ]
    for texto in casos:
        salida = redactar(texto)
        _ok('[dato oculto]' in salida, f'oculta secreto en {texto[:24]!r}...')

    normal = 'Hola, quiero saber como importar mi inventario en CSV.'
    _ok(redactar(normal) == normal, 'el texto normal no se altera')
    _ok(redactar(None) == '', 'None devuelve cadena vacia')
    _ok('abcdefghijklmnop' not in redactar(casos[1]), 'no filtra la clave sk-or')


def _probar_entrada():
    print('\n=== Saneado de entrada ===')
    from backend.asistente.seguridad import sanear_entrada

    _ok(sanear_entrada(None) == '', 'None -> cadena vacia')
    _ok(sanear_entrada('  hola  ') == 'hola', 'recorta espacios exteriores')
    _ok(sanear_entrada('a\n\n\n\nb') == 'a\n\nb', 'colapsa saltos de linea')
    larga = sanear_entrada('x' * 2000, 100)
    _ok(len(larga) <= 101 and larga.endswith('…'), 'trunca al limite indicado')


def _probar_prompts():
    print('\n=== Prompts por rol ===')
    from backend.asistente.prompts import mensajes_para, system_prompt

    prompt = system_prompt('comerciante')
    _ok('Localis' in prompt, 'el prompt menciona la marca')
    _ok('COMERCIANTE' in prompt, 'el prompt incluye el rol')
    _ok('contrasenas' in prompt.lower(), 'prohibe pedir contrasenas')
    _ok('SOLO informacion' in prompt, 'el prompt declara que solo da informacion')
    _ok('No ejecutas acciones' in prompt, 'el prompt prohibe ejecutar acciones')
    _ok('WhatsApp' in prompt, 'el prompt deriva a WhatsApp para intervenciones')
    _ok('tardar' in prompt, 'el prompt avisa de que la atencion puede tardar')

    mensajes = mensajes_para(
        'cliente',
        'hola',
        'CONTEXTO-X',
        [
            {'autor': 'usuario', 'contenido': 'q1'},
            {'autor': 'asistente', 'contenido': 'a1'},
        ],
    )
    _ok(mensajes[0]['role'] == 'system', 'el primer mensaje es system')
    _ok(any(m['content'].startswith('CONTEXTO') for m in mensajes), 'inyecta el contexto RAG')
    _ok(any(m.get('role') == 'assistant' for m in mensajes), 'mapea el historial a assistant')
    _ok(mensajes[-1] == {'role': 'user', 'content': 'hola'}, 'el ultimo mensaje es el del usuario')


def _probar_interruptor():
    print('\n=== Interruptor de habilitacion ===')
    from config import asistente_habilitado

    with patch.dict(os.environ, {'LOCALIS_ASISTENTE_API_KEY': ''}, clear=True):
        _ok(asistente_habilitado() is False, 'sin clave -> apagado')
    with patch.dict(os.environ, {'LOCALIS_ASISTENTE_API_KEY': 'clave'}, clear=True):
        _ok(asistente_habilitado() is True, 'con clave -> habilitado')
    with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'clave'}, clear=True):
        _ok(asistente_habilitado() is True, 'acepta el alias OPENROUTER_API_KEY')
    with patch.dict(
        os.environ,
        {'LOCALIS_ASISTENTE_API_KEY': 'clave', 'LOCALIS_ASISTENTE': '0'},
        clear=True,
    ):
        _ok(asistente_habilitado() is False, 'LOCALIS_ASISTENTE=0 -> apagado')


def main() -> int:
    _probar_redaccion()
    _probar_entrada()
    _probar_prompts()
    _probar_interruptor()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK guardarrailes del asistente')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

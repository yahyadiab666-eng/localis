#!/usr/bin/env python3
"""OCR remoto: configuracion, codificacion y cortacircuitos. Sin red ni BD."""

from __future__ import annotations

import base64
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


def _reset():
    from backend import ocr_remoto

    ocr_remoto._fallos_seguidos = 0
    ocr_remoto._circuito_abierto_hasta = 0.0


def _probar_configuracion():
    print('\n=== Configuracion (apagado por defecto) ===')
    from backend import ocr_remoto

    with patch.dict(os.environ, {}, clear=True):
        _ok(ocr_remoto.configurado() is False, 'sin variables -> no configurado')
    with patch.dict(os.environ, {'LOCALIS_OCR_SERVICE_URL': 'https://w.example'}, clear=True):
        _ok(ocr_remoto.configurado() is False, 'URL sin token -> no configurado')
    with patch.dict(
        os.environ,
        {'LOCALIS_OCR_SERVICE_URL': 'https://w.example', 'LOCALIS_OCR_TOKEN': 't'},
        clear=True,
    ):
        _ok(ocr_remoto.configurado() is True, 'URL + token -> configurado')
    with patch.dict(
        os.environ,
        {'LOCALIS_OCR_SERVICE_URL': 'https://w.example', 'LOCALIS_CRON_TOKEN': 'c'},
        clear=True,
    ):
        _ok(ocr_remoto.configurado() is True, 'acepta LOCALIS_CRON_TOKEN como respaldo')


def _probar_codificacion():
    print('\n=== Codificacion de la imagen ===')
    from backend import ocr_remoto

    codificado = ocr_remoto._codificar(b'abc123')
    _ok(isinstance(codificado, str), 'devuelve str')
    _ok(base64.b64decode(codificado) == b'abc123', 'round-trip correcto')
    _ok(ocr_remoto._codificar(None) is None, 'None -> None (sin lanzar)')


def _probar_cortacircuitos():
    print('\n=== Cortacircuitos ===')
    from backend import ocr_remoto

    _reset()
    _ok(ocr_remoto._circuito_abierto() is False, 'al inicio cerrado')
    ocr_remoto._registrar_fallo('x')
    ocr_remoto._registrar_fallo('x')
    _ok(ocr_remoto._circuito_abierto() is False, '2 fallos: sigue cerrado')
    ocr_remoto._registrar_fallo('x')
    _ok(ocr_remoto._circuito_abierto() is True, '3 fallos: se abre')
    ocr_remoto._registrar_ok()
    _ok(ocr_remoto._circuito_abierto() is True, 'un exito no cierra el circuito abierto')
    _reset()
    _ok(ocr_remoto._circuito_abierto() is False, 'reset -> cerrado')

    estado = ocr_remoto.estado()
    _ok(
        set(estado) == {'configurado', 'circuito_abierto', 'fallos_seguidos', 'timeout_seg'},
        'estado() con las claves esperadas',
    )


def _probar_timeout():
    print('\n=== Timeout configurable ===')
    from backend import ocr_remoto

    with patch.dict(os.environ, {'LOCALIS_OCR_REMOTO_TIMEOUT_SEG': '5'}, clear=True):
        _ok(ocr_remoto._timeout() == 5.0, 'lee el timeout')
    with patch.dict(os.environ, {'LOCALIS_OCR_REMOTO_TIMEOUT_SEG': 'basura'}, clear=True):
        _ok(ocr_remoto._timeout() == 12.0, 'valor invalido -> por defecto')
    with patch.dict(os.environ, {'LOCALIS_OCR_REMOTO_TIMEOUT_SEG': '1'}, clear=True):
        _ok(ocr_remoto._timeout() >= 3.0, 'nunca por debajo de 3 s')


def main() -> int:
    _probar_configuracion()
    _probar_codificacion()
    _probar_cortacircuitos()
    _probar_timeout()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK OCR remoto (opcional, con respaldo local)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

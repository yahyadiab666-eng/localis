#!/usr/bin/env python3
"""Rutas del asistente (/api/asistente/*) con el proveedor simulado.

Sin red ni BD: el asistente es informativo y no toca la base de datos, asi que
estas pruebas no necesitan DATABASE_URL. Se parchea el proveedor LLM.
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


def _app():
    from flask import Flask

    from backend.blueprints.asistente import bp

    app = Flask(__name__)
    app.secret_key = 'clave-de-prueba'
    app.config['TESTING'] = True
    app.register_blueprint(bp)
    return app


def _probar_apagado():
    print('\n=== Sin clave: asistente apagado ===')
    cliente = _app().test_client()
    with patch.dict(os.environ, {}, clear=True):
        estado = cliente.get('/api/asistente/estado')
        _ok(
            estado.status_code == 200 and estado.get_json().get('habilitado') is False,
            '/estado informa deshabilitado',
        )
        chat = cliente.post('/api/asistente/chat', json={'mensaje': 'hola'})
        _ok(chat.status_code == 503, '/chat responde 503 sin clave')


def _probar_chat_ok():
    print('\n=== Con clave y proveedor OK ===')
    cliente = _app().test_client()
    falso = {
        'contenido': 'Puedes importar tu inventario con un archivo CSV o Excel.',
        'tool_calls': [],
        'tokens': 42,
        'modelo': 'test',
    }
    with patch.dict(os.environ, {'LOCALIS_ASISTENTE_API_KEY': 'clave-test'}, clear=True):
        with patch('backend.asistente.proveedor.responder', return_value=falso):
            respuesta = cliente.post(
                '/api/asistente/chat', json={'mensaje': '¿Como importo mi inventario?'}
            )
            datos = respuesta.get_json()
            _ok(respuesta.status_code == 200, 'responde 200')
            _ok(bool(datos.get('ok')), 'marca ok')
            _ok('inventario' in (datos.get('respuesta') or ''), 'devuelve la respuesta')
            _ok(bool(datos.get('sugerencias')), 'devuelve sugerencias')
            _ok('conversacion_id' not in datos, 'no persiste conversacion (sin BD)')


def _probar_historial_del_cliente():
    print('\n=== Historial aportado por el navegador ===')
    cliente = _app().test_client()
    capturado = {}

    def falso(mensajes, herramientas=None, timeout=None):
        capturado['mensajes'] = mensajes
        return {'contenido': 'ok', 'tool_calls': [], 'tokens': 1, 'modelo': 'test'}

    with patch.dict(os.environ, {'LOCALIS_ASISTENTE_API_KEY': 'clave-test'}, clear=True):
        with patch('backend.asistente.proveedor.responder', side_effect=falso):
            respuesta = cliente.post(
                '/api/asistente/chat',
                json={
                    'mensaje': '¿y el precio?',
                    'historial': [
                        {'autor': 'usuario', 'contenido': 'hola'},
                        {'autor': 'asistente', 'contenido': '¿En que te ayudo?'},
                    ],
                },
            )
            _ok(respuesta.status_code == 200, 'responde 200 con historial')
            mensajes = capturado.get('mensajes') or []
            _ok(
                any(m.get('role') == 'assistant' for m in mensajes),
                'reinyecta el historial del cliente como assistant',
            )
            _ok(
                mensajes[-1] == {'role': 'user', 'content': '¿y el precio?'},
                'el ultimo mensaje es la nueva pregunta',
            )


def _probar_mensaje_vacio():
    print('\n=== Mensaje vacio ===')
    cliente = _app().test_client()
    with patch.dict(os.environ, {'LOCALIS_ASISTENTE_API_KEY': 'clave-test'}, clear=True):
        respuesta = cliente.post('/api/asistente/chat', json={'mensaje': '   '})
        _ok(respuesta.status_code == 400, 'responde 400')
        _ok(respuesta.get_json().get('estado') == 'vacio', 'estado vacio')


def _probar_escalado():
    print('\n=== Escalado a una persona (ticket + WhatsApp) ===')
    cliente = _app().test_client()
    respuesta = cliente.post(
        '/api/asistente/escalar',
        json={'motivo': 'necesito ayuda con un pago', 'historial': [
            {'autor': 'usuario', 'contenido': 'hola'},
            {'autor': 'asistente', 'contenido': '¿en que te ayudo?'},
        ]},
    )
    datos = respuesta.get_json()
    _ok(respuesta.status_code == 200 and datos.get('ok'), 'escalar responde 200')
    _ok('wa.me' in (datos.get('whatsapp_url') or ''), 'devuelve enlace de WhatsApp')
    _ok(datos.get('ticket_creado') is False, 'sin correo no crea ticket')


def _probar_feedback():
    print('\n=== Feedback (util / no util) ===')
    cliente = _app().test_client()
    respuesta = cliente.post('/api/asistente/feedback', json={'util': True})
    _ok(respuesta.status_code == 200, 'feedback responde 200')
    _ok(respuesta.get_json().get('ok') is True, 'marca ok')


def _probar_metricas_en_estado():
    print('\n=== Metricas en /estado ===')
    cliente = _app().test_client()
    with patch.dict(os.environ, {'LOCALIS_ASISTENTE_API_KEY': 'clave-test'}, clear=True):
        datos = cliente.get('/api/asistente/estado').get_json()
    _ok(datos.get('habilitado') is True, 'informa habilitado')
    _ok(isinstance(datos.get('metricas'), dict), 'incluye metricas')


def _probar_proveedor_caido():
    print('\n=== Proveedor no disponible ===')
    from backend.asistente.proveedor import ProveedorNoDisponible

    cliente = _app().test_client()
    with patch.dict(os.environ, {'LOCALIS_ASISTENTE_API_KEY': 'clave-test'}, clear=True):
        with patch(
            'backend.asistente.proveedor.responder',
            side_effect=ProveedorNoDisponible('sin respuesta'),
        ):
            respuesta = cliente.post('/api/asistente/chat', json={'mensaje': 'hola'})
            _ok(respuesta.status_code == 503, 'responde 503')
            _ok(
                respuesta.get_json().get('estado') == 'no_disponible',
                'estado no_disponible',
            )


def main() -> int:
    _probar_apagado()
    _probar_chat_ok()
    _probar_historial_del_cliente()
    _probar_mensaje_vacio()
    _probar_escalado()
    _probar_feedback()
    _probar_metricas_en_estado()
    _probar_proveedor_caido()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK rutas del asistente')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

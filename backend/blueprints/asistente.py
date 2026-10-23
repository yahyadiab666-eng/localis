"""Blueprint del asistente de IA (/api/asistente/*).

Asistente de atencion al cliente SOLO INFORMATIVO: responde con la
documentacion y no toca la base de datos (no consulta cuentas, pagos ni
comercios). La derivacion a una persona es un enlace a WhatsApp que arma el
navegador; el servidor no crea tickets.

Es aditivo: si ``LOCALIS_ASISTENTE`` esta apagado o falta la clave, responde
503 y el widget no se muestra.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request, session

from backend.asistente import orquestador
from backend.rate_limit import limite
from config import asistente_habilitado

logger = logging.getLogger('localis.blueprints.asistente')

bp = Blueprint('asistente', __name__, url_prefix='/api/asistente')


def _rol_sesion():
    if session.get('es_admin'):
        return 'admin'
    if session.get('es_comerciante') or session.get('comercio_id'):
        return 'comerciante'
    if session.get('usuario_id'):
        return 'cliente'
    return 'invitado'


def _contexto_usuario():
    return {
        'usuario_id': session.get('usuario_id'),
        'rol': _rol_sesion(),
    }


@bp.route('/estado')
def estado():
    """El widget consulta si el asistente esta disponible."""
    return jsonify({'habilitado': asistente_habilitado()}), 200


@bp.route('/chat', methods=['POST'])
@limite('15 per minute;80 per hour')
def chat():
    if not asistente_habilitado():
        return (
            jsonify(
                {
                    'ok': False,
                    'estado': 'apagado',
                    'mensaje': 'El asistente no esta disponible en este momento.',
                }
            ),
            503,
        )

    datos = request.get_json(silent=True) or {}
    mensaje = datos.get('mensaje') or request.form.get('mensaje')
    historial = datos.get('historial')

    resultado = orquestador.responder(_contexto_usuario(), mensaje, historial)

    if resultado.get('ok'):
        return jsonify(resultado), 200

    motivo = resultado.get('estado')
    if motivo == 'ocupado':
        codigo = 429
    elif motivo == 'vacio':
        codigo = 400
    else:
        codigo = 503
    return jsonify(resultado), codigo

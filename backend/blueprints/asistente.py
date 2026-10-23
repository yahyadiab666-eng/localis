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
import time
from urllib.parse import quote

from flask import Blueprint, jsonify, request, session

from backend.asistente import observacion, orquestador
from backend.rate_limit import limite
from config import (
    ASISTENTE_WHATSAPP_URL,
    WHATSAPP_SOPORTE_URL,
    asistente_habilitado,
)

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
        'comercio_id': session.get('comercio_id'),
        'rol': _rol_sesion(),
    }


@bp.route('/estado')
def estado():
    """El widget consulta si el asistente esta disponible (+ metricas)."""
    return jsonify(
        {'habilitado': asistente_habilitado(), 'metricas': observacion.estado()}
    ), 200


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

    inicio = time.time()
    resultado = orquestador.responder(_contexto_usuario(), mensaje, historial)
    observacion.registrar_mensaje(bool(resultado.get('ok')), (time.time() - inicio) * 1000)

    if resultado.get('ok'):
        return jsonify(resultado), 200

    motivo = resultado.get('estado')
    if motivo in ('ocupado', 'presupuesto'):
        codigo = 429
    elif motivo == 'vacio':
        codigo = 400
    else:
        codigo = 503
    return jsonify(resultado), codigo


@bp.route('/feedback', methods=['POST'])
@limite('30 per minute')
def feedback():
    """Registra si la respuesta fue util (👍/👎). No guarda nada mas."""
    datos = request.get_json(silent=True) or {}
    observacion.registrar_feedback(bool(datos.get('util')))
    return jsonify({'ok': True}), 200


def _resumen_historial(historial):
    """Transcripcion breve para el ticket del equipo. Nunca lanza."""
    if not isinstance(historial, list):
        return ''
    lineas = []
    for turno in historial[-12:]:
        if not isinstance(turno, dict):
            continue
        contenido = str(turno.get('contenido') or '').strip()
        if not contenido:
            continue
        quien = 'Usuario' if turno.get('autor') == 'usuario' else 'Asistente'
        lineas.append(f'{quien}: {contenido[:400]}')
    return '\n'.join(lineas)


@bp.route('/escalar', methods=['POST'])
@limite('6 per minute')
def escalar():
    """Deriva a una persona: crea ticket (si hay correo) y devuelve WhatsApp.

    El ticket aparece en la bandeja que ya existe en el panel de administracion.
    Para usuarios anonimos (sin correo) solo se abre el WhatsApp.
    """
    datos = request.get_json(silent=True) or {}
    correo = (datos.get('correo') or session.get('correo') or '').strip()
    motivo = (
        datos.get('motivo') or 'El usuario pidio hablar con una persona.'
    ).strip()
    resumen = _resumen_historial(datos.get('historial'))

    ticket_creado = False
    if correo:
        try:
            from backend.admin import crear_ticket_soporte_o_reporte

            mensaje = f'[Asistente IA] {motivo}\n\nConversacion:\n{resumen}'
            ok, _ = crear_ticket_soporte_o_reporte(
                session.get('usuario_id'), 'soporte', correo, mensaje[:4000]
            )
            ticket_creado = bool(ok)
        except Exception as error:  # nunca rompe el escalado
            logger.warning('no se pudo crear el ticket de escalado: %s', error)

    base = ASISTENTE_WHATSAPP_URL or WHATSAPP_SOPORTE_URL
    texto = quote(f'Hola, necesito ayuda con Localis. {motivo}')
    separador = '&' if '?' in base else '?'
    return jsonify(
        {
            'ok': True,
            'ticket_creado': ticket_creado,
            'whatsapp_url': f'{base}{separador}text={texto}',
        }
    ), 200

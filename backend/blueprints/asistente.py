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

from backend.asistente import acciones, observacion, orquestador
from backend.rate_limit import limite
from config import (
    ASISTENTE_WHATSAPP_URL,
    WHATSAPP_SOPORTE_URL,
    asistente_habilitado,
)

logger = logging.getLogger('localis.blueprints.asistente')

bp = Blueprint('asistente', __name__, url_prefix='/api/asistente')

# Acciones pendientes de confirmar (en la sesion del servidor: un solo uso, con
# caducidad). El cliente solo envia el ``id``; el resto lo guarda el servidor.
CLAVE_ACCIONES = 'asistente_acciones'
_MAX_ACCIONES = 5
_EXPIRA_ACCION_SEG = 600


def _guardar_accion(propuesta):
    pendientes = session.get(CLAVE_ACCIONES)
    if not isinstance(pendientes, dict):
        pendientes = {}
    pendientes[propuesta['id']] = {
        'accion': propuesta.get('accion'),
        'producto_id': (propuesta.get('parametros') or {}).get('producto_id'),
        'titulo': propuesta.get('resumen'),
        'ts': time.time(),
    }
    ahora = time.time()
    for clave in list(pendientes):
        if ahora - float(pendientes[clave].get('ts') or 0) > _EXPIRA_ACCION_SEG:
            pendientes.pop(clave, None)
    if len(pendientes) > _MAX_ACCIONES:
        sobran = len(pendientes) - _MAX_ACCIONES
        for clave, _ in sorted(
            pendientes.items(), key=lambda kv: kv[1].get('ts') or 0
        )[:sobran]:
            pendientes.pop(clave, None)
    session[CLAVE_ACCIONES] = pendientes
    session.modified = True


def _tomar_accion(nonce):
    """Extrae y consume una accion pendiente (un solo uso). Nunca lanza."""
    if not nonce:
        return None
    pendientes = session.get(CLAVE_ACCIONES)
    if not isinstance(pendientes, dict):
        return None
    datos = pendientes.pop(nonce, None)
    session[CLAVE_ACCIONES] = pendientes
    session.modified = True
    if not datos:
        return None
    if time.time() - float(datos.get('ts') or 0) > _EXPIRA_ACCION_SEG:
        return None
    return datos


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

    # Guardar la propuesta de accion en la sesion (el cambio solo ocurre al
    # confirmar). El nonce es de un solo uso.
    propuesta = resultado.get('accion')
    if isinstance(propuesta, dict) and propuesta.get('id'):
        try:
            _guardar_accion(propuesta)
        except Exception as error:
            logger.warning('no se pudo guardar la accion pendiente: %s', error)
            resultado.pop('accion', None)

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


@bp.route('/accion/confirmar', methods=['POST'])
@limite('10 per minute')
def accion_confirmar():
    """Aplica una accion previamente propuesta (nonce de un solo uso)."""
    datos = request.get_json(silent=True) or {}
    nonce = str(datos.get('id') or '').strip()
    if not nonce:
        return jsonify({'ok': False, 'mensaje': 'Solicitud no valida.'}), 400

    pendiente = _tomar_accion(nonce)
    if not pendiente:
        return (
            jsonify({'ok': False, 'mensaje': 'La confirmacion expiro. Vuelve a pedirlo.'}),
            400,
        )

    usuario = _contexto_usuario()
    ok, mensaje = acciones.ejecutar(
        usuario.get('usuario_id'),
        usuario.get('comercio_id'),
        pendiente.get('accion'),
        pendiente.get('producto_id'),
    )
    return jsonify({'ok': ok, 'mensaje': mensaje}), (200 if ok else 400)


@bp.route('/accion/cancelar', methods=['POST'])
@limite('30 per minute')
def accion_cancelar():
    """Descarta una accion pendiente sin aplicar nada."""
    datos = request.get_json(silent=True) or {}
    _tomar_accion(str(datos.get('id') or '').strip())
    return jsonify({'ok': True}), 200

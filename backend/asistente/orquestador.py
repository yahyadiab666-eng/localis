"""Orquestador del asistente: guardarrailes + RAG + proveedor.

Por ahora el asistente es SOLO INFORMATIVO y no toca la base de datos: no
consulta cuentas, no verifica pagos ni modifica nada. El contexto de la
conversacion lo aporta el navegador (historial) y no se persiste.

Devuelve siempre un diccionario (nunca lanza) con ``ok`` y un ``estado``
legible para que la UI decida el aviso.
"""

from __future__ import annotations

import logging

from config import (
    ASISTENTE_LIMITE_MENSAJE,
    ASISTENTE_MAX_CONTEXTO,
    ASISTENTE_MAX_HISTORIAL,
    asistente_habilitado,
)

from backend.asistente import datos_vivos, proveedor
from backend.asistente.conocimiento import contexto_para
from backend.asistente.prompts import mensajes_para
from backend.asistente.seguridad import redactar, sanear_entrada
from backend.asistente.semaforo import cupo_disponible

_log = logging.getLogger('localis.asistente')

_LARGO_TURNO_HISTORIAL = 800

_MENSAJE_OCUPADO = (
    'Estoy atendiendo varias consultas en este momento. Intenta de nuevo en unos '
    'segundos o escribenos por WhatsApp.'
)
_MENSAJE_NO_DISPONIBLE = (
    'No pude responder ahora mismo. Puedes escribirnos por WhatsApp con el boton '
    '"Escribir a soporte".'
)

_SUGERENCIAS = {
    'cliente': [
        '¿Como busco un producto?',
        '¿Como contacto a un comercio?',
        '¿Como reporto un comercio o producto?',
    ],
    'invitado': [
        '¿Que es Localis?',
        '¿Como busco un producto?',
        '¿Como registro mi comercio?',
    ],
    'comerciante': [
        '¿Como importo mi inventario (CSV/Excel)?',
        '¿Por que mi producto no tiene foto?',
        '¿Que incluye mi plan?',
    ],
    'admin': [
        '¿Como funciona la bandeja tecnica?',
        '¿Que significan los estados de pago?',
        '¿Como reviso las imagenes pendientes?',
    ],
}


def sugerencias(rol):
    return _SUGERENCIAS.get(rol or 'invitado', _SUGERENCIAS['invitado'])


def _limpiar_historial(historial):
    """Acepta solo los ultimos turnos enviados por el cliente, saneados."""
    if not isinstance(historial, list):
        return []
    limite = ASISTENTE_MAX_HISTORIAL if ASISTENTE_MAX_HISTORIAL > 0 else 0
    turnos = historial[-limite:] if limite else []
    limpio = []
    for turno in turnos:
        if not isinstance(turno, dict):
            continue
        contenido = sanear_entrada(turno.get('contenido'), _LARGO_TURNO_HISTORIAL)
        if not contenido:
            continue
        autor = 'usuario' if turno.get('autor') == 'usuario' else 'asistente'
        limpio.append({'autor': autor, 'contenido': contenido})
    return limpio


def _construir_contexto(usuario, rol, texto):
    """Une documentacion (RAG) + datos en vivo (precios, tasa, mi comercio)."""
    partes = []
    documentacion = contexto_para(texto, max_chars=ASISTENTE_MAX_CONTEXTO)
    if documentacion:
        partes.append(documentacion)

    tasa = datos_vivos.tasa_del_dia()
    if tasa:
        partes.append(f'TASA DEL DIA: {tasa:.2f} Bs por USD')

    precios = datos_vivos.buscar_precios(texto)
    if precios:
        partes.append('PRECIOS DEL CATALOGO (datos en vivo):\n' + precios)

    if rol == 'comerciante' and datos_vivos.menciona_comercio(texto):
        resumen = datos_vivos.resumen_comercio(
            usuario.get('usuario_id'),
            usuario.get('comercio_id'),
            incluir_imagenes=datos_vivos.menciona_imagenes(texto),
        )
        if resumen:
            partes.append('DATOS DE TU COMERCIO (datos en vivo):\n' + resumen)

    return '\n\n---\n\n'.join(partes)


def responder(usuario, mensaje, historial=None):
    usuario = usuario or {}
    rol = usuario.get('rol') or 'invitado'

    if not asistente_habilitado():
        return {'ok': False, 'estado': 'apagado', 'mensaje': _MENSAJE_NO_DISPONIBLE}

    texto = sanear_entrada(mensaje, ASISTENTE_LIMITE_MENSAJE)
    if not texto:
        return {
            'ok': False,
            'estado': 'vacio',
            'mensaje': 'Escribe tu pregunta para poder ayudarte.',
        }

    contexto = _construir_contexto(usuario, rol, texto)
    mensajes = mensajes_para(rol, texto, contexto, _limpiar_historial(historial))

    with cupo_disponible() as cupo:
        if not cupo:
            return {'ok': False, 'estado': 'ocupado', 'mensaje': _MENSAJE_OCUPADO}
        try:
            salida = proveedor.responder(mensajes)
        except proveedor.ProveedorNoDisponible as error:
            _log.warning('proveedor no disponible: %s', error)
            return {
                'ok': False,
                'estado': 'no_disponible',
                'mensaje': _MENSAJE_NO_DISPONIBLE,
            }

    respuesta = redactar((salida.get('contenido') or '').strip())
    if not respuesta:
        return {'ok': False, 'estado': 'no_disponible', 'mensaje': _MENSAJE_NO_DISPONIBLE}

    return {
        'ok': True,
        'estado': 'ok',
        'respuesta': respuesta,
        'sugerencias': sugerencias(rol),
    }

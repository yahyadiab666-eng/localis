"""Interceptores globales de Flask: errores controlados sin cerrar sesión ni redirigir en falso."""

import psycopg2
from flask import jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException

from backend.diagnostics import reportar_error_critico_async


def _es_peticion_api():
    return request.path.startswith('/api/')


def _respuesta_amigable(exc, codigo_http, titulo, mensaje_usuario):
    """JSON para API; página amigable para HTML. No invalida la sesión."""
    if _es_peticion_api():
        cuerpo = {'error': mensaje_usuario, 'codigo': codigo_http}
        if codigo_http >= 500:
            cuerpo['detalle'] = str(exc)[:500]
        return jsonify(cuerpo), codigo_http

    return (
        render_template(
            'error_servidor.html',
            codigo=codigo_http,
            titulo=titulo,
            mensaje=mensaje_usuario,
            sesion_activa=bool(session.get('usuario_id')),
        ),
        codigo_http,
    )


def registrar_manejadores_errores(app):
    """Registra manejadores globales en la aplicación Flask."""

    @app.errorhandler(404)
    def pagina_no_encontrada(exc):
        return _respuesta_amigable(
            exc,
            404,
            'Página no encontrada',
            'La ruta solicitada no existe en Localis.',
        )

    @app.errorhandler(500)
    def error_interno_servidor(exc):
        reportar_error_critico_async(exc, request)
        return _respuesta_amigable(
            exc,
            500,
            'Error interno',
            'Ocurrió un problema inesperado. El equipo técnico fue notificado.',
        )

    @app.errorhandler(psycopg2.Error)
    def error_base_datos(exc):
        reportar_error_critico_async(exc, request)
        return _respuesta_amigable(
            exc,
            503,
            'Base de datos no disponible',
            'No pudimos completar la operación por un fallo temporal de la base de datos. '
            'Intenta de nuevo en unos segundos.',
        )

    @app.errorhandler(Exception)
    def error_no_controlado(exc):
        if isinstance(exc, HTTPException):
            return exc
        reportar_error_critico_async(exc, request)
        return _respuesta_amigable(
            exc,
            500,
            'Error inesperado',
            'Ocurrió un error inesperado. El equipo técnico fue notificado.',
        )

    # RequestEntityTooLarge y CSRFError se registran en main.py (requieren imports locales).

"""Observabilidad: logging configurable y registro de acceso por request.

- ``configurar_logging()``: formato de texto (por defecto) o JSON si
  ``LOCALIS_LOG_JSON=1``. Nivel por ``LOCALIS_LOG_LEVEL``.
- ``registrar_request_logging(app)``: asigna un ``request-id``, mide la
  duracion y registra una linea de acceso; expone ``X-Request-ID``.

Nunca lanza: la observabilidad no debe tumbar una peticion.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid

from flask import g, request

from backend import metricas, trafico


class JsonFormatter(logging.Formatter):
    """Formatea cada registro como una linea JSON (facil de ingerir en prod)."""

    def format(self, record):
        datos = {
            'ts': self.formatTime(record),
            'nivel': record.levelname,
            'logger': record.name,
            'msg': record.getMessage(),
        }
        extra = getattr(record, 'extra_datos', None)
        if isinstance(extra, dict):
            datos.update(extra)
        return json.dumps(datos, ensure_ascii=False)


def configurar_logging():
    """Configura el logger raiz segun entorno (texto o JSON)."""
    nivel = (os.environ.get('LOCALIS_LOG_LEVEL') or 'INFO').upper()
    usar_json = (os.environ.get('LOCALIS_LOG_JSON') or '').strip().lower() in (
        '1', 'true', 'yes', 'on'
    )
    manejador = logging.StreamHandler()
    if usar_json:
        manejador.setFormatter(JsonFormatter())
    else:
        manejador.setFormatter(
            logging.Formatter('[%(levelname)s] %(name)s: %(message)s')
        )
    raiz = logging.getLogger()
    for previo in list(raiz.handlers):
        raiz.removeHandler(previo)
    raiz.addHandler(manejador)
    raiz.setLevel(getattr(logging, nivel, logging.INFO))


def registrar_request_logging(app):
    """Registra hooks de before/after request para trazabilidad."""
    log = logging.getLogger('localis.acceso')

    @app.before_request
    def _marcar_inicio_request():
        try:
            g.request_id = (
                request.headers.get('X-Request-ID') or uuid.uuid4().hex[:12]
            )
            g.request_inicio = time.perf_counter()
        except Exception:
            pass
        # Arranca el backfill de imágenes en el primer request (post-fork): un
        # hilo creado al importar el módulo no sobrevive al fork de gunicorn en
        # producción (mismo motivo por el que el volcado de tráfico se movió al
        # request). Idempotente y muy barato (early-return si ya está activo).
        try:
            from backend.image_backfill import asegurar_backfill_activo

            asegurar_backfill_activo()
        except Exception:
            pass
        # Arranca (post-fork) el planificador del reporte diario de tráfico.
        try:
            from backend.reporte_trafico import asegurar_reporte_diario_activo

            asegurar_reporte_diario_activo()
        except Exception:
            pass
        # Envía (una sola vez) el informe de marketing a los administradores.
        try:
            from backend.informe_marketing import asegurar_informe_marketing

            asegurar_informe_marketing()
        except Exception:
            pass

    @app.after_request
    def _registrar_acceso(response):
        try:
            inicio = getattr(g, 'request_inicio', None)
            duracion = (time.perf_counter() - inicio) * 1000 if inicio else 0.0
            rid = getattr(g, 'request_id', '-')
            response.headers.setdefault('X-Request-ID', rid)
            metricas.registrar(response.status_code, duracion)
            trafico.registrar_peticion(
                request.path,
                response.status_code,
                user_agent=request.headers.get('User-Agent') or '',
                remote_addr=request.remote_addr or '',
                query=(
                    request.query_string.decode('latin-1', 'ignore')
                    if request.query_string
                    else ''
                ),
            )
            # Volcado oportunista: persiste los contadores a lo sumo cada
            # LOCALIS_TRAFICO_FLUSH_SEC segundos, desde el propio request
            # (sin depender de un hilo de fondo).
            trafico.volcar_oportunista()
            if not request.path.startswith('/static/'):
                log.info(
                    '%s %s -> %s %.1fms rid=%s',
                    request.method,
                    request.path,
                    response.status_code,
                    duracion,
                    rid,
                    extra={
                        'extra_datos': {
                            'metodo': request.method,
                            'ruta': request.path,
                            'estado': response.status_code,
                            'ms': round(duracion, 1),
                            'request_id': rid,
                        }
                    },
                )
        except Exception:
            pass
        return response

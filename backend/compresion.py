"""Compresión gzip de las respuestas de texto (sin dependencias nuevas).

Motivo (medido): la portada pesa ~220 KB de HTML y se servía **sin comprimir**.
Con gzip baja a ~17 KB (93 % menos), que en redes móviles es la diferencia
entre varios segundos y unas décimas.

Decisiones deliberadas:

- Solo se comprimen respuestas de texto (HTML/JSON/JS/XML/SVG) de tamaño
  razonable. Las imágenes/fuentes ya vienen comprimidas y recomprimir gasta CPU.
- No se toca ``direct_passthrough``: los ficheros estáticos (que el navegador
  cachea 7 días) se dejan como están para no materializarlos en memoria.
- Se añade ``Vary: Accept-Encoding`` incluso cuando no se comprime, para que
  ningún caché intermedio sirva una versión a quien no la pidió.
- Desactivable con ``LOCALIS_GZIP=0``.
"""

from __future__ import annotations

import gzip
import logging
import os

logger = logging.getLogger('localis.compresion')

_MINIMO_BYTES = 500
_MAXIMO_BYTES = 2 * 1024 * 1024
_NIVEL = 6

_TIPOS = (
    'text/',
    'application/json',
    'application/javascript',
    'application/xml',
    'application/xhtml+xml',
    'image/svg+xml',
)


def habilitado():
    valor = (os.environ.get('LOCALIS_GZIP') or '1').strip().lower()
    return valor not in ('0', 'false', 'no', 'off')


def _tipo_comprimible(response):
    tipo = (response.content_type or '').split(';')[0].strip().lower()
    return any(tipo.startswith(prefijo) or tipo == prefijo for prefijo in _TIPOS)


def comprimir_respuesta(response):
    """Devuelve la respuesta comprimida con gzip si aporta. Nunca lanza."""
    try:
        from flask import request

        # Sin esto, un caché podría servir gzip a quien no lo acepta.
        response.headers.setdefault('Vary', 'Accept-Encoding')

        if not habilitado():
            return response
        if response.direct_passthrough:
            return response
        if response.headers.get('Content-Encoding'):
            return response
        if 'gzip' not in (request.headers.get('Accept-Encoding') or '').lower():
            return response
        if not _tipo_comprimible(response):
            return response

        datos = response.get_data()
        if len(datos) < _MINIMO_BYTES or len(datos) > _MAXIMO_BYTES:
            return response

        comprimido = gzip.compress(datos, _NIVEL)
        if len(comprimido) >= len(datos):
            return response

        response.set_data(comprimido)
        response.headers['Content-Encoding'] = 'gzip'
        response.headers['Content-Length'] = str(len(comprimido))
        return response
    except Exception as error:
        logger.warning(f'[Localis Gzip] omitido: {type(error).__name__}: {error}')
        return response


def configurar_compresion(app):
    """Registra la compresión en la app. Idempotente."""
    app.after_request(comprimir_respuesta)
    logger.info('compresión gzip %s', 'activa' if habilitado() else 'desactivada')
    return app

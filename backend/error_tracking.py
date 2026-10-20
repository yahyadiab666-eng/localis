"""Sentry opcional (env-gated). Complementa el reporte por email existente.

- Sin ``LOCALIS_SENTRY_DSN`` → no-op (no cambia nada).
- Con DSN pero sin ``sentry-sdk`` instalado → aviso, no-op.
- Con DSN y SDK → inicializa la integracion de Flask.

Nunca lanza: la observabilidad no debe tumbar el arranque.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger('localis.error_tracking')


def iniciar_error_tracking(app):
    """Devuelve True si Sentry quedo activo; False si no-op."""
    dsn = (os.environ.get('LOCALIS_SENTRY_DSN') or '').strip()
    if not dsn:
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration
    except Exception:
        logger.warning(
            'LOCALIS_SENTRY_DSN definido pero sentry-sdk no esta instalado; se omite.'
        )
        return False

    try:
        try:
            import config

            ambiente = 'production' if config.es_entorno_produccion() else 'staging'
        except Exception:
            ambiente = os.environ.get('RENDER') and 'production' or 'development'

        try:
            traces = float(os.environ.get('LOCALIS_SENTRY_TRACES', '0') or 0)
        except (TypeError, ValueError):
            traces = 0.0

        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=max(0.0, min(1.0, traces)),
            environment=ambiente,
            send_default_pii=False,
        )
        logger.info('Sentry activo (environment=%s).', ambiente)
        return True
    except Exception:
        logger.error('No se pudo inicializar Sentry.', exc_info=True)
        return False

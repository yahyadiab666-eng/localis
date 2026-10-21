"""Envío único del informe de marketing a los administradores.

Lee ``docs/plan-marketing.md`` y lo manda por correo **una sola vez** (marca en
``configuracion_sistema``). Se dispara de forma perezosa desde el primer request
(post-fork) en un hilo creado dentro del worker — nunca al importar el módulo.
Nunca lanza.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from config import ADMIN_EMAILS
from backend.correo import correo_configurado, enviar_correo

logger = logging.getLogger('localis.informe_marketing')

_LOG = '[Localis Informe Marketing]'
_CLAVE_MARCA = 'informe_marketing_enviado'
_RUTA_PLAN = Path(__file__).resolve().parents[1] / 'docs' / 'plan-marketing.md'

_iniciado = False
_ultimo_intento = 0.0
_lock = threading.Lock()


def _marca_puesta():
    """True si el informe ya se envió (o no se puede comprobar). Nunca lanza."""
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT 1 FROM configuracion_sistema WHERE clave = ?',
                (_CLAVE_MARCA,),
            )
            return cursor.fetchone() is not None
    except Exception:
        # Ante la duda, no enviar (mejor no duplicar).
        return True


def _poner_marca():
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                INSERT INTO configuracion_sistema (clave, valor)
                VALUES (?, ?)
                ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor
                """,
                (_CLAVE_MARCA, time.strftime('%Y-%m-%d %H:%M:%S')),
            )
            conexion.commit()
        return True
    except Exception as error:
        logger.error(f'{_LOG} no se pudo guardar la marca: {error}')
        return False


def _leer_plan():
    try:
        return _RUTA_PLAN.read_text(encoding='utf-8')
    except Exception as error:
        logger.error(f'{_LOG} no se pudo leer el plan: {error}')
        return ''


def enviar_informe_marketing():
    """Envía el plan una vez. Devuelve True si se envió. Nunca lanza."""
    try:
        if not correo_configurado():
            return False
        if _marca_puesta():
            return False
        cuerpo = _leer_plan()
        if not cuerpo:
            return False
        asunto = '[Localis] Plan de captación de comercios y usuarios'
        enviados = 0
        for destino in ADMIN_EMAILS:
            try:
                ok, detalle = enviar_correo(destino, asunto, cuerpo)
            except Exception as error:
                ok, detalle = False, str(error)
            if ok:
                enviados += 1
            else:
                logger.error(f'{_LOG} fallo a {destino}: {detalle}')
        if enviados:
            _poner_marca()
            logger.info(f'{_LOG} informe enviado a {enviados} admin(s)')
        return enviados > 0
    except Exception as error:
        logger.error(f'{_LOG} fallo el envío: {type(error).__name__}: {error}')
        return False


def asegurar_informe_marketing():
    """Dispara el envío (una vez) desde el request, en un hilo. Nunca lanza."""
    global _iniciado, _ultimo_intento
    try:
        if _iniciado:
            return
        ahora = time.time()
        if ahora - _ultimo_intento < 30:
            return
        _ultimo_intento = ahora
        with _lock:
            if _iniciado:
                return
            _iniciado = True
        hilo = threading.Thread(
            target=enviar_informe_marketing, name='localis-marketing', daemon=True
        )
        hilo.start()
    except Exception:
        pass

"""Envío de correos transaccionales (best-effort, no bloquea la petición).

Reutiliza la configuración SMTP existente (``config.py``). Pensado para avisar
al dueño cuando se le transfiere/reasigna una tienda. Nunca lanza: si el correo
falla, solo se registra en consola.
"""
import logging
logger = logging.getLogger('localis.correo')

import os
import smtplib
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import (
    SMTP_FROM,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USE_TLS,
    SMTP_USER,
)

LOG_PREFIX = '[Localis Correo]'


def _base_url():
    """URL pública para los enlaces de los correos (una sola fuente: ``config.SITIO_URL``)."""
    try:
        from config import SITIO_URL

        return SITIO_URL
    except Exception:
        return (
            os.environ.get('LOCALIS_BASE_URL')
            or os.environ.get('RENDER_EXTERNAL_URL')
            or 'https://localis.onrender.com'
        ).rstrip('/')


def correo_configurado():
    return bool(SMTP_USER and SMTP_PASSWORD)


def enviar_correo(destinatario, asunto, cuerpo):
    """Envía un correo de texto plano. Retorna ``(ok, mensaje)``. Nunca lanza."""
    if not destinatario:
        return False, 'destinatario vacío'
    if not correo_configurado():
        return False, 'SMTP_USER/SMTP_PASSWORD no configurados'
    try:
        mensaje = MIMEMultipart()
        mensaje['From'] = SMTP_FROM
        mensaje['To'] = destinatario
        mensaje['Subject'] = asunto
        mensaje.attach(MIMEText(cuerpo, 'plain', 'utf-8'))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as servidor:
            if SMTP_USE_TLS:
                servidor.starttls()
            servidor.login(SMTP_USER, SMTP_PASSWORD)
            servidor.sendmail(SMTP_FROM, [destinatario], mensaje.as_string())
        return True, 'enviado'
    except Exception as error:
        print(
            f'{LOG_PREFIX} fallo al enviar a {destinatario}: '
            f'{type(error).__name__}: {error}',
            flush=True,
        )
        return False, str(error)


def enviar_correo_async(destinatario, asunto, cuerpo):
    """Dispara el envío en un hilo daemon (no bloquea la respuesta HTTP)."""
    if not destinatario:
        return
    try:
        hilo = threading.Thread(
            target=enviar_correo,
            args=(destinatario, asunto, cuerpo),
            name='localis-correo',
            daemon=True,
        )
        hilo.start()
    except Exception as error:
        logger.error(f'{LOG_PREFIX} no se pudo lanzar el envío: {error}')


def notificar_propietario_tienda(nombre_tienda, email, vinculado):
    """Avisa al dueño que tiene una tienda en Localis.

    ``vinculado`` = True si el usuario ya existía (se enlazó al instante);
    False si aún debe iniciar sesión por primera vez con ese correo.
    """
    nombre_tienda = (nombre_tienda or 'tu tienda').strip()
    login = f'{_base_url()}/login'
    if vinculado:
        cuerpo = (
            f'¡Hola!\n\n'
            f'La tienda "{nombre_tienda}" quedó asociada a tu cuenta en Localis.\n'
            f'Inicia sesión con Google para administrarla:\n{login}\n\n'
            f'Si no reconoces esta tienda, ignora este mensaje.\n\n'
            f'— Equipo Localis'
        )
    else:
        cuerpo = (
            f'¡Hola!\n\n'
            f'Te preparamos la tienda "{nombre_tienda}" en Localis y quedó '
            f'reservada a este correo.\n'
            f'Inicia sesión por primera vez con Google usando esta misma '
            f'dirección y se enlazará automáticamente a tu cuenta:\n{login}\n\n'
            f'Si no reconoces esta tienda, ignora este mensaje.\n\n'
            f'— Equipo Localis'
        )
    enviar_correo_async(email, f'Tu tienda "{nombre_tienda}" está en Localis', cuerpo)


def notificar_dueno_anterior(nombre_tienda, email):
    """Avisa al dueño anterior que la tienda fue reasignada a otro correo."""
    if not email:
        return
    nombre_tienda = (nombre_tienda or 'tu tienda').strip()
    cuerpo = (
        f'¡Hola!\n\n'
        f'La tienda "{nombre_tienda}" fue transferida a otro correo y ya no '
        f'está a tu cargo.\n'
        f'Si crees que es un error, contacta a soporte.\n\n'
        f'— Equipo Localis'
    )
    enviar_correo_async(email, f'La tienda "{nombre_tienda}" fue transferida', cuerpo)

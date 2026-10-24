"""Vencimiento del dominio propio (aviso preventivo).

**Por qué existe:** NIC.ve no avisa ni renueva automáticamente; si el dominio
vence, se cae la web **y el APK** (el TWA carga la web remota). Para que la
renovación manual no dependa de la memoria de nadie, la fecha vive en la
variable de entorno ``LOCALIS_DOMINIO_VENCE`` (``YYYY-MM-DD``) y la app avisa en
el reporte diario a los 60, 30 y 7 días.

Nada de aquí lanza: si la fecha falta o está mal, simplemente no hay aviso.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime

logger = logging.getLogger('localis.dominio')

_LOG = '[Localis Dominio]'
DIAS_AVISO = (60, 30, 7)


def dominio_configurado():
    """Dominio propio declarado (informativo), o cadena vacía."""
    try:
        return (os.environ.get('LOCALIS_DOMINIO') or '').strip()
    except Exception:
        return ''


def fecha_vencimiento():
    """``date`` de vencimiento, o None si no está configurada o está mal."""
    try:
        texto = (os.environ.get('LOCALIS_DOMINIO_VENCE') or '').strip()
        if not texto:
            return None
        return datetime.strptime(texto[:10], '%Y-%m-%d').date()
    except Exception:
        logger.error(f'{_LOG} LOCALIS_DOMINIO_VENCE mal formada (usa YYYY-MM-DD)')
        return None


def dias_restantes():
    """Días hasta el vencimiento (negativo si ya venció), o None."""
    vence = fecha_vencimiento()
    if not vence:
        return None
    return (vence - date.today()).days


def aviso_vencimiento():
    """Texto de aviso si toca avisar, o cadena vacía. Nunca lanza."""
    try:
        dias = dias_restantes()
        if dias is None:
            return ''
        nombre = dominio_configurado() or 'El dominio'
        if dias < 0:
            return (
                f'URGENTE: {nombre} VENCIÓ hace {abs(dias)} día(s). Hay que '
                f'renovarlo en nic.ve: sin eso se caen la web y el APK.'
            )
        for umbral in DIAS_AVISO:
            if dias <= umbral:
                return (
                    f'{nombre} vence en {dias} día(s). Renueva en nic.ve '
                    f'(no avisan ni renuevan solos).'
                )
        return ''
    except Exception:
        return ''


def estado():
    """Resumen para el panel de admin y los diagnósticos."""
    vence = fecha_vencimiento()
    return {
        'dominio': dominio_configurado(),
        'vence': vence.isoformat() if vence else None,
        'dias': dias_restantes(),
        'aviso': aviso_vencimiento(),
    }
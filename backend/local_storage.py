"""Respaldo local de imágenes cuando Supabase Storage no está disponible."""

import os
import re

from config import RUTA_RAIZ, es_entorno_produccion

LOG_PREFIX = '[Localis Storage]'
UPLOADS_STATIC_PREFIX = '/static/uploads/'
_CARPETA_SEGURA = re.compile(r'[^a-zA-Z0-9_-]')
_advertencia_efimero_emitida = False


def _emitir_advertencia_efimero():
    global _advertencia_efimero_emitida
    if _advertencia_efimero_emitida or not es_entorno_produccion():
        return
    _advertencia_efimero_emitida = True
    print(
        f'{LOG_PREFIX} ADVERTENCIA: en producción el disco local puede ser efímero '
        '(p. ej. Render). Las URLs se guardan en PostgreSQL; el archivo persiste '
        'mientras el contenedor no se reinicie.'
    )


def _sanitizar_carpeta(carpeta):
    texto = (carpeta or 'misc').strip().strip('/\\').replace('\\', '/')
    partes = [p for p in texto.split('/') if p and p not in ('.', '..')]
    if not partes:
        return 'misc'
    limpias = []
    for parte in partes:
        segura = _CARPETA_SEGURA.sub('', parte)
        if segura:
            limpias.append(segura)
    return '/'.join(limpias) if limpias else 'misc'


def _sanitizar_nombre_archivo(filename):
    nombre = os.path.basename(str(filename or '').replace('\\', '/'))
    if not nombre or nombre in ('.', '..'):
        raise ValueError('Nombre de archivo inválido.')
    return nombre


def directorio_uploads_absoluto(carpeta):
    carpeta_segura = _sanitizar_carpeta(carpeta)
    destino = os.path.join(RUTA_RAIZ, 'static', 'uploads', *carpeta_segura.split('/'))
    os.makedirs(destino, exist_ok=True)
    return destino, carpeta_segura


def url_publica_local(carpeta, filename):
    _, carpeta_segura = directorio_uploads_absoluto(carpeta)
    nombre = _sanitizar_nombre_archivo(filename)
    return f'{UPLOADS_STATIC_PREFIX}{carpeta_segura}/{nombre}'


def guardar_bytes_local(data, filename, carpeta):
    """
    Guarda bytes en static/uploads/{carpeta}/ y retorna la ruta pública /static/uploads/...
    """
    if not data:
        raise ValueError('No hay datos para guardar localmente.')

    destino_dir, carpeta_segura = directorio_uploads_absoluto(carpeta)
    nombre = _sanitizar_nombre_archivo(filename)
    ruta_absoluta = os.path.join(destino_dir, nombre)

    with open(ruta_absoluta, 'wb') as archivo:
        archivo.write(data)

    _emitir_advertencia_efimero()
    return f'{UPLOADS_STATIC_PREFIX}{carpeta_segura}/{nombre}'

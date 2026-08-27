"""Respaldo local de imágenes cuando Supabase Storage no está disponible."""

import os

from config import UPLOAD_FOLDER


def ruta_publica_uploads(subcarpeta, filename):
    sub = subcarpeta.strip('/').replace('\\', '/')
    return f'/static/uploads/{sub}/{filename}'


def guardar_imagen_local(data, filename, subcarpeta='misc'):
    """Guarda bytes en static/uploads/ y devuelve la ruta pública /static/uploads/..."""
    if not data:
        raise ValueError('No hay datos de imagen para guardar en disco.')

    sub = subcarpeta.strip('/').replace('\\', '/')
    destino_dir = os.path.join(UPLOAD_FOLDER, sub)
    os.makedirs(destino_dir, exist_ok=True)

    destino = os.path.join(destino_dir, filename)
    with open(destino, 'wb') as archivo:
        archivo.write(data)

    return ruta_publica_uploads(sub, filename)

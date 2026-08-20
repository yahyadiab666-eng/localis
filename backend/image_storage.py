"""Almacenamiento local de imágenes en static/images (sin clientes externos)."""

import os

from backend.images import comprimir_bytes_a_bytes, comprimir_file_storage_a_bytes, validar_archivo_subida
from backend.utils import imagen_url_almacenada

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
STATIC_IMAGES_ROOT = os.path.join(BASE_DIR, 'static', 'images')


class ImageUploadError(Exception):
    """Error al guardar una imagen en disco."""


def _ruta_publica(carpeta, filename):
    carpeta_limpia = carpeta.strip('/').replace('\\', '/')
    return f'/static/images/{carpeta_limpia}/{filename}'


def _guardar_bytes_en_static(data, carpeta, filename):
    destino_dir = os.path.join(STATIC_IMAGES_ROOT, carpeta.strip('/'))
    os.makedirs(destino_dir, exist_ok=True)
    ruta_absoluta = os.path.join(destino_dir, filename)
    with open(ruta_absoluta, 'wb') as archivo:
        archivo.write(data)
    url = _ruta_publica(carpeta, filename)
    validada = imagen_url_almacenada(url)
    if not validada:
        raise ImageUploadError('No se pudo generar una URL persistible para la imagen.')
    return validada


def subir_imagen_local(
    file_storage,
    prefijo='img',
    carpeta='comercios',
    max_dimension=800,
):
    """Comprime y guarda una imagen subida; retorna ruta /static/... para PostgreSQL."""
    if not file_storage:
        raise ImageUploadError('No se recibió ningún archivo de imagen.')

    error_validacion = validar_archivo_subida(file_storage)
    if error_validacion:
        raise ImageUploadError(error_validacion)

    comprimido = comprimir_file_storage_a_bytes(
        file_storage, prefijo=prefijo, max_dimension=max_dimension
    )
    if not comprimido:
        raise ImageUploadError('No se pudo comprimir la imagen subida.')

    data, _content_type, filename = comprimido
    return _guardar_bytes_en_static(data, carpeta, filename)


def subir_bytes_local(data, filename, carpeta='pagos', max_dimension=1920, prefijo='img'):
    """Guarda bytes de imagen (p. ej. comprobante de pago) en static/images."""
    if not data:
        raise ImageUploadError('No hay datos de imagen para guardar.')

    comprimido = comprimir_bytes_a_bytes(
        data, prefijo=prefijo, max_dimension=max_dimension
    )
    if not comprimido:
        raise ImageUploadError('No se pudo procesar la imagen.')

    payload, _content_type, nombre_final = comprimido
    return _guardar_bytes_en_static(payload, carpeta, nombre_final or filename)

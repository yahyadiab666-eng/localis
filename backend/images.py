"""Procesamiento centralizado de imágenes con Pillow y paralelismo."""

import io
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image
from werkzeug.utils import secure_filename

from config import MAX_UPLOAD_BYTES

MAX_DIMENSION = 800
QUALITY = 80
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
DEFAULT_WORKERS = 8


def archivo_imagen_valido(filename):
    if not filename or '.' not in filename:
        return False
    return filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _obtener_tamano_subida(file_storage):
    """Intenta obtener el tamaño del archivo sin leerlo completo a memoria."""
    content_length = getattr(file_storage, 'content_length', None)
    if content_length is not None:
        return content_length

    stream = getattr(file_storage, 'stream', None)
    if stream is None:
        return None

    try:
        pos = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(pos)
        return size
    except Exception:
        return None


def validar_archivo_subida(file_storage, max_bytes=None):
    """
    Valida extensión y tamaño antes de procesar en memoria o subir a Supabase.
    Retorna None si es válido, o un mensaje de error en español.
    """
    max_bytes = max_bytes or MAX_UPLOAD_BYTES

    if not file_storage or not getattr(file_storage, 'filename', ''):
        return 'No se recibió ningún archivo.'

    if not archivo_imagen_valido(file_storage.filename):
        return 'Formato de imagen no permitido. Usa PNG, JPG o WebP.'

    tamano = _obtener_tamano_subida(file_storage)
    if tamano is not None and tamano > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        return f'El archivo supera el tamaño máximo permitido ({max_mb} MB).'

    return None


def leer_bytes_limitados(file_storage, max_bytes=None):
    """
    Lee bytes del archivo validando extensión y tope de tamaño.
    Retorna (bytes, None) o (None, mensaje_error).
    """
    max_bytes = max_bytes or MAX_UPLOAD_BYTES
    error = validar_archivo_subida(file_storage, max_bytes=max_bytes)
    if error:
        return None, error

    try:
        stream = file_storage.stream
        stream.seek(0)
        data = stream.read(max_bytes + 1)
        if len(data) > max_bytes:
            max_mb = max_bytes // (1024 * 1024)
            return None, f'El archivo supera el tamaño máximo permitido ({max_mb} MB).'
        stream.seek(0)
        return data, None
    except Exception as error:
        return None, f'No se pudo leer el archivo: {error}'


def _preparar_imagen(img):
    """Convierte RGBA/P/LA a RGB sobre lienzo blanco para evitar fondos negros."""
    if img.mode in ('RGBA', 'LA', 'P'):
        if img.mode == 'P':
            img = img.convert('RGBA')
        elif img.mode == 'LA':
            img = img.convert('RGBA')
        fondo = Image.new('RGB', img.size, (255, 255, 255))
        fondo.paste(img, mask=img.split()[3])
        return fondo
    if img.mode != 'RGB':
        return img.convert('RGB')
    return img


def _nombre_archivo_seguro(prefijo, extension='webp'):
    prefijo_limpio = secure_filename(prefijo) or 'img'
    token = uuid.uuid4().hex[:8]
    return f'{prefijo_limpio}_{token}.{extension}'


def comprimir_pil_a_bytes(
    img,
    prefijo='img',
    max_dimension=MAX_DIMENSION,
    quality=QUALITY,
    formato='WEBP',
):
    """Comprime un objeto PIL y retorna (bytes, content_type, filename) o None."""
    try:
        img = _preparar_imagen(img)
        img.thumbnail((max_dimension, max_dimension))

        extension = 'webp' if formato.upper() == 'WEBP' else 'jpg'
        filename = _nombre_archivo_seguro(prefijo, extension)
        buffer = io.BytesIO()

        if extension == 'webp':
            img.save(buffer, 'WEBP', quality=quality, optimize=True)
            content_type = 'image/webp'
        else:
            img.save(buffer, 'JPEG', quality=quality, optimize=True)
            content_type = 'image/jpeg'

        return buffer.getvalue(), content_type, filename
    except Exception as e:
        print(f'Error al comprimir imagen a bytes: {e}')
        return None


def comprimir_bytes_a_bytes(
    data_bytes,
    prefijo='img',
    max_dimension=MAX_DIMENSION,
    quality=QUALITY,
    formato='WEBP',
):
    """Comprime bytes de imagen y retorna (bytes, content_type, filename) o None."""
    if not data_bytes:
        return None
    try:
        img = Image.open(io.BytesIO(data_bytes))
        return comprimir_pil_a_bytes(
            img, prefijo=prefijo, max_dimension=max_dimension, quality=quality, formato=formato
        )
    except Exception as e:
        print(f'Error al comprimir bytes de imagen: {e}')
        return None


def comprimir_file_storage_a_bytes(
    file_storage,
    prefijo='img',
    max_dimension=MAX_DIMENSION,
    quality=QUALITY,
    formato='WEBP',
):
    """Comprime un archivo subido y retorna (bytes, content_type, filename) o None."""
    error = validar_archivo_subida(file_storage)
    if error:
        print(f'Archivo rechazado: {error}')
        return None

    if not file_storage or not getattr(file_storage, 'filename', ''):
        return None

    nombre_original = secure_filename(file_storage.filename)
    if not nombre_original:
        return None

    try:
        file_storage.stream.seek(0)
        img = Image.open(file_storage.stream)
        prefijo_base = prefijo or nombre_original.rsplit('.', 1)[0]
        return comprimir_pil_a_bytes(
            img, prefijo_base, max_dimension, quality, formato
        )
    except Exception as e:
        print(f'Error al comprimir archivo a bytes: {e}')
        return None


def procesar_tarea_imagen(tarea):
    """
    Ejecuta una tarea de imagen en un worker sin almacenamiento local.
    Retorna dict con producto_id (opcional) y url resultante.
    """
    prefijo = tarea.get('prefijo', 'img')
    producto_id = tarea.get('producto_id')
    url = None
    tipo = tarea.get('tipo')

    if tipo == 'url' and tarea.get('url'):
        url_externa = tarea['url']
        if url_externa.startswith('http'):
            url = url_externa
        else:
            url = url_externa

    elif tipo == 'buscar':
        # Deshabilitado: no asignar imágenes por búsqueda genérica.
        url = None

    return {'producto_id': producto_id, 'url': url}


def procesar_imagenes_paralelo(tareas, max_workers=DEFAULT_WORKERS):
    """
    Procesa múltiples imágenes en paralelo con ThreadPoolExecutor.
    Retorna lista de resultados {producto_id, url}.
    """
    if not tareas:
        return []

    resultados = []
    workers = min(max_workers, max(1, len(tareas)))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futuros = {executor.submit(procesar_tarea_imagen, t): t for t in tareas}
        for futuro in as_completed(futuros):
            try:
                resultados.append(futuro.result())
            except Exception as e:
                tarea = futuros[futuro]
                print(f'Error en worker de imagen (prod {tarea.get("producto_id")}): {e}')

    return resultados

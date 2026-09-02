"""Procesamiento centralizado de imágenes con Pillow y paralelismo."""

import io
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image
from werkzeug.utils import secure_filename

from config import MAX_UPLOAD_BYTES

MAX_DIMENSION = 800
QUALITY = 78
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
FONDO_LIENZO = (255, 254, 251)  # #fffefb, mismo tono que las tarjetas
DEFAULT_WORKERS = 4


class ImageProcessingError(Exception):
    """Fallo al leer o comprimir una imagen subida (mensaje para el usuario/logs)."""


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


def _encajar_en_lienzo(img, lado, fondo=FONDO_LIENZO):
    """Centra la foto en un cuadrado de fondo limpio (sin estirar)."""
    img = _preparar_imagen(img)
    copia = img.copy()
    copia.thumbnail((lado, lado))
    lado_final = max(copia.width, copia.height) or lado
    lienzo = Image.new('RGB', (lado_final, lado_final), fondo)
    x = (lado_final - copia.width) // 2
    y = (lado_final - copia.height) // 2
    lienzo.paste(copia, (x, y))
    return lienzo


def comprimir_pil_a_bytes(
    img,
    prefijo='img',
    max_dimension=MAX_DIMENSION,
    quality=QUALITY,
    formato='WEBP',
    lienzo_cuadrado=False,
):
    """Comprime un objeto PIL y retorna (bytes, content_type, filename)."""
    try:
        if lienzo_cuadrado:
            img = _encajar_en_lienzo(img, max_dimension)
        else:
            img = _preparar_imagen(img)
            img.thumbnail((max_dimension, max_dimension))

        extension = 'webp' if formato.upper() == 'WEBP' else 'jpg'
        filename = _nombre_archivo_seguro(prefijo, extension)
        buffer = io.BytesIO()

        if extension == 'webp':
            img.save(buffer, 'WEBP', quality=quality, method=4)
            content_type = 'image/webp'
        else:
            img.save(buffer, 'JPEG', quality=quality, optimize=True)
            content_type = 'image/jpeg'

        return buffer.getvalue(), content_type, filename
    except ImageProcessingError:
        raise
    except Exception as error:
        raise ImageProcessingError(
            f'No se pudo comprimir la imagen ({type(error).__name__}): {error}'
        ) from error


def comprimir_bytes_a_bytes(
    data_bytes,
    prefijo='img',
    max_dimension=MAX_DIMENSION,
    quality=QUALITY,
    formato='WEBP',
    lienzo_cuadrado=False,
):
    """Comprime bytes de imagen y retorna (bytes, content_type, filename)."""
    if not data_bytes:
        raise ImageProcessingError('No hay datos de imagen para comprimir.')
    try:
        img = Image.open(io.BytesIO(data_bytes))
        img.load()
        return comprimir_pil_a_bytes(
            img,
            prefijo=prefijo,
            max_dimension=max_dimension,
            quality=quality,
            formato=formato,
            lienzo_cuadrado=lienzo_cuadrado,
        )
    except ImageProcessingError:
        raise
    except Exception as error:
        raise ImageProcessingError(
            f'No se pudo abrir la imagen ({type(error).__name__}): {error}'
        ) from error


def comprimir_file_storage_a_bytes(
    file_storage,
    prefijo='img',
    max_dimension=MAX_DIMENSION,
    quality=QUALITY,
    formato='WEBP',
    lienzo_cuadrado=False,
):
    """Comprime un archivo subido y retorna (bytes, content_type, filename)."""
    error = validar_archivo_subida(file_storage)
    if error:
        raise ImageProcessingError(error)

    nombre_original = secure_filename(file_storage.filename)
    if not nombre_original:
        raise ImageProcessingError('El archivo no tiene un nombre válido.')

    data, error_lectura = leer_bytes_limitados(file_storage)
    if error_lectura:
        raise ImageProcessingError(error_lectura)
    prefijo_base = prefijo or nombre_original.rsplit('.', 1)[0]
    return comprimir_bytes_a_bytes(
        data,
        prefijo=prefijo_base,
        max_dimension=max_dimension,
        quality=quality,
        formato=formato,
        lienzo_cuadrado=lienzo_cuadrado,
    )


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

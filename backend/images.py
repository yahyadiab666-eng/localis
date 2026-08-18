"""Procesamiento centralizado de imágenes con Pillow y paralelismo."""

import io
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from PIL import Image
from werkzeug.utils import secure_filename

MAX_DIMENSION = 800
QUALITY = 80
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}
DEFAULT_WORKERS = 8


def archivo_imagen_valido(filename):
    if not filename or '.' not in filename:
        return False
    return filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


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


def comprimir_file_storage_a_bytes(
    file_storage,
    prefijo='img',
    max_dimension=MAX_DIMENSION,
    quality=QUALITY,
    formato='WEBP',
):
    """Comprime un archivo subido y retorna (bytes, content_type, filename) o None."""
    if not file_storage or not getattr(file_storage, 'filename', ''):
        return None
    if not archivo_imagen_valido(file_storage.filename):
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


def comprimir_pil_a_archivo(
    img,
    upload_folder,
    prefijo='img',
    max_dimension=MAX_DIMENSION,
    quality=QUALITY,
    formato='WEBP',
):
    """Comprime un objeto PIL y lo guarda como WebP o JPEG. Retorna ruta web o None."""
    try:
        img = _preparar_imagen(img)
        img.thumbnail((max_dimension, max_dimension))

        extension = 'webp' if formato.upper() == 'WEBP' else 'jpg'
        filename = _nombre_archivo_seguro(prefijo, extension)
        filepath = os.path.join(upload_folder, filename)
        os.makedirs(upload_folder, exist_ok=True)

        if extension == 'webp':
            img.save(filepath, 'WEBP', quality=quality, optimize=True)
        else:
            img.save(filepath, 'JPEG', quality=quality, optimize=True)

        return f'/static/uploads/{filename}'
    except Exception as e:
        print(f'Error al comprimir imagen en memoria: {e}')
        return None


def comprimir_bytes(
    data,
    upload_folder,
    prefijo='img',
    max_dimension=MAX_DIMENSION,
    quality=QUALITY,
):
    """Comprime bytes de imagen en memoria y guarda WebP."""
    try:
        img = Image.open(io.BytesIO(data))
        return comprimir_pil_a_archivo(
            img, upload_folder, prefijo, max_dimension, quality
        )
    except Exception as e:
        print(f'Error al comprimir bytes: {e}')
        return None


def comprimir_y_guardar(
    file_storage,
    upload_folder,
    prefijo='img',
    max_dimension=MAX_DIMENSION,
    quality=QUALITY,
    formato='WEBP',
):
    """
    Comprime y guarda una imagen subida.
    Retorna la ruta web (/static/uploads/...) o None si falla.
    """
    if not file_storage or not getattr(file_storage, 'filename', ''):
        return None

    if not archivo_imagen_valido(file_storage.filename):
        return None

    nombre_original = secure_filename(file_storage.filename)
    if not nombre_original:
        return None

    try:
        img = Image.open(file_storage.stream)
        prefijo_base = prefijo or nombre_original.rsplit('.', 1)[0]
        return comprimir_pil_a_archivo(
            img, upload_folder, prefijo_base, max_dimension, quality, formato
        )
    except Exception as e:
        print(f'Error al comprimir imagen: {e}')
        return None


def descargar_y_comprimir_url(
    url,
    upload_folder,
    prefijo='img',
    max_dimension=MAX_DIMENSION,
    quality=QUALITY,
    timeout=6,
):
    """Descarga una URL externa, comprime en memoria y guarda localmente."""
    if not url or not url.startswith('http'):
        return None
    try:
        res = requests.get(
            url,
            timeout=timeout,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'
                )
            },
        )
        if res.status_code != 200 or not res.content:
            return None
        return comprimir_bytes(
            res.content, upload_folder, prefijo, max_dimension, quality
        )
    except Exception as e:
        print(f'Error descargando imagen {url[:60]}: {e}')
        return None


def procesar_tarea_imagen(tarea):
    """
    Ejecuta una tarea de imagen en un worker.
    tarea: dict con keys tipo ('buscar'|'url'|'archivo'), upload_folder, prefijo,
           y datos específicos.
    Retorna dict con producto_id (opcional) y url resultante.
    """
    upload_folder = tarea['upload_folder']
    prefijo = tarea.get('prefijo', 'img')
    producto_id = tarea.get('producto_id')

    url = None
    tipo = tarea.get('tipo')

    if tipo == 'url' and tarea.get('url'):
        url_externa = tarea['url']
        if url_externa.startswith('/static/'):
            url = url_externa
        elif url_externa.startswith('http'):
            url = descargar_y_comprimir_url(
                url_externa, upload_folder, prefijo=prefijo
            )
            if not url:
                url = url_externa
        else:
            url = url_externa

    elif tipo == 'buscar':
        from backend.image_search import obtener_url_imagen_automatica

        url_encontrada = obtener_url_imagen_automatica(
            nombre=tarea.get('nombre', ''),
            codigo_barras=tarea.get('codigo_barras'),
            descripcion=tarea.get('descripcion'),
            modo_rapido=True,
        )
        if url_encontrada and url_encontrada.startswith('http'):
            url = descargar_y_comprimir_url(
                url_encontrada, upload_folder, prefijo=prefijo
            ) or url_encontrada
        else:
            url = url_encontrada or '/static/images/default-product.webp'

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

"""Subida persistente de imágenes al bucket de Supabase Storage."""

from backend.images import comprimir_file_storage_a_bytes, validar_archivo_subida
from backend.supabase_client import SUPABASE_BUCKET_IMAGENES


class SupabaseUploadError(Exception):
    """Error al subir imagen a Supabase Storage."""


def subir_imagen_a_supabase(
    file_storage,
    supabase_client,
    prefijo='img',
    carpeta='comercios',
    max_dimension=800,
):
    """
    Comprime la imagen y la sube al bucket configurado.
    Retorna la URL pública para persistir en SQL (logo_url, imagen_url, banner_principal).
    """
    if not supabase_client:
        raise SupabaseUploadError(
            'Supabase Storage no está configurado. '
            'Define SUPABASE_URL y SUPABASE_KEY en el entorno.'
        )
    if not file_storage:
        raise SupabaseUploadError('No se recibió ningún archivo de imagen.')

    error_validacion = validar_archivo_subida(file_storage)
    if error_validacion:
        raise SupabaseUploadError(error_validacion)

    comprimido = comprimir_file_storage_a_bytes(
        file_storage, prefijo=prefijo, max_dimension=max_dimension
    )
    if not comprimido:
        raise SupabaseUploadError('No se pudo comprimir la imagen subida.')

    data, content_type, filename = comprimido
    ruta_storage = f'{carpeta.strip("/")}/{filename}'

    try:
        bucket = supabase_client.storage.from_(SUPABASE_BUCKET_IMAGENES)
        bucket.upload(
            ruta_storage,
            data,
            file_options={
                'content-type': content_type,
                'upsert': 'true',
                'cache-control': '3600',
            },
        )
        url_publica = bucket.get_public_url(ruta_storage)
        if not url_publica:
            raise SupabaseUploadError('Supabase no devolvió URL pública para la imagen.')
        return url_publica
    except SupabaseUploadError:
        raise
    except Exception as error:
        raise SupabaseUploadError(
            f'Error al subir imagen a Supabase ({SUPABASE_BUCKET_IMAGENES}): {error}'
        ) from error


def subir_bytes_a_supabase(
    data,
    supabase_client,
    filename,
    content_type='image/webp',
    carpeta='pagos',
):
    """Sube bytes comprimidos al bucket Supabase."""
    if not supabase_client:
        raise SupabaseUploadError(
            'Supabase Storage no está configurado. '
            'Define SUPABASE_URL y SUPABASE_KEY en el entorno.'
        )
    if not data:
        raise SupabaseUploadError('No hay datos de imagen para subir.')

    ruta_storage = f'{carpeta.strip("/")}/{filename}'
    try:
        bucket = supabase_client.storage.from_(SUPABASE_BUCKET_IMAGENES)
        bucket.upload(
            ruta_storage,
            data,
            file_options={
                'content-type': content_type,
                'upsert': 'true',
                'cache-control': '3600',
            },
        )
        url_publica = bucket.get_public_url(ruta_storage)
        if not url_publica:
            raise SupabaseUploadError('Supabase no devolvió URL pública para el comprobante.')
        return url_publica
    except SupabaseUploadError:
        raise
    except Exception as error:
        raise SupabaseUploadError(
            f'Error al subir comprobante a Supabase ({SUPABASE_BUCKET_IMAGENES}): {error}'
        ) from error


def subir_imagen_comercio_a_supabase(file_storage, supabase_client, prefijo='comercio'):
    """Compatibilidad: logos e imágenes de comercio."""
    return subir_imagen_a_supabase(
        file_storage, supabase_client, prefijo=prefijo, carpeta='comercios'
    )

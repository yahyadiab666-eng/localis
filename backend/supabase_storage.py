"""Subida persistente de imágenes al bucket de Supabase Storage."""

from backend.images import comprimir_file_storage_a_bytes
from backend.supabase_client import SUPABASE_BUCKET_IMAGENES


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
    if not supabase_client or not file_storage:
        return None

    comprimido = comprimir_file_storage_a_bytes(
        file_storage, prefijo=prefijo, max_dimension=max_dimension
    )
    if not comprimido:
        return None

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
        return bucket.get_public_url(ruta_storage)
    except Exception as error:
        print(f'Error al subir imagen a Supabase ({SUPABASE_BUCKET_IMAGENES}): {error}')
        return None


def subir_imagen_comercio_a_supabase(file_storage, supabase_client, prefijo='comercio'):
    """Compatibilidad: logos e imágenes de comercio."""
    return subir_imagen_a_supabase(
        file_storage, supabase_client, prefijo=prefijo, carpeta='comercios'
    )

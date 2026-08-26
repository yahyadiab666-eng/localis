"""Subida de imágenes al bucket Supabase Storage."""

from backend.images import comprimir_bytes_a_bytes, comprimir_file_storage_a_bytes, validar_archivo_subida
from backend.supabase_client import (
    SUPABASE_BUCKET_IMAGENES,
    construir_url_publica_storage,
    obtener_cliente_storage,
    storage_usa_service_role,
)
from backend.utils import url_imagen_supabase_valida


class SupabaseUploadError(Exception):
    """Error al subir imagen a Supabase Storage."""


def _validar_url_publica(url):
    valida = url_imagen_supabase_valida(url)
    if not valida:
        raise SupabaseUploadError(
            'Supabase no devolvió una URL pública válida del bucket. '
            'Verifica que el bucket sea público y que la ruta incluya '
            '/storage/v1/object/public/.'
        )
    return valida


def _mensaje_error_storage(error):
    try:
        from storage3.exceptions import StorageApiError

        if isinstance(error, StorageApiError):
            status_raw = getattr(error, 'status', None)
            try:
                status = int(status_raw)
            except (TypeError, ValueError):
                status = 0

            mensaje = str(getattr(error, 'message', None) or error.args[0] or error)

            if status == 403:
                if storage_usa_service_role():
                    return (
                        f'Supabase Storage rechazó la subida por permisos en el bucket '
                        f'"{SUPABASE_BUCKET_IMAGENES}". Revisa políticas RLS del bucket.'
                    )
                return (
                    f'Supabase Storage rechazó la subida por permisos (RLS). '
                    f'Configura SUPABASE_SERVICE_ROLE_KEY en el servidor (Settings → API → '
                    f'service_role) o añade una política INSERT en el bucket '
                    f'"{SUPABASE_BUCKET_IMAGENES}". Detalle: {mensaje}'
                )
            if status == 413:
                return 'La imagen supera el tamaño permitido por Supabase Storage.'
            if status == 404:
                return (
                    f'El bucket "{SUPABASE_BUCKET_IMAGENES}" no existe o no es accesible. '
                    f'Verifica SUPABASE_BUCKET_IMAGENES en el entorno.'
                )
            if status == 400:
                return f'Petición inválida a Supabase Storage: {mensaje}'
            return f'Error de Supabase Storage ({status}): {mensaje}'
    except ImportError:
        pass

    return f'Error al subir imagen a Supabase ({SUPABASE_BUCKET_IMAGENES}): {error}'


def _resolver_cliente_storage(supabase_client):
    cliente = supabase_client or obtener_cliente_storage()
    if not cliente:
        raise SupabaseUploadError(
            'Supabase Storage no está configurado. '
            'Define SUPABASE_URL y SUPABASE_KEY (o SUPABASE_SERVICE_ROLE_KEY) en el entorno.'
        )
    return cliente


def _url_publica_tras_subida(cliente, ruta_storage):
    """Construye y valida la URL pública canónica tras un upload exitoso."""
    url_canonica = construir_url_publica_storage(ruta_storage)
    try:
        url_sdk = cliente.storage.from_(SUPABASE_BUCKET_IMAGENES).get_public_url(ruta_storage)
        from backend.supabase_client import normalizar_url_publica_storage

        url_normalizada = normalizar_url_publica_storage(
            url_sdk,
            ruta=ruta_storage,
        )
    except Exception:
        url_normalizada = url_canonica

    return _validar_url_publica(url_normalizada or url_canonica)


def _subir_bytes_al_bucket(cliente, ruta_storage, data, content_type):
    bucket = cliente.storage.from_(SUPABASE_BUCKET_IMAGENES)
    try:
        bucket.upload(
            ruta_storage,
            data,
            file_options={
                'content-type': content_type,
                'upsert': 'true',
                'cache-control': '3600',
            },
        )
    except Exception as error:
        raise SupabaseUploadError(_mensaje_error_storage(error)) from error

    return _url_publica_tras_subida(cliente, ruta_storage)


def subir_imagen_a_supabase(
    file_storage,
    supabase_client=None,
    prefijo='img',
    carpeta='comercios',
    max_dimension=800,
):
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
    cliente = _resolver_cliente_storage(supabase_client)

    return _subir_bytes_al_bucket(cliente, ruta_storage, data, content_type)


def subir_bytes_a_supabase(
    data,
    filename,
    supabase_client=None,
    content_type='image/webp',
    carpeta='pagos',
):
    if not data:
        raise SupabaseUploadError('No hay datos de imagen para subir.')

    ruta_storage = f'{carpeta.strip("/")}/{filename}'
    cliente = _resolver_cliente_storage(supabase_client)
    return _subir_bytes_al_bucket(cliente, ruta_storage, data, content_type)

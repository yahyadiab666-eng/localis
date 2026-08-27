"""Subida de imágenes al bucket Supabase Storage con respaldo local."""

from backend.images import comprimir_bytes_a_bytes, comprimir_file_storage_a_bytes, validar_archivo_subida
from backend.local_storage import guardar_imagen_local
from backend.supabase_client import (
    SUPABASE_BUCKET_IMAGENES,
    construir_url_publica_storage,
    es_error_red_supabase,
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


def _error_permite_respaldo_local(error):
    """True ante fallos de red/DNS o Storage no configurado (no errores RLS/permisos)."""
    causa = error
    visitadas = set()
    while causa is not None and id(causa) not in visitadas:
        visitadas.add(id(causa))
        if es_error_red_supabase(causa):
            return True
        causa = getattr(causa, '__cause__', None) or getattr(causa, '__context__', None)

    if isinstance(error, SupabaseUploadError):
        mensaje = str(error).lower()
        if 'no está configurado' in mensaje or 'storage no configurado' in mensaje:
            return True
    return False


def _guardar_respaldo_local(data, filename, carpeta, motivo):
    url_local = guardar_imagen_local(data, filename, carpeta)
    print(
        f'[Localis Storage] Supabase no disponible ({motivo}); '
        f'imagen guardada en {url_local}'
    )
    return url_local


def _subir_bytes_con_respaldo(data, filename, content_type, carpeta, supabase_client=None):
    ruta_storage = f'{carpeta.strip("/")}/{filename}'
    cliente = supabase_client or obtener_cliente_storage()

    if not cliente:
        return _guardar_respaldo_local(
            data,
            filename,
            carpeta,
            'Storage no configurado',
        )

    try:
        return _subir_bytes_al_bucket(cliente, ruta_storage, data, content_type)
    except SupabaseUploadError as error:
        if _error_permite_respaldo_local(error):
            return _guardar_respaldo_local(
                data,
                filename,
                carpeta,
                str(error),
            )
        raise
    except Exception as error:
        if _error_permite_respaldo_local(error):
            return _guardar_respaldo_local(
                data,
                filename,
                carpeta,
                type(error).__name__,
            )
        raise SupabaseUploadError(_mensaje_error_storage(error)) from error


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
    return _subir_bytes_con_respaldo(
        data,
        filename,
        content_type,
        carpeta,
        supabase_client=supabase_client,
    )


def subir_bytes_a_supabase(
    data,
    filename,
    supabase_client=None,
    content_type='image/webp',
    carpeta='pagos',
):
    if not data:
        raise SupabaseUploadError('No hay datos de imagen para subir.')

    return _subir_bytes_con_respaldo(
        data,
        filename,
        content_type,
        carpeta,
        supabase_client=supabase_client,
    )

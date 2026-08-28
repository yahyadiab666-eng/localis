"""
Subida de archivos: Supabase Storage (primario) con respaldo local automático.

Arquitectura híbrida Localis:
- URLs externas / catálogo maestro → texto https en PostgreSQL.
- Archivos subidos → Supabase Storage; si falla la red o la config, static/uploads/.
"""

from backend.images import comprimir_file_storage_a_bytes, validar_archivo_subida
from backend.local_storage import guardar_bytes_local
from backend.supabase_client import (
    SUPABASE_BUCKET_IMAGENES,
    construir_url_publica_storage,
    es_error_red_supabase,
    obtener_cliente_storage,
    storage_usa_service_role,
)
from backend.utils import url_imagen_subida_storage_valida

LOG_PREFIX = '[Localis Storage]'


class SupabaseUploadError(Exception):
    """Error irrecuperable al procesar o persistir una subida de imagen."""


def _validar_url_publica_subida(url):
    valida = url_imagen_subida_storage_valida(url)
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


def _describir_error(error):
    if isinstance(error, SupabaseUploadError):
        return str(error)
    nombre = type(error).__name__
    if es_error_red_supabase(error):
        return f'red/DNS ({nombre})'
    return f'{nombre}: {error}'


def _registrar_fallo_supabase(error, carpeta, filename):
    print(
        f'{LOG_PREFIX} Supabase no disponible ({_describir_error(error)}). '
        f'Respaldo local -> static/uploads/{carpeta.strip("/")}/{filename}'
    )


def _registrar_exito_supabase(ruta_storage):
    print(f'{LOG_PREFIX} Supabase OK: {SUPABASE_BUCKET_IMAGENES}/{ruta_storage}')


def _registrar_exito_local(url_local):
    print(f'{LOG_PREFIX} Respaldo local OK: {url_local}')


def _resolver_cliente_storage(supabase_client):
    return supabase_client or obtener_cliente_storage()


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

    return _validar_url_publica_subida(url_normalizada or url_canonica)


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


def _persistir_con_respaldo(data, filename, content_type, carpeta, supabase_client=None):
    """Intenta Supabase; ante cualquier fallo operativo usa static/uploads/."""
    ruta_storage = f'{carpeta.strip("/")}/{filename}'
    cliente = _resolver_cliente_storage(supabase_client)

    if cliente:
        try:
            url = _subir_bytes_al_bucket(cliente, ruta_storage, data, content_type)
            _registrar_exito_supabase(ruta_storage)
            return url
        except Exception as error:
            _registrar_fallo_supabase(error, carpeta, filename)
    else:
        print(
            f'{LOG_PREFIX} Supabase Storage no configurado. '
            f'Usando respaldo local -> static/uploads/{carpeta.strip("/")}/{filename}'
        )

    try:
        url_local = guardar_bytes_local(data, filename, carpeta)
        _registrar_exito_local(url_local)
        return url_local
    except Exception as error:
        print(
            f'{LOG_PREFIX} ERROR CRÍTICO: respaldo local falló '
            f'({type(error).__name__}: {error})'
        )
        raise SupabaseUploadError(
            f'No se pudo guardar la imagen (Supabase ni disco local): {error}'
        ) from error


def subir_imagen_con_respaldo(
    file_storage,
    supabase_client=None,
    prefijo='img',
    carpeta='comercios',
    max_dimension=800,
):
    """Subida manual: Supabase primario, static/uploads/ si la red o Storage fallan."""
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
    return _persistir_con_respaldo(
        data,
        filename,
        content_type,
        carpeta,
        supabase_client=supabase_client,
    )


def subir_bytes_con_respaldo(
    data,
    filename,
    supabase_client=None,
    content_type='image/webp',
    carpeta='pagos',
):
    """Subida de bytes (comprobantes): Supabase primario, respaldo local automático."""
    if not data:
        raise SupabaseUploadError('No hay datos de imagen para subir.')

    return _persistir_con_respaldo(
        data,
        filename,
        content_type,
        carpeta,
        supabase_client=supabase_client,
    )


def subir_imagen_a_supabase(
    file_storage,
    supabase_client=None,
    prefijo='img',
    carpeta='comercios',
    max_dimension=800,
):
    """Alias retrocompatible → subir_imagen_con_respaldo."""
    return subir_imagen_con_respaldo(
        file_storage,
        supabase_client=supabase_client,
        prefijo=prefijo,
        carpeta=carpeta,
        max_dimension=max_dimension,
    )


def subir_bytes_a_supabase(
    data,
    filename,
    supabase_client=None,
    content_type='image/webp',
    carpeta='pagos',
):
    """Alias retrocompatible → subir_bytes_con_respaldo."""
    return subir_bytes_con_respaldo(
        data,
        filename=filename,
        supabase_client=supabase_client,
        content_type=content_type,
        carpeta=carpeta,
    )

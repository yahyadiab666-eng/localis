"""
Subida de archivos a Supabase Storage (obligatorio en produccion).

- URLs externas / catalogo maestro -> texto https en PostgreSQL.
- Archivos subidos -> Supabase Storage; si falla, SupabaseUploadError explicito (sin disco local).
"""

from backend.images import comprimir_file_storage_a_bytes, validar_archivo_subida
from backend.supabase_client import (
    SUPABASE_BUCKET_IMAGENES,
    SUPABASE_KEY,
    SUPABASE_URL,
    SUPABASE_URL_VALIDA,
    construir_url_publica_storage,
    es_error_red_supabase,
    obtener_cliente_storage,
    storage_usa_service_role,
)
from backend.utils import url_imagen_subida_storage_valida

LOG_PREFIX = '[Localis Storage]'


class SupabaseUploadError(Exception):
    """Error al subir o persistir una imagen en Supabase Storage."""


def _validar_url_publica_subida(url):
    valida = url_imagen_subida_storage_valida(url)
    if not valida:
        raise SupabaseUploadError(
            'La URL canonica de Supabase Storage no paso la validacion interna. '
            'Verifica SUPABASE_URL y que el bucket sea publico '
            '(/storage/v1/object/public/).'
        )
    return valida


def _mensaje_cliente_no_configurado() -> str:
    if not SUPABASE_URL_VALIDA or not SUPABASE_URL:
        return (
            'Supabase Storage no disponible: SUPABASE_URL ausente o invalida. '
            'Define https://TU_REF.supabase.co en el entorno (Render).'
        )
    if not storage_usa_service_role() and not SUPABASE_KEY:
        return (
            'Supabase Storage no disponible: configura SUPABASE_SERVICE_ROLE_KEY '
            '(recomendado para subidas server-side) o SUPABASE_KEY en el entorno.'
        )
    return (
        'Supabase Storage no disponible: no se pudo crear el cliente SDK. '
        'Revisa SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY y los logs de arranque '
        '([Localis Supabase]).'
    )


def _mensaje_error_storage(error):
    if isinstance(error, SupabaseUploadError):
        return str(error)

    if es_error_red_supabase(error):
        return (
            'No se pudo conectar con Supabase Storage (error de red o DNS). '
            'Verifica SUPABASE_URL, que el host resuelva en DNS y que el servidor '
            'tenga salida HTTPS hacia *.supabase.co:443. '
            f'Detalle: {type(error).__name__}: {error}'
        )

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
                        f'Supabase Storage rechazo la subida (HTTP 403) en el bucket '
                        f'"{SUPABASE_BUCKET_IMAGENES}". Revisa politicas RLS/politicas '
                        f'del bucket en Supabase Dashboard -> Storage.'
                        f' Detalle: {mensaje}'
                    )
                return (
                    f'Supabase Storage rechazo la subida por permisos (HTTP 403 / RLS). '
                    f'Configura SUPABASE_SERVICE_ROLE_KEY en Render (Settings -> API -> '
                    f'service_role) o anade una politica INSERT en el bucket '
                    f'"{SUPABASE_BUCKET_IMAGENES}". Detalle: {mensaje}'
                )
            if status == 413:
                return 'La imagen supera el tamano permitido por Supabase Storage (HTTP 413).'
            if status == 404:
                return (
                    f'El bucket "{SUPABASE_BUCKET_IMAGENES}" no existe o no es accesible '
                    f'(HTTP 404). Verifica SUPABASE_BUCKET_IMAGENES en el entorno.'
                )
            if status == 400:
                return f'Peticion invalida a Supabase Storage (HTTP 400): {mensaje}'
            return f'Error de Supabase Storage (HTTP {status}): {mensaje}'
    except ImportError:
        pass

    return f'Error al subir imagen a Supabase ({SUPABASE_BUCKET_IMAGENES}): {error}'


def _describir_error(error):
    if isinstance(error, SupabaseUploadError):
        return str(error)
    return _mensaje_error_storage(error)


def _registrar_fallo_supabase(error, ruta_storage):
    print(
        f'{LOG_PREFIX} FALLO subida '
        f'({SUPABASE_BUCKET_IMAGENES}/{ruta_storage}): {_describir_error(error)}'
    )


def _registrar_exito_supabase(ruta_storage, url_publica):
    print(
        f'{LOG_PREFIX} Supabase OK: {SUPABASE_BUCKET_IMAGENES}/{ruta_storage} -> {url_publica}'
    )


def _resolver_cliente_storage(supabase_client):
    return supabase_client or obtener_cliente_storage()


def _url_publica_tras_subida(ruta_storage):
    """Solo se llama despues de un upload exitoso. Nunca anticipa la URL publica."""
    url_canonica = construir_url_publica_storage(ruta_storage)
    return _validar_url_publica_subida(url_canonica)


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

    try:
        return _url_publica_tras_subida(ruta_storage)
    except SupabaseUploadError:
        raise
    except Exception as error:
        raise SupabaseUploadError(
            'La imagen se subio a Storage pero no se pudo construir la URL publica. '
            f'Detalle: {type(error).__name__}: {error}'
        ) from error


def _persistir_en_supabase(data, filename, content_type, carpeta, supabase_client=None):
    """Sube a Supabase Storage o lanza SupabaseUploadError; sin respaldo local."""
    ruta_storage = f'{carpeta.strip("/")}/{filename}'
    cliente = _resolver_cliente_storage(supabase_client)

    if not cliente:
        mensaje = _mensaje_cliente_no_configurado()
        print(f'{LOG_PREFIX} {mensaje}')
        raise SupabaseUploadError(mensaje)

    try:
        url = _subir_bytes_al_bucket(cliente, ruta_storage, data, content_type)
    except SupabaseUploadError as error:
        _registrar_fallo_supabase(error, ruta_storage)
        raise
    except Exception as error:
        upload_error = SupabaseUploadError(_mensaje_error_storage(error))
        _registrar_fallo_supabase(upload_error, ruta_storage)
        raise upload_error from error

    _registrar_exito_supabase(ruta_storage, url)
    return url


# Alias interno usado por scripts de prueba
_persistir_con_respaldo = _persistir_en_supabase


def subir_imagen_con_respaldo(
    file_storage,
    supabase_client=None,
    prefijo='img',
    carpeta='comercios',
    max_dimension=800,
):
    """Subida manual a Supabase Storage (sin respaldo en disco local)."""
    if not file_storage:
        raise SupabaseUploadError('No se recibio ningun archivo de imagen.')

    error_validacion = validar_archivo_subida(file_storage)
    if error_validacion:
        raise SupabaseUploadError(error_validacion)

    comprimido = comprimir_file_storage_a_bytes(
        file_storage, prefijo=prefijo, max_dimension=max_dimension
    )
    if not comprimido:
        raise SupabaseUploadError('No se pudo comprimir la imagen subida.')

    data, content_type, filename = comprimido
    return _persistir_en_supabase(
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
    """Subida de bytes (comprobantes) a Supabase Storage."""
    if not data:
        raise SupabaseUploadError('No hay datos de imagen para subir.')

    return _persistir_en_supabase(
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
    """Alias retrocompatible -> subir_imagen_con_respaldo."""
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
    """Alias retrocompatible -> subir_bytes_con_respaldo."""
    return subir_bytes_con_respaldo(
        data,
        filename=filename,
        supabase_client=supabase_client,
        content_type=content_type,
        carpeta=carpeta,
    )

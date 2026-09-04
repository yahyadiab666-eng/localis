"""
Subida de archivos a Supabase Storage (modo hibrido).

- Con SUPABASE_SERVICE_ROLE_KEY valida: sube al bucket imagenes.
- Sin llave o ante 403/red: comprime, guarda en static/uploads/ y no bloquea.
- En segundo plano intenta copiar la foto local al bucket cuando hay service_role.
"""

from urllib.parse import quote
import threading

import httpx

from backend.images import (
    ImageProcessingError,
    comprimir_file_storage_a_bytes,
    validar_archivo_subida,
)
from backend.supabase_client import (
    SUPABASE_BUCKET_IMAGENES,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_URL,
    SUPABASE_URL_VALIDA,
    auditar_claves_supabase,
    clave_es_service_role,
    construir_url_publica_storage,
    es_error_red_supabase,
    headers_storage_service_role,
    obtener_cliente_storage,
    supabase as _cliente_anon,
    storage_usa_service_role,
    _rol_claim_jwt,
)
from backend.utils import url_imagen_subida_storage_valida

LOG_PREFIX = '[Localis Storage]'
AVISO_HIBRIDO_USUARIO = (
    'La foto no se pudo guardar en Storage. El catalogo seguira con la imagen '
    'oficial o la que ya tenia el producto. Revisa SUPABASE_SERVICE_ROLE_KEY.'
)


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


def url_publica_storage_accesible(url, timeout=6.0):
    """True si el objeto público existe y el cuerpo es una imagen real."""
    valida = url_imagen_subida_storage_valida(url)
    if not valida:
        return False
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as http:
            respuesta = http.get(valida)
        if respuesta.status_code != 200:
            return False
        tipo = (respuesta.headers.get('content-type') or '').lower()
        if not tipo.startswith('image/'):
            return False
        return len(respuesta.content or b'') > 200
    except Exception:
        return False


def _mensaje_cliente_no_configurado() -> str:
    if not SUPABASE_URL_VALIDA or not SUPABASE_URL:
        return (
            'Supabase Storage no disponible: SUPABASE_URL ausente o invalida. '
            'Define https://TU_REF.supabase.co en el entorno (Render).'
        )
    if not SUPABASE_SERVICE_ROLE_KEY:
        auditoria = auditar_claves_supabase()
        if auditoria.get('service_rechazada_al_iniciar') or auditoria.get(
            'service_jwt_role'
        ) == 'anon':
            return (
                'Supabase Storage no disponible: SUPABASE_SERVICE_ROLE_KEY tiene '
                f"jwt_role={auditoria.get('service_jwt_role')!r} (se esperaba "
                'service_role). En Render reemplaza esa variable por la llave secreta '
                'Dashboard -> Settings -> API -> service_role. No uses la anon.'
            )
        return (
            'Supabase Storage no disponible: falta SUPABASE_SERVICE_ROLE_KEY. '
            'En el backend las subidas usan solo la llave service_role '
            '(Dashboard -> Settings -> API -> service_role), no la clave anon/publica.'
        )
    if not storage_usa_service_role():
        return (
            'Supabase Storage no disponible: no se pudo crear el cliente con '
            'SUPABASE_SERVICE_ROLE_KEY. Revisa que la llave sea la service_role '
            f'completa y los logs de arranque ({LOG_PREFIX}).'
        )
    return (
        'Supabase Storage no disponible: no hay cliente service_role. '
        f'Revisa SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY y {LOG_PREFIX}.'
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
                rol = _rol_claim_jwt(SUPABASE_SERVICE_ROLE_KEY) or 'ausente'
                return (
                    f'Supabase Storage rechazo la subida (HTTP 403) en el bucket '
                    f'"{SUPABASE_BUCKET_IMAGENES}" (RLS en storage.objects). '
                    f'El backend debe usar service_role; jwt_role detectado={rol}. '
                    f'Revisa SUPABASE_SERVICE_ROLE_KEY (no SUPABASE_KEY/anon) y las '
                    f'politicas INSERT/SELECT/UPDATE del bucket. Detalle: {mensaje}'
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


def _mensaje_por_status_http(status, mensaje):
    dummy = type(
        'StorageHttpError',
        (),
        {'status': status, 'message': mensaje, 'args': (mensaje,)},
    )()
    texto = _mensaje_error_storage(dummy)
    if texto.startswith('Error al subir imagen a Supabase'):
        if status == 403:
            rol = _rol_claim_jwt(SUPABASE_SERVICE_ROLE_KEY) or 'ausente'
            return (
                f'Supabase Storage rechazo la subida (HTTP 403) en el bucket '
                f'"{SUPABASE_BUCKET_IMAGENES}" (RLS en storage.objects). '
                f'El backend debe usar service_role; jwt_role detectado={rol}. '
                f'Revisa SUPABASE_SERVICE_ROLE_KEY (no SUPABASE_KEY/anon) y las '
                f'politicas INSERT/SELECT/UPDATE del bucket. Detalle: {mensaje}'
            )
        return f'Error de Supabase Storage (HTTP {status}): {mensaje}'
    return texto


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
    """Nunca el cliente anon. Un mock de tests se acepta; si no, solo service_role."""
    if supabase_client is not None and supabase_client is _cliente_anon:
        print(
            f'{LOG_PREFIX} Se ignoro el cliente anon/publica; las subidas usan service_role.'
        )
        supabase_client = None
    if supabase_client is not None:
        return supabase_client
    return obtener_cliente_storage()


def _url_objeto_storage(ruta_storage):
    partes = [p for p in str(ruta_storage).replace('\\', '/').split('/') if p]
    ruta_enc = '/'.join(quote(p, safe='') for p in partes)
    bucket = quote((SUPABASE_BUCKET_IMAGENES or 'imagenes').strip('/'), safe='')
    return f'{SUPABASE_URL.rstrip("/")}/storage/v1/object/{bucket}/{ruta_enc}'


def _timeout_upload(*, diferido=False):
    if diferido:
        return httpx.Timeout(connect=5.0, read=25.0, write=25.0, pool=5.0)
    return httpx.Timeout(connect=2.0, read=8.0, write=8.0, pool=2.0)


def _url_publica_tras_subida(ruta_storage):
    """Solo se llama despues de un upload exitoso. Nunca anticipa la URL publica."""
    url_canonica = construir_url_publica_storage(ruta_storage)
    return _validar_url_publica_subida(url_canonica)


def _subir_bytes_al_bucket(cliente, ruta_storage, data, content_type, *, diferido=False):
    """POST a Storage con Authorization service_role. No usa el SDK anon."""
    del cliente
    if not clave_es_service_role(SUPABASE_SERVICE_ROLE_KEY):
        raise SupabaseUploadError(_mensaje_cliente_no_configurado())
    try:
        headers = headers_storage_service_role(
            {
                'Content-Type': content_type or 'application/octet-stream',
                'x-upsert': 'true',
            }
        )
    except RuntimeError as error:
        raise SupabaseUploadError(str(error)) from error

    url = _url_objeto_storage(ruta_storage)
    try:
        with httpx.Client(timeout=_timeout_upload(diferido=diferido), follow_redirects=True) as http:
            respuesta = http.post(url, content=data, headers=headers)
            if respuesta.status_code == 409:
                respuesta = http.put(url, content=data, headers=headers)
    except Exception as error:
        raise SupabaseUploadError(_mensaje_error_storage(error)) from error

    if respuesta.status_code not in (200, 201):
        detalle = (respuesta.text or respuesta.reason_phrase or '')[:500]
        raise SupabaseUploadError(_mensaje_por_status_http(respuesta.status_code, detalle))

    try:
        return _url_publica_tras_subida(ruta_storage)
    except SupabaseUploadError:
        raise
    except Exception as error:
        raise SupabaseUploadError(
            'La imagen se subio a Storage pero no se pudo construir la URL publica. '
            f'Detalle: {type(error).__name__}: {error}'
        ) from error


def _persistir_en_supabase(
    data,
    filename,
    content_type,
    carpeta,
    supabase_client=None,
    *,
    diferido=False,
):
    """Sube a Supabase Storage o lanza SupabaseUploadError; sin respaldo local."""
    ruta_storage = f'{carpeta.strip("/")}/{filename}'
    if not clave_es_service_role(SUPABASE_SERVICE_ROLE_KEY):
        mensaje = _mensaje_cliente_no_configurado()
        print(f'{LOG_PREFIX} {mensaje}')
        raise SupabaseUploadError(mensaje)

    cliente = _resolver_cliente_storage(supabase_client)

    try:
        url = _subir_bytes_al_bucket(
            cliente, ruta_storage, data, content_type, diferido=diferido
        )
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


def _guardar_respaldo_local(data, filename, carpeta):
    from backend.uploads_locales import guardar_bytes_upload

    return guardar_bytes_upload(data, filename, carpeta=carpeta)


def intentar_subir_imagen(
    file_storage,
    supabase_client=None,
    prefijo='img',
    carpeta='comercios',
    max_dimension=800,
):
    """
    Comprime el archivo, intenta Storage y si falla deja la foto en
    static/uploads/. Retorna (url, aviso). Nunca lanza hacia la ruta HTTP.
    """
    if not file_storage or not getattr(file_storage, 'filename', ''):
        return None, None
    try:
        error_validacion = validar_archivo_subida(file_storage)
        if error_validacion:
            return None, error_validacion
        comprimido = comprimir_file_storage_a_bytes(
            file_storage,
            prefijo=prefijo,
            max_dimension=max_dimension,
            lienzo_cuadrado=(carpeta == 'productos'),
        )
    except ImageProcessingError as error:
        print(f'{LOG_PREFIX} compresion omitida: {error}')
        return None, str(error)
    except Exception as error:
        print(
            f'{LOG_PREFIX} modo hibrido, error inesperado: '
            f'{type(error).__name__}: {error}'
        )
        return None, AVISO_HIBRIDO_USUARIO

    data, content_type, filename = comprimido
    url_local = None
    if carpeta == 'productos':
        url_local = _guardar_respaldo_local(data, filename, carpeta)
        if url_local:
            # El producto nace con foto visible. Storage se sincroniza
            # en segundo plano tras el INSERT (programar_sincronizacion_storage).
            return url_local, None
    try:
        url = _persistir_en_supabase(
            data,
            filename,
            content_type,
            carpeta,
            supabase_client=supabase_client,
        )
        return url or url_local, None
    except SupabaseUploadError as error:
        print(f'{LOG_PREFIX} modo hibrido, subida omitida: {error}')
        if url_local:
            return url_local, None
        url_local = _guardar_respaldo_local(data, filename, carpeta)
        if url_local:
            return url_local, None
        return None, AVISO_HIBRIDO_USUARIO
    except Exception as error:
        print(
            f'{LOG_PREFIX} modo hibrido, error inesperado: '
            f'{type(error).__name__}: {error}'
        )
        if url_local:
            return url_local, None
        url_local = _guardar_respaldo_local(data, filename, carpeta)
        if url_local:
            return url_local, None
        return None, AVISO_HIBRIDO_USUARIO


def programar_sincronizacion_storage(producto_id, ruta_local, carpeta='productos'):
    """Copia en segundo plano una foto local al bucket y actualiza productos.imagen_url."""
    from backend.uploads_locales import leer_bytes_upload, url_upload_local_valida
    from backend.utils import url_imagen_subida_storage_valida

    if not producto_id or not url_upload_local_valida(ruta_local):
        return
    if not clave_es_service_role(SUPABASE_SERVICE_ROLE_KEY):
        return

    def _run():
        try:
            data, filename, content_type = leer_bytes_upload(ruta_local)
            if not data:
                return
            url = _persistir_en_supabase(
                data,
                filename,
                content_type,
                carpeta,
                diferido=True,
            )
            if not url_imagen_subida_storage_valida(url):
                return
            from backend.db import get_db_connection

            with get_db_connection() as conexion:
                cursor = conexion.cursor()
                cursor.execute(
                    """
                    UPDATE productos
                    SET imagen_url = ?
                    WHERE id = ? AND imagen_url = ?
                    """,
                    (url, int(producto_id), ruta_local),
                )
                conexion.commit()
            print(
                f'{LOG_PREFIX} sync diferida producto={producto_id} -> {url}'
            )
        except Exception as error:
            print(
                f'{LOG_PREFIX} sync diferida omitida producto={producto_id}: '
                f'{type(error).__name__}: {error}'
            )

    hilo = threading.Thread(
        target=_run,
        daemon=True,
        name=f'localis-sync-storage-{producto_id}',
    )
    hilo.start()


def intentar_subir_bytes(
    data,
    filename,
    supabase_client=None,
    content_type='image/webp',
    carpeta='pagos',
):
    """Igual que intentar_subir_imagen para bytes (comprobantes). No lanza."""
    if not data:
        return None, AVISO_HIBRIDO_USUARIO
    try:
        url = subir_bytes_con_respaldo(
            data,
            filename=filename,
            supabase_client=supabase_client,
            content_type=content_type,
            carpeta=carpeta,
        )
        return url, None
    except SupabaseUploadError as error:
        print(f'{LOG_PREFIX} modo hibrido, bytes omitidos: {error}')
        return None, AVISO_HIBRIDO_USUARIO
    except Exception as error:
        print(
            f'{LOG_PREFIX} modo hibrido, error inesperado bytes: '
            f'{type(error).__name__}: {error}'
        )
        return None, AVISO_HIBRIDO_USUARIO


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

    try:
        comprimido = comprimir_file_storage_a_bytes(
            file_storage,
            prefijo=prefijo,
            max_dimension=max_dimension,
            lienzo_cuadrado=(carpeta == 'productos'),
        )
    except ImageProcessingError as error:
        raise SupabaseUploadError(str(error)) from error

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


def asegurar_politicas_bucket_imagenes():
    """
    Políticas RLS del bucket imagenes: lectura pública + escritura service_role.
    Idempotente. El JWT service_role bypasea RLS; esto cubre upsert (INSERT+SELECT+UPDATE).
    """
    from backend.db import get_db_connection, using_postgres

    if not using_postgres():
        return False
    sql = """
    DO $pol$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'storage' AND tablename = 'objects'
          AND policyname = 'localis_public_select_imagenes'
      ) THEN
        CREATE POLICY localis_public_select_imagenes
          ON storage.objects FOR SELECT
          TO public
          USING (bucket_id = 'imagenes');
      END IF;
      IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'storage' AND tablename = 'objects'
          AND policyname = 'localis_service_select_imagenes'
      ) THEN
        CREATE POLICY localis_service_select_imagenes
          ON storage.objects FOR SELECT
          TO service_role
          USING (bucket_id = 'imagenes');
      END IF;
      IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'storage' AND tablename = 'objects'
          AND policyname = 'localis_service_insert_imagenes'
      ) THEN
        CREATE POLICY localis_service_insert_imagenes
          ON storage.objects FOR INSERT
          TO service_role
          WITH CHECK (bucket_id = 'imagenes');
      END IF;
      IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'storage' AND tablename = 'objects'
          AND policyname = 'localis_service_update_imagenes'
      ) THEN
        CREATE POLICY localis_service_update_imagenes
          ON storage.objects FOR UPDATE
          TO service_role
          USING (bucket_id = 'imagenes')
          WITH CHECK (bucket_id = 'imagenes');
      END IF;
    END
    $pol$;
    """
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(sql)
            conexion.commit()
        print(f'{LOG_PREFIX} Politicas RLS del bucket imagenes verificadas (service_role + SELECT publico).')
        return True
    except Exception as error:
        print(
            f'{LOG_PREFIX} No se pudieron asegurar politicas RLS: '
            f'{type(error).__name__}: {error}'
        )
        return False

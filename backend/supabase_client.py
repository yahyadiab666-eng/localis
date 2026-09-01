"""Cliente Supabase Storage + helpers de URL pública."""

from __future__ import annotations

import base64
import json
import os
import re
import socket
from typing import Optional
from urllib.parse import quote, urlparse

import httpx
from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from backend.supabase_connectivity import (
    ResultadoUrlSupabase,
    SUPABASE_URL_POR_DEFECTO,
    _limpiar_texto_env,
    imprimir_alerta_supabase_url,
    leer_supabase_url_entorno,
    sanitizar_url_supabase,
)

_STORAGE_PUBLIC_PREFIX = '/storage/v1/object/public/'
_SUBASE_TYPO_RE = re.compile(r'/subase/', re.IGNORECASE)
SUPABASE_HTTP_TIMEOUT = 10.0
SUPABASE_CONNECT_TIMEOUT = 5.0


def _rol_claim_jwt(token):
    """Lee el claim `role` de un JWT de Supabase sin verificar firma (solo diagnóstico)."""
    texto = _limpiar_valor_env(token)
    if not texto or texto.count('.') < 2:
        return None
    try:
        payload = texto.split('.', 2)[1]
        payload += '=' * (-len(payload) % 4)
        datos = json.loads(base64.urlsafe_b64decode(payload.encode('ascii')))
        rol = datos.get('role')
        return str(rol) if rol else None
    except Exception:
        return None


def clave_es_service_role(token):
    """True si la clave es service_role (JWT legacy) o secret key (sb_secret_…)."""
    texto = _limpiar_valor_env(token)
    if not texto:
        return False
    if texto.startswith('sb_secret_'):
        return True
    if texto.startswith(('sb_publishable_', 'sb_anon_')):
        return False
    return _rol_claim_jwt(texto) == 'service_role'


def _limpiar_valor_env(valor):
    """Limpia claves/env auxiliares (comillas y saltos de linea al copiar en Render)."""
    return _limpiar_texto_env(valor)


ENV_SERVICE_ROLE = 'SUPABASE_SERVICE_ROLE_KEY'
_ALIAS_SERVICE_ROLE = (
    'SUPABASE_SERVICE_ROLE_KEY',
    'SUPABASE_SECRET_KEY',
    'SUPABASE_SERVICE_ROL_KEY',
)
_SERVICE_ROLE_NOMBRE_TYPO = False
_SERVICE_ROLE_NOMBRE_LEIDO = ENV_SERVICE_ROLE


def _leer_clave_service_role_entorno():
    """Lee la llave de servicio. Nombre canónico: SUPABASE_SERVICE_ROLE_KEY."""
    global _SERVICE_ROLE_NOMBRE_TYPO, _SERVICE_ROLE_NOMBRE_LEIDO
    for nombre in _ALIAS_SERVICE_ROLE:
        valor = _limpiar_valor_env(os.getenv(nombre))
        if not valor:
            continue
        _SERVICE_ROLE_NOMBRE_LEIDO = nombre
        if nombre != ENV_SERVICE_ROLE:
            _SERVICE_ROLE_NOMBRE_TYPO = True
            print(
                f'[Localis Supabase] ADVERTENCIA: se leyo {nombre}. '
                f'Renombrala a {ENV_SERVICE_ROLE} en .env y en Render.'
            )
        return valor
    return ''


def _extraer_host_desde_url(url):
    """Host sin puerto (logs y comparación de URLs de Storage)."""
    if not url:
        return ''
    return (urlparse(url.strip()).hostname or '').lower()


def _mascara_secreto(valor, visible=4):
    texto = _limpiar_valor_env(valor)
    if not texto:
        return 'ausente'
    if len(texto) <= visible * 2:
        return 'presente'
    return f'{texto[:visible]}…{texto[-visible:]}'


def _timeout_http_supabase() -> httpx.Timeout:
    """Timeouts recomendados para entornos cloud (Render): fail-fast en DNS/connect."""
    return httpx.Timeout(
        connect=SUPABASE_CONNECT_TIMEOUT,
        read=SUPABASE_HTTP_TIMEOUT,
        write=SUPABASE_HTTP_TIMEOUT,
        pool=SUPABASE_CONNECT_TIMEOUT,
    )


def _opciones_cliente_supabase() -> SyncClientOptions:
    """
    Opciones del SDK supabase-py 2.x.

    No inyectamos un httpx.Client compartido: el SDK lo reutiliza entre PostgREST,
    Storage y Auth y puede mutar base_url entre servicios (bug conocido en versiones
    recientes). Cada subservicio crea su propio cliente httpx con estos timeouts.
    """
    timeout = _timeout_http_supabase()
    return SyncClientOptions(
        postgrest_client_timeout=timeout,
        storage_client_timeout=int(SUPABASE_HTTP_TIMEOUT),
        function_client_timeout=int(SUPABASE_HTTP_TIMEOUT),
        httpx_client=None,
    )


def _crear_cliente_supabase(api_key, etiqueta='anon'):
    if not SUPABASE_URL or not api_key:
        return None
    try:
        return create_client(
            SUPABASE_URL,
            api_key,
            options=_opciones_cliente_supabase(),
        )
    except Exception as error:
        host = _extraer_host_desde_url(SUPABASE_URL) or SUPABASE_URL
        print(
            f'WARNING Localis Supabase ({etiqueta}): no se pudo inicializar cliente '
            f'para host {host}: {type(error).__name__}: {error}'
        )
        return None


def _aplicar_config_url(resultado: ResultadoUrlSupabase, url_raw: str | None = None) -> None:
    imprimir_alerta_supabase_url(resultado, url_raw)


def _imprimir_estado_supabase():
    """Log legible al arrancar; no imprime claves completas."""
    prefijo = '[Localis Supabase]'

    if not SUPABASE_URL and not SUPABASE_SERVICE_ROLE_KEY:
        print(
            f'{prefijo} No configurado (SUPABASE_URL/SUPABASE_SERVICE_ROLE_KEY ausentes). '
            'Las subidas a Storage fallaran hasta configurar el service_role.'
        )
        return

    if not SUPABASE_URL:
        print(
            f'{prefijo} SUPABASE_URL vacia o invalida tras sanitizacion. '
            'Las subidas a Storage fallaran.'
        )
        return

    host = _extraer_host_desde_url(SUPABASE_URL) or SUPABASE_URL
    raw_host = _extraer_host_desde_url(_limpiar_valor_env(os.getenv('SUPABASE_URL')))
    if raw_host and raw_host != host:
        print(f'{prefijo} Host tras sanitización: {raw_host} -> {host}')
    raw_limpia = _limpiar_valor_env(_URL_RAW).rstrip('/')
    if raw_limpia and SUPABASE_URL and raw_limpia != SUPABASE_URL:
        print(f'{prefijo} URL sanitizada para el cliente: {SUPABASE_URL}')
    print(
        f'{prefijo} URL={SUPABASE_URL} host={host} | '
        f'SUPABASE_KEY={_mascara_secreto(SUPABASE_KEY)} | '
        f'SERVICE_ROLE={_mascara_secreto(SUPABASE_SERVICE_ROLE_KEY)}'
    )

    if supabase_storage_admin:
        print(
            f'{prefijo} Cliente Storage inicializado con service_role '
            f'(bucket={SUPABASE_BUCKET_IMAGENES}, jwt_role={_rol_claim_jwt(SUPABASE_SERVICE_ROLE_KEY) or "sb_secret"}).'
        )
    else:
        if not SUPABASE_SERVICE_ROLE_KEY:
            clave_msg = 'SUPABASE_SERVICE_ROLE_KEY ausente'
        elif not SUPABASE_URL_VALIDA:
            clave_msg = 'SUPABASE_URL invalida'
        else:
            clave_msg = 'fallo al crear cliente service_role'
        print(
            f'{prefijo} Storage no disponible ({clave_msg}). '
            'Modo hibrido: el catalogo usara fotos oficiales; las subidas a Storage se omiten.'
        )
        if _SERVICE_ROLE_RECHAZADA:
            print(
                f'{prefijo} ADVERTENCIA: {ENV_SERVICE_ROLE} fue rechazada '
                f'(jwt_role={_SERVICE_ROLE_ROL_CRUDO or "desconocido"}). '
                f'En Render usa Dashboard -> Settings -> API -> service_role. '
                f'Nombre exacto: {ENV_SERVICE_ROLE}.'
            )
    if supabase:
        print(f'{prefijo} Cliente API (anon/publishable) disponible para PostgREST.')
    elif SUPABASE_KEY:
        print(f'{prefijo} Cliente API anon no se pudo crear; PostgREST HTTP deshabilitado.')


_URL_RAW = leer_supabase_url_entorno()
_URL_INFO = sanitizar_url_supabase(_URL_RAW, database_url=os.getenv('DATABASE_URL'))
if not _URL_INFO.url:
    _URL_INFO.url = SUPABASE_URL_POR_DEFECTO
    _URL_INFO.host = SUPABASE_URL_POR_DEFECTO.split('://', 1)[-1]
    _URL_INFO.valida = True
    _URL_INFO.errores.clear()
_aplicar_config_url(_URL_INFO, _URL_RAW)

SUPABASE_URL = _URL_INFO.url or SUPABASE_URL_POR_DEFECTO
SUPABASE_URL_VALIDA = bool(SUPABASE_URL)
SUPABASE_URL_ADVERTENCIAS = list(_URL_INFO.advertencias)
SUPABASE_URL_ERRORES = list(_URL_INFO.errores)
SUPABASE_KEY = _limpiar_valor_env(os.getenv('SUPABASE_KEY')) or _limpiar_valor_env(
    os.getenv('SUPABASE_ANON_KEY')
)
SUPABASE_SERVICE_ROLE_KEY = _leer_clave_service_role_entorno()
SUPABASE_BUCKET_IMAGENES = _limpiar_valor_env(os.getenv('SUPABASE_BUCKET_IMAGENES')) or 'imagenes'

_SERVICE_ROLE_RECHAZADA = False
_SERVICE_ROLE_ROL_CRUDO = _rol_claim_jwt(SUPABASE_SERVICE_ROLE_KEY)

if SUPABASE_KEY and SUPABASE_SERVICE_ROLE_KEY and SUPABASE_KEY == SUPABASE_SERVICE_ROLE_KEY:
    if not clave_es_service_role(SUPABASE_SERVICE_ROLE_KEY):
        print(
            f'[Localis Supabase] ADVERTENCIA: {ENV_SERVICE_ROLE} es identica a '
            'SUPABASE_KEY (anon). Modo hibrido: el catalogo sigue con fotos oficiales; '
            'no se sube al bucket. Dashboard -> Settings -> API -> service_role (secret).'
        )
        _SERVICE_ROLE_RECHAZADA = True
        SUPABASE_SERVICE_ROLE_KEY = ''
    else:
        print(
            f'[Localis Supabase] ADVERTENCIA: SUPABASE_KEY y {ENV_SERVICE_ROLE} son '
            'la misma llave service_role. Funciona para Storage; deja SUPABASE_KEY como anon.'
        )

if SUPABASE_SERVICE_ROLE_KEY and not clave_es_service_role(SUPABASE_SERVICE_ROLE_KEY):
    rol_detectado = _SERVICE_ROLE_ROL_CRUDO or 'desconocido'
    print(
        f'[Localis Supabase] ADVERTENCIA: {ENV_SERVICE_ROLE} tiene jwt_role='
        f'{rol_detectado!r}, se esperaba service_role. Modo hibrido activo: '
        'el catalogo no se bloquea; las subidas a Storage se omiten hasta corregir la llave.'
    )
    _SERVICE_ROLE_RECHAZADA = True
    SUPABASE_SERVICE_ROLE_KEY = ''

supabase: Optional[Client] = None
supabase_storage_admin: Optional[Client] = None
_postgrest_circuito_abierto = False
_modo_catalogo_logeado = False


def auditar_claves_supabase() -> dict:
    """
    Diagnóstico de llaves sin imprimir secretos.
    Lee el entorno crudo (Render/.env) para detectar anon pegada en service_role.
    """
    cruda_service = (
        _limpiar_valor_env(os.getenv('SUPABASE_SERVICE_ROLE_KEY'))
        or _limpiar_valor_env(os.getenv('SUPABASE_SECRET_KEY'))
        or _limpiar_valor_env(os.getenv('SUPABASE_SERVICE_ROL_KEY'))
    )
    cruda_anon = _limpiar_valor_env(os.getenv('SUPABASE_KEY')) or _limpiar_valor_env(
        os.getenv('SUPABASE_ANON_KEY')
    )
    typo_nombre = bool(
        _limpiar_valor_env(os.getenv('SUPABASE_SERVICE_ROL_KEY'))
    ) and not _limpiar_valor_env(os.getenv('SUPABASE_SERVICE_ROLE_KEY'))
    rol_service = _rol_claim_jwt(cruda_service)
    rol_anon = _rol_claim_jwt(cruda_anon)
    identicas = bool(cruda_service and cruda_anon and cruda_service == cruda_anon)
    rol_es_service = clave_es_service_role(cruda_service)
    service_ok = rol_es_service and not (identicas and rol_service == 'anon')
    return {
        'supabase_url_presente': bool(_limpiar_valor_env(os.getenv('SUPABASE_URL'))),
        'anon_presente': bool(cruda_anon),
        'anon_jwt_role': rol_anon,
        'service_presente': bool(cruda_service),
        'service_jwt_role': rol_service or (
            'sb_secret' if (cruda_service or '').startswith('sb_secret_') else None
        ),
        'claves_identicas': identicas,
        'nombre_variable_typo': typo_nombre,
        'nombre_env_canonico': ENV_SERVICE_ROLE,
        'nombre_env_leido': _SERVICE_ROLE_NOMBRE_LEIDO,
        'modo_hibrido': not service_ok,
        'service_ok': service_ok,
        'service_rechazada_al_iniciar': _SERVICE_ROLE_RECHAZADA,
        'storage_cliente_ok': supabase_storage_admin is not None,
        'bucket': SUPABASE_BUCKET_IMAGENES,
    }


def abrir_circuito_postgrest(motivo: str = '') -> None:
    """Tras fallo de red, deja de intentar PostgREST HTTP hasta reiniciar el proceso."""
    global _postgrest_circuito_abierto
    if _postgrest_circuito_abierto:
        return
    _postgrest_circuito_abierto = True
    detalle = f' ({motivo})' if motivo else ''
    print(
        f'[Localis Supabase] PostgREST deshabilitado por red{detalle}. '
        'Catálogo maestro usará PostgreSQL directo (DATABASE_URL).'
    )


def postgrest_circuito_abierto() -> bool:
    return _postgrest_circuito_abierto


def postgrest_http_habilitado() -> bool:
    return (
        SUPABASE_URL_VALIDA
        and bool(clave_api_servidor())
        and not _postgrest_circuito_abierto
    )


def aplicar_diagnostico_conectividad(informe: dict) -> None:
    """Tras diagnóstico de arranque, abre circuito si la API HTTP no es alcanzable."""
    if informe.get('omitido'):
        return
    if informe.get('ok'):
        return
    capa = informe.get('capa_fallo') or 'desconocida'
    abrir_circuito_postgrest(f'diagnóstico arranque: capa={capa}')
    for aviso in informe.get('advertencias_config') or []:
        print(f'[Localis Supabase] URL: {aviso}')
    for error in informe.get('errores_config') or []:
        print(f'[Localis Supabase] URL inválida: {error}')


def registrar_modo_catalogo_maestro() -> None:
    """Log único del backend elegido para catálogo maestro."""
    global _modo_catalogo_logeado
    if _modo_catalogo_logeado:
        return
    _modo_catalogo_logeado = True
    from backend.db import DATABASE_URL, using_postgres

    if using_postgres() and DATABASE_URL:
        print(
            '[Localis Catálogo maestro] Backend: PostgreSQL directo (DATABASE_URL). '
            'PostgREST HTTP solo si PostgreSQL no está disponible.'
        )
    elif postgrest_http_habilitado():
        print(
            '[Localis Catálogo maestro] Backend: PostgREST HTTP (httpx, sin SDK).'
        )
    else:
        print('[Localis Catálogo maestro] Backend: no disponible (revisa DATABASE_URL).')


if SUPABASE_URL and SUPABASE_KEY:
    supabase = _crear_cliente_supabase(SUPABASE_KEY, etiqueta='anon')
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase_storage_admin = _crear_cliente_supabase(
        SUPABASE_SERVICE_ROLE_KEY,
        etiqueta='service_role',
    )

_imprimir_estado_supabase()


def supabase_api_habilitado():
    """True si hay URL y cliente anon (PostgREST). Storage exige service_role aparte."""
    return supabase is not None and bool(SUPABASE_URL)


def supabase_storage_habilitado():
    """True si hay cliente Storage con service_role (subidas)."""
    return supabase_storage_admin is not None and bool(SUPABASE_URL)


def obtener_diagnostico_supabase():
    """Estado de configuración y URL sanitizada."""
    return {
        'ok': SUPABASE_URL_VALIDA and bool(SUPABASE_SERVICE_ROLE_KEY),
        'url_sanitizada': SUPABASE_URL,
        'host': _extraer_host_desde_url(SUPABASE_URL),
        'raw_presente': bool(_limpiar_valor_env(os.getenv('SUPABASE_URL'))),
        'advertencias': SUPABASE_URL_ADVERTENCIAS,
        'errores': SUPABASE_URL_ERRORES,
        'project_ref': _URL_INFO.project_ref,
        'id_sospechoso': _URL_INFO.id_sospechoso,
        'url_recomendada': _URL_INFO.url_recomendada,
        'postgrest_circuito_abierto': postgrest_circuito_abierto(),
        'url_raw': _limpiar_valor_env(_URL_RAW),
    }


def es_error_red_supabase(error):
    """True si el fallo parece DNS/red (p. ej. Name or service not known / Errno -2)."""
    nombre = type(error).__name__
    if nombre in (
        'ConnectError',
        'ConnectTimeout',
        'ReadTimeout',
        'NetworkError',
        'RemoteProtocolError',
    ):
        return True
    if isinstance(error, (ConnectionError, TimeoutError, socket.gaierror, OSError)):
        return True
    mensaje = str(error).lower()
    return any(
        fragmento in mensaje
        for fragmento in (
            'connecterror',
            'name or service not known',
            'errno -2',
            'errno -3',
            'getaddrinfo failed',
            'failed to resolve',
            'name not resolved',
            'nameresolutionerror',
            'temporary failure in name resolution',
            'connection refused',
            'network is unreachable',
            'nodename nor servname provided',
        )
    )


def _host_supabase():
    if not SUPABASE_URL:
        return ''
    return _extraer_host_desde_url(SUPABASE_URL)


def ruta_storage_objeto(carpeta: str, nombre_archivo: str) -> str:
    """Ruta relativa dentro del bucket (p. ej. productos/archivo.webp)."""
    carpeta_limpia = carpeta.strip('/').replace('\\', '/')
    archivo = nombre_archivo.lstrip('/')
    return f'{carpeta_limpia}/{archivo}' if carpeta_limpia else archivo


def construir_url_publica_storage(ruta: str, bucket: str | None = None) -> str:
    """
    URL pública canónica documentada por Supabase:
    {SUPABASE_URL}/storage/v1/object/public/{bucket}/{ruta}
    """
    if not SUPABASE_URL:
        raise RuntimeError(
            'Supabase no está configurado. Define SUPABASE_URL y '
            'SUPABASE_SERVICE_ROLE_KEY.'
        )
    bucket_nombre = (bucket or SUPABASE_BUCKET_IMAGENES).strip('/')
    partes = [p for p in ruta.replace('\\', '/').split('/') if p]
    ruta_codificada = '/'.join(quote(parte, safe='') for parte in partes)
    return f'{SUPABASE_URL}{_STORAGE_PUBLIC_PREFIX}{bucket_nombre}/{ruta_codificada}'


def corregir_typo_ruta_storage(url: str) -> str:
    """Corrige /subase/ -> /storage/ en URLs legacy o mal formadas."""
    if not url:
        return url
    return _SUBASE_TYPO_RE.sub('/storage/', url)


def normalizar_url_publica_storage(
    url: str | None,
    *,
    ruta: str,
    bucket: str | None = None,
) -> str:
    """
    Asegura dominio SUPABASE_URL + ruta /storage/v1/object/public/{bucket}/...
    Usa la URL del SDK cuando es válida; si no, reconstruye la canónica.
    """
    bucket_nombre = bucket or SUPABASE_BUCKET_IMAGENES
    canonica = construir_url_publica_storage(ruta, bucket_nombre)

    if not url:
        return canonica

    url_limpia = corregir_typo_ruta_storage(str(url).strip())
    if not url_limpia.startswith(('http://', 'https://')):
        return canonica

    host_esperado = _host_supabase()
    parsed = urlparse(url_limpia)
    host_url = (urlparse(url_limpia).hostname or '').lower()

    if not host_esperado or host_url != host_esperado:
        return canonica

    if _STORAGE_PUBLIC_PREFIX not in url_limpia.lower():
        return canonica

    if parsed.query:
        base, _, query = url_limpia.partition('?')
        if _STORAGE_PUBLIC_PREFIX in base.lower():
            return base if not query else f'{base}?{query}'
    return url_limpia


def url_publica_desde_sdk(ruta: str, bucket: str | None = None) -> str:
    """
    URL canonica local. No llama a get_public_url del SDK: ese metodo puede
    devolver un host malformado si SUPABASE_URL llego sucia al cliente.
    """
    return construir_url_publica_storage(ruta, bucket)


def url_publica_bucket(carpeta: str, nombre_archivo: str) -> str:
    """URL pública de un objeto en el bucket configurado."""
    ruta = ruta_storage_objeto(carpeta, nombre_archivo)
    return url_publica_desde_sdk(ruta)


def es_host_supabase(url: str) -> bool:
    if not url or not SUPABASE_URL:
        return False
    host_config = _host_supabase()
    return host_config in corregir_typo_ruta_storage(url or '').lower()


def storage_supabase_disponible():
    """True si hay cliente Storage configurado (no implica conectividad de red)."""
    return obtener_cliente_storage() is not None


def obtener_cliente_storage():
    """
    Cliente para subidas server-side. Solo SUPABASE_SERVICE_ROLE_KEY
    (bypasea RLS del bucket). Nunca usa la clave anon/pública.
    """
    return supabase_storage_admin


def clave_api_servidor():
    """Clave server-side: solo service_role. Nunca la anon/pública."""
    return SUPABASE_SERVICE_ROLE_KEY if clave_es_service_role(SUPABASE_SERVICE_ROLE_KEY) else ''


def headers_storage_service_role(extra: dict | None = None) -> dict[str, str]:
    """Cabeceras HTTP para Storage: Bearer + apikey = service_role, nunca anon."""
    key = clave_api_servidor()
    if not clave_es_service_role(key):
        raise RuntimeError(
            'Subida a Storage denegada: falta SUPABASE_SERVICE_ROLE_KEY válida '
            '(jwt role=service_role o sb_secret_). No se usa SUPABASE_KEY/anon. '
            'Si en Render pegaste la llave anon en SUPABASE_SERVICE_ROLE_KEY, '
            'reemplázala por Dashboard -> Settings -> API -> service_role (secret).'
        )
    headers = {
        'Authorization': f'Bearer {key}',
        'apikey': key,
    }
    if extra:
        headers.update(extra)
    return headers


def storage_usa_service_role():
    return supabase_storage_admin is not None and bool(clave_api_servidor())

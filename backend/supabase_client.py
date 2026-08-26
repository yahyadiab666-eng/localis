"""Cliente Supabase Storage + helpers de URL pública."""

import os
import re
import socket
from typing import Optional
from urllib.parse import quote, urlparse

from supabase import Client, create_client

_STORAGE_PUBLIC_PREFIX = '/storage/v1/object/public/'
_SUBASE_TYPO_RE = re.compile(r'/subase/', re.IGNORECASE)
_FORMATO_URL_EJEMPLO = 'https://TU_PROJECT_REF.supabase.co'
_CARACTERES_INVISIBLES_HOST = ('\ufeff', '\u200b', '\u200c', '\u200d', '\u00ad', '\x00')


def _limpiar_valor_env(valor):
    """Limpia espacios, comillas y saltos de línea sin rechazar la URL."""
    if valor is None:
        return ''
    return str(valor).strip().strip('"').strip("'").replace('\r', '').replace('\n', '').strip()


def _contiene_dominio_supabase(url):
    """True si la cadena incluye https:// y .supabase.co."""
    lower = (url or '').lower()
    return 'https://' in lower and '.supabase.co' in lower


def _preparar_url_supabase(url):
    """Limpia y asegura prefijo https:// cuando hay dominio Supabase."""
    limpia = _limpiar_valor_env(url).rstrip('/')
    if not limpia:
        return ''

    lower = limpia.lower()
    if lower.startswith('https://'):
        return 'https://' + limpia[8:].lstrip('/')
    if lower.startswith('http://'):
        return 'https://' + limpia[7:].lstrip('/')
    if '.supabase.co' in lower:
        return 'https://' + limpia.lstrip('/')
    return limpia


def _normalizar_host_supabase(host):
    """
    Host DNS limpio: sin puerto, sin punto final ni caracteres invisibles de copiar/pegar.
    """
    if not host:
        return ''

    texto = str(host).strip().lower().rstrip('.')
    for caracter in _CARACTERES_INVISIBLES_HOST:
        texto = texto.replace(caracter, '')
    return texto.strip()


def _extraer_host_desde_url(url):
    """Host sin puerto (logs y URL canónica)."""
    url_limpia = _preparar_url_supabase(url)
    if not url_limpia:
        return ''
    return _normalizar_host_supabase(urlparse(url_limpia).hostname)


def sanitizar_supabase_url(url):
    """Devuelve https://HOST si reconoce dominio Supabase; si no, cadena vacía."""
    preparada = _preparar_url_supabase(url)
    if not _contiene_dominio_supabase(preparada):
        return ''

    host = _extraer_host_desde_url(preparada)
    if host:
        return f'https://{host}'

    return preparada.split('?')[0].split('#')[0].rstrip('/')


def diagnosticar_supabase_url(url_raw=None, url_cruda_env=None):
    """
    Limpia SUPABASE_URL y la acepta si contiene https:// y .supabase.co.
    No bloquea el cliente cuando el dominio es correcto.
    """
    cruda_env = url_cruda_env if url_cruda_env is not None else os.getenv('SUPABASE_URL')
    cruda_repr = repr(cruda_env)
    raw = _limpiar_valor_env(url_raw if url_raw is not None else cruda_env)
    diagnostico = {
        'raw_presente': bool(raw),
        'url_cruda_repr': cruda_repr,
        'url_sanitizada': '',
        'host': '',
        'ok': False,
        'problema': None,
        'pista': None,
    }

    if not raw:
        diagnostico['problema'] = 'vacia'
        diagnostico['pista'] = f'SUPABASE_URL vacía (valor leído: {cruda_repr}).'
        return diagnostico

    preparada = _preparar_url_supabase(raw)
    if _contiene_dominio_supabase(preparada):
        url_final = sanitizar_supabase_url(preparada) or preparada.rstrip('/')
        diagnostico['url_sanitizada'] = url_final
        diagnostico['host'] = _extraer_host_desde_url(url_final) or url_final.replace('https://', '').split('/')[0]
        diagnostico['ok'] = True
        return diagnostico

    diagnostico['problema'] = 'sin_dominio_supabase'
    diagnostico['pista'] = (
        f'SUPABASE_URL debe contener https:// y .supabase.co. Valor leído: {cruda_repr}'
    )
    print(f'[Localis Supabase] URL no aceptada; valor crudo: {cruda_repr}')
    return diagnostico


def _mascara_secreto(valor, visible=4):
    texto = _limpiar_valor_env(valor)
    if not texto:
        return 'ausente'
    if len(texto) <= visible * 2:
        return 'presente'
    return f'{texto[:visible]}…{texto[-visible:]}'


def _imprimir_diagnostico_supabase(diagnostico):
    """Log legible al arrancar; no imprime claves completas."""
    prefijo = '[Localis Supabase]'

    if not diagnostico.get('raw_presente') and not SUPABASE_KEY:
        print(
            f'{prefijo} No configurado (SUPABASE_URL/SUPABASE_KEY ausentes). '
            'Catálogo maestro usará PostgreSQL directo.'
        )
        return

    if diagnostico.get('problema') == 'vacia':
        print(
            f'{prefijo} SUPABASE_URL vacía. '
            f'Formato esperado: {_FORMATO_URL_EJEMPLO}'
        )
        return

    if not diagnostico.get('ok'):
        print(f'{prefijo} SUPABASE_URL no usable: {diagnostico.get("problema")}.')
        if diagnostico.get('pista'):
            print(f'{prefijo} {diagnostico["pista"]}')
        elif diagnostico.get('url_cruda_repr'):
            print(f'{prefijo} Valor crudo: {diagnostico["url_cruda_repr"]}')
        print(
            f'{prefijo} Se omitirá la API de Supabase; catálogo maestro usará PostgreSQL.'
        )
        return

    host = diagnostico.get('host') or '(sin host)'
    print(
        f'{prefijo} URL host={host} | '
        f'SUPABASE_KEY={_mascara_secreto(SUPABASE_KEY)} | '
        f'SERVICE_ROLE={_mascara_secreto(SUPABASE_SERVICE_ROLE_KEY)}'
    )

    if supabase:
        print(f'{prefijo} Cliente API inicializado (PostgREST + Storage).')
    else:
        clave_msg = 'SUPABASE_KEY ausente' if not SUPABASE_KEY else 'fallo al crear cliente'
        print(
            f'{prefijo} Cliente API no disponible ({clave_msg}). '
            'Catálogo maestro usará PostgreSQL.'
        )


def _crear_cliente_supabase(api_key, etiqueta='anon'):
    if not SUPABASE_URL or not api_key:
        return None
    try:
        return create_client(SUPABASE_URL, api_key)
    except Exception as error:
        host = _extraer_host_desde_url(SUPABASE_URL) or SUPABASE_URL
        print(
            f'WARNING Localis Supabase ({etiqueta}): no se pudo inicializar cliente '
            f'para host {host}: {type(error).__name__}: {error}'
        )
        return None


SUPABASE_URL_RAW_ENV = os.getenv('SUPABASE_URL')
SUPABASE_URL_RAW = _limpiar_valor_env(SUPABASE_URL_RAW_ENV)
_DIAGNOSTICO_SUPABASE = diagnosticar_supabase_url(
    SUPABASE_URL_RAW,
    url_cruda_env=SUPABASE_URL_RAW_ENV,
)
SUPABASE_URL = _DIAGNOSTICO_SUPABASE.get('url_sanitizada', '') if _DIAGNOSTICO_SUPABASE.get('ok') else ''
SUPABASE_KEY = _limpiar_valor_env(os.getenv('SUPABASE_KEY'))
SUPABASE_SERVICE_ROLE_KEY = _limpiar_valor_env(os.getenv('SUPABASE_SERVICE_ROLE_KEY'))
SUPABASE_BUCKET_IMAGENES = _limpiar_valor_env(os.getenv('SUPABASE_BUCKET_IMAGENES')) or 'imagenes'

supabase: Optional[Client] = None
supabase_storage_admin: Optional[Client] = None


def _inicializar_clientes_supabase():
    global supabase, supabase_storage_admin
    supabase = None
    supabase_storage_admin = None

    if not SUPABASE_URL:
        return

    if SUPABASE_KEY:
        supabase = _crear_cliente_supabase(SUPABASE_KEY, etiqueta='anon')
    if SUPABASE_SERVICE_ROLE_KEY:
        supabase_storage_admin = _crear_cliente_supabase(
            SUPABASE_SERVICE_ROLE_KEY,
            etiqueta='service_role',
        )


_inicializar_clientes_supabase()

_imprimir_diagnostico_supabase(_DIAGNOSTICO_SUPABASE)


def supabase_api_habilitado():
    """True solo si la URL pasó validación y el cliente anon se creó correctamente."""
    return supabase is not None and bool(SUPABASE_URL)


def obtener_diagnostico_supabase():
    """Diagnóstico de configuración (para arranque y health checks)."""
    return dict(_DIAGNOSTICO_SUPABASE)


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
            'Supabase no está configurado. Define SUPABASE_URL y SUPABASE_KEY.'
        )
    bucket_nombre = (bucket or SUPABASE_BUCKET_IMAGENES).strip('/')
    partes = [p for p in ruta.replace('\\', '/').split('/') if p]
    ruta_codificada = '/'.join(quote(parte, safe='') for parte in partes)
    return f'{SUPABASE_URL}{_STORAGE_PUBLIC_PREFIX}{bucket_nombre}/{ruta_codificada}'


def corregir_typo_ruta_storage(url: str) -> str:
    """Corrige /subase/ → /storage/ en URLs legacy o mal formadas."""
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
    host_url = _normalizar_host_supabase(parsed.hostname)

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
    """get_public_url del SDK + normalización al dominio SUPABASE_URL."""
    bucket_nombre = bucket or SUPABASE_BUCKET_IMAGENES
    if supabase:
        url_sdk = supabase.storage.from_(bucket_nombre).get_public_url(ruta)
        return normalizar_url_publica_storage(url_sdk, ruta=ruta, bucket=bucket_nombre)
    return construir_url_publica_storage(ruta, bucket_nombre)


def url_publica_bucket(carpeta: str, nombre_archivo: str) -> str:
    """URL pública de un objeto en el bucket configurado."""
    ruta = ruta_storage_objeto(carpeta, nombre_archivo)
    return url_publica_desde_sdk(ruta)


def es_host_supabase(url: str) -> bool:
    if not url or not SUPABASE_URL:
        return False
    host_config = _host_supabase()
    return host_config in corregir_typo_ruta_storage(url or '').lower()


def obtener_cliente_storage():
    """
    Cliente para subidas server-side en Storage.
    Prefiere SUPABASE_SERVICE_ROLE_KEY (evita rechazos RLS con anon key).
    """
    if supabase_storage_admin:
        return supabase_storage_admin
    return supabase


def storage_usa_service_role():
    return supabase_storage_admin is not None

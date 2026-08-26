"""Cliente Supabase Storage + helpers de URL pública."""

import os
import re
import socket
from typing import Optional
from urllib.parse import quote, urlparse

from supabase import Client, create_client

_STORAGE_PUBLIC_PREFIX = '/storage/v1/object/public/'
_SUBASE_TYPO_RE = re.compile(r'/subase/', re.IGNORECASE)


def _limpiar_valor_env(valor):
    """Elimina espacios, comillas y saltos de línea típicos de copiar/pegar en Render."""
    if valor is None:
        return ''
    texto = str(valor).strip().strip('"').strip("'")
    return texto.replace('\r', '').replace('\n', '').strip()


def sanitizar_supabase_url(url):
    """
    Normaliza SUPABASE_URL para evitar Errno -2 (hostname inválido).
    Acepta valores con o sin https:// y rechaza hosts vacíos o sin punto.
    """
    url_limpia = _limpiar_valor_env(url).rstrip('/')
    if not url_limpia:
        return ''

    if not url_limpia.startswith(('http://', 'https://')):
        url_limpia = 'https://' + url_limpia.lstrip('/')

    parsed = urlparse(url_limpia)
    host = (parsed.netloc or '').lower()
    if not host or '.' not in host:
        return ''

    return url_limpia


def _crear_cliente_supabase(api_key):
    if not SUPABASE_URL or not api_key:
        return None
    try:
        return create_client(SUPABASE_URL, api_key)
    except Exception as error:
        print(
            'WARNING Localis: no se pudo inicializar cliente Supabase '
            f'({SUPABASE_URL}): {error}'
        )
        return None


SUPABASE_URL = sanitizar_supabase_url(os.getenv('SUPABASE_URL'))
SUPABASE_KEY = _limpiar_valor_env(os.getenv('SUPABASE_KEY'))
SUPABASE_SERVICE_ROLE_KEY = _limpiar_valor_env(os.getenv('SUPABASE_SERVICE_ROLE_KEY'))
SUPABASE_BUCKET_IMAGENES = _limpiar_valor_env(os.getenv('SUPABASE_BUCKET_IMAGENES')) or 'imagenes'

supabase: Optional[Client] = None
supabase_storage_admin: Optional[Client] = None

if SUPABASE_URL and SUPABASE_KEY:
    supabase = _crear_cliente_supabase(SUPABASE_KEY)
if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
    supabase_storage_admin = _crear_cliente_supabase(SUPABASE_SERVICE_ROLE_KEY)
elif SUPABASE_URL or SUPABASE_KEY:
    print(
        'WARNING Localis: SUPABASE_URL y SUPABASE_KEY deben definirse juntas. '
        'Storage y catalogo maestro via Supabase quedaran desactivados.'
    )
else:
    print(
        'WARNING Localis: SUPABASE_URL/SUPABASE_KEY no configuradas. '
        'Subidas a Storage y catalogo maestro via Supabase no estaran disponibles.'
    )


def es_error_red_supabase(error):
    """True si el fallo parece DNS/red (p. ej. Name or service not known / Errno -2)."""
    if isinstance(error, (ConnectionError, TimeoutError, socket.gaierror, OSError)):
        return True
    mensaje = str(error).lower()
    return any(
        fragmento in mensaje
        for fragmento in (
            'name or service not known',
            'errno -2',
            'errno -3',
            'getaddrinfo failed',
            'failed to resolve',
            'temporary failure in name resolution',
            'connection refused',
            'network is unreachable',
        )
    )


def _host_supabase():
    if not SUPABASE_URL:
        return ''
    return urlparse(SUPABASE_URL).netloc.lower()


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
    host_url = (parsed.netloc or '').lower()

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

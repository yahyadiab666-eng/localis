"""Cliente Supabase Storage + helpers de URL pública."""

import os
import re
from typing import Optional
from urllib.parse import quote, urlparse

from supabase import Client, create_client

SUPABASE_URL = (os.getenv('SUPABASE_URL') or '').strip().rstrip('/')
SUPABASE_KEY = (os.getenv('SUPABASE_KEY') or '').strip()
SUPABASE_BUCKET_IMAGENES = (os.getenv('SUPABASE_BUCKET_IMAGENES') or 'imagenes').strip()

_STORAGE_PUBLIC_PREFIX = '/storage/v1/object/public/'
_SUBASE_TYPO_RE = re.compile(r'/subase/', re.IGNORECASE)

supabase: Optional[Client] = None

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
elif SUPABASE_URL or SUPABASE_KEY:
    print(
        '⚠️  Localis: SUPABASE_URL y SUPABASE_KEY deben definirse juntas. '
        'Storage y catálogo maestro vía Supabase quedarán desactivados.'
    )
else:
    print(
        '⚠️  Localis: SUPABASE_URL/SUPABASE_KEY no configuradas. '
        'Subidas a Storage y catálogo maestro vía Supabase no estarán disponibles.'
    )


def _host_supabase():
    if not SUPABASE_URL:
        return ''
    return urlparse(SUPABASE_URL).netloc.lower()


def ruta_storage_objeto(carpeta: str, nombre_archivo: str) -> str:
    """Ruta relativa dentro del bucket (p. ej. productos/default-product.webp)."""
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

    # Conserva query string del SDK (p. ej. download) si existe.
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

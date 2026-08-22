"""Cliente Supabase Storage + helpers de URL pública."""

import os
from typing import Optional

from supabase import Client, create_client

SUPABASE_URL = (os.getenv('SUPABASE_URL') or '').strip().rstrip('/')
SUPABASE_KEY = (os.getenv('SUPABASE_KEY') or '').strip()
SUPABASE_BUCKET_IMAGENES = (os.getenv('SUPABASE_BUCKET_IMAGENES') or 'imagenes').strip()

supabase: Optional[Client] = None

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def url_publica_bucket(carpeta: str, nombre_archivo: str) -> str:
    """URL pública vía SDK: supabase.storage.from_(bucket).get_public_url(ruta)."""
    if not supabase:
        raise RuntimeError(
            'Supabase no está configurado. Define SUPABASE_URL y SUPABASE_KEY.'
        )
    carpeta_limpia = carpeta.strip('/').replace('\\', '/')
    archivo = nombre_archivo.lstrip('/')
    ruta = f'{carpeta_limpia}/{archivo}' if carpeta_limpia else archivo
    url = supabase.storage.from_(SUPABASE_BUCKET_IMAGENES).get_public_url(ruta)
    if not url:
        raise RuntimeError(
            f'Supabase no devolvió URL pública para {SUPABASE_BUCKET_IMAGENES}/{ruta}'
        )
    return url


def es_host_supabase(url: str) -> bool:
    if not url or not SUPABASE_URL:
        return False
    host_config = SUPABASE_URL.replace('https://', '').replace('http://', '').split('/')[0]
    return host_config.lower() in (url or '').lower()

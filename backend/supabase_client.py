"""Cliente Supabase Storage + helpers de URL pública."""

import os
from typing import Optional
from urllib.parse import quote

from supabase import Client, create_client

SUPABASE_URL = (os.getenv('SUPABASE_URL') or '').strip().rstrip('/')
SUPABASE_KEY = (os.getenv('SUPABASE_KEY') or '').strip()
SUPABASE_BUCKET_IMAGENES = (os.getenv('SUPABASE_BUCKET_IMAGENES') or 'imagenes').strip()

supabase: Optional[Client] = None

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def url_publica_bucket(carpeta: str, nombre_archivo: str) -> str:
    """Construye la URL pública de un objeto en el bucket configurado."""
    carpeta_limpia = carpeta.strip('/').replace('\\', '/')
    archivo = quote(nombre_archivo.lstrip('/'), safe='./-_')
    ruta = f'{carpeta_limpia}/{archivo}' if carpeta_limpia else archivo
    return f'{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET_IMAGENES}/{ruta}'


def es_host_supabase(url: str) -> bool:
    if not url or not SUPABASE_URL:
        return False
    host_config = SUPABASE_URL.replace('https://', '').replace('http://', '').split('/')[0]
    return host_config.lower() in (url or '').lower()

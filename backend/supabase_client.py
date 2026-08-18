"""Cliente Supabase Storage + variables de entorno."""

import os
from typing import Optional

from supabase import Client, create_client

SUPABASE_URL = (os.getenv('SUPABASE_URL') or '').strip()
SUPABASE_KEY = (os.getenv('SUPABASE_KEY') or '').strip()
SUPABASE_BUCKET_IMAGENES = os.getenv('SUPABASE_BUCKET_IMAGENES', 'imágenes')

supabase: Optional[Client] = None

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

"""Prueba rápida de catálogo maestro (Supabase API + fallback PostgreSQL)."""
import importlib
import os
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, '.env'), override=True)

import backend.supabase_client as sc
import backend.catalogo_maestro as cm

importlib.reload(sc)
importlib.reload(cm)

from backend.catalogo_maestro import (
    _catalogo_disponible,
    guardar_imagen_maestro,
    imagen_maestro_por_codigo,
    mapa_imagenes_maestro,
)
from backend.db import DATABASE_URL, get_db_connection

CODIGO = '7590000040110'
URL_TEST = (
    'https://wsrv.nl/?url=https%3A%2F%2Fimages.openfoodfacts.org%2Fimages%2Fproducts%2F'
    '759%2F000%2F004%2F0110%2Ffront_es.400.jpg&w=300&h=300&fit=cover&output=webp&q=80'
)


def main():
    print('=== Config ===')
    print('catalogo_disponible:', _catalogo_disponible())
    print('supabase client:', sc.supabase is not None)
    host = (DATABASE_URL or '').split('@')[-1] if DATABASE_URL else 'vacia'
    print('DATABASE host:', host)
    key = os.getenv('SUPABASE_KEY') or ''
    print('SUPABASE_KEY prefix:', key[:24] + ('...' if len(key) > 24 else ''))

    print('\n=== Test conexion PostgreSQL ===')
    try:
        with get_db_connection() as conn:
            cur = conn.cursor()
            cur.execute('SELECT 1')
            print('postgres SELECT 1: OK')
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_name = 'catalogo_maestro_imagenes'
                )
                """
            )
            row = cur.fetchone()
            existe = row[0] if not isinstance(row, dict) else row.get('exists')
            print('tabla catalogo_maestro_imagenes existe:', existe)
    except Exception as error:
        print('postgres ERROR:', type(error).__name__, error)

    print('\n=== Test catalogo maestro (Supabase API) ===')
    try:
        ok = guardar_imagen_maestro(CODIGO, URL_TEST)
        print('guardar:', ok)
        leida = imagen_maestro_por_codigo(CODIGO)
        if leida:
            print('leer: OK', leida[:70] + '...')
        else:
            print('leer: None')
        mapa = mapa_imagenes_maestro([CODIGO, '1234567890123'])
        print('mapa lote:', len(mapa), 'entradas', list(mapa.keys()))
    except Exception as error:
        print('catalogo ERROR:', type(error).__name__, error)

    print('\n=== Test fallback PostgreSQL (sin Supabase client) ===')
    sc.supabase = None
    importlib.reload(cm)
    try:
        ok2 = cm.guardar_imagen_maestro(CODIGO, URL_TEST)
        print('postgres guardar:', ok2)
        leida2 = cm.imagen_maestro_por_codigo(CODIGO)
        print('postgres leer:', 'OK' if leida2 else 'None')
    except Exception as error:
        print('postgres fallback ERROR:', type(error).__name__, error)


if __name__ == '__main__':
    main()

"""Catálogo maestro de imágenes usando conexión directa a PostgreSQL."""

import os

import psycopg2

from backend.utils import normalizar_codigo_barras, texto_campo_imagen

TABLA_CATALOGO_MAESTRO = 'catalogo_maestro_imagenes'
_LOTE_CONSULTA = 100

def _obtener_conexion():
    """Obtiene una conexión directa a PostgreSQL usando DATABASE_URL."""
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        return None
    return psycopg2.connect(database_url)

def _url_maestro_valida(valor):
    url = (texto_campo_imagen(valor, default=None) or '').strip()
    if url and url.startswith('https://'):
        return url
    return None

def imagen_maestro_por_codigo(codigo_barras):
    """URL de imagen para un código de barras en el catálogo maestro."""
    codigo = normalizar_codigo_barras(codigo_barras)
    if not codigo:
        return None
    
    conn = None
    try:
        conn = _obtener_conexion()
        if not conn:
            return None
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT url_imagen FROM {TABLA_CATALOGO_MAESTRO} WHERE codigo_barras = %s LIMIT 1",
                (codigo,)
            )
            fila = cur.fetchone()
            if fila:
                url = _url_maestro_valida(fila[0])
                if url:
                    return url
    except Exception as error:
        print(f'Error al consultar catálogo maestro ({codigo}): {error}')
    finally:
        if conn:
            conn.close()
    return None

def guardar_imagen_maestro(codigo_barras, url_imagen):
    """Persiste URL optimizada en catalogo_maestro_imagenes (insert o update)."""
    codigo = normalizar_codigo_barras(codigo_barras)
    url = _url_maestro_valida(url_imagen)
    if not codigo or not url:
        return False

    conn = None
    try:
        conn = _obtener_conexion()
        if not conn:
            return False
        with conn.cursor() as cur:
            # Upsert compatible con PostgreSQL
            cur.execute(
                f"""
                INSERT INTO {TABLA_CATALOGO_MAESTRO} (codigo_barras, url_imagen)
                VALUES (%s, %s)
                ON CONFLICT (codigo_barras) 
                DO UPDATE SET url_imagen = EXCLUDED.url_imagen
                """,
                (codigo, url)
            )
            conn.commit()
            return True
    except Exception as error:
        print(f'Error al guardar en catálogo maestro ({codigo}): {error}')
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

def mapa_imagenes_maestro(codigos):
    """Mapa codigo_barras normalizado → url_imagen desde el catálogo maestro en lote."""
    normalizados = []
    vistos = set()
    for codigo in codigos or []:
        limpio = normalizar_codigo_barras(codigo)
        if limpio and limpio not in vistos:
            vistos.add(limpio)
            normalizados.append(limpio)
    if not normalizados:
        return {}

    resultado = {}
    conn = None
    try:
        conn = _obtener_conexion()
        if not conn:
            return {}
        with conn.cursor() as cur:
            for inicio in range(0, len(normalizados), _LOTE_CONSULTA):
                lote = normalizados[inicio : inicio + _LOTE_CONSULTA]
                # Creamos placeholders dinámicos seguros (%s, %s, ...)
                placeholders = ','.join(['%s'] * len(lote))
                query = f"SELECT codigo_barras, url_imagen FROM {TABLA_CATALOGO_MAESTRO} WHERE codigo_barras IN ({placeholders})"
                
                cur.execute(query, lote)
                filas = cur.fetchall()
                for fila in filas:
                    codigo_db = normalizar_codigo_barras(fila[0])
                    url = _url_maestro_valida(fila[1])
                    if codigo_db and url and codigo_db not in resultado:
                        resultado[codigo_db] = url
    except Exception as error:
        print(f'Error al consultar catálogo maestro en lote: {error}')
    finally:
        if conn:
            conn.close()
    return resultado

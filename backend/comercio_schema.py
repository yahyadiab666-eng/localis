"""Mapeo de columnas de comercios (esquema oficial Supabase + alias internos)."""

import re
import threading

from backend.db import get_db_connection

# Nombres oficiales (pueden ir entre comillas en PostgreSQL).
COL_BANNER_OFICIAL = 'URL del banner'
COL_LOGO_OFICIAL = 'URL del logotipo'
COL_PORTADA_OFICIAL = 'imagen_portada'

# Alias internos usados en plantillas y código Python.
COL_BANNER_INTERNO = 'banner_url'
COL_LOGO_INTERNO = 'logo_url'

CANDIDATOS_LOGO = (COL_LOGO_OFICIAL, COL_LOGO_INTERNO)
CANDIDATOS_BANNER = (COL_BANNER_OFICIAL, COL_BANNER_INTERNO, COL_PORTADA_OFICIAL)
CANDIDATOS_PORTADA = (COL_PORTADA_OFICIAL, COL_BANNER_OFICIAL, COL_BANNER_INTERNO)

_IDENT_SIMPLE = re.compile(r'^[a-z_][a-z0-9_]*$')
_cache_columnas = None
_cache_lock = threading.Lock()


def quote_ident(nombre):
    """Identificador SQL seguro, con comillas si hay espacios o mayúsculas."""
    texto = str(nombre or '')
    if _IDENT_SIMPLE.match(texto):
        return texto
    return '"' + texto.replace('"', '""') + '"'


def _nombres_columnas_comercios(cursor=None):
    global _cache_columnas
    if _cache_columnas is not None:
        return _cache_columnas
    with _cache_lock:
        if _cache_columnas is not None:
            return _cache_columnas
        try:
            if cursor is not None:
                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'comercios'
                    """
                )
                filas = cursor.fetchall()
            else:
                with get_db_connection() as conexion:
                    cur = conexion.cursor()
                    cur.execute(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = 'public' AND table_name = 'comercios'
                        """
                    )
                    filas = cur.fetchall()
            nombres = set()
            for fila in filas:
                if isinstance(fila, dict):
                    nombres.add(fila.get('column_name') or next(iter(fila.values())))
                else:
                    nombres.add(fila[0])
            _cache_columnas = nombres
        except Exception as error:
            print(f'[Localis Schema] No se pudieron leer columnas de comercios: {error}')
            _cache_columnas = {
                COL_LOGO_INTERNO,
                COL_BANNER_INTERNO,
                COL_PORTADA_OFICIAL,
            }
        return _cache_columnas


def invalidar_cache_columnas_comercios():
    global _cache_columnas
    _cache_columnas = None


def _primera_columna_existente(candidatos, columnas):
    lookup = {str(c): c for c in columnas}
    lookup_lower = {str(c).lower(): c for c in columnas}
    for nombre in candidatos:
        if nombre in lookup:
            return lookup[nombre]
        hallado = lookup_lower.get(nombre.lower())
        if hallado:
            return hallado
    return None


def columna_logo_fisica(cursor=None):
    return _primera_columna_existente(CANDIDATOS_LOGO, _nombres_columnas_comercios(cursor))


def columna_banner_fisica(cursor=None):
    return _primera_columna_existente(
        (COL_BANNER_OFICIAL, COL_BANNER_INTERNO),
        _nombres_columnas_comercios(cursor),
    )


def columna_portada_fisica(cursor=None):
    return _primera_columna_existente(
        (COL_PORTADA_OFICIAL,),
        _nombres_columnas_comercios(cursor),
    )


def columnas_escritura_logo(cursor=None):
    cols = _nombres_columnas_comercios(cursor)
    return [c for c in CANDIDATOS_LOGO if _primera_columna_existente((c,), cols)]


def columnas_escritura_banner(cursor=None):
    cols = _nombres_columnas_comercios(cursor)
    destinos = []
    for nombre in (COL_BANNER_OFICIAL, COL_BANNER_INTERNO, COL_PORTADA_OFICIAL):
        real = _primera_columna_existente((nombre,), cols)
        if real and real not in destinos:
            destinos.append(real)
    return destinos


def valor_campo(registro, *nombres):
    """Lee un campo probando nombres oficiales y alias. Nunca lanza."""
    if not registro:
        return None
    try:
        if hasattr(registro, 'items'):
            items = {str(k): v for k, v in registro.items()}
        else:
            items = {}
            for nombre in nombres:
                items[nombre] = getattr(registro, nombre, None)
        lower_map = {k.lower(): v for k, v in items.items()}
        for nombre in nombres:
            valor = items.get(nombre)
            if valor is None:
                valor = lower_map.get(str(nombre).lower())
            if valor is None:
                continue
            texto = str(valor).strip()
            if texto and texto.lower() not in ('', 'none', 'null'):
                return valor
        return None
    except Exception:
        return None


def normalizar_fila_comercio(fila):
    """
    Convierte una fila de comercios al contrato interno:
    logo_url, banner_url, imagen_portada.
    Conserva las claves originales.
    """
    if not fila:
        return fila
    try:
        datos = dict(fila)
        datos[COL_LOGO_INTERNO] = valor_campo(datos, *CANDIDATOS_LOGO)
        datos[COL_BANNER_INTERNO] = valor_campo(datos, *CANDIDATOS_BANNER)
        datos[COL_PORTADA_OFICIAL] = valor_campo(datos, *CANDIDATOS_PORTADA)
        return datos
    except Exception:
        try:
            return dict(fila)
        except Exception:
            return fila


def normalizar_filas_comercio(filas):
    return [normalizar_fila_comercio(f) for f in (filas or [])]


def sql_set_imagenes(cursor, logo_url=None, banner_url=None):
    """
    Fragmentos SET para UPDATE de logo/banner usando columnas físicas reales.
    Retorna (lista_sql, valores).
    """
    fragmentos = []
    valores = []
    if logo_url:
        for col in columnas_escritura_logo(cursor):
            fragmentos.append(f'{quote_ident(col)} = ?')
            valores.append(logo_url)
    if banner_url:
        for col in columnas_escritura_banner(cursor):
            fragmentos.append(f'{quote_ident(col)} = ?')
            valores.append(banner_url)
    return fragmentos, valores

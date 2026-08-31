"""Catálogo maestro de imágenes: PostgreSQL directo (preferido) + PostgREST httpx opcional."""

import os
from urllib.parse import quote

from backend.db import DATABASE_URL, using_postgres
from backend.postgrest_http import (
    consultar_imagen_maestro,
    consultar_mapa_maestro,
    guardar_imagen_maestro_http,
    postgrest_http_configurado,
)
from backend.supabase_client import (
    postgrest_http_habilitado,
    registrar_modo_catalogo_maestro,
)
from backend.utils import normalizar_codigo_barras, texto_campo_imagen

TABLA_CATALOGO_MAESTRO = 'catalogo_maestro_imagenes'
_LOTE_CONSULTA = 100
_LOG = '[Localis Imagen]'
# Timeout corto en lectura: si la tabla no responde, se pasa a la semilla.
_TIMEOUT_LECTURA_MS = int(os.getenv('CATALOGO_IMAGEN_TIMEOUT_MS', '2000'))
# Misma normalización que productos: recorta espacios y sufijo .0 de Excel.
_EXPR_CODIGO = (
    "regexp_replace("
    "regexp_replace(TRIM(BOTH FROM CAST(codigo_barras AS TEXT)), '\\s+', '', 'g'), "
    "'\\.0+$', '', 'g')"
)
_URL_NO_VACIA = (
    "url_imagen IS NOT NULL "
    "AND TRIM(BOTH FROM CAST(url_imagen AS TEXT)) <> '' "
    "AND LOWER(TRIM(BOTH FROM CAST(url_imagen AS TEXT))) "
    "NOT IN ('none', 'null', 'nan', 'n/a', '-')"
)


def _url_wsrv_oficial(url_origen):
    return (
        'https://wsrv.nl/?url='
        + quote(url_origen, safe='')
        + '&w=300&h=300&fit=cover&output=webp&q=80'
    )


# Activo en memoria: no depende de Supabase ni de catalogo_maestro_imagenes.
_URL_HARINA_PAN = _url_wsrv_oficial(
    'https://images.openfoodfacts.org/images/products/759/100/200/0547/front_es.24.400.jpg'
)
_URL_ACEITE_VATEL = _url_wsrv_oficial(
    'https://images.openfoodfacts.org/images/products/759/104/900/1903/front_es.3.400.jpg'
)

IMAGENES_CATALOGO_SEMILLA = {
    '7591001000011': _URL_HARINA_PAN,  # Harina PAN (código de prueba)
    '7591002000011': _URL_HARINA_PAN,  # Polar Harina PAN
    '7591002000547': _URL_HARINA_PAN,  # Harina PAN 1 kg (OFF)
    '7591001000035': _URL_ACEITE_VATEL,  # Aceite Vatel (código de prueba)
    '7591049001903': _URL_ACEITE_VATEL,  # Aceite Vatel 1 L (OFF)
}


def _url_semilla_por_codigo(codigo):
    clave = normalizar_codigo_barras(codigo) or ''
    return IMAGENES_CATALOGO_SEMILLA.get(clave)


def _completar_con_semilla(mapa, codigos):
    """Rellena faltantes desde IMAGENES_CATALOGO_SEMILLA (siempre, sin BD)."""
    resultado = dict(mapa or {})
    for codigo in codigos or []:
        clave = normalizar_codigo_barras(codigo) or ''
        if clave and clave not in resultado:
            semilla = IMAGENES_CATALOGO_SEMILLA.get(clave)
            if semilla:
                resultado[clave] = semilla
    return resultado


def _url_maestro_valida(valor):
    url = (texto_campo_imagen(valor, default=None) or '').strip()
    if url and url.startswith('https://'):
        return url
    return None


def _debug_imagen(mensaje, error=None):
    """Solo con LOCALIS_IMAGEN_DEBUG=1; no satura la consola en producción."""
    if os.getenv('LOCALIS_IMAGEN_DEBUG', '').strip().lower() not in ('1', 'true', 'yes'):
        return
    detalle = f': {type(error).__name__}' if error is not None else ''
    print(f'{_LOG} {mensaje}{detalle}')


def _postgresql_directo_disponible() -> bool:
    try:
        return using_postgres() and bool((DATABASE_URL or '').strip())
    except Exception:
        return False


def _postgrest_disponible() -> bool:
    try:
        return postgrest_http_configurado() and postgrest_http_habilitado()
    except Exception:
        return False


def _catalogo_disponible():
    try:
        registrar_modo_catalogo_maestro()
    except Exception:
        pass
    return _postgresql_directo_disponible() or _postgrest_disponible()


def _aplicar_timeout_lectura(cursor):
    if _TIMEOUT_LECTURA_MS <= 0:
        return
    try:
        cursor.execute(f'SET LOCAL statement_timeout = {_TIMEOUT_LECTURA_MS}')
    except Exception:
        pass


def _imagen_maestro_por_codigo_postgres(codigo):
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        _aplicar_timeout_lectura(cursor)
        cursor.execute(
            f"""
            SELECT url_imagen
            FROM {TABLA_CATALOGO_MAESTRO}
            WHERE {_EXPR_CODIGO} = ?
              AND {_URL_NO_VACIA}
            LIMIT 1
            """,
            (codigo,),
        )
        fila = cursor.fetchone()
        if not fila:
            return None
        url_raw = fila[0] if not isinstance(fila, dict) else fila.get('url_imagen')
        return _url_maestro_valida(url_raw)


def _imagen_maestro_por_codigo_postgrest(codigo):
    return _url_maestro_valida(consultar_imagen_maestro(codigo))


def _consultar_catalogo_supabase(codigo):
    """
    Fuente 1: tabla catalogo_maestro_imagenes (Postgres, luego PostgREST).
    Si un transporte falla, prueba el siguiente. Si responde vacío, no insiste.
    Nunca propaga la excepción.
    """
    transportes = []
    if _postgresql_directo_disponible():
        transportes.append(('postgres', _imagen_maestro_por_codigo_postgres))
    if _postgrest_disponible():
        transportes.append(('postgrest', _imagen_maestro_por_codigo_postgrest))

    for nombre, consultar in transportes:
        try:
            url = _url_maestro_valida(consultar(codigo))
            if url:
                return url
            return None
        except Exception as error:
            _debug_imagen(f'catalogo_maestro {nombre}', error)
            continue
    return None


def _consultar_semilla_memoria(codigo):
    """Fuente 2: diccionario en memoria (URLs públicas OFF/wsrv). Sin red."""
    try:
        clave = normalizar_codigo_barras(codigo) or codigo or ''
        return _url_maestro_valida(IMAGENES_CATALOGO_SEMILLA.get(clave))
    except Exception:
        return None


def url_semilla_catalogo(codigo_barras):
    """Respaldo en memoria. Nunca lanza; None si no hay código clave."""
    return _consultar_semilla_memoria(codigo_barras)


def _resolver_en_cascada(codigo):
    """Prueba cada fuente; un fallo silencioso pasa a la siguiente."""
    fuentes = (
        ('supabase', _consultar_catalogo_supabase),
        ('semilla', _consultar_semilla_memoria),
    )
    for nombre, consultar in fuentes:
        try:
            url = _url_maestro_valida(consultar(codigo))
            if url:
                return url
        except Exception as error:
            _debug_imagen(nombre, error)
            continue
    return None


def imagen_maestro_por_codigo(codigo_barras):
    """
    URL de imagen por código de barras, en cascada:
    1) catalogo_maestro_imagenes (Postgres / PostgREST)
    2) IMAGENES_CATALOGO_SEMILLA en memoria (respaldo público, sin HTTP)
    Error, timeout o vacío → siguiente fuente. Si todo falla, None.
    Nunca propaga excepción (no rompe el render de Flask).
    """
    try:
        codigo = normalizar_codigo_barras(codigo_barras)
        if not codigo:
            return None
        url = _resolver_en_cascada(codigo)
        if url:
            return url
        return _consultar_semilla_memoria(codigo)
    except Exception:
        try:
            return _consultar_semilla_memoria(codigo_barras)
        except Exception:
            return None


def _asegurar_indice_unico_codigo(cursor):
    """En Supabase el PK es id (uuid); el upsert va por codigo_barras."""
    cursor.execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_catalogo_maestro_codigo
        ON {TABLA_CATALOGO_MAESTRO} (codigo_barras)
        """
    )


def _guardar_imagen_maestro_postgres(codigo, url):
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        _asegurar_indice_unico_codigo(cursor)
        cursor.execute(
            f"""
            INSERT INTO {TABLA_CATALOGO_MAESTRO} (codigo_barras, url_imagen)
            VALUES (?, ?)
            ON CONFLICT (codigo_barras)
            DO UPDATE SET url_imagen = EXCLUDED.url_imagen
            """,
            (codigo, url),
        )
    return True


def guardar_imagen_maestro(codigo_barras, url_imagen):
    """Persiste URL optimizada en catalogo_maestro_imagenes (upsert)."""
    codigo = normalizar_codigo_barras(codigo_barras)
    url = _url_maestro_valida(url_imagen)
    if not codigo or not url or not _catalogo_disponible():
        return False

    try:
        if _postgresql_directo_disponible():
            return _guardar_imagen_maestro_postgres(codigo, url)

        if _postgrest_disponible():
            if guardar_imagen_maestro_http(codigo, url):
                return True
            if _postgresql_directo_disponible():
                return _guardar_imagen_maestro_postgres(codigo, url)
            return False

        return _guardar_imagen_maestro_postgres(codigo, url)
    except Exception as error:
        print(f'Error al guardar en catálogo maestro ({codigo}): {error}')
        return False


def _mapa_imagenes_maestro_postgres(lote):
    from backend.db import get_db_connection

    placeholders = ','.join(['?'] * len(lote))
    resultado = {}
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        _aplicar_timeout_lectura(cursor)
        cursor.execute(
            f"""
            SELECT codigo_barras, url_imagen
            FROM {TABLA_CATALOGO_MAESTRO}
            WHERE {_EXPR_CODIGO} IN ({placeholders})
              AND {_URL_NO_VACIA}
            """,
            lote,
        )
        for fila in cursor.fetchall():
            if isinstance(fila, dict):
                codigo_raw, url_raw = fila.get('codigo_barras'), fila.get('url_imagen')
            else:
                codigo_raw, url_raw = fila[0], fila[1]
            codigo_db = normalizar_codigo_barras(codigo_raw)
            url = _url_maestro_valida(url_raw)
            if codigo_db and url and codigo_db not in resultado:
                resultado[codigo_db] = url
    return resultado


def _mapa_imagenes_maestro_postgrest(lote):
    mapa = consultar_mapa_maestro(lote) or {}
    resultado = {}
    for codigo_raw, url_raw in mapa.items():
        codigo_db = normalizar_codigo_barras(codigo_raw)
        url = _url_maestro_valida(url_raw)
        if codigo_db and url and codigo_db not in resultado:
            resultado[codigo_db] = url
    return resultado


def _mapa_lote(lote):
    """Una pasada al catálogo remoto. No lanza: fallo → {}."""
    transportes = []
    if _postgresql_directo_disponible():
        transportes.append(('postgres', _mapa_imagenes_maestro_postgres))
    if _postgrest_disponible():
        transportes.append(('postgrest', _mapa_imagenes_maestro_postgrest))

    for nombre, consultar in transportes:
        try:
            return consultar(lote) or {}
        except Exception as error:
            _debug_imagen(f'lote {nombre}', error)
            continue
    return {}


def mapa_imagenes_maestro(codigos):
    """Mapa codigo → url: Supabase en lote, luego semilla en memoria para faltantes."""
    try:
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
        try:
            for inicio in range(0, len(normalizados), _LOTE_CONSULTA):
                lote = normalizados[inicio : inicio + _LOTE_CONSULTA]
                resultado.update(_mapa_lote(lote))
        except Exception as error:
            _debug_imagen('lote catalogo_maestro', error)
        return _completar_con_semilla(resultado, normalizados)
    except Exception:
        try:
            return _completar_con_semilla({}, codigos)
        except Exception:
            return {}


def sembrar_catalogo_maestro_imagenes():
    """Upsert de URLs de prueba en catalogo_maestro_imagenes."""
    guardados = 0
    for codigo, url in IMAGENES_CATALOGO_SEMILLA.items():
        if guardar_imagen_maestro(codigo, url):
            guardados += 1
        else:
            print(f'{_LOG} no se pudo sembrar catalogo_maestro codigo={codigo!r}')
    print(f'{_LOG} catalogo_maestro semilla: {guardados}/{len(IMAGENES_CATALOGO_SEMILLA)} URL(s)')
    return guardados

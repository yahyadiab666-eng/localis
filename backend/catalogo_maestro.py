"""Catálogo maestro de imágenes: PostgreSQL directo (preferido) + PostgREST httpx opcional."""

import os

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
from backend.utils import (
    normalizar_codigo_barras,
    url_imagen_local_valida,
    url_imagen_subida_storage_valida,
)

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
_CANDIDATOS_COL_URL = ('url_imagen', 'imagen_url', 'url')
_COL_URL_CACHE = None


# Respaldo en memoria: vacío a propósito (sin URLs de prueba hardcodeadas).
# Las fotos salen de catalogo_maestro_imagenes o de Supabase Storage.
IMAGENES_CATALOGO_SEMILLA = {}


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
    """Solo Storage o upload local. Nunca URLs remotas OFF/API."""
    return url_imagen_subida_storage_valida(valor) or url_imagen_local_valida(valor)


def _error_imagen(mensaje, error=None):
    """Siempre escribe el error real; no traga fallos de BD/red en silencio."""
    if error is not None:
        print(f'{_LOG} ERROR {mensaje}: {type(error).__name__}: {error}')
        return
    print(f'{_LOG} ERROR {mensaje}')


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


def _sql_url_no_vacia(col):
    return (
        f"{col} IS NOT NULL "
        f"AND TRIM(BOTH FROM CAST({col} AS TEXT)) <> '' "
        f"AND LOWER(TRIM(BOTH FROM CAST({col} AS TEXT))) "
        "NOT IN ('none', 'null', 'nan', 'n/a', '-')"
    )


def _columna_url_maestro(cursor=None):
    """Columna real de la URL (url_imagen o imagen_url). Nunca usa updated_at."""
    global _COL_URL_CACHE
    if _COL_URL_CACHE:
        return _COL_URL_CACHE
    nombres = set()
    try:
        if cursor is None:
            from backend.db import get_db_connection

            with get_db_connection() as conexion:
                return _columna_url_maestro(conexion.cursor())
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            """,
            (TABLA_CATALOGO_MAESTRO,),
        )
        for fila in cursor.fetchall():
            if isinstance(fila, dict):
                nombres.add(str(fila.get('column_name') or next(iter(fila.values()))))
            else:
                nombres.add(str(fila[0]))
    except Exception as error:
        _error_imagen('columnas catalogo_maestro', error)
        _COL_URL_CACHE = 'url_imagen'
        return _COL_URL_CACHE
    for cand in _CANDIDATOS_COL_URL:
        if cand in nombres:
            _COL_URL_CACHE = cand
            return cand
    _COL_URL_CACHE = 'url_imagen'
    return _COL_URL_CACHE


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
        col = _columna_url_maestro(cursor)
        cursor.execute(
            f"""
            SELECT {col} AS url_imagen
            FROM {TABLA_CATALOGO_MAESTRO}
            WHERE {_EXPR_CODIGO} = ?
              AND {_sql_url_no_vacia(col)}
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
            _error_imagen(f'catalogo_maestro {nombre}', error)
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
            _error_imagen(nombre, error)
            continue
    return None


def imagen_maestro_por_codigo(codigo_barras):
    """
    URL de imagen por código de barras:
    1) catalogo_maestro_imagenes (solo Storage o /static/uploads/)
    Error de BD/red se registra; vacío o fallo → None (la vista usa placeholder).
    """
    try:
        codigo = normalizar_codigo_barras(codigo_barras)
        if not codigo:
            return None
        url = _resolver_en_cascada(codigo)
        if url:
            return url
        return _consultar_semilla_memoria(codigo)
    except Exception as error:
        _error_imagen(f'imagen_maestro_por_codigo({codigo_barras!r})', error)
        try:
            return _consultar_semilla_memoria(codigo_barras)
        except Exception as error_semilla:
            _error_imagen('semilla catalogo_maestro', error_semilla)
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
        col = _columna_url_maestro(cursor)
        cursor.execute(
            f"""
            SELECT codigo_barras, {col} AS url_imagen
            FROM {TABLA_CATALOGO_MAESTRO}
            WHERE {_EXPR_CODIGO} IN ({placeholders})
              AND {_sql_url_no_vacia(col)}
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
            _error_imagen(f'lote {nombre}', error)
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
            _error_imagen('lote catalogo_maestro', error)
        return _completar_con_semilla(resultado, normalizados)
    except Exception as error:
        _error_imagen('mapa_imagenes_maestro', error)
        try:
            return _completar_con_semilla({}, codigos)
        except Exception as error_semilla:
            _error_imagen('completar semilla lote', error_semilla)
            return {}


def purgar_urls_imagen_artificiales():
    """Quita placeholders locales y Pexels de prueba. Conserva Storage, OFF y /static/uploads/."""
    from backend.db import get_db_connection

    sql_productos = """
        UPDATE productos
        SET imagen_url = NULL
        WHERE imagen_url IS NOT NULL
          AND (
            LOWER(CAST(imagen_url AS TEXT)) LIKE '%pexels.com%'
            OR LOWER(CAST(imagen_url AS TEXT)) LIKE '%default-product%'
            OR (
              CAST(imagen_url AS TEXT) LIKE '/static/%'
              AND CAST(imagen_url AS TEXT) NOT LIKE '/static/uploads/%'
            )
          )
    """
    sql_maestro = f"""
        DELETE FROM {TABLA_CATALOGO_MAESTRO}
        WHERE LOWER(CAST(url_imagen AS TEXT)) LIKE '%pexels.com%'
           OR CAST(url_imagen AS TEXT) LIKE '/static/%'
           OR LOWER(CAST(url_imagen AS TEXT)) LIKE '%default-product%'
    """
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(sql_productos)
            n_prod = cursor.rowcount
            cursor.execute(
                """
                UPDATE comercios
                SET logo_url = NULL
                WHERE logo_url IS NOT NULL
                  AND (
                    LOWER(CAST(logo_url AS TEXT)) LIKE '%openfoodfacts%'
                    OR LOWER(CAST(logo_url AS TEXT)) LIKE '%wsrv.nl%'
                    OR LOWER(CAST(logo_url AS TEXT)) LIKE '%pexels.com%'
                    OR CAST(logo_url AS TEXT) LIKE '/static/%'
                  )
                """
            )
            cursor.execute(
                """
                UPDATE comercios
                SET banner_url = NULL, imagen_portada = NULL
                WHERE (
                    banner_url IS NOT NULL
                    AND (
                      LOWER(CAST(banner_url AS TEXT)) LIKE '%openfoodfacts%'
                      OR LOWER(CAST(banner_url AS TEXT)) LIKE '%wsrv.nl%'
                      OR LOWER(CAST(banner_url AS TEXT)) LIKE '%pexels.com%'
                      OR CAST(banner_url AS TEXT) LIKE '/static/%'
                    )
                  )
                  OR (
                    imagen_portada IS NOT NULL
                    AND (
                      LOWER(CAST(imagen_portada AS TEXT)) LIKE '%openfoodfacts%'
                      OR LOWER(CAST(imagen_portada AS TEXT)) LIKE '%wsrv.nl%'
                      OR LOWER(CAST(imagen_portada AS TEXT)) LIKE '%pexels.com%'
                      OR CAST(imagen_portada AS TEXT) LIKE '/static/%'
                    )
                  )
                """
            )
            for col_sql in ('"URL del banner"', '"URL del logotipo"'):
                try:
                    cursor.execute(
                        f"""
                        UPDATE comercios
                        SET {col_sql} = NULL
                        WHERE {col_sql} IS NOT NULL
                          AND (
                            LOWER(CAST({col_sql} AS TEXT)) LIKE '%openfoodfacts%'
                            OR LOWER(CAST({col_sql} AS TEXT)) LIKE '%wsrv.nl%'
                            OR LOWER(CAST({col_sql} AS TEXT)) LIKE '%pexels.com%'
                          )
                        """
                    )
                except Exception as error_col:
                    print(f'{_LOG} aviso purga {col_sql}: {error_col}')
            cursor.execute(sql_maestro)
            n_mae = cursor.rowcount
        print(
            f'{_LOG} purga URLs artificiales: productos={n_prod} '
            f'catalogo_maestro={n_mae}'
        )
        return True
    except Exception as error:
        _error_imagen('purgar_urls_imagen_artificiales', error)
        return False


def sembrar_catalogo_maestro_imagenes():
    """Políticas Storage + relleno de fotos faltantes en segundo plano."""
    try:
        from backend.supabase_storage import asegurar_politicas_bucket_imagenes

        asegurar_politicas_bucket_imagenes()
    except Exception as error:
        _error_imagen('politicas storage', error)
    try:
        from backend.image_lookup import programar_relleno_imagenes_catalogo

        programar_relleno_imagenes_catalogo()
    except Exception as error:
        _error_imagen('relleno catalogo', error)
    return True

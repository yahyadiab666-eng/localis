"""Catálogo maestro de imágenes: PostgreSQL directo (preferido) + PostgREST httpx opcional."""

from backend.db import DATABASE_URL, using_postgres
from backend.postgrest_http import (
    consultar_imagen_maestro,
    consultar_mapa_maestro,
    guardar_imagen_maestro_http,
    postgrest_http_configurado,
)
from backend.supabase_client import (
    postgrest_circuito_abierto,
    postgrest_http_habilitado,
    registrar_modo_catalogo_maestro,
)
from backend.utils import normalizar_codigo_barras, texto_campo_imagen

TABLA_CATALOGO_MAESTRO = 'catalogo_maestro_imagenes'
_LOTE_CONSULTA = 100
_LOG = '[Localis Imagen]'
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


def _url_maestro_valida(valor):
    url = (texto_campo_imagen(valor, default=None) or '').strip()
    if url and url.startswith('https://'):
        return url
    return None


def _postgresql_directo_disponible() -> bool:
    return using_postgres() and bool((DATABASE_URL or '').strip())


def _postgrest_disponible() -> bool:
    return postgrest_http_configurado() and postgrest_http_habilitado()


def _catalogo_disponible():
    registrar_modo_catalogo_maestro()
    return _postgresql_directo_disponible() or _postgrest_disponible()


def _imagen_maestro_por_codigo_postgres(codigo):
    from backend.db import get_db_connection

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
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
                print(f'{_LOG} catalogo_maestro sin URL para codigo={codigo!r}')
                return None
            url_raw = fila[0] if not isinstance(fila, dict) else fila.get('url_imagen')
            url = _url_maestro_valida(url_raw)
            if not url:
                print(
                    f'{_LOG} catalogo_maestro URL invalida o nula '
                    f'codigo={codigo!r} raw={url_raw!r}'
                )
            return url
    except Exception as error:
        print(f'{_LOG} error consultando catalogo_maestro ({codigo}): {error}')
        return None


def _imagen_maestro_por_codigo_postgrest(codigo):
    url = consultar_imagen_maestro(codigo)
    return _url_maestro_valida(url)


def _resolver_imagen_maestro(codigo):
    if _postgresql_directo_disponible():
        return _imagen_maestro_por_codigo_postgres(codigo)

    if _postgrest_disponible():
        url = _imagen_maestro_por_codigo_postgrest(codigo)
        if url or not postgrest_circuito_abierto():
            return url

    if _postgresql_directo_disponible():
        return _imagen_maestro_por_codigo_postgres(codigo)
    return None


def imagen_maestro_por_codigo(codigo_barras):
    """URL de imagen para un código de barras en el catálogo maestro."""
    codigo = normalizar_codigo_barras(codigo_barras)
    if not codigo:
        print(f'{_LOG} codigo_barras vacio o nulo; no se consulta catalogo_maestro')
        return None
    if not _catalogo_disponible():
        print(
            f'{_LOG} catalogo_maestro no disponible; no hay respaldo para '
            f'codigo={codigo!r}'
        )
        return None
    try:
        return _resolver_imagen_maestro(codigo)
    except Exception as error:
        print(f'{_LOG} fallo al resolver catalogo_maestro ({codigo}): {error}')
        return None


def _guardar_imagen_maestro_postgres(codigo, url):
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
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
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
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
    except Exception as error:
        print(f'{_LOG} error lote catalogo_maestro: {error}')
        return {}
    return resultado


def _mapa_imagenes_maestro_postgrest(lote):
    mapa = consultar_mapa_maestro(lote)
    resultado = {}
    for codigo_raw, url_raw in mapa.items():
        codigo_db = normalizar_codigo_barras(codigo_raw)
        url = _url_maestro_valida(url_raw)
        if codigo_db and url and codigo_db not in resultado:
            resultado[codigo_db] = url
    return resultado


def _mapa_lote(lote):
    if _postgresql_directo_disponible():
        return _mapa_imagenes_maestro_postgres(lote)

    if _postgrest_disponible():
        mapa = _mapa_imagenes_maestro_postgrest(lote)
        if mapa or not postgrest_circuito_abierto():
            return mapa

    if _postgresql_directo_disponible():
        return _mapa_imagenes_maestro_postgres(lote)
    return {}


def mapa_imagenes_maestro(codigos):
    """Mapa codigo_barras normalizado -> url_imagen desde el catálogo maestro en lote."""
    normalizados = []
    vistos = set()
    for codigo in codigos or []:
        limpio = normalizar_codigo_barras(codigo)
        if limpio and limpio not in vistos:
            vistos.add(limpio)
            normalizados.append(limpio)
    if not normalizados:
        return {}
    if not _catalogo_disponible():
        print(f'{_LOG} catalogo_maestro no disponible; lote de {len(normalizados)} codigo(s) sin respaldo')
        return {}

    resultado = {}
    try:
        for inicio in range(0, len(normalizados), _LOTE_CONSULTA):
            lote = normalizados[inicio : inicio + _LOTE_CONSULTA]
            resultado.update(_mapa_lote(lote))
    except Exception as error:
        print(f'Error al consultar catálogo maestro en lote: {error}')
    return resultado

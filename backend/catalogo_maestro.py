"""Catálogo maestro de imágenes: Supabase (PostgREST) con fallback a PostgreSQL."""

from backend.supabase_client import es_error_red_supabase, supabase, supabase_api_habilitado
from backend.utils import normalizar_codigo_barras, texto_campo_imagen

TABLA_CATALOGO_MAESTRO = 'catalogo_maestro_imagenes'
_LOTE_CONSULTA = 100


def _avisar_respaldo_postgres(error, contexto, estado_log=None):
    """Un solo aviso por petición ante fallos de red repetidos."""
    if estado_log is not None and es_error_red_supabase(error):
        if estado_log.get('red'):
            return
        estado_log['red'] = True
        print(
            'Catálogo maestro: Supabase no alcanzable por red '
            f'({type(error).__name__}). Usando PostgreSQL como respaldo.'
        )
        return

    detalle = f'{type(error).__name__}: {error}'
    if es_error_red_supabase(error):
        print(
            f'Catálogo maestro: fallo de red con Supabase ({detalle}). '
            'Usando PostgreSQL como respaldo.'
        )
    else:
        print(
            f'Catálogo maestro ({contexto}): fallo Supabase API ({detalle}). '
            'Usando PostgreSQL como respaldo.'
        )


def _url_maestro_valida(valor):
    url = (texto_campo_imagen(valor, default=None) or '').strip()
    if url and url.startswith('https://'):
        return url
    return None


def _catalogo_disponible():
    from backend.db import DATABASE_URL

    return supabase_api_habilitado() or bool((DATABASE_URL or '').strip())


def _imagen_maestro_por_codigo_supabase(codigo):
    response = (
        supabase.table(TABLA_CATALOGO_MAESTRO)
        .select('url_imagen')
        .eq('codigo_barras', codigo)
        .limit(1)
        .execute()
    )
    if not response.data:
        return None
    return _url_maestro_valida(response.data[0].get('url_imagen'))


def _imagen_maestro_por_codigo_postgres(codigo):
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            f"""
            SELECT url_imagen
            FROM {TABLA_CATALOGO_MAESTRO}
            WHERE codigo_barras = ?
            LIMIT 1
            """,
            (codigo,),
        )
        fila = cursor.fetchone()
        if not fila:
            return None
        url_raw = fila[0] if not isinstance(fila, dict) else fila.get('url_imagen')
        return _url_maestro_valida(url_raw)


def _consultar_supabase_con_respaldo(codigo, operacion, fallback, estado_log=None):
    """
    Intenta PostgREST; ante fallo de red o API usa PostgreSQL directo.
    """
    if not supabase_api_habilitado():
        return fallback(codigo)

    try:
        return operacion(codigo)
    except Exception as error:
        _avisar_respaldo_postgres(error, codigo, estado_log)
        try:
            return fallback(codigo)
        except Exception as pg_error:
            print(f'Catálogo maestro: fallback PostgreSQL también falló: {pg_error}')
            return None


def imagen_maestro_por_codigo(codigo_barras):
    """URL de imagen para un código de barras en el catálogo maestro."""
    codigo = normalizar_codigo_barras(codigo_barras)
    if not codigo or not _catalogo_disponible():
        return None

    return _consultar_supabase_con_respaldo(
        codigo,
        _imagen_maestro_por_codigo_supabase,
        _imagen_maestro_por_codigo_postgres,
    )


def _guardar_imagen_maestro_supabase(codigo, url):
    actualizado = (
        supabase.table(TABLA_CATALOGO_MAESTRO)
        .update({'url_imagen': url})
        .eq('codigo_barras', codigo)
        .execute()
    )
    if actualizado.data:
        return True

    try:
        supabase.table(TABLA_CATALOGO_MAESTRO).upsert(
            {'codigo_barras': codigo, 'url_imagen': url},
            on_conflict='codigo_barras',
        ).execute()
        return True
    except Exception:
        supabase.table(TABLA_CATALOGO_MAESTRO).insert(
            {'codigo_barras': codigo, 'url_imagen': url}
        ).execute()
        return True


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
        if supabase_api_habilitado():
            try:
                return _guardar_imagen_maestro_supabase(codigo, url)
            except Exception as error:
                print(
                    f'Catálogo maestro: no se pudo guardar vía Supabase ({error}). '
                    'Intentando PostgreSQL.'
                )
                return _guardar_imagen_maestro_postgres(codigo, url)
        return _guardar_imagen_maestro_postgres(codigo, url)
    except Exception as error:
        print(f'Error al guardar en catálogo maestro ({codigo}): {error}')
        return False


def _mapa_imagenes_maestro_supabase(lote):
    response = (
        supabase.table(TABLA_CATALOGO_MAESTRO)
        .select('codigo_barras, url_imagen')
        .in_('codigo_barras', lote)
        .execute()
    )
    resultado = {}
    for fila in response.data or []:
        codigo_db = normalizar_codigo_barras(fila.get('codigo_barras'))
        url = _url_maestro_valida(fila.get('url_imagen'))
        if codigo_db and url and codigo_db not in resultado:
            resultado[codigo_db] = url
    return resultado


def _mapa_imagenes_maestro_postgres(lote):
    from backend.db import get_db_connection

    placeholders = ','.join(['?'] * len(lote))
    resultado = {}
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            f"""
            SELECT codigo_barras, url_imagen
            FROM {TABLA_CATALOGO_MAESTRO}
            WHERE codigo_barras IN ({placeholders})
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


def _mapa_lote_con_respaldo(lote, estado_log=None):
    if not supabase_api_habilitado():
        return _mapa_imagenes_maestro_postgres(lote)

    try:
        return _mapa_imagenes_maestro_supabase(lote)
    except Exception as error:
        _avisar_respaldo_postgres(error, 'lote', estado_log)
        return _mapa_imagenes_maestro_postgres(lote)


def mapa_imagenes_maestro(codigos):
    """Mapa codigo_barras normalizado → url_imagen desde el catálogo maestro en lote."""
    normalizados = []
    vistos = set()
    for codigo in codigos or []:
        limpio = normalizar_codigo_barras(codigo)
        if limpio and limpio not in vistos:
            vistos.add(limpio)
            normalizados.append(limpio)
    if not normalizados or not _catalogo_disponible():
        return {}

    resultado = {}
    estado_log = {'red': False}
    try:
        for inicio in range(0, len(normalizados), _LOTE_CONSULTA):
            lote = normalizados[inicio : inicio + _LOTE_CONSULTA]
            resultado.update(_mapa_lote_con_respaldo(lote, estado_log))
    except Exception as error:
        print(f'Error al consultar catálogo maestro en lote: {error}')
    return resultado

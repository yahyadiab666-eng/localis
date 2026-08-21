"""Catálogo maestro de imágenes en Supabase (tabla catalogo_maestro_imagenes)."""

from backend.supabase_client import supabase
from backend.utils import normalizar_codigo_barras, texto_campo_imagen

TABLA_CATALOGO_MAESTRO = 'catalogo_maestro_imagenes'
_LOTE_CONSULTA = 100


def _url_maestro_valida(valor):
    url = (texto_campo_imagen(valor, default=None) or '').strip()
    if url and url.startswith('https://'):
        return url
    return None


def imagen_maestro_por_codigo(codigo_barras):
    """URL de imagen para un código de barras en el catálogo maestro."""
    codigo = normalizar_codigo_barras(codigo_barras)
    if not codigo or not supabase:
        return None
    try:
        respuesta = (
            supabase.table(TABLA_CATALOGO_MAESTRO)
            .select('url_imagen')
            .eq('codigo_barras', codigo)
            .limit(1)
            .execute()
        )
        for fila in respuesta.data or []:
            url = _url_maestro_valida(fila.get('url_imagen'))
            if url:
                return url
    except Exception as error:
        print(f'Error al consultar catálogo maestro ({codigo}): {error}')
    return None


def guardar_imagen_maestro(codigo_barras, url_imagen):
    """Persiste URL optimizada en catalogo_maestro_imagenes (insert o update)."""
    codigo = normalizar_codigo_barras(codigo_barras)
    url = _url_maestro_valida(url_imagen)
    if not codigo or not url or not supabase:
        return False

    existente = imagen_maestro_por_codigo(codigo)
    if existente == url:
        return True

    payload = {'codigo_barras': codigo, 'url_imagen': url}
    try:
        supabase.table(TABLA_CATALOGO_MAESTRO).upsert(
            payload,
            on_conflict='codigo_barras',
        ).execute()
        return True
    except Exception as error:
        print(f'Aviso upsert catálogo maestro ({codigo}): {error}')

    try:
        if existente:
            supabase.table(TABLA_CATALOGO_MAESTRO).update(
                {'url_imagen': url},
            ).eq('codigo_barras', codigo).execute()
        else:
            supabase.table(TABLA_CATALOGO_MAESTRO).insert(payload).execute()
        return True
    except Exception as error:
        print(f'Error al guardar en catálogo maestro ({codigo}): {error}')
        return False


def mapa_imagenes_maestro(codigos):
    """Mapa codigo_barras normalizado → url_imagen desde el catálogo maestro."""
    normalizados = []
    vistos = set()
    for codigo in codigos or []:
        limpio = normalizar_codigo_barras(codigo)
        if limpio and limpio not in vistos:
            vistos.add(limpio)
            normalizados.append(limpio)
    if not normalizados or not supabase:
        return {}

    resultado = {}
    for inicio in range(0, len(normalizados), _LOTE_CONSULTA):
        lote = normalizados[inicio : inicio + _LOTE_CONSULTA]
        try:
            respuesta = (
                supabase.table(TABLA_CATALOGO_MAESTRO)
                .select('codigo_barras, url_imagen')
                .in_('codigo_barras', lote)
                .execute()
            )
            for fila in respuesta.data or []:
                codigo = normalizar_codigo_barras(fila.get('codigo_barras'))
                url = _url_maestro_valida(fila.get('url_imagen'))
                if codigo and url and codigo not in resultado:
                    resultado[codigo] = url
        except Exception as error:
            print(f'Error al consultar catálogo maestro en lote: {error}')
    return resultado

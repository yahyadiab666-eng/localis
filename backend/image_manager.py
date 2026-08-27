"""
Gestión centralizada de imágenes para Localis.

Flujo:
1. Catálogo maestro Supabase (cache permanente por código de barras)
2. Catálogos oficiales (OpenFoodFacts por EAN) con filtro de calidad
3. Optimización wsrv.nl + persistencia automática en catálogo maestro
4. None si no hay imagen apta (sin placeholder genérico)
"""

import time
from urllib.parse import quote

import requests

from backend.catalogo_maestro import (
    guardar_imagen_maestro,
    imagen_maestro_por_codigo,
    mapa_imagenes_maestro,
)
from backend.utils import (
    imagen_url_almacenada,
    normalizar_codigo_barras,
    url_imagen_subida_storage_valida,
)

_USER_AGENT = 'LocalisApp/1.0 (Localis; contacto@localis.app)'
_OFF_API = 'https://world.openfoodfacts.org/api/v2/product/{codigo}.json'
_WSRV_ANCHO = 300
_WSRV_ALTO = 300
_WSRV_FIT = 'cover'
_WSRV_FORMATO = 'webp'
_WSRV_CALIDAD = 80
_PAUSA_OFF_SEG = 0.12
_MIN_LADO_PX = 200

_HOSTS_OFICIALES = (
    'openfoodfacts.org',
    'static.openfoodfacts.org',
    'images.openfoodfacts.org',
)

_PATRONES_RECHAZO = (
    'thumb',
    'thumbnail',
    '_small',
    '_100.',
    '_150.',
    '_200.',
    'avatar',
    'selfie',
    '/user/',
    '/users/',
    'profile',
    'placeholder',
    'no-image',
    'no_image',
    '.svg',
    'emoji',
    'icon',
)


def optimizar_url_wsrv(url_original):
    """Proxy WebP vía wsrv.nl: 300×300, cover, q=80."""
    if not url_original or not str(url_original).startswith('http'):
        return None
    if 'wsrv.nl' in str(url_original).lower():
        return url_original
    if any(
        bad in str(url_original).lower()
        for bad in ('.svg', 'placeholder', 'default-product')
    ):
        return None
    url_enc = quote(url_original, safe='')
    return (
        f'https://wsrv.nl/?url={url_enc}'
        f'&w={_WSRV_ANCHO}&h={_WSRV_ALTO}&fit={_WSRV_FIT}'
        f'&output={_WSRV_FORMATO}&q={_WSRV_CALIDAD}'
    )


def _url_manual_valida(imagen_manual):
    """URL ya en BD: Storage (subida manual) o https externa (texto/catálogo)."""
    manual = imagen_url_almacenada(imagen_manual)
    if not manual:
        return None
    if url_imagen_subida_storage_valida(manual):
        return manual
    return optimizar_url_wsrv(manual) or manual


def _url_pasa_filtro_calidad(url, ancho=None, alto=None):
    """Descarta miniaturas, SVG, placeholders y URLs sospechosas."""
    if not url or not str(url).startswith('https://'):
        return False
    lower = str(url).lower()
    if any(patron in lower for patron in _PATRONES_RECHAZO):
        return False
    if ancho is not None and alto is not None:
        try:
            if int(ancho) < _MIN_LADO_PX or int(alto) < _MIN_LADO_PX:
                return False
        except (TypeError, ValueError):
            pass
    return any(host in lower for host in _HOSTS_OFICIALES)


def _candidatos_imagen_off(producto):
    """Prioriza fotos frontales oficiales de empaque (OpenFoodFacts)."""
    if not producto:
        return []

    candidatos = []
    vistos = set()

    def _agregar(url, ancho=None, alto=None):
        if not url or url in vistos:
            return
        vistos.add(url)
        candidatos.append((url, ancho, alto))

    imagenes = producto.get('images') or {}
    frente = imagenes.get('front') or {}
    for clave in ('full', '400', '200'):
        meta = frente.get(clave) or {}
        url = meta.get('url') or meta.get('imgurl')
        _agregar(url, meta.get('w'), meta.get('h'))

    seleccion = (producto.get('selected_images') or {}).get('front') or {}
    display = seleccion.get('display') or {}
    for url in display.values():
        _agregar(url)

    for campo in ('image_front_url', 'image_url'):
        _agregar(producto.get(campo))

    return candidatos


def _buscar_openfoodfacts_por_codigo(codigo_barras):
    """Consulta exclusiva por código EAN/UPC en OpenFoodFacts."""
    codigo = normalizar_codigo_barras(codigo_barras)
    if not codigo or not codigo.isdigit() or len(codigo) < 8:
        return None

    try:
        respuesta = requests.get(
            _OFF_API.format(codigo=codigo),
            headers={'User-Agent': _USER_AGENT},
            timeout=5,
        )
        if respuesta.status_code != 200:
            return None
        datos = respuesta.json() or {}
        if datos.get('status') != 1:
            return None
        producto = datos.get('product') or {}
        if not producto.get('code') and not producto.get('id'):
            return None

        for url, ancho, alto in _candidatos_imagen_off(producto):
            if _url_pasa_filtro_calidad(url, ancho, alto):
                return url
    except Exception as error:
        print(f'Error OpenFoodFacts ({codigo}): {error}')
    return None


def _descubrir_y_persistir_oficial(codigo_barras):
    """
    Busca en catálogo oficial, optimiza con wsrv.nl y guarda en catálogo maestro.
    Retorna URL optimizada o None.
    """
    codigo = normalizar_codigo_barras(codigo_barras)
    if not codigo:
        return None

    url_origen = _buscar_openfoodfacts_por_codigo(codigo)
    if not url_origen:
        return None

    url_final = optimizar_url_wsrv(url_origen)
    if not url_final:
        return None

    guardar_imagen_maestro(codigo, url_final)
    return url_final


def resolver_imagen(
    codigo_barras=None,
    imagen_manual=None,
    mapa_maestro=None,
    *,
    para_escritura=False,
):
    """
    Resuelve la imagen de un producto.

    para_escritura=True → None si no hay imagen (no persistir en productos).
    para_escritura=False → None si no hay imagen (vista sin comodín).
    """
    manual = _url_manual_valida(imagen_manual)
    if manual:
        return manual

    codigo = normalizar_codigo_barras(codigo_barras)
    if codigo:
        url = None
        if mapa_maestro is not None:
            url = mapa_maestro.get(codigo)
        if not url:
            url = imagen_maestro_por_codigo(codigo)
        if url:
            if mapa_maestro is not None:
                mapa_maestro[codigo] = url
            return url

        url = _descubrir_y_persistir_oficial(codigo)
        if url:
            if mapa_maestro is not None:
                mapa_maestro[codigo] = url
            return url

    return None


def resolver_imagen_escritura(
    imagen_manual=None,
    codigo_barras=None,
    mapa_maestro=None,
):
    """URL para guardar en PostgreSQL (productos). NULL si no hay imagen real."""
    return resolver_imagen(
        codigo_barras=codigo_barras,
        imagen_manual=imagen_manual,
        mapa_maestro=mapa_maestro,
        para_escritura=True,
    )


def resolver_imagen_catalogo(
    imagen_url=None,
    codigo_barras=None,
    mapa_maestro=None,
):
    """URL para mostrar en catálogo. Solo lectura: PostgreSQL → catálogo maestro."""
    manual = imagen_url_almacenada(imagen_url)
    if manual:
        return manual

    codigo = normalizar_codigo_barras(codigo_barras)
    if not codigo:
        return None

    if mapa_maestro is not None:
        return mapa_maestro.get(codigo)

    return imagen_maestro_por_codigo(codigo) or None


def completar_mapa_imagenes(codigos, mapa_maestro=None, buscar_oficial=True):
    """
    Completa mapa codigo → url_imagen.
    Consulta catálogo maestro en lote; opcionalmente OpenFoodFacts para faltantes.
    """
    mapa = dict(mapa_maestro or {})
    normalizados = []
    vistos = set()
    for codigo in codigos or []:
        limpio = normalizar_codigo_barras(codigo)
        if limpio and limpio not in vistos:
            vistos.add(limpio)
            normalizados.append(limpio)

    faltantes = [c for c in normalizados if c not in mapa]
    if faltantes:
        mapa.update(mapa_imagenes_maestro(faltantes))
        faltantes = [c for c in faltantes if c not in mapa]

    if not buscar_oficial:
        return mapa

    for codigo in faltantes:
        url = _descubrir_y_persistir_oficial(codigo)
        if url:
            mapa[codigo] = url
        if _PAUSA_OFF_SEG:
            time.sleep(_PAUSA_OFF_SEG)

    return mapa


def preparar_mapa_imagenes_importacion(productos, snapshot_imagenes=None):
    """
    Precalcula imágenes antes del INSERT masivo (fuera del lock de BD).
    Respeta URL manual del CSV y snapshot del comercio; completa vía maestro + OFF.
    """
    snapshot_imagenes = snapshot_imagenes or {}
    mapa = dict(snapshot_imagenes)
    codigos_a_resolver = []
    vistos = set()

    for prod in productos or []:
        if imagen_url_almacenada(prod.get('imagen_url')):
            continue
        codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
        if not codigo or codigo in mapa or codigo in vistos:
            continue
        vistos.add(codigo)
        codigos_a_resolver.append(codigo)

    return completar_mapa_imagenes(codigos_a_resolver, mapa_maestro=mapa, buscar_oficial=True)

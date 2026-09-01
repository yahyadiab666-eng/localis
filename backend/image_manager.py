"""
Gestión centralizada de imágenes para Localis.

Flujo:
1. Catálogo maestro (cache por código de barras)
2. Cascada multifuente según categoría (Food / Products / Beauty Facts, Wikimedia)
3. Filtro de calidad visual (resolución, proporción, nitidez)
4. Espejo a Storage si hay service_role; si no, URL oficial persistida
"""

import socket
import time
import unicodedata
from urllib.parse import quote

import requests

from backend.catalogo_maestro import (
    guardar_imagen_maestro,
    imagen_maestro_por_codigo,
    mapa_imagenes_maestro,
)
from backend.image_quality import (
    MAX_BYTES_INSPECCION,
    MIN_LADO_PX,
    evaluar_imagen_bytes,
    metadatos_pasan_umbral,
    registrar_rechazo,
)
from backend.image_sources import (
    FAMILIA_ALIMENTOS,
    buscar_wikimedia,
    clasificar_familia,
    fuentes_para_familia,
    hits_por_nombre,
    producto_por_codigo,
    usa_wikimedia,
)
from backend.utils import (
    imagen_url_almacenada,
    normalizar_codigo_barras,
    url_imagen_catalogo_valida,
    url_imagen_local_valida,
    url_imagen_subida_storage_valida,
)

_USER_AGENT = 'LocalisApp/1.0 (Localis; contacto@localis.app)'
_WSRV_ANCHO = 400
_WSRV_ALTO = 400
_WSRV_FIT = 'contain'
_WSRV_FORMATO = 'webp'
_WSRV_CALIDAD = 82
_PAUSA_OFF_SEG = 0.12
_OFF_TIMEOUT_SEG = 4
_LOG_IMAGEN = '[Localis Imagen]'
_STOP_NOMBRE = frozenset({
    'de', 'la', 'el', 'los', 'las', 'del', 'y', 'en', 'con',
    'kg', 'g', 'l', 'ml', 'un', 'una', 'refresco',
})
_GENERIC_FOOD = frozenset({
    'harina', 'arroz', 'aceite', 'cafe', 'pasta', 'spaghetti', 'leche',
    'atun', 'mantequilla', 'panela', 'refresco', 'pan',
})
_ERRORES_RED_IMAGEN = (
    requests.Timeout,
    requests.ConnectionError,
    requests.RequestException,
    socket.gaierror,
    socket.timeout,
    TimeoutError,
    OSError,
)

_HOSTS_OFICIALES = (
    'openfoodfacts.org',
    'static.openfoodfacts.org',
    'images.openfoodfacts.org',
    'openproductsfacts.org',
    'openbeautyfacts.org',
    'openpetfoodfacts.org',
    'upload.wikimedia.org',
    'wikipedia.org',
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


def _advertir_fallo_imagen(contexto, error):
    print(f'{_LOG_IMAGEN} aviso {contexto}: {type(error).__name__}: {error}')


def _http_get_imagen(url, headers=None):
    """GET externo de imagen/metadatos. Timeout corto; nunca propaga red/DNS."""
    try:
        return requests.get(
            url,
            headers=headers or {'User-Agent': _USER_AGENT},
            timeout=_OFF_TIMEOUT_SEG,
        )
    except _ERRORES_RED_IMAGEN as error:
        _advertir_fallo_imagen(url, error)
        return None
    except Exception as error:
        _advertir_fallo_imagen(url, error)
        return None


_CALIDAD_CACHE = {}


def optimizar_url_wsrv(url_original):
    """Proxy WebP vía wsrv.nl: 400×400 contain sobre fondo blanco."""
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
        f'&cbg=white&output={_WSRV_FORMATO}&q={_WSRV_CALIDAD}'
    )


def _url_manual_valida(imagen_manual):
    """URL de Storage, catálogo oficial o foto local subida por el comerciante."""
    return (
        url_imagen_catalogo_valida(imagen_manual)
        or url_imagen_subida_storage_valida(imagen_manual)
        or url_imagen_local_valida(imagen_manual)
    )


def _url_pasa_filtro_calidad(url, ancho=None, alto=None):
    """Descarta miniaturas, SVG, placeholders y URLs fuera de catálogos oficiales."""
    if not url or not str(url).startswith('https://'):
        return False
    lower = str(url).lower()
    if any(patron in lower for patron in _PATRONES_RECHAZO):
        return False
    meta = metadatos_pasan_umbral(ancho, alto, min_lado=MIN_LADO_PX)
    if meta is False:
        return False
    return any(host in lower for host in _HOSTS_OFICIALES)


def _inspeccionar_bytes_url(url):
    """Descarga acotada y evalúa nitidez/resolución. None si no se pudo leer."""
    cached = _CALIDAD_CACHE.get(url)
    if cached is not None:
        return cached
    respuesta = _http_get_imagen(url)
    if respuesta is None or respuesta.status_code != 200:
        _CALIDAD_CACHE[url] = False
        return False
    data = respuesta.content or b''
    if len(data) > MAX_BYTES_INSPECCION:
        registrar_rechazo(url, 'descarga demasiado pesada')
        _CALIDAD_CACHE[url] = False
        return False
    resultado = evaluar_imagen_bytes(data)
    ok = bool(resultado.get('ok'))
    if not ok:
        registrar_rechazo(url, resultado.get('motivo'))
    _CALIDAD_CACHE[url] = ok
    if len(_CALIDAD_CACHE) > 256:
        _CALIDAD_CACHE.clear()
    return ok


def _confirmar_url_catalogo(url, ancho=None, alto=None, inspeccionar=False):
    """Heurística de URL + umbral de tamaño; Pillow si hace falta."""
    if not _url_pasa_filtro_calidad(url, ancho, alto):
        return None
    meta = metadatos_pasan_umbral(ancho, alto)
    if meta is False:
        registrar_rechazo(url, f'metadatos {ancho}x{alto}')
        return None
    necesita_bytes = inspeccionar or meta is None
    if 'wikimedia' in str(url).lower() or 'wikipedia' in str(url).lower():
        necesita_bytes = True
    if necesita_bytes and not _inspeccionar_bytes_url(url):
        return None
    return url_imagen_catalogo_valida(url) or url


def _primera_url_producto_facts(producto, inspeccionar=False):
    for url, ancho, alto in _candidatos_imagen_off(producto):
        confirmada = _confirmar_url_catalogo(
            url, ancho, alto, inspeccionar=inspeccionar
        )
        if confirmada:
            return confirmada
    return None


def _candidatos_imagen_off(producto):
    """Prioriza fotos frontales oficiales de empaque (Open Facts)."""
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
    """Compat: código en Open Food Facts (primera fuente de alimentos)."""
    return _buscar_codigo_en_fuentes(codigo_barras, FAMILIA_ALIMENTOS)


def _slug_archivo(valor):
    texto = ''.join(
        ch for ch in unicodedata.normalize('NFKD', str(valor or ''))
        if not unicodedata.combining(ch)
    )
    limpio = ''.join(ch.lower() if ch.isalnum() else '-' for ch in texto)
    while '--' in limpio:
        limpio = limpio.replace('--', '-')
    return (limpio.strip('-') or 'producto')[:40]


def _tokens_nombre(nombre):
    texto = ''.join(
        ch for ch in unicodedata.normalize('NFKD', str(nombre or '').lower())
        if not unicodedata.combining(ch)
    )
    texto = texto.replace('x', ' ')
    crudos = []
    for parte in texto.replace('/', ' ').split():
        parte = parte.strip('.,;()[]')
        if not parte or parte in _STOP_NOMBRE:
            continue
        if any(ch.isdigit() for ch in parte) and len(parte) <= 5:
            continue
        crudos.append(parte)
    return crudos


def _texto_hit(valor):
    if valor is None:
        return ''
    if isinstance(valor, (list, tuple)):
        return ' '.join(str(item) for item in valor if item)
    return str(valor)


def _score_hit_nombre(tokens, hit):
    if not tokens:
        return 0
    nombre = ' '.join(
        filter(
            None,
            (
                _texto_hit(hit.get('product_name')),
                _texto_hit(hit.get('product_name_es')),
                _texto_hit(hit.get('product_name_en')),
                _texto_hit(hit.get('brands')),
            ),
        )
    ).lower()
    nombre_norm = ''.join(
        ch for ch in unicodedata.normalize('NFKD', nombre)
        if not unicodedata.combining(ch)
    )
    aciertos = sum(1 for tok in tokens if tok in nombre_norm)
    if aciertos == 0:
        return 0
    if len(tokens) == 1:
        return aciertos * 2
    if aciertos >= 2 or (aciertos == 1 and len(tokens[0]) >= 5):
        return aciertos
    return 0


def _url_imagen_hit_off(hit, inspeccionar=True):
    for campo in ('image_front_url', 'image_url', 'image_small_url'):
        url = hit.get(campo)
        if campo == 'image_small_url':
            continue
        confirmada = _confirmar_url_catalogo(url, inspeccionar=inspeccionar)
        if confirmada:
            return confirmada
    producto = hit.get('product') if isinstance(hit.get('product'), dict) else hit
    return _primera_url_producto_facts(producto, inspeccionar=inspeccionar)


def _mejor_hit_por_nombre(fuente, nombre):
    tokens = _tokens_nombre(nombre)
    if not tokens:
        return None
    consulta = ' '.join(tokens[:4])
    try:
        hits = hits_por_nombre(fuente, consulta)
        mejor = None
        mejor_score = 0
        for hit in hits:
            score = _score_hit_nombre(tokens, hit)
            if score <= mejor_score:
                continue
            url = _url_imagen_hit_off(hit, inspeccionar=True)
            if not url:
                continue
            mejor = url
            mejor_score = score
        return mejor
    except Exception as error:
        _advertir_fallo_imagen(f'{fuente.get("id")} nombre={nombre!r}', error)
        return None


def _buscar_openfoodfacts_por_nombre(nombre):
    """Compat: búsqueda por nombre en Open Food Facts."""
    fuentes = fuentes_para_familia(FAMILIA_ALIMENTOS)
    if not fuentes:
        return None
    return _mejor_hit_por_nombre(fuentes[0], nombre)


def _buscar_codigo_en_fuentes(codigo_barras, familia):
    codigo = normalizar_codigo_barras(codigo_barras)
    if not codigo or not codigo.isdigit() or len(codigo) < 8:
        return None
    for fuente in fuentes_para_familia(familia):
        try:
            producto = producto_por_codigo(fuente, codigo)
            if not producto:
                continue
            url = _primera_url_producto_facts(producto, inspeccionar=False)
            if url:
                print(f'{_LOG_IMAGEN} codigo={codigo} fuente={fuente["id"]}')
                return url
        except Exception as error:
            _advertir_fallo_imagen(f'{fuente["id"]} codigo', error)
    return None


def _buscar_nombre_en_fuentes(nombre, familia):
    if not nombre:
        return None
    for fuente in fuentes_para_familia(familia):
        url = _mejor_hit_por_nombre(fuente, nombre)
        if url:
            print(f'{_LOG_IMAGEN} nombre={nombre!r} fuente={fuente["id"]}')
            return url
    if usa_wikimedia(familia):
        wiki = buscar_wikimedia(nombre)
        if wiki:
            url = _confirmar_url_catalogo(
                wiki.get('url'),
                wiki.get('ancho'),
                wiki.get('alto'),
                inspeccionar=True,
            )
            if url:
                print(f'{_LOG_IMAGEN} nombre={nombre!r} fuente=wikimedia')
                return url
    return None


def _espejar_en_storage(url, clave):
    """Sube la foto oficial al bucket imagenes si hay service_role. Si no, None."""
    try:
        from backend.supabase_client import clave_api_servidor, clave_es_service_role

        if not clave_es_service_role(clave_api_servidor()):
            return None
        respuesta = _http_get_imagen(url)
        if respuesta is None or respuesta.status_code != 200 or not respuesta.content:
            return None
        from backend.images import comprimir_bytes_a_bytes
        from backend.supabase_storage import _subir_bytes_al_bucket

        data, content_type, filename = comprimir_bytes_a_bytes(
            respuesta.content,
            prefijo=f'cat_{_slug_archivo(clave)}',
            max_dimension=400,
        )
        return _subir_bytes_al_bucket(None, f'productos/{filename}', data, content_type)
    except Exception as error:
        _advertir_fallo_imagen('espejo storage', error)
        return None


def espejar_url_oficial_en_storage(url, clave):
    """Sube una URL oficial al bucket si hay service_role. None si no aplica."""
    return _espejar_en_storage(url, clave)


def _descubrir_y_persistir_oficial(codigo_barras, nombre=None, categoria=None):
    """
    Cascada por familia: barcode en fuentes oficiales, luego nombre, luego Wikimedia.
    Espeja a Storage si hay service_role; si no, cachea la URL oficial.
    """
    familia = clasificar_familia(nombre=nombre, categoria=categoria)
    url = None
    codigo = normalizar_codigo_barras(codigo_barras)
    if codigo:
        url = _buscar_codigo_en_fuentes(codigo, familia)
    if not url and nombre:
        url = _buscar_nombre_en_fuentes(nombre, familia)
    if not url and nombre:
        for token in _tokens_nombre(nombre):
            if token in _GENERIC_FOOD or len(token) < 4:
                continue
            url = _buscar_nombre_en_fuentes(token, familia)
            if url:
                break
            if _PAUSA_OFF_SEG:
                time.sleep(_PAUSA_OFF_SEG)
    if not url:
        return None
    espejo = _espejar_en_storage(url, codigo or nombre)
    final = url_imagen_catalogo_valida(espejo) or url_imagen_catalogo_valida(url) or url
    if final and codigo:
        try:
            guardar_imagen_maestro(codigo, final)
        except Exception as error:
            _advertir_fallo_imagen('guardar maestro', error)
    return final


def resolver_imagen(
    codigo_barras=None,
    imagen_manual=None,
    mapa_maestro=None,
    nombre=None,
    categoria=None,
    *,
    para_escritura=False,
    buscar_oficial=True,
):
    """
    Resuelve la imagen de un producto.

    para_escritura=True → None si no hay imagen (no persistir placeholder).
    buscar_oficial=False → no consulta APIs externas (ruta crítica CSV).
    """
    del para_escritura
    try:
        manual = _url_manual_valida(imagen_manual)
        if manual:
            return manual

        codigo = normalizar_codigo_barras(codigo_barras)
        url = None
        if codigo:
            if mapa_maestro is not None:
                url = mapa_maestro.get(codigo)
            if not url:
                url = imagen_maestro_por_codigo(codigo)
            if url:
                if mapa_maestro is not None:
                    mapa_maestro[codigo] = url
                return url

        if not buscar_oficial:
            return None

        url = _descubrir_y_persistir_oficial(
            codigo, nombre, categoria=categoria
        )
        if url and codigo and mapa_maestro is not None:
            mapa_maestro[codigo] = url
        return url
    except Exception as error:
        _advertir_fallo_imagen('resolver_imagen', error)
        return None


def resolver_imagen_escritura(
    imagen_manual=None,
    codigo_barras=None,
    mapa_maestro=None,
    nombre=None,
    categoria=None,
    *,
    buscar_oficial=True,
):
    """URL para guardar en PostgreSQL (productos). NULL si no hay imagen real."""
    try:
        return resolver_imagen(
            codigo_barras=codigo_barras,
            imagen_manual=imagen_manual,
            mapa_maestro=mapa_maestro,
            nombre=nombre,
            categoria=categoria,
            para_escritura=True,
            buscar_oficial=buscar_oficial,
        )
    except Exception as error:
        _advertir_fallo_imagen('resolver_imagen_escritura', error)
        return None


def resolver_imagen_catalogo(
    imagen_url=None,
    codigo_barras=None,
    mapa_maestro=None,
):
    """URL para mostrar en catálogo. Solo lectura: PostgreSQL → catálogo maestro."""
    try:
        manual = imagen_url_almacenada(imagen_url)
        if manual:
            return manual

        codigo = normalizar_codigo_barras(codigo_barras)
        if not codigo:
            return None

        if mapa_maestro is not None:
            return mapa_maestro.get(codigo)

        return imagen_maestro_por_codigo(codigo) or None
    except Exception as error:
        _advertir_fallo_imagen('resolver_imagen_catalogo', error)
        return None


def completar_mapa_imagenes(codigos, mapa_maestro=None, buscar_oficial=True):
    """
    Completa mapa codigo → url_imagen.
    Consulta catálogo maestro en lote; opcionalmente OpenFoodFacts para faltantes.
    """
    try:
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
            try:
                mapa.update(mapa_imagenes_maestro(faltantes))
            except Exception as error:
                _advertir_fallo_imagen('catalogo_maestro', error)
            faltantes = [c for c in faltantes if c not in mapa]

        if not buscar_oficial:
            return mapa

        for codigo in faltantes:
            try:
                url = _descubrir_y_persistir_oficial(codigo)
                if url:
                    mapa[codigo] = url
            except Exception as error:
                _advertir_fallo_imagen(f'OpenFoodFacts:{codigo}', error)
            if _PAUSA_OFF_SEG:
                time.sleep(_PAUSA_OFF_SEG)

        return mapa
    except Exception as error:
        _advertir_fallo_imagen('completar_mapa_imagenes', error)
        return dict(mapa_maestro or {})


def preparar_mapa_imagenes_importacion(productos, snapshot_imagenes=None):
    """
    Precalcula imágenes locales antes del INSERT (CSV, snapshot, catálogo maestro).
    No consulta OpenFoodFacts: eso se difiere al hilo post-importación.
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

    return completar_mapa_imagenes(
        codigos_a_resolver, mapa_maestro=mapa, buscar_oficial=False
    )


def descubrir_imagen_catalogo(nombre=None, categoria=None, codigo_barras=None):
    """Punto de entrada de pruebas y alta: cascada multifuente + calidad."""
    return _descubrir_y_persistir_oficial(
        codigo_barras, nombre=nombre, categoria=categoria
    )

"""
Gestión centralizada de imágenes para Localis.

Flujo:
1. Catálogo maestro (cache por código de barras)
2. Cascada multifuente (EAN, luego nombre+categoría)
3. Estudio IA en segundo plano (rembg + lienzo #fffefb) al espejar
4. Storage o disco local; si falla, URL oficial original
"""

from __future__ import annotations

import re
import socket
import time
import unicodedata
from difflib import SequenceMatcher
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
    buscar_commons,
    buscar_openverse,
    buscar_wikimedia,
    clasificar_familia,
    fuentes_para_codigo,
    fuentes_para_familia,
    hits_por_nombre,
    producto_por_codigo,
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
_OFF_TIMEOUT_SEG = 5
_HTTP_REINTENTOS = 2
_LOG_IMAGEN = '[Localis Imagen]'
_STOP_NOMBRE = frozenset({
    'de', 'la', 'el', 'los', 'las', 'del', 'y', 'en', 'con',
    'kg', 'g', 'l', 'ml', 'un', 'una', 'refresco',
})
_RE_PREFIJO_QA = re.compile(r'__localis[\w-]*__', re.IGNORECASE)
_TOKENS_RUIDO = frozenset({'localis', 'qa', 'img', 'viva', 'test', 'prueba'})
_ALIAS_LIBRE = {
    'martillo': ('Hammer', 'Claw hammer'),
    'camisa': ('Polo shirt', 'Shirt'),
    'polo': ('Polo shirt',),
    'iphone': ('iPhone',),
    'harina': ('Harina PAN', 'Cornmeal'),
    'nutella': ('Nutella',),
    'destornillador': ('Screwdriver',),
    'taladro': ('Drill',),
}
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
    'wikimedia.org',
    'staticflickr.com',
    'flickr.com',
    'openverse.org',
    'unsplash.com',
    'images.unsplash.com',
    'pxhere.com',
    'rawpixel.com',
    'nappy.co',
    'stocksnap.io',
)

_PATRONES_RECHAZO = (
    '_100.',
    '_150.',
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
)


def _advertir_fallo_imagen(contexto, error):
    print(f'{_LOG_IMAGEN} aviso {contexto}: {type(error).__name__}: {error}')


def _http_get_imagen(url, headers=None):
    """GET externo con un reintento ante timeout/5xx. Nunca propaga red/DNS."""
    ultimo = None
    for intento in range(_HTTP_REINTENTOS):
        try:
            respuesta = requests.get(
                url,
                headers=headers or {'User-Agent': _USER_AGENT},
                timeout=_OFF_TIMEOUT_SEG,
            )
            if respuesta.status_code in (429, 500, 502, 503, 504):
                ultimo = respuesta.status_code
                if intento + 1 < _HTTP_REINTENTOS:
                    time.sleep(0.35 * (intento + 1))
                    continue
                return None
            return respuesta
        except _ERRORES_RED_IMAGEN as error:
            ultimo = error
            if intento + 1 < _HTTP_REINTENTOS:
                time.sleep(0.35 * (intento + 1))
                continue
            _advertir_fallo_imagen(url, error)
            return None
        except Exception as error:
            _advertir_fallo_imagen(url, error)
            return None
    if ultimo and not isinstance(ultimo, int):
        _advertir_fallo_imagen(url, ultimo)
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


def _url_pasa_filtro_calidad(url, ancho=None, alto=None, permitir_libre=False):
    """Descarta placeholders y URLs fuera de catálogos oficiales/libres."""
    if not url or not str(url).startswith('https://'):
        return False
    lower = str(url).lower()
    es_oficial = any(host in lower for host in _HOSTS_OFICIALES)
    if not es_oficial and not permitir_libre:
        return False
    if any(patron in lower for patron in _PATRONES_RECHAZO):
        return False
    meta = metadatos_pasan_umbral(ancho, alto, min_lado=MIN_LADO_PX)
    if meta is False:
        return False
    return True


def _inspeccionar_bytes_url(url):
    """Descarga acotada y evalúa nitidez/resolución. None si no se pudo leer."""
    cached = _CALIDAD_CACHE.get(url)
    if cached is not None:
        return cached
    respuesta = _http_get_imagen(url)
    if respuesta is None:
        return False
    if respuesta.status_code != 200:
        if respuesta.status_code >= 500 or respuesta.status_code == 429:
            return False
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


def _confirmar_url_catalogo(url, ancho=None, alto=None, inspeccionar=False, permitir_libre=False):
    """Heurística de URL + umbral de tamaño; Pillow si hace falta."""
    if not url:
        return None
    url = str(url).strip()
    if url.startswith('http://'):
        url = 'https://' + url[7:]
    try:
        from urllib.parse import urlsplit, urlunsplit

        partes = urlsplit(url)
        host = (partes.netloc or '').lower()
        if 'wikimedia.org' in host or 'wikipedia.org' in host:
            url = urlunsplit((partes.scheme, partes.netloc, partes.path, '', ''))
    except Exception:
        pass
    if not _url_pasa_filtro_calidad(
        url, ancho, alto, permitir_libre=permitir_libre
    ):
        return None
    meta = metadatos_pasan_umbral(ancho, alto)
    if meta is False:
        registrar_rechazo(url, f'metadatos {ancho}x{alto}')
        return None
    necesita_bytes = inspeccionar or meta is None
    if necesita_bytes and not _inspeccionar_bytes_url(url):
        if meta is True:
            return url_imagen_catalogo_valida(url) or url
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
    texto = texto.replace('×', ' ')
    texto = re.sub(r'(?<=\d)x(?=\d)', ' ', texto)
    crudos = []
    for parte in texto.replace('/', ' ').split():
        parte = parte.strip('.,;()[]')
        if not parte or parte in _STOP_NOMBRE:
            continue
        if parte.startswith('__') or parte in _TOKENS_RUIDO:
            continue
        if len(parte) < 3:
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


def _score_hit_nombre(tokens, hit, tokens_desc=None):
    """
    Evita falsos positivos: si el nombre tiene 2+ tokens, exige 2 coincidencias
    o una similitud alta (SequenceMatcher) sobre el nombre/marca del hit.
    """
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
                _texto_hit(hit.get('generic_name')),
            ),
        )
    ).lower()
    nombre_norm = ''.join(
        ch for ch in unicodedata.normalize('NFKD', nombre)
        if not unicodedata.combining(ch)
    )
    palabras = nombre_norm.split()
    aciertos = 0
    for tok in tokens:
        if tok in nombre_norm:
            aciertos += 1
            continue
        if len(tok) >= 4 and any(
            (p.startswith(tok) or tok.startswith(p))
            for p in palabras
            if len(p) >= 4
        ):
            aciertos += 1
    if aciertos == 0:
        return 0
    ratio = SequenceMatcher(None, ' '.join(tokens), nombre_norm).ratio()
    fuertes = [tok for tok in tokens if len(tok) >= 4] or list(tokens)
    aciertos_fuertes = sum(1 for tok in fuertes if tok in nombre_norm)
    if len(fuertes) >= 2 and aciertos_fuertes < 2 and ratio < 0.68:
        return 0
    bonus = 0
    if tokens_desc:
        bonus = sum(1 for tok in tokens_desc if tok in nombre_norm)
    return aciertos * 2 + bonus + int(ratio * 4)


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


def _texto_respaldo_nombre(nombre, descripcion=None, categoria=None):
    """Consulta de respaldo: nombre limpio + descripción + categoría."""
    nombre_limpio = _nombre_consulta(nombre)
    desc = ' '.join(str(descripcion or '').split()).strip()
    desc_low = desc.lower().rstrip('.')
    if desc_low in {
        '',
        'none',
        'null',
        'sin descripcion',
        'sin descripción',
        'sin descripcion disponible',
        'sin descripción disponible',
    }:
        desc = ''
    cat = ' '.join(str(categoria or '').split()).strip()
    partes = [p for p in (nombre_limpio, desc, cat) if p]
    return ' '.join(partes)


def _nombre_consulta(nombre):
    texto = _RE_PREFIJO_QA.sub(' ', str(nombre or ''))
    return ' '.join(texto.split()).strip()


def _consultas_libres(nombre):
    """
    Consultas cortas para Commons/Wikipedia/Openverse.
    Solo el nombre (sin categoría): "Martillo Ferretería" no existe en Wikipedia.
    """
    tokens = _tokens_nombre(_nombre_consulta(nombre))
    vistas = []

    def _agregar(consulta):
        q = ' '.join(str(consulta or '').split()).strip()
        if len(q) < 3:
            return
        clave = q.lower()
        if clave in {item.lower() for item in vistas}:
            return
        vistas.append(q)

    if tokens:
        _agregar(' '.join(tokens[:4]))
        _agregar(tokens[0])
        if tokens[0] == 'iphone':
            _agregar('iPhone')
        for tok in tokens[:3]:
            for alias in _ALIAS_LIBRE.get(tok, ()):
                _agregar(alias)
    return vistas


def _mejor_hit_por_nombre(fuente, nombre, descripcion=None, categoria=None):
    tokens = _tokens_nombre(_nombre_consulta(nombre))
    tokens_desc = _tokens_nombre(descripcion) if descripcion else []
    if not tokens:
        return None
    consultas = [' '.join(tokens[:4])]
    if tokens_desc:
        consultas.append(f'{consultas[0]} {" ".join(tokens_desc[:3])}'.strip())
    cat_tok = _tokens_nombre(categoria) if categoria else []
    if cat_tok:
        consultas.append(f'{consultas[0]} {cat_tok[0]}'.strip())
    vistas = []
    for consulta in consultas:
        if not consulta or consulta in vistas:
            continue
        vistas.append(consulta)
        try:
            hits = hits_por_nombre(fuente, consulta)
            mejor = None
            mejor_score = 0
            for hit in hits:
                score = _score_hit_nombre(tokens, hit, tokens_desc=tokens_desc)
                if score <= mejor_score:
                    continue
                url = _url_imagen_hit_off(hit, inspeccionar=False)
                if not url:
                    continue
                mejor = url
                mejor_score = score
            if mejor:
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
    for fuente in fuentes_para_codigo(familia):
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


def _meta_libre_a_hit(meta):
    tags = meta.get('tags') or []
    if isinstance(tags, list):
        tag_txt = ' '.join(
            item.get('name') if isinstance(item, dict) else str(item)
            for item in tags
            if item
        )
    else:
        tag_txt = str(tags)
    return {
        'product_name': meta.get('titulo') or '',
        'brands': '',
        'generic_name': tag_txt,
    }


def _meta_libre_coincide(nombre, meta):
    tokens = _tokens_nombre(nombre)
    if not tokens or not meta:
        return False
    score = _score_hit_nombre(tokens, _meta_libre_a_hit(meta))
    if score > 0:
        return True
    titulo = ''.join(
        ch for ch in unicodedata.normalize(
            'NFKD', str(meta.get('titulo') or '').lower()
        )
        if not unicodedata.combining(ch)
    )
    return any(tok in titulo for tok in tokens)


def _confirmar_meta_libre(meta, nombre, etiqueta):
    if not meta or not _meta_libre_coincide(nombre, meta):
        return None
    tiene_medida = meta.get('ancho') and meta.get('alto')
    url = _confirmar_url_catalogo(
        meta.get('url'),
        meta.get('ancho'),
        meta.get('alto'),
        inspeccionar=not tiene_medida,
        permitir_libre=(etiqueta == 'openverse'),
    )
    if url:
        print(f'{_LOG_IMAGEN} respaldo nombre={nombre!r} fuente={etiqueta}')
    return url


def _buscar_en_fuentes_libres(consulta, nombre_score):
    """Commons → Wikipedia → Openverse para una consulta concreta."""
    url = _confirmar_meta_libre(
        buscar_commons(consulta), nombre_score, 'commons'
    )
    if url:
        return url
    wiki = buscar_wikimedia(consulta)
    if wiki:
        url = _confirmar_meta_libre(wiki, nombre_score, 'wikimedia')
        if url:
            return url
        titulo = ''.join(
            ch
            for ch in unicodedata.normalize(
                'NFKD', str(wiki.get('titulo') or '').lower()
            )
            if not unicodedata.combining(ch)
        )
        nucleo = _tokens_nombre(nombre_score)
        consulta_toks = _tokens_nombre(consulta)
        if (nucleo and nucleo[0] in titulo) or (
            consulta_toks and consulta_toks[0] in titulo
        ):
            url = _confirmar_url_catalogo(
                wiki.get('url'),
                wiki.get('ancho'),
                wiki.get('alto'),
                inspeccionar=False,
            )
            if url:
                print(f'{_LOG_IMAGEN} respaldo nombre={nombre_score!r} fuente=wikimedia')
                return url
    return _confirmar_meta_libre(
        buscar_openverse(consulta), nombre_score, 'openverse'
    )


def _buscar_nombre_en_fuentes(nombre, familia, descripcion=None, categoria=None):
    nombre_limpio = _nombre_consulta(nombre)
    if not nombre_limpio:
        return None
    for fuente in fuentes_para_familia(familia):
        url = _mejor_hit_por_nombre(
            fuente,
            nombre_limpio,
            descripcion=descripcion,
            categoria=categoria,
        )
        if url:
            print(
                f'{_LOG_IMAGEN} respaldo nombre={nombre_limpio!r} '
                f'desc={bool(descripcion)} fuente={fuente["id"]}'
            )
            return url
    for consulta in _consultas_libres(nombre_limpio):
        url = _buscar_en_fuentes_libres(consulta, consulta)
        if url:
            return url
    return None


def _espejar_en_storage(url, clave):
    """
    Descarga una foto oficial, aplica estudio (IA + lienzo #fffefb) y sube
    al bucket o a disco local. Nunca lanza: si algo falla, None.
    """
    try:
        from backend.images import comprimir_bytes_a_bytes
        from backend.supabase_client import clave_api_servidor, clave_es_service_role
        from backend.supabase_storage import _subir_bytes_al_bucket

        respuesta = _http_get_imagen(url)
        if respuesta is None or respuesta.status_code != 200 or not respuesta.content:
            return None

        prefijo = f'cat_{_slug_archivo(clave)}'
        procesado = None
        try:
            from backend.image_ai import procesar_descarga_oficial

            procesado = procesar_descarga_oficial(respuesta.content, prefijo=prefijo)
        except Exception as error:
            _advertir_fallo_imagen('ia estudio', error)

        if procesado:
            data, content_type, filename = procesado
            print(f'{_LOG_IMAGEN} estudio ia ok clave={clave!r}')
        else:
            data, content_type, filename = comprimir_bytes_a_bytes(
                respuesta.content,
                prefijo=prefijo,
                max_dimension=400,
                lienzo_cuadrado=True,
            )

        if clave_es_service_role(clave_api_servidor()):
            try:
                subida = _subir_bytes_al_bucket(
                    None, f'productos/{filename}', data, content_type
                )
                if subida:
                    return subida
            except Exception as error:
                _advertir_fallo_imagen('espejo storage', error)

        try:
            from backend.uploads_locales import guardar_bytes_upload

            local = guardar_bytes_upload(data, filename, carpeta='productos')
            if local:
                return local
        except Exception as error:
            _advertir_fallo_imagen('espejo local', error)
        return None
    except Exception as error:
        _advertir_fallo_imagen('espejo storage', error)
        return None


def espejar_url_oficial_en_storage(url, clave):
    """Sube una URL oficial al bucket si hay service_role. None si no aplica."""
    return _espejar_en_storage(url, clave)


def _descubrir_y_persistir_oficial(
    codigo_barras, nombre=None, categoria=None, descripcion=None
):
    """
    Cascada:
    1) Código de barras en catálogos oficiales.
    2) Si falta o falla: nombre + marca + categoría.
    El estudio IA corre aquí (hilo daemon), no en HTTP.
    """
    try:
        familia = clasificar_familia(nombre=nombre, categoria=categoria)
        url = None
        codigo = normalizar_codigo_barras(codigo_barras)
        if codigo:
            url = _buscar_codigo_en_fuentes(codigo, familia)
            if url:
                print(f'{_LOG_IMAGEN} prioridad=codigo codigo={codigo}')
        if not url:
            url = _buscar_nombre_en_fuentes(
                nombre, familia, descripcion=descripcion, categoria=categoria
            )
            if url:
                print(
                    f'{_LOG_IMAGEN} prioridad=respaldo_nombre '
                    f'codigo={codigo or "-"} nombre={nombre!r}'
                )
        if not url:
            return None
        try:
            espejo = _espejar_en_storage(url, codigo or nombre)
        except Exception as error:
            _advertir_fallo_imagen('espejo estudio', error)
            espejo = None
        final = (
            imagen_url_almacenada(espejo)
            or url_imagen_catalogo_valida(url)
            or url
        )
        if final and codigo:
            try:
                guardar_imagen_maestro(codigo, final)
            except Exception as error:
                _advertir_fallo_imagen('guardar maestro', error)
        return final
    except Exception as error:
        _advertir_fallo_imagen('descubrir oficial', error)
        return None


def resolver_imagen(
    codigo_barras=None,
    imagen_manual=None,
    mapa_maestro=None,
    nombre=None,
    categoria=None,
    descripcion=None,
    *,
    para_escritura=False,
    buscar_oficial=True,
    reproceso_maestro=True,
):
    """
    Resuelve la imagen de un producto.

    para_escritura=True → None si no hay imagen (no persistir placeholder).
    buscar_oficial=False → no consulta APIs externas (ruta crítica CSV).
    reproceso_maestro=False → no vuelve a pasar por IA una URL ya cacheada.
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
                if (
                    buscar_oficial
                    and reproceso_maestro
                    and not url_imagen_local_valida(url)
                ):
                    try:
                        espejo = _espejar_en_storage(url, codigo or nombre)
                        nuevo = imagen_url_almacenada(espejo)
                        if nuevo:
                            url = nuevo
                            try:
                                guardar_imagen_maestro(codigo, url)
                            except Exception as error:
                                _advertir_fallo_imagen('guardar maestro estudio', error)
                    except Exception as error:
                        _advertir_fallo_imagen('estudio maestro', error)
                if mapa_maestro is not None:
                    mapa_maestro[codigo] = url
                return url

        if not buscar_oficial:
            return None

        url = _descubrir_y_persistir_oficial(
            codigo, nombre, categoria=categoria, descripcion=descripcion
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
    descripcion=None,
    *,
    buscar_oficial=True,
    reproceso_maestro=True,
):
    """URL para guardar en PostgreSQL (productos). NULL si no hay imagen real."""
    try:
        return resolver_imagen(
            codigo_barras=codigo_barras,
            imagen_manual=imagen_manual,
            mapa_maestro=mapa_maestro,
            nombre=nombre,
            categoria=categoria,
            descripcion=descripcion,
            para_escritura=True,
            buscar_oficial=buscar_oficial,
            reproceso_maestro=reproceso_maestro,
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


def descubrir_imagen_catalogo(
    nombre=None, categoria=None, codigo_barras=None, descripcion=None
):
    """Punto de entrada de pruebas y alta: cascada barcode → nombre+descripcion."""
    return _descubrir_y_persistir_oficial(
        codigo_barras,
        nombre=nombre,
        categoria=categoria,
        descripcion=descripcion,
    )

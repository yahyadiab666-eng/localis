"""Pipeline profesional de imágenes de producto para Localis.

Reemplaza por completo el lookup por APIs globales de códigos de barras
(Barcode Spider y similares), que no funcionan para el mercado venezolano.

No usa suscripciones de pago mensuales: se apoya en librerías locales de IA
(``rembg`` + ``Pillow``/``OpenCV``) y en fuentes web gratuitas.

Flujo automático (se dispara al crear un producto o importar un CSV):

  Paso A. Búsqueda inteligente
     1. Intenta resolver por EAN/UPC (Open Food/Beauty/Products Facts, gratis).
     2. Si falla (lo común en productos locales), busca en la web con la
        combinación estructurada:
            [Nombre] + [Marca] + [Presentación] + "venezuela"

  Paso B. Validación de fuente y calidad
     - Prioriza fuentes confiables (marcas oficiales, Farmatodo, Locatel y
       grandes distribuidores nacionales).
     - Descarta imágenes pequeñas, borrosas, planas o con aspecto de logo/banner.

  Paso C. Procesamiento de imagen profesional
     - ``rembg`` elimina el fondo; el producto se recompone sobre un lienzo
       blanco puro (#FFFFFF), recortado y centrado (estilo estudio).

  Paso D. Almacenamiento y vinculación
     - La imagen optimizada se sube a Supabase Storage (o queda en
       ``static/uploads`` como respaldo) y se actualiza ``productos.imagen_url``.

Uso::

    from services.professional_image_pipeline import programar_procesamiento_producto
    programar_procesamiento_producto(producto_id, categoria='Alimentos')

Variables de entorno relevantes (todas opcionales)::

    LOCALIS_IMG_PIPELINE=1              # 0 desactiva todo el pipeline
    LOCALIS_IMG_MIN_SIDE=320            # lado mínimo aceptado (px)
    LOCALIS_IMG_MAX_CANDIDATOS=8
    LOCALIS_IMG_BLUR_MIN=35             # varianza Laplaciana mínima
    LOCALIS_IMG_LADO_FINAL=800          # lienzo final cuadrado
    LOCALIS_IMG_MAX_CONCURRENT=1        # hilos de rembg simultáneos
    LOCALIS_IMG_BUDGET_SEC=120          # presupuesto por producto
    LOCALIS_IMG_CSV_MAX=25              # productos por lote CSV
    LOCALIS_IMG_CSV_BUDGET_SEC=180
    LOCALIS_REMBG_MODEL=u2net
"""

from __future__ import annotations

import hashlib
import html
import io
import os
import re
import threading
import time
import unicodedata
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
from urllib.parse import urlparse

import requests

from backend import http_client as _http

_LOG = '[Localis Imagen Pro]'
_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)

PLACEHOLDER_PRODUCTO = '/static/img/placeholder-producto.svg'


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
def _env_int(nombre, defecto):
    try:
        return int(str(os.getenv(nombre, '')).strip() or defecto)
    except (TypeError, ValueError):
        return defecto


def _env_float(nombre, defecto):
    try:
        return float(str(os.getenv(nombre, '')).strip() or defecto)
    except (TypeError, ValueError):
        return defecto


def pipeline_habilitado() -> bool:
    valor = str(os.getenv('LOCALIS_IMG_PIPELINE', '1')).strip().lower()
    return valor not in ('0', 'false', 'no', 'off')


_MIN_SIDE = _env_int('LOCALIS_IMG_MIN_SIDE', 320)
_MAX_BYTES = _env_int('LOCALIS_IMG_MAX_BYTES', 7_000_000)
_MAX_CANDIDATOS = _env_int('LOCALIS_IMG_MAX_CANDIDATOS', 8)
_BLUR_MIN = _env_float('LOCALIS_IMG_BLUR_MIN', 35.0)
_LADO_FINAL = _env_int('LOCALIS_IMG_LADO_FINAL', 800)
_BUDGET_SEC = _env_float('LOCALIS_IMG_BUDGET_SEC', 120.0)
_CSV_MAX = _env_int('LOCALIS_IMG_CSV_MAX', 2000)
_CSV_BUDGET_SEC = _env_float('LOCALIS_IMG_CSV_BUDGET_SEC', 600.0)
_TIMEOUT_DESCARGA = _env_float('LOCALIS_IMG_TIMEOUT_SEC', 12.0)
_DESCARGA_INTENTOS = max(1, _env_int('LOCALIS_IMG_DESCARGA_INTENTOS', 2))
_TIMEOUT_BUSQUEDA = _env_float('LOCALIS_IMG_SEARCH_TIMEOUT_SEC', 8.0)
_BUSQUEDA_WEB = str(os.getenv('LOCALIS_IMG_BUSQUEDA_WEB', '1')).strip().lower() not in (
    '0',
    'false',
    'no',
    'off',
)
# Búsqueda ligera en paralelo (I/O) + caché agresiva por TTL.
_BUSQUEDA_PARALELA = max(1, _env_int('LOCALIS_IMG_PARALELO', 4))
_IMG_TRABAJADORES = max(1, _env_int('LOCALIS_IMG_TRABAJADORES', 4))
_CACHE_BUSQUEDA_TTL = max(30, _env_int('LOCALIS_IMG_CACHE_TTL_SEC', 3600))
_MAX_SITIOS = max(0, _env_int('LOCALIS_IMG_SITIOS', 2))
# Rastreo og:image de páginas de producto de Bing (emulación de búsqueda humana).
_BING_OG = str(os.getenv('LOCALIS_IMG_BING_OG', '1')).strip().lower() not in (
    '0', 'false', 'no', 'off',
)

_SEMAFORO = threading.Semaphore(max(1, _env_int('LOCALIS_IMG_MAX_CONCURRENT', 1)))
_EN_VUELO: set = set()
_LOCK_VUELO = threading.Lock()

_REMBG_SESION = None
_REMBG_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Fuentes confiables y bloqueadas (registro modular)
# ---------------------------------------------------------------------------
from backend.fuentes_imagenes import (  # noqa: E402
    DOMINIOS_BLOQUEADOS as _DOMINIOS_BLOQUEADOS,
    dominios_confiables as _dominios_confiables,
    fuentes_site as _fuentes_site,
    fuentes_vtex as _fuentes_vtex,
)

_EXT_IMAGEN_OK = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tiff', '.tif')


def _token_mercadolibre():
    return (
        os.getenv('MELI_ACCESS_TOKEN')
        or os.getenv('MERCADOLIBRE_ACCESS_TOKEN')
        or ''
    ).strip()


_ACENTOS = str.maketrans(
    'áéíóúüñÁÉÍÓÚÜÑ',
    'aeiouunAEIOUUN',
)


def _texto_plano(valor) -> str:
    if valor is None:
        return ''
    texto = str(valor).strip()
    return texto.translate(_ACENTOS)


def _slug(valor, maximo=90) -> str:
    base = _texto_plano(valor).lower()
    base = re.sub(r'[^a-z0-9]+', '-', base).strip('-')
    return (base or 'producto')[:maximo]


def _tokens_relevancia(nombre, marca, descripcion):
    texto = ' '.join(
        _texto_plano(v).lower() for v in (nombre, marca, descripcion) if v
    )
    tokens = {
        t
        for t in re.split(r'[^a-z0-9]+', texto)
        if len(t) >= 4 and t not in _STOPWORDS
    }
    return tokens


_STOPWORDS = frozenset({
    'para', 'con', 'sin', 'los', 'las', 'del', 'una', 'uno', 'por',
    'producto', 'articulo', 'presentacion', 'unidad', 'unidades', 'color',
    'tamano', 'tipo', 'marca', 'contenido', 'original', 'nuevo', 'nueva',
})


def _inferir_marca(nombre, descripcion=None, marca=None):
    """Marca de la fila o reconocida del texto (marcas criollas/importadas)."""
    if marca and str(marca).strip():
        return str(marca).strip()
    try:
        from backend.marcas_ve import detectar_marca

        return detectar_marca(nombre, descripcion)
    except Exception:
        return None


_RE_PRESENTACION = re.compile(
    r'\b(\d+(?:[.,]\d+)?\s?(?:kg|kgs|g|gr|grs|gramos|mg|l|lt|lts|litros|'
    r'ml|cc|oz|lb|lbs|un|und|unid|unidades?|tabletas?|capsulas?|sobres?|'
    r'rollos?|paños?|panos?|x\s?\d+))\b',
    re.IGNORECASE,
)


def _inferir_presentacion(nombre, descripcion=None, presentacion=None):
    if presentacion and str(presentacion).strip():
        return str(presentacion).strip()
    texto = f'{nombre or ""} {descripcion or ""}'
    match = _RE_PRESENTACION.search(texto)
    return match.group(1).strip() if match else None


# ---------------------------------------------------------------------------
# Modelo de candidato
# ---------------------------------------------------------------------------
@dataclass
class Candidato:
    url: str
    fuente: str
    dominio: str = ''
    score: float = 0.0
    ancho: int = 0
    alto: int = 0
    titulo: str = ''
    confiable: bool = False
    urls_alternas: list = field(default_factory=list)

    def variantes(self):
        vistas = []
        for url in [self.url, *self.urls_alternas]:
            if url and url not in vistas:
                vistas.append(url)
        return vistas


@dataclass
class ResultadoProcesamiento:
    ok: bool
    url: str | None = None
    fuente: str | None = None
    motivo: str | None = None
    detalle: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def _log(mensaje):
    print(f'{_LOG} {mensaje}', flush=True)


def _log_pipeline(producto_id, ean, resultado, fuente=None, motivo=None):
    """Registra el intento en image_pipeline_log (si la tabla existe)."""
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                INSERT INTO image_pipeline_log
                    (ean, producto_id, resultado, fuente, motivo_descarte)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    ean,
                    int(producto_id) if producto_id else None,
                    str(resultado)[:60],
                    (str(fuente)[:120] if fuente else None),
                    (str(motivo)[:240] if motivo else None),
                ),
            )
            conexion.commit()
    except Exception as error:
        _log(f'log pipeline omitido ({type(error).__name__}: {error})')


# ---------------------------------------------------------------------------
# Paso A — Búsqueda inteligente
# ---------------------------------------------------------------------------
def _dominio(url):
    try:
        return (urlparse(url).hostname or '').lower()
    except Exception:
        return ''


def _dominio_bloqueado(url):
    host = _dominio(url)
    if not host:
        return True
    ruta = (urlparse(url).path or '').lower()
    cadena = f'{host}{ruta}'
    for marca in _DOMINIOS_BLOQUEADOS:
        m = marca.lower()
        if '.' in m:
            if m.endswith('.'):
                if host.startswith(m) or f'.{m}' in f'{host}.':
                    return True
            elif host == m or host.endswith(f'.{m}'):
                return True
        elif m in cadena:
            return True
    return False


def _url_imagen_valida(url, confiable=False):
    if not url or not isinstance(url, str):
        return False
    texto = html.unescape(url).strip().replace('\\/', '/')
    if not texto.lower().startswith(('http://', 'https://')):
        return False
    # Fuentes confiables (VTEX local, Mercado Libre API) se aceptan aunque su
    # CDN esté en la lista de bloqueo para scraping genérico.
    if not confiable and _dominio_bloqueado(texto):
        return False
    ruta = urlparse(texto).path.lower()
    if ruta.endswith('.svg'):
        return False
    return True


def _puntuar(candidato: Candidato, marca=None):
    host_path = f'{candidato.dominio} {candidato.url}'.lower()
    score = 0.0
    if any(d in host_path for d in _dominios_confiables()):
        score += 250.0
    if marca:
        marca_norm = _texto_plano(marca).lower().replace(' ', '')
        if marca_norm and marca_norm in candidato.dominio.replace('-', '').replace('.', ''):
            score += 120.0
    if candidato.dominio.endswith('.ve') or '.com.ve' in candidato.dominio:
        score += 45.0
    if candidato.fuente.startswith('openfoodfacts') or 'openfoodfacts' in host_path:
        score += 60.0
    if any(k in host_path for k in ('/producto', '/product', 'catalogo', 'catalog', '/p/')):
        score += 25.0
    if candidato.ancho and candidato.alto:
        lado = min(candidato.ancho, candidato.alto)
        if lado >= 700:
            score += 30.0
        elif lado >= 500:
            score += 15.0
    if 'front' in candidato.url.lower() or 'principal' in candidato.url.lower():
        score += 20.0
    candidato.score = score
    return score


def _relevante_web(candidato: Candidato, tokens):
    """Evita asignar fotos que no tienen relación con el producto."""
    host_path = f'{candidato.dominio}{urlparse(candidato.url).path}'.lower()
    if any(d in host_path for d in _dominios_confiables()):
        return True
    if not tokens:
        # Sin tokens fiables solo confiamos en fuentes explícitamente confiables.
        return False
    texto = f'{candidato.titulo} {host_path}'.lower()
    return any(token in texto for token in tokens)


def _get_json(url, *, params=None, headers=None):
    try:
        respuesta = _http.get(
            url,
            params=params,
            headers={'User-Agent': _UA, 'Accept': 'application/json', **(headers or {})},
            timeout=_TIMEOUT_BUSQUEDA,
        )
        if respuesta.status_code != 200:
            return None
        return respuesta.json()
    except Exception:
        return None


def _off_url_alta_res(url):
    """Open Food Facts sirve variantes ``.400.jpg``; la original no lleva el px."""
    if not url:
        return url
    return re.sub(r'\.(\d{2,4})\.(jpg|jpeg|png|webp)$', r'.\2', url, flags=re.I)


def _variantes_off(url):
    """Tamaños/formatos alternativos de una imagen de Open Food Facts."""
    if not url:
        return []
    variantes = []

    def _add(valor):
        if valor and valor not in variantes:
            variantes.append(valor)

    _add(url)
    if re.search(r'\.\d{2,4}\.(?:jpg|jpeg|png|webp)$', url, re.I):
        _add(re.sub(r'\.\d{2,4}\.(jpg|jpeg|png|webp)$', r'.\1', url, flags=re.I))
        _add(re.sub(r'\.\d{2,4}\.(jpg|jpeg|png|webp)$', r'.full.\1', url, flags=re.I))
    else:
        _add(re.sub(r'\.(jpg|jpeg|png|webp)$', r'.400.\1', url, flags=re.I))
        _add(re.sub(r'\.(jpg|jpeg|png|webp)$', r'.full.\1', url, flags=re.I))
    return variantes


def _urls_imagen_off(producto):
    """URLs de imagen de un producto OFF (campos directos + estructura `images`)."""
    urls = []

    def _add(valor):
        if isinstance(valor, str) and valor.strip():
            urls.append(valor.strip())

    for clave in ('image_front_url', 'image_url', 'image_front_small_url'):
        _add(producto.get(clave))

    imagenes = producto.get('images') or {}
    if isinstance(imagenes, dict):
        for clave, valor in imagenes.items():
            if 'front' not in str(clave).lower() and 'principal' not in str(clave).lower():
                continue
            if isinstance(valor, str):
                _add(valor)
            elif isinstance(valor, dict):
                for campo in ('url', 'display_url', 'small_url', 'medium_url'):
                    _add(valor.get(campo))
                tamanos = valor.get('sizes') or {}
                if isinstance(tamanos, dict):
                    for campo in ('400', 'full', 'display', '800'):
                        tam = tamanos.get(campo)
                        if isinstance(tam, dict):
                            _add(tam.get('url'))
    return urls


def _candidatos_desde_off(producto, fuente='openfoodfacts'):
    candidatos = []
    if not isinstance(producto, dict):
        return candidatos
    vistos = set()
    for url in _urls_imagen_off(producto):
        if not url or url in vistos:
            continue
        vistos.add(url)
        variantes = _variantes_off(url)
        candidato = Candidato(
            url=variantes[0] if variantes else url,
            fuente=fuente,
            dominio=_dominio(url),
            urls_alternas=variantes[1:],
        )
        candidatos.append(candidato)
    return candidatos


def _buscar_off_por_ean(ean):
    campos = 'product_name,product_name_es,brands,quantity,image_front_url,image_url,images'
    for host in (
        'world.openfoodfacts.org',
        'world.openbeautyfacts.org',
        'world.openproductsfacts.org',
    ):
        datos = _get_json(
            f'https://{host}/api/v2/product/{ean}.json',
            params={'fields': campos},
        )
        if datos and datos.get('status') == 1 and datos.get('product'):
            return _candidatos_desde_off(datos['product'], fuente=host.split('.')[1])
    return []


_OFF_HOSTS = (
    'world.openfoodfacts.org',
    'world.openbeautyfacts.org',
    'world.openproductsfacts.org',
)


def _buscar_off_por_nombre(consulta, hosts=None, page_size=8):
    """Búsqueda por nombre en los catálogos abiertos (OFF/OBF/OPF)."""
    consulta = str(consulta or '').strip()
    if not consulta:
        return []
    candidatos = []
    for host in (hosts or _OFF_HOSTS):
        datos = _get_json(
            f'https://{host}/cgi/search.pl',
            params={
                'search_terms': consulta,
                'search_simple': 1,
                'action': 'process',
                'json': 1,
                'page_size': page_size,
                'fields': 'product_name,product_name_es,brands,quantity,image_front_url,image_url',
            },
        )
        if not datos:
            continue
        for producto in (datos.get('products') or [])[:page_size]:
            candidatos.extend(
                _candidatos_desde_off(producto, fuente=host.split('.')[1])
            )
    return candidatos


def _clave_serpapi():
    return (os.getenv('SERPAPI_KEY') or os.getenv('SERPAPI_API_KEY') or '').strip()


def _buscar_serpapi(consulta, limite=10):
    """Google Images vía SerpAPI (opcional, si hay clave). Búsqueda 'humana'."""
    clave = _clave_serpapi()
    consulta = str(consulta or '').strip()
    if not clave or not consulta:
        return []
    datos = _get_json(
        'https://serpapi.com/search.json',
        params={'engine': 'google_images', 'q': consulta, 'num': limite, 'api_key': clave},
    )
    if not datos:
        return []
    candidatos = []
    for item in (datos.get('images_results') or [])[:limite]:
        url = item.get('original') or item.get('thumbnail')
        if _url_imagen_valida(url, confiable=True):
            candidatos.append(
                Candidato(
                    url=url,
                    fuente='serpapi',
                    dominio=_dominio(url),
                    titulo=str(item.get('title') or ''),
                    confiable=True,
                )
            )
    return candidatos


def _clave_brave():
    return (os.getenv('BRAVE_SEARCH_API_KEY') or os.getenv('BRAVE_API_KEY') or '').strip()


def _buscar_brave(consulta, limite=10):
    """Brave Image Search (opcional, si hay clave)."""
    clave = _clave_brave()
    consulta = str(consulta or '').strip()
    if not clave or not consulta:
        return []
    datos = _get_json(
        'https://api.search.brave.com/res/v1/images/search',
        params={'q': consulta, 'count': limite},
        headers={'X-Subscription-Token': clave, 'Accept': 'application/json'},
    )
    if not datos:
        return []
    candidatos = []
    for item in (datos.get('results') or [])[:limite]:
        propiedades = item.get('properties') or {}
        url = propiedades.get('url') or (item.get('thumbnail') or {}).get('src')
        if _url_imagen_valida(url, confiable=True):
            candidatos.append(
                Candidato(
                    url=url,
                    fuente='brave',
                    dominio=_dominio(url),
                    titulo=str(item.get('title') or ''),
                    confiable=True,
                )
            )
    return candidatos


def _clave_google_cse():
    key = (os.getenv('GOOGLE_CSE_KEY') or os.getenv('GOOGLE_API_KEY') or '').strip()
    cx = (os.getenv('GOOGLE_CSE_CX') or os.getenv('GOOGLE_CSE_ID') or '').strip()
    return key, cx


def _buscar_google_cse(consulta, limite=10):
    """Google Programmable Search (imágenes), opcional con clave + CX."""
    key, cx = _clave_google_cse()
    consulta = str(consulta or '').strip()
    if not key or not cx or not consulta:
        return []
    datos = _get_json(
        'https://www.googleapis.com/customsearch/v1',
        params={
            'key': key, 'cx': cx, 'q': consulta,
            'searchType': 'image', 'num': min(10, limite),
        },
    )
    if not datos:
        return []
    candidatos = []
    for item in (datos.get('items') or [])[:limite]:
        url = item.get('link') or (item.get('image') or {}).get('thumbnailLink')
        if _url_imagen_valida(url, confiable=True):
            candidatos.append(
                Candidato(
                    url=url,
                    fuente='google-cse',
                    dominio=_dominio(url),
                    titulo=str(item.get('title') or ''),
                    confiable=True,
                )
            )
    return candidatos


def _clave_bing_api():
    return (
        os.getenv('BING_SEARCH_V7_KEY')
        or os.getenv('BING_SEARCH_API_KEY')
        or ''
    ).strip()


def _buscar_bing_api(consulta, limite=10):
    """Bing Image Search API oficial (Azure), opcional con clave."""
    key = _clave_bing_api()
    consulta = str(consulta or '').strip()
    if not key or not consulta:
        return []
    datos = _get_json(
        'https://api.bing.microsoft.com/v7.0/images/search',
        params={'q': consulta, 'count': limite, 'mkt': 'es-VE'},
        headers={'Ocp-Apim-Subscription-Key': key},
    )
    if not datos:
        return []
    candidatos = []
    for item in (datos.get('value') or [])[:limite]:
        url = item.get('contentUrl') or item.get('thumbnailUrl')
        if _url_imagen_valida(url, confiable=True):
            candidatos.append(
                Candidato(
                    url=url,
                    fuente='bing-api',
                    dominio=_dominio(url),
                    titulo=str(item.get('name') or ''),
                    confiable=True,
                )
            )
    return candidatos


def _buscar_brave_html(consulta, limite=5):
    """Brave Search (HTML plano) → página de producto → og:image.

    Motor secundario que funciona incluso cuando Bing/DuckDuckGo bloquean la IP:
    se extrae el resultado, se abre la ficha y se toma la imagen de estudio.
    """
    consulta = str(consulta or '').strip()
    if not consulta:
        return []
    try:
        respuesta = _http.get(
            'https://search.brave.com/search',
            params={'q': consulta},
            timeout=_TIMEOUT_BUSQUEDA,
            tipo='html',
        )
        if respuesta.status_code != 200:
            return []
        enlaces = re.findall(r'href="(https?://[^"]+)"', respuesta.text)
    except Exception:
        return []

    paginas = []
    vistos = set()
    for enlace in enlaces:
        url = html.unescape(enlace).split('#')[0]
        if not url or url in vistos:
            continue
        if any(
            dominio in url
            for dominio in (
                'brave.com', 'google.', 'bing.', 'yandex', 'duckduckgo',
                'startpage', 'search.', 'facebook.', 'pinterest.', 'youtube.',
            )
        ):
            continue
        vistos.add(url)
        paginas.append(url)
        if len(paginas) >= limite:
            break

    candidatos = []
    for pagina in paginas:
        try:
            ficha = _http.get(pagina, timeout=_TIMEOUT_BUSQUEDA, tipo='html')
            if ficha.status_code != 200:
                continue
            coincidencias = _RE_OG.findall(ficha.text)
            if not coincidencias:
                coincidencias = re.findall(
                    r'"image"\s*:\s*"(https?://[^"]+\.(?:jpg|jpeg|png|webp))"',
                    ficha.text,
                    re.IGNORECASE,
                )
            titulo_m = re.search(r'<title[^>]*>(.*?)</title>', ficha.text, re.S | re.IGNORECASE)
            titulo = html.unescape(titulo_m.group(1)).strip() if titulo_m else ''
        except Exception:
            continue
        for url in coincidencias[:2]:
            url = html.unescape(url)
            if _url_imagen_valida(url, confiable=True):
                candidatos.append(
                    Candidato(
                        url=url,
                        fuente='brave-og',
                        dominio=_dominio(url),
                        titulo=f'{titulo} {pagina}',
                        confiable=True,
                    )
                )
                break
    return candidatos


_RE_OG = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)',
    re.IGNORECASE,
)


def _buscar_bing_og(consulta, limite=2):
    """Rastrea las páginas de producto de Bing Images y extrae su og:image.

    Emula la búsqueda manual: imagen de estudio => página => og:image.
    """
    consulta = str(consulta or '').strip()
    if not consulta:
        return []
    try:
        respuesta = _http.get(
            'https://www.bing.com/images/search',
            params={'q': consulta, 'form': 'HDRSC2', 'first': '1'},
            headers={'User-Agent': _UA, 'Accept-Language': 'es-VE,es;q=0.9,en;q=0.8'},
            timeout=_TIMEOUT_BUSQUEDA,
        )
        if respuesta.status_code != 200:
            return []
        purls = re.findall(r'purl&quot;:&quot;(.*?)&quot;', respuesta.text)
        purls = [html.unescape(u) for u in purls][: max(0, limite)]
    except Exception:
        return []
    candidatos = []
    for purl in purls:
        try:
            pagina = _http.get(
                purl,
                headers={'User-Agent': _UA, 'Accept-Language': 'es-VE,es;q=0.9'},
                timeout=_TIMEOUT_BUSQUEDA,
            )
            if pagina.status_code != 200:
                continue
            coincidencias = _RE_OG.findall(pagina.text)
        except Exception:
            continue
        for url in coincidencias[:2]:
            url = html.unescape(url)
            if _url_imagen_valida(url, confiable=True):
                candidatos.append(
                    Candidato(url=url, fuente='bing-og', dominio=_dominio(url), confiable=True)
                )
                break
    return candidatos


def _parsear_bing(texto):
    urls = re.findall(r'murl&quot;:&quot;(.*?)&quot;', texto)
    if not urls:
        urls = re.findall(r'"murl":"(.*?)"', texto)
    return [html.unescape(u).replace('\\/', '/') for u in urls]


def _buscar_web_bing(consulta, limite=15):
    try:
        respuesta = _http.get(
            'https://www.bing.com/images/search',
            params={'q': consulta, 'form': 'HDRSC2', 'first': '1'},
            headers={'User-Agent': _UA, 'Accept-Language': 'es-VE,es;q=0.9,en;q=0.8'},
            timeout=_TIMEOUT_BUSQUEDA,
        )
        if respuesta.status_code != 200:
            return []
        urls = _parsear_bing(respuesta.text)[:limite]
    except Exception:
        return []
    return [
        Candidato(url=u, fuente='bing-web', dominio=_dominio(u))
        for u in urls
        if _url_imagen_valida(u)
    ]


def _buscar_web_ddg(consulta, limite=15):
    try:
        sesion = requests.Session()
        inicio = _http.get(
            'https://duckduckgo.com/',
            params={'q': consulta, 'iax': 'images', 'ia': 'images'},
            headers={'User-Agent': _UA, 'Accept-Language': 'es-VE,es;q=0.9'},
            timeout=_TIMEOUT_BUSQUEDA,
        )
        match = re.search(r'vqd=["\']?([\d-]+)', inicio.text)
        if not match:
            return []
        datos = _http.get(
            'https://duckduckgo.com/i.js',
            params={'l': 'wt-wt', 'o': 'json', 'q': consulta, 'vqd': match.group(1), 'p': '1'},
            headers={
                'User-Agent': _UA,
                'Referer': 'https://duckduckgo.com/',
                'X-Requested-With': 'XMLHttpRequest',
            },
            timeout=_TIMEOUT_BUSQUEDA,
        )
        if datos.status_code != 200:
            return []
        resultados = (datos.json() or {}).get('results') or []
    except Exception:
        return []
    candidatos = []
    for item in resultados[:limite]:
        url = item.get('image')
        if not _url_imagen_valida(url):
            continue
        candidatos.append(
            Candidato(
                url=url,
                fuente='ddg-web',
                dominio=_dominio(url),
                ancho=int(item.get('width') or 0),
                alto=int(item.get('height') or 0),
            )
        )
    return candidatos


def _buscar_vtex(consulta, limite=6):
    """Catálogo VTEX (nacionales y regionales): imágenes directas.

    Fuente fiable multirrubro (farmacia, tecnología, hogar, bebés, alimentos…).
    Incluye el nombre del producto en ``titulo`` para filtrar por relevancia.
    """
    consulta = str(consulta or '').strip()
    if not consulta:
        return []
    candidatos = []
    for host in _fuentes_vtex():
        try:
            respuesta = _http.get(
                f'https://{host}/api/catalog_system/pub/products/search/',
                params={'ft': consulta, '_from': 0, '_to': max(1, limite) - 1},
                headers={'User-Agent': _UA, 'Accept': 'application/json'},
                timeout=_TIMEOUT_BUSQUEDA,
            )
            if respuesta.status_code not in (200, 206):
                continue
            datos = respuesta.json()
        except Exception:
            continue
        if not isinstance(datos, list):
            continue
        for producto in datos[:limite]:
            if not isinstance(producto, dict):
                continue
            nombre = str(producto.get('productName') or '').strip()
            marca = str(producto.get('brand') or '').strip()
            titulo = ' '.join(filter(None, (nombre, marca)))
            for item in producto.get('items', []) or []:
                url = None
                for imagen in item.get('images', []) or []:
                    cand = imagen.get('imageUrl') if isinstance(imagen, dict) else None
                    if _url_imagen_valida(cand, confiable=True):
                        url = cand
                        break
                if url:
                    candidatos.append(
                        Candidato(
                            url=url,
                            fuente=f'vtex:{host}',
                            dominio=_dominio(url),
                            titulo=titulo,
                            confiable=True,
                        )
                    )
                    break
    return candidatos


def _buscar_mercadolibre(consulta, limite=6):
    """Mercado Libre Venezuela vía API oficial (requiere MELI_ACCESS_TOKEN).

    Sin token se omite en silencio (la cascada continúa con las demás fuentes).
    """
    token = _token_mercadolibre()
    consulta = str(consulta or '').strip()
    if not token or not consulta:
        return []
    try:
        respuesta = _http.get(
            'https://api.mercadolibre.com/sites/MLV/search',
            params={'q': consulta, 'limit': limite},
            headers={'Authorization': f'Bearer {token}', 'User-Agent': _UA},
            timeout=_TIMEOUT_BUSQUEDA,
        )
        if respuesta.status_code != 200:
            return []
        datos = respuesta.json()
    except Exception:
        return []
    candidatos = []
    for producto in (datos.get('results') or [])[:limite]:
        titulo = str(producto.get('title') or '').strip()
        urls = []
        for imagen in producto.get('pictures') or []:
            urls.append(imagen.get('secure_url') or imagen.get('url'))
        urls.append(producto.get('thumbnail'))
        for url in urls:
            if _url_imagen_valida(url, confiable=True):
                candidatos.append(
                    Candidato(
                        url=url,
                        fuente='mercadolibre',
                        dominio=_dominio(url),
                        titulo=titulo,
                        confiable=True,
                    )
                )
                break
    return candidatos


_RE_TOKEN_NUM = re.compile(r'^\d+$')
_RE_TOKEN_UNIDAD = re.compile(
    r'^\d+(?:[.,]\d+)?\s?'
    r'(?:kg|kgs|g|gr|grs|gramos|mg|l|lt|lts|litro|litros|ml|cc|oz|lb|lbs|un|und|unid|'
    r'unidad|unidades|%|x\d*)$'
)
_SINONIMOS_LOCALES = {
    'refresco': 'gaseosa', 'gaseosa': 'refresco', 'soda': 'refresco',
    'champu': 'shampoo', 'shampoo': 'champu', 'acondicionador': 'crema enjuague',
    'celular': 'telefono', 'telefono': 'celular', 'computadora': 'computador',
    'computador': 'computadora', 'audifonos': 'auriculares',
    'detergente': 'jabon en polvo', 'panal': 'panal', 'atun': 'atun en lata',
    'nevera': 'refrigerador', 'refrigerador': 'nevera', 'licuadora': 'batidora',
    'desodorante': 'antitranspirante', 'toalla': 'toalla sanitaria',
    'taladro': 'perforador', 'llave': 'llave inglesa', 'caucho': 'llanta',
}


def _tokens_consulta(nombre, marca=None):
    texto = _texto_plano(f'{nombre or ""} {marca or ""}').lower()
    texto = re.sub(r'[^a-z0-9]+', ' ', texto)
    tokens = []
    vistos = set()
    for token in texto.split():
        if token in vistos:
            continue
        if token in _STOPWORDS or _RE_TOKEN_NUM.match(token):
            continue
        if _RE_TOKEN_UNIDAD.match(token):
            continue
        if len(token) < 3:
            continue
        vistos.add(token)
        tokens.append(token)
    return tokens


def _consultas_busqueda(
    nombre, marca, presentacion, descripcion, categoria=None, codigo_barras=None
):
    """Variantes de consulta tipo búsqueda humana.

    Combina nombre completo, marca, presentación, código de barras, categoría y
    sinónimos locales; elimina gramajes/unidades y limita a 8 variantes para no
    saturar la red.
    """
    marca_txt = _inferir_marca(nombre, descripcion, marca)
    tokens = _tokens_consulta(nombre, marca_txt)
    core = ' '.join(tokens)
    primeros = ' '.join(tokens[:3])
    nombre_txt = str(nombre or '').strip()

    variantes = []

    def _add(valor):
        texto = ' '.join(str(valor or '').split()).strip()
        if not texto or len(texto) < 3:
            return
        if texto.lower() in {v.lower() for v in variantes}:
            return
        variantes.append(texto)

    if codigo_barras:
        _add(str(codigo_barras))
        _add(f'{codigo_barras} {marca_txt or ""}')
    _add(core)
    _add(f'{core} venezuela')
    if marca_txt and primeros:
        if marca_txt.lower() in primeros.lower():
            _add(primeros)
        else:
            _add(f'{marca_txt} {primeros}')
    _add(nombre_txt)
    if core:
        _add(f'{core} producto')
        _add(f'{core} fondo blanco')
    for indice, token in enumerate(tokens[:4]):
        alterno = _SINONIMOS_LOCALES.get(token)
        if alterno:
            _add(' '.join(tokens[:indice] + [alterno] + tokens[indice + 1 :]))
    if categoria and primeros:
        _add(f'{primeros} {str(categoria).strip()}')
    if marca_txt:
        _add(marca_txt)
        if categoria:
            _add(f'{marca_txt} {str(categoria).strip()}')
    if not variantes:
        _add(' '.join(
            str(p).strip() for p in (nombre, marca, categoria) if p and str(p).strip()
        ))
    return variantes[:12]


def _clave_cache_candidatos(codigo_barras, nombre, marca, presentacion, categoria, nivel=0):
    base = '|'.join(
        str(p or '').strip().lower()
        for p in (codigo_barras, nombre, marca, presentacion, categoria, nivel)
    )
    return hashlib.sha1(base.encode('utf-8', 'ignore')).hexdigest()


def buscar_candidatos(
    *,
    codigo_barras=None,
    nombre=None,
    marca=None,
    presentacion=None,
    descripcion=None,
    categoria=None,
    limite=None,
    nivel=0,
):
    """Candidatos ordenados por confianza (mejor primero).

    ``nivel`` permite búsquedas persistentes por escenarios: cada reintento en
    segundo plano explora variantes de consulta nuevas (más genéricas) en lugar
    de repetir la misma.
    """
    limite = limite or _MAX_CANDIDATOS
    nivel = max(0, min(int(nivel or 0), 3))
    parametros = {
        'codigo_barras': codigo_barras,
        'nombre': nombre,
        'marca': marca,
        'presentacion': presentacion,
        'descripcion': descripcion,
        'categoria': categoria,
        'limite': limite,
        'nivel': nivel,
    }
    clave = _clave_cache_candidatos(
        codigo_barras, nombre, marca, presentacion, categoria, nivel
    )
    try:
        from backend.runtime_cache import get_or_load

        return get_or_load(
            f'candidatos_img:{clave}',
            lambda: _buscar_candidatos_impl(**parametros),
            ttl_seconds=_CACHE_BUSQUEDA_TTL,
        )
    except Exception:
        return _buscar_candidatos_impl(**parametros)


def _buscar_candidatos_impl(
    *,
    codigo_barras=None,
    nombre=None,
    marca=None,
    presentacion=None,
    descripcion=None,
    categoria=None,
    limite=None,
    nivel=0,
):
    from backend.utils import normalizar_codigo_barras

    limite = limite or _MAX_CANDIDATOS
    nivel = max(0, min(int(nivel or 0), 3))
    marca = _inferir_marca(nombre, descripcion, marca)
    presentacion = _inferir_presentacion(nombre, descripcion, presentacion)
    tokens = _tokens_relevancia(nombre, marca, descripcion)

    candidatos = []
    ean = normalizar_codigo_barras(codigo_barras)

    if ean:
        try:
            from backend.catalogo_maestro import imagen_maestro_por_codigo

            url_maestro = imagen_maestro_por_codigo(ean)
        except Exception:
            url_maestro = None
        if _url_imagen_valida(url_maestro):
            candidatos.append(
                Candidato(url=url_maestro, fuente='catalogo_maestro', dominio=_dominio(url_maestro))
            )

    consultas = _consultas_busqueda(
        nombre,
        marca,
        presentacion,
        descripcion,
        categoria=categoria,
        codigo_barras=ean,
    )
    # Escenario actual (0..3): cada reintento explora consultas nuevas.
    offset = nivel
    ventana_web = consultas[offset : offset + 4] or consultas[:4]
    ventana_dos = consultas[offset : offset + 2] or consultas[:2]
    base = ventana_web[0] if ventana_web else (consultas[0] if consultas else '')

    # Tareas de red (I/O) en paralelo: variantes de búsqueda humana en varios
    # motores, catálogos abiertos, site:host locales y catálogos directos.
    tareas = []
    if _BUSQUEDA_WEB:
        for consulta in ventana_web:
            tareas.append((_buscar_web_bing, consulta))
            tareas.append((_buscar_web_ddg, consulta))
        # Motor secundario gratuito (HTML plano) que suele funcionar cuando
        # Bing/DuckDuckGo bloquean la IP del datacenter.
        for consulta in ventana_web[:3]:
            tareas.append((_buscar_brave_html, consulta))
        if _clave_serpapi():
            for consulta in ventana_web[:3]:
                tareas.append((_buscar_serpapi, consulta))
        if _clave_brave():
            for consulta in ventana_web[:3]:
                tareas.append((_buscar_brave, consulta))
        if _clave_google_cse()[0] and _clave_google_cse()[1]:
            for consulta in ventana_web[:3]:
                tareas.append((_buscar_google_cse, consulta))
        if _clave_bing_api():
            for consulta in ventana_web[:3]:
                tareas.append((_buscar_bing_api, consulta))
        if base and _MAX_SITIOS > 0:
            for host in _fuentes_site()[:_MAX_SITIOS]:
                tareas.append((_buscar_web_bing, f'{base} site:{host}'))
        if base and _BING_OG:
            tareas.append((_buscar_bing_og, base))
    for consulta in ventana_dos:
        tareas.append((_buscar_off_por_nombre, consulta))
    if ean:
        tareas.append((_buscar_off_por_ean, ean))
    for consulta in ventana_dos:
        tareas.append((_buscar_vtex, consulta))
    if base and _token_mercadolibre():
        tareas.append((_buscar_mercadolibre, base))

    if tareas:
        workers = min(_BUSQUEDA_PARALELA, len(tareas))
        with ThreadPoolExecutor(max_workers=workers) as ejecutor:
            futuros = [ejecutor.submit(fn, arg) for fn, arg in tareas]
            for futuro in as_completed(futuros):
                try:
                    candidatos.extend(futuro.result() or [])
                except Exception:
                    continue

    # Deduplicar preservando la mejor fuente.
    unicos = {}
    for candidato in candidatos:
        if not _url_imagen_valida(
            candidato.url, confiable=getattr(candidato, 'confiable', False)
        ):
            continue
        clave = candidato.url.split('?')[0]
        if clave not in unicos:
            unicos[clave] = candidato

    puntuados = []
    fuentes_filtrables = (
        'bing-web', 'ddg-web', 'vtex', 'mercadolibre', 'serpapi', 'brave',
        'brave-og', 'bing-og', 'google-cse', 'bing-api',
    )
    for candidato in unicos.values():
        fuente_base = (candidato.fuente or '').split(':')[0]
        if fuente_base in fuentes_filtrables and not _relevante_web(candidato, tokens):
            continue
        _puntuar(candidato, marca=marca)
        puntuados.append(candidato)

    puntuados.sort(key=lambda c: c.score, reverse=True)
    return puntuados[:limite]


# ---------------------------------------------------------------------------
# Paso B — Validación de calidad
# ---------------------------------------------------------------------------
def _abrir_imagen(data):
    from PIL import Image

    imagen = Image.open(io.BytesIO(data))
    imagen.load()
    return imagen


def _varianza_laplaciana(imagen_rgb):
    try:
        import cv2
        import numpy as np

        gris = np.asarray(imagen_rgb.convert('L'), dtype='float64')
        return float(cv2.Laplacian(gris, cv2.CV_64F).var())
    except Exception:
        return None


def _ratio_borde_blanco(imagen_rgb):
    try:
        import numpy as np

        arreglo = np.asarray(imagen_rgb.convert('RGB'), dtype='uint8')
        alto, ancho = arreglo.shape[:2]
        margen = max(1, min(alto, ancho) // 20)
        bordes = np.concatenate(
            [
                arreglo[:margen].reshape(-1, 3),
                arreglo[-margen:].reshape(-1, 3),
                arreglo[:, :margen].reshape(-1, 3),
                arreglo[:, -margen:].reshape(-1, 3),
            ]
        )
        claros = np.all(bordes >= 235, axis=1)
        return float(claros.mean())
    except Exception:
        return 0.0


def validar_calidad(data, *, url=None):
    """(ok, motivo, metadatos). Descarta fotos no profesionales."""
    if not data:
        return False, 'vacia', {}
    try:
        imagen = _abrir_imagen(data)
    except Exception as error:
        return False, f'ilegible:{type(error).__name__}', {}

    ancho, alto = imagen.size
    meta = {'ancho': ancho, 'alto': alto}

    if min(ancho, alto) < _MIN_SIDE:
        return False, f'dimension_minima:{ancho}x{alto}', meta

    ratio = max(ancho, alto) / max(1, min(ancho, alto))
    meta['ratio'] = round(ratio, 2)
    if ratio > 3.5:
        return False, f'aspecto_extremo:{ratio:.2f}', meta

    rgb = imagen.convert('RGB')
    varianza = _varianza_laplaciana(rgb)
    if varianza is not None:
        meta['nitidez'] = round(varianza, 1)
        if varianza < _BLUR_MIN:
            return False, f'borrosa:{varianza:.1f}', meta

    bordes_blancos = _ratio_borde_blanco(rgb)
    meta['fondo_blanco'] = round(bordes_blancos, 3)

    if varianza is not None:
        try:
            import numpy as np

            desviacion = float(np.asarray(rgb, dtype='float64').std())
        except Exception:
            desviacion = 255.0
        meta['desviacion'] = round(desviacion, 1)
        if desviacion < 8:
            return False, 'imagen_plana', meta

    return True, 'ok', meta


# ---------------------------------------------------------------------------
# Paso C — Procesamiento local (rembg) → fondo blanco puro
# ---------------------------------------------------------------------------
def _sesion_rembg():
    global _REMBG_SESION
    if _REMBG_SESION is not None:
        return _REMBG_SESION
    with _REMBG_LOCK:
        if _REMBG_SESION is None:
            from rembg import new_session

            modelo = (os.getenv('LOCALIS_REMBG_MODEL') or 'u2net').strip() or 'u2net'
            _log(f'cargando modelo rembg={modelo}')
            _REMBG_SESION = new_session(modelo)
    return _REMBG_SESION


def _recortar_sobre_blanco(imagen, lado=None):
    """Recorta el sujeto (canal alfa) y lo centra en un lienzo blanco cuadrado."""
    from PIL import Image

    lado = lado or _LADO_FINAL
    if imagen.mode != 'RGBA':
        imagen = imagen.convert('RGBA')
    bbox = None
    try:
        bbox = imagen.getchannel('A').getbbox()
    except Exception:
        bbox = imagen.getbbox()
    if bbox:
        imagen = imagen.crop(bbox)
    ancho, alto = imagen.size
    if ancho <= 0 or alto <= 0:
        raise ValueError('recorte vacío')
    margen = int(max(ancho, alto) * 0.08)
    mayor = max(ancho, alto) + margen * 2
    lienzo = Image.new('RGB', (mayor, mayor), (255, 255, 255))
    lienzo.paste(imagen, ((mayor - ancho) // 2, (mayor - alto) // 2), imagen)
    lienzo = lienzo.resize((lado, lado), Image.LANCZOS)
    return lienzo


def _quitar_fondo_rembg(data):
    from rembg import remove

    sesion = _sesion_rembg()
    salida = remove(data, session=sesion, post_process_mask=True)
    return _abrir_imagen(salida).convert('RGBA')


def _recorte_fondo_claro(data):
    """Respaldo sin rembg: recorta el fondo claro uniforme (foto de estudio)."""
    from PIL import Image

    imagen = _abrir_imagen(data).convert('RGB')
    ancho, alto = imagen.size
    esquinas = [
        imagen.getpixel((2, 2)),
        imagen.getpixel((ancho - 3, 2)),
        imagen.getpixel((2, alto - 3)),
        imagen.getpixel((ancho - 3, alto - 3)),
    ]
    fondo = tuple(sum(c[i] for c in esquinas) // 4 for i in range(3))
    if min(fondo) < 232:
        return None

    try:
        import numpy as np

        arreglo = np.asarray(imagen, dtype='int16')
        distancia = np.abs(arreglo - np.array(fondo)).sum(axis=2)
        mascara = distancia > 45
        cobertura = float(mascara.mean())
        if cobertura < 0.02 or cobertura > 0.95:
            return None
        filas = np.where(mascara.any(axis=1))[0]
        columnas = np.where(mascara.any(axis=0))[0]
        bbox = (
            int(columnas[0]),
            int(filas[0]),
            int(columnas[-1]) + 1,
            int(filas[-1]) + 1,
        )
    except Exception:
        return None

    recorte = imagen.crop(bbox).convert('RGBA')
    return recorte


def _fondo_ya_limpio(data):
    """True si la imagen ya tiene fondo blanco/limpio (foto de estudio).

    En ese caso se evita rembg (CPU) y basta un recorte + lienzo blanco.
    """
    try:
        from PIL import Image
        import numpy as np

        imagen = Image.open(io.BytesIO(data)).convert('RGB')
        arreglo = np.asarray(imagen, dtype='uint8')
        alto, ancho = arreglo.shape[:2]
        if alto < 8 or ancho < 8:
            return False
        margen = max(1, min(alto, ancho) // 25)
        bordes = np.concatenate(
            [
                arreglo[:margen].reshape(-1, 3),
                arreglo[-margen:].reshape(-1, 3),
                arreglo[:, :margen].reshape(-1, 3),
                arreglo[:, -margen:].reshape(-1, 3),
            ]
        )
        return bool(np.all(bordes >= 235, axis=1).mean() >= 0.9)
    except Exception:
        return False


def procesar_fondo_blanco(data, *, url=None):
    """Retorna (bytes_webp, content_type) con fondo blanco puro, o (None, motivo).

    Atajo de velocidad: si la imagen ya viene con fondo blanco (catálogos VTEX,
    Mercado Libre, marcas), se recorta sin usar IA (mucho menos CPU). Solo se
    invoca rembg cuando el fondo no es limpio.
    """
    try:
        recorte = None
        if _fondo_ya_limpio(data):
            recorte = _recorte_fondo_claro(data)
        if recorte is None:
            # rembg es pesado en CPU: se serializa con el semáforo.
            with _SEMAFORO:
                try:
                    recorte = _quitar_fondo_rembg(data)
                except Exception as error:
                    _log(f'rembg no disponible ({type(error).__name__}: {error}); respaldo recorte claro')
                    recorte = None
        if recorte is None:
            recorte = _recorte_fondo_claro(data)
        if recorte is None:
            return None, 'sin_fondo_procesable'

        lienzo = _recortar_sobre_blanco(recorte, _LADO_FINAL)
        buffer = io.BytesIO()
        lienzo.save(buffer, 'WEBP', quality=92, method=5)
        optimizada = buffer.getvalue()
        if not optimizada:
            return None, 'procesamiento_vacio'
        return optimizada, 'image/webp'
    except Exception as error:
        return None, f'procesamiento_error:{type(error).__name__}'


# ---------------------------------------------------------------------------
# Descarga y evaluación de candidatos
# ---------------------------------------------------------------------------
def _descargar(url, intentos=None):
    """Descarga una imagen con reintentos (los CDN pueden fallar intermitentemente)."""
    intentos = intentos or _DESCARGA_INTENTOS
    for intento in range(max(1, intentos)):
        try:
            respuesta = _http.get(
                url,
                headers={
                    'User-Agent': _UA,
                    'Accept': 'image/avif,image/webp,image/*,*/*;q=0.8',
                    'Referer': f'{urlparse(url).scheme}://{urlparse(url).netloc}/',
                },
                timeout=_TIMEOUT_DESCARGA,
                stream=True,
                allow_redirects=True,
            )
            if respuesta.status_code == 200:
                tipo = (respuesta.headers.get('content-type') or '').lower()
                if tipo.startswith('text/') or 'html' in tipo:
                    return None
                if tipo and not tipo.startswith('image/'):
                    if not any(
                        urlparse(url).path.lower().endswith(ext) for ext in _EXT_IMAGEN_OK
                    ):
                        return None
                datos = bytearray()
                for fragmento in respuesta.iter_content(65536):
                    datos.extend(fragmento)
                    if len(datos) > _MAX_BYTES:
                        return None
                return bytes(datos) or None
            if respuesta.status_code in (403, 404, 410):
                return None
        except Exception as error:
            if intento + 1 >= intentos:
                _log(f'descarga fallida {url[:90]}: {type(error).__name__}: {error}')
                return None
        time.sleep(0.25 * (2 ** intento))
    return None


def _evaluar_candidato(candidato: Candidato):
    """Descarga, valida y procesa un candidato. Retorna (bytes, meta) o (None, motivo)."""
    ultimo_motivo = 'sin_descarga'
    for url in candidato.variantes():
        data = _descargar(url)
        if not data:
            ultimo_motivo = 'descarga_fallida'
            continue
        ok, motivo, meta = validar_calidad(data, url=url)
        if not ok:
            ultimo_motivo = motivo
            continue
        procesada, content_type = procesar_fondo_blanco(data, url=url)
        if not procesada:
            ultimo_motivo = str(content_type)
            continue
        ok_final, motivo_final, meta_final = validar_calidad(procesada, url=url)
        if not ok_final:
            ultimo_motivo = f'procesada_{motivo_final}'
            continue
        meta = {**meta, **meta_final, 'content_type': content_type, 'url_origen': url}
        return procesada, meta
    return None, ultimo_motivo


# ---------------------------------------------------------------------------
# Paso D — Almacenamiento y vinculación
# ---------------------------------------------------------------------------
def _almacenar_imagen(data, prefijo):
    token = hashlib.sha1(data).hexdigest()[:10]
    filename = f'auto_{_slug(prefijo)}_{token}.webp'
    try:
        from backend.supabase_storage import subir_bytes_con_respaldo

        url = subir_bytes_con_respaldo(
            data,
            filename=filename,
            content_type='image/webp',
            carpeta='productos',
        )
        if url:
            return url, 'supabase'
    except Exception as error:
        _log(f'storage no disponible ({type(error).__name__}); uso respaldo local')
    try:
        from backend.uploads_locales import guardar_bytes_upload

        url_local = guardar_bytes_upload(data, filename, carpeta='productos')
        if url_local:
            return url_local, 'local'
    except Exception as error:
        _log(f'respaldo local fallo: {type(error).__name__}: {error}')
    return None, None


def _guardar_en_catalogo_maestro(ean, url, *, nombre=None, marca=None, categoria=None):
    """Cachea la imagen procesada en el catálogo maestro para futuros imports."""
    if not ean or not url:
        return
    try:
        from backend.catalogo_maestro import guardar_imagen_maestro

        guardar_imagen_maestro(
            ean, url, nombre=nombre, marca=marca, categoria=categoria
        )
    except Exception as error:
        _log(f'catálogo maestro no actualizado ({type(error).__name__}: {error})')


def _imagen_puede_reemplazarse(imagen_actual, imagen_estado=None):
    estado = str(imagen_estado or '').strip().lower()
    if estado == 'real':
        return False
    if estado == 'logo':
        # El logo/monograma es un respaldo: se sigue intentando la foto real.
        return True
    if not imagen_actual:
        return True
    if isinstance(imagen_actual, memoryview):
        imagen_actual = bytes(imagen_actual)
    texto = str(imagen_actual).strip()
    if not texto:
        return True
    bajo = texto.lower()
    if 'placeholder' in bajo:
        return True
    # Una imagen ya almacenada (Storage) o subida a mano (/static/uploads) no se toca.
    if '/storage/v1/object/public/' in bajo:
        return False
    if bajo.startswith('/static/uploads/'):
        return False
    # URL externa del pipeline viejo (API): se puede reemplazar por la procesada.
    if bajo.startswith(('http://', 'https://')):
        return True
    return False


def _leer_producto(producto_id):
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT id, nombre, descripcion, codigo_barras, imagen_url, imagen_estado
            FROM productos
            WHERE id = ?
            """,
            (int(producto_id),),
        )
        fila = cursor.fetchone()
    if not fila:
        return None
    if isinstance(fila, dict):
        return fila
    return {
        'id': fila[0],
        'nombre': fila[1],
        'descripcion': fila[2],
        'codigo_barras': fila[3],
        'imagen_url': fila[4],
        'imagen_estado': fila[5] if len(fila) > 5 else None,
    }


def _actualizar_imagen(producto_id, url, fuente, estado='real'):
    from backend.db import get_db_connection

    # Sin comodines '%' en el SQL: psycopg2 interpreta '%' como formato cuando
    # se pasan parámetros y lanzaba "IndexError: tuple index out of range".
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            UPDATE productos
            SET imagen_url = ?, imagen_fuente = ?, imagen_estado = ?
            WHERE id = ?
              AND COALESCE(imagen_estado, 'pendiente') <> 'real'
              AND (
                imagen_url IS NULL
                OR TRIM(CAST(imagen_url AS TEXT)) = ''
                OR POSITION('placeholder' IN LOWER(CAST(imagen_url AS TEXT))) > 0
                OR COALESCE(imagen_estado, 'pendiente') = 'logo'
                OR (
                  LEFT(LOWER(CAST(imagen_url AS TEXT)), 4) = 'http'
                  AND POSITION(
                        '/storage/v1/object/public/'
                        IN CAST(imagen_url AS TEXT)
                      ) = 0
                )
              )
            """,
            (url, fuente, estado, int(producto_id)),
        )
        actualizadas = cursor.rowcount
        conexion.commit()
    return bool(actualizadas)


def _marcar_estado_imagen(producto_id, estado):
    """Marca rechazada/pendiente sin tocar la imagen (nunca degrada una real)."""
    from backend.db import get_db_connection

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                UPDATE productos
                SET imagen_estado = ?
                WHERE id = ?
                  AND COALESCE(imagen_estado, 'pendiente') <> 'real'
                """,
                (estado, int(producto_id)),
            )
            conexion.commit()
        return True
    except Exception as error:
        _log(f'marcar estado producto={producto_id} falló: {type(error).__name__}: {error}')
        return False


def _marcar_intento(producto_id):
    """Cuenta un intento de búsqueda (cola persistente con prioridad)."""
    from backend.db import get_db_connection

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                UPDATE productos
                SET imagen_intentos = COALESCE(imagen_intentos, 0) + 1,
                    imagen_ultimo_intento = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND COALESCE(imagen_estado, 'pendiente') <> 'real'
                """,
                (int(producto_id),),
            )
            conexion.commit()
    except Exception as error:
        _log(f'contar intento producto={producto_id} falló: {type(error).__name__}: {error}')


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------
def procesar_producto(
    producto_id,
    *,
    codigo_barras=None,
    nombre=None,
    descripcion=None,
    marca=None,
    presentacion=None,
    categoria=None,
    forzar=False,
    nivel=0,
):
    """Ejecuta el pipeline completo para un producto. Nunca lanza."""
    if not pipeline_habilitado():
        return ResultadoProcesamiento(ok=False, motivo='pipeline_deshabilitado')

    estado_actual = None
    if producto_id and (nombre is None or codigo_barras is None or descripcion is None):
        fila = _leer_producto(producto_id) or {}
        nombre = nombre or fila.get('nombre')
        descripcion = descripcion or fila.get('descripcion')
        codigo_barras = codigo_barras or fila.get('codigo_barras')
        imagen_actual = fila.get('imagen_url')
        estado_actual = fila.get('imagen_estado')
    else:
        imagen_actual = None

    if producto_id and not forzar and not _imagen_puede_reemplazarse(
        imagen_actual, estado_actual
    ):
        return ResultadoProcesamiento(ok=False, motivo='imagen_manual_conservada')

    if producto_id:
        _marcar_intento(producto_id)

    ean = None
    try:
        from backend.utils import normalizar_codigo_barras

        ean = normalizar_codigo_barras(codigo_barras)
    except Exception:
        ean = codigo_barras

    try:
        from backend.categorias_producto import clasificar_categoria, imagen_para_categoria
    except Exception:
        clasificar_categoria = None
        imagen_para_categoria = None

    categoria_efectiva = categoria
    if not categoria_efectiva and clasificar_categoria is not None:
        categoria_efectiva = clasificar_categoria(
            nombre=nombre, descripcion=descripcion, marca=marca
        )

    inicio = time.monotonic()
    _log(
        f'producto={producto_id} inicio ean={ean!r} nombre={nombre!r} '
        f'marca={marca!r} presentacion={presentacion!r} categoria={categoria_efectiva!r}'
    )

    candidatos = buscar_candidatos(
        codigo_barras=ean,
        nombre=nombre,
        marca=marca,
        presentacion=presentacion,
        descripcion=descripcion,
        categoria=categoria_efectiva,
        nivel=nivel,
    )
    _log(f'producto={producto_id} candidatos={len(candidatos)} nivel={nivel}')

    ultimo_motivo = 'sin_candidatos'
    for candidato in candidatos:
        if time.monotonic() - inicio > _BUDGET_SEC:
            ultimo_motivo = 'presupuesto_agotado'
            break
        procesada, meta_o_motivo = _evaluar_candidato(candidato)
        if not procesada:
            ultimo_motivo = str(meta_o_motivo)
            continue
        url, destino = _almacenar_imagen(procesada, nombre or f'producto-{producto_id}')
        if not url:
            ultimo_motivo = 'almacenamiento_fallido'
            continue
        from backend.utils import imagen_url_para_persistir

        url = imagen_url_para_persistir(url)
        if not url:
            ultimo_motivo = 'url_no_persistible'
            continue
        fuente = f'profesional_{candidato.fuente}_{destino}'
        try:
            actualizado = _actualizar_imagen(producto_id, url, fuente) if producto_id else True
        except Exception as error:
            _log(f'producto={producto_id} no se pudo actualizar la BD: {type(error).__name__}: {error}')
            actualizado = False
        if not actualizado:
            ultimo_motivo = 'no_actualizado'
            continue
        _guardar_en_catalogo_maestro(ean, url, nombre=nombre, marca=marca, categoria=categoria)
        _log_pipeline(producto_id, ean, 'asignada', fuente=fuente)
        _log(f'producto={producto_id} OK fuente={candidato.fuente} url={url}')
        return ResultadoProcesamiento(
            ok=True, url=url, fuente=fuente, detalle={'origen': meta_o_motivo}
        )

    _log_pipeline(producto_id, ean, 'sin_imagen', motivo=ultimo_motivo)
    _log(f'producto={producto_id} sin imagen real ({ultimo_motivo}); respaldo por marca')

    # Respaldo visual secundario: logo/monograma de la marca (nunca vacío ni
    # placeholder genérico si la marca es conocida). Se mantiene reintentable
    # para conseguir la foto real en segundo plano.
    if producto_id:
        candidatos_hubo = bool(candidatos)
        marca_efectiva = marca or _inferir_marca(nombre, descripcion)
        logo_url = None
        logo_fuente = None
        if marca_efectiva:
            try:
                from backend.marca_logo import resolver_logo_marca

                logo_url, logo_fuente = resolver_logo_marca(marca_efectiva)
            except Exception as error:
                _log(f'producto={producto_id} logo de marca no resuelto: {type(error).__name__}')
        if logo_url:
            try:
                if _actualizar_imagen(
                    producto_id, logo_url, logo_fuente or 'logo_marca', estado='logo'
                ):
                    _log(
                        f'producto={producto_id} logo de marca asignado '
                        f'({logo_fuente!r}) marca={marca_efectiva!r}'
                    )
                    return ResultadoProcesamiento(
                        ok=False, url=logo_url, fuente=logo_fuente, motivo=ultimo_motivo
                    )
            except Exception as error:
                _log(f'producto={producto_id} logo no aplicado: {type(error).__name__}: {error}')

        # Sin marca conocida: tarjeta limpia y **distinta por producto** (pendiente).
        # Sustituye al placeholder genérico de categoría para que ninguna tarjeta
        # quede con un recurso vacío/impersonal; sigue reintentable para foto real.
        try:
            fila = _leer_producto(producto_id) or {}
            actual = str(fila.get('imagen_url') or '').strip().lower()
            reemplazable = (
                not actual
                or 'placeholder' in actual
                or actual.startswith('/static/img/')
            )
            if reemplazable:
                tarjeta = None
                try:
                    from backend.marca_logo import archivo_tarjeta_producto

                    tarjeta = archivo_tarjeta_producto(nombre, categoria_efectiva)
                except Exception as error:
                    _log(
                        f'producto={producto_id} tarjeta no generada: '
                        f'{type(error).__name__}'
                    )
                if tarjeta:
                    _actualizar_imagen(
                        producto_id, tarjeta, 'tarjeta_producto', estado='pendiente'
                    )
                elif imagen_para_categoria is not None:
                    fallback = imagen_para_categoria(categoria_efectiva or 'otros')
                    _actualizar_imagen(
                        producto_id, fallback, 'placeholder_categoria', estado='pendiente'
                    )
        except Exception as error:
            _log(f'producto={producto_id} fallback no aplicado: {type(error).__name__}: {error}')

        # Si hubo candidatas y todas se rechazaron, marcar 'rechazada' para
        # reintento con estrategia ampliada más adelante.
        estado_final = 'rechazada' if candidatos_hubo else 'pendiente'
        _marcar_estado_imagen(producto_id, estado_final)

    return ResultadoProcesamiento(ok=False, motivo=ultimo_motivo)


def programar_procesamiento_producto(producto_id, **kwargs):
    """Lanza el pipeline en un hilo daemon. Nunca bloquea la respuesta HTTP."""
    if not producto_id or not pipeline_habilitado():
        return False
    pid = int(producto_id)
    with _LOCK_VUELO:
        if pid in _EN_VUELO:
            _log(f'producto={pid} ya está en vuelo, omitido')
            return False
        _EN_VUELO.add(pid)

    def _trabajo():
        try:
            procesar_producto(pid, **kwargs)
        except Exception as error:
            _log(f'producto={pid} fallo inesperado: {type(error).__name__}: {error}')
        finally:
            with _LOCK_VUELO:
                _EN_VUELO.discard(pid)

    threading.Thread(
        target=_trabajo,
        name=f'localis-img-pro-{pid}',
        daemon=True,
    ).start()
    return True


def _productos_pendientes(comercio_id):
    from backend.db import get_db_connection

    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            """
            SELECT id, nombre, descripcion, codigo_barras, imagen_url,
                   imagen_estado, imagen_intentos
            FROM productos
            WHERE comercio_id = ?
            ORDER BY imagen_ultimo_intento ASC NULLS FIRST,
                     COALESCE(imagen_intentos, 0) ASC,
                     id ASC
            """,
            (int(comercio_id),),
        )
        filas = cursor.fetchall()
    pendientes = []
    for fila in filas:
        registro = fila if isinstance(fila, dict) else {
            'id': fila[0],
            'nombre': fila[1],
            'descripcion': fila[2],
            'codigo_barras': fila[3],
            'imagen_url': fila[4],
            'imagen_estado': fila[5] if len(fila) > 5 else None,
            'imagen_intentos': fila[6] if len(fila) > 6 else 0,
        }
        estado = str(registro.get('imagen_estado') or '').strip().lower()
        if estado == 'real':
            continue
        # Pendiente/rechazada/logo o URL reemplazable (placeholder/vacía/externa).
        if estado in ('pendiente', 'rechazada', 'logo') or _imagen_puede_reemplazarse(
            registro.get('imagen_url')
        ):
            pendientes.append(registro)
    return pendientes


def procesar_inventario(comercio_id, limite=None, presupuesto_seg=None):
    """Procesa en lote los productos sin imagen de un comercio (CSV).

    ``presupuesto_seg`` permite acotar el ciclo (p. ej. 30-60 s) sin tocar el
    presupuesto global del pipeline.
    """
    if not pipeline_habilitado():
        return 0
    limite = limite or _CSV_MAX
    try:
        pendientes = _productos_pendientes(comercio_id)
    except Exception as error:
        _log(f'inventario comercio={comercio_id} no consultable: {type(error).__name__}: {error}')
        return 0

    inicio = time.monotonic()
    actualizados = 0
    seleccion = pendientes[:limite]
    if not seleccion:
        _log(f'inventario comercio={comercio_id} sin pendientes')
        return 0

    presupuesto = _CSV_BUDGET_SEC if presupuesto_seg is None else max(5.0, float(presupuesto_seg))

    def _una(producto):
        if time.monotonic() - inicio > presupuesto:
            return False
        try:
            intentos = int(producto.get('imagen_intentos') or 0)
            resultado = procesar_producto(
                producto.get('id'),
                codigo_barras=producto.get('codigo_barras'),
                nombre=producto.get('nombre'),
                descripcion=producto.get('descripcion'),
                nivel=min(intentos, 3),
            )
            return bool(resultado.ok)
        except Exception as error:
            _log(f'inventario producto={producto.get("id")} fallo: {type(error).__name__}')
            return False

    # Búsqueda/descarga en paralelo (I/O); rembg queda serializado por semáforo.
    # Encolado acotado: nunca se registran los 2.000 productos de golpe, así el
    # presupuesto del ciclo se respeta de verdad (solo quedan en vuelo <= workers).
    trabajadores = min(max(1, _IMG_TRABAJADORES), len(seleccion))
    cola = list(seleccion)
    ejecutor = ThreadPoolExecutor(max_workers=trabajadores)
    en_vuelo = set()
    try:
        while True:
            while (
                len(en_vuelo) < trabajadores
                and cola
                and (time.monotonic() - inicio) <= presupuesto
            ):
                en_vuelo.add(ejecutor.submit(_una, cola.pop(0)))
            if not en_vuelo:
                break
            restante = presupuesto - (time.monotonic() - inicio)
            if restante <= 0:
                break
            hechos, en_vuelo = wait(
                en_vuelo,
                timeout=min(restante, 5.0),
                return_when=FIRST_COMPLETED,
            )
            for futuro in hechos:
                try:
                    if futuro.result():
                        actualizados += 1
                except Exception:
                    continue
    finally:
        for futuro in en_vuelo:
            futuro.cancel()
        ejecutor.shutdown(wait=False, cancel_futures=True)

    _log(
        f'inventario comercio={comercio_id} actualizados={actualizados}/'
        f'{len(pendientes)} (lote={len(seleccion)}, workers={trabajadores}, '
        f'presupuesto={presupuesto:.0f}s)'
    )
    return actualizados


def programar_procesamiento_inventario(comercio_id, limite=None):
    """Lanza el procesamiento por lotes en segundo plano."""
    if not comercio_id:
        return None
    if not pipeline_habilitado():
        return None

    def _trabajo():
        try:
            procesar_inventario(comercio_id, limite=limite)
        except Exception as error:
            _log(f'inventario comercio={comercio_id} fallo: {type(error).__name__}: {error}')

    hilo = threading.Thread(
        target=_trabajo,
        name=f'localis-img-pro-csv-{comercio_id}',
        daemon=True,
    )
    hilo.start()
    return hilo

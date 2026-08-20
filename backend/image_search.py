"""Búsqueda de imágenes en catálogos externos (sin descargar al servidor)."""

import re
import time

import requests
from urllib.parse import quote

from backend.utils import normalizar_codigo_barras

# Tarjetas de catálogo (~180px CSS): proxy compacto vía wsrv.nl
_WSRV_ANCHO = 300
_WSRV_ALTO = 300
_WSRV_FIT = 'cover'
_WSRV_FORMATO = 'webp'
_WSRV_CALIDAD = 80


def limpiar_nombre_producto(nombre: str) -> str:
    """Elimina medidas y ajusta sinónimos de productos locales."""
    if not nombre:
        return ''

    nombre_limpio = nombre.lower().strip()

    sinonimos = {
        'panela las llaves': 'jabon las llaves panela azul',
        'harina pan': 'harina pan blanca',
    }
    for clave, reemplazo in sinonimos.items():
        if clave in nombre_limpio:
            nombre_limpio = nombre_limpio.replace(clave, reemplazo)

    nombre_limpio = re.sub(
        r'\b\d+(\.\d+)?\s*(g|gr|kg|ml|l|lt|ltr|oz|lb|unid|v|w|pulg|mm|cm|m|pack)\b',
        '',
        nombre_limpio,
        flags=re.IGNORECASE,
    )
    nombre_limpio = re.sub(r'[^\w\s]', ' ', nombre_limpio)
    return ' '.join(nombre_limpio.split())


def optimizar_url_imagen(url_original: str) -> str:
    """Proxy WebP vía wsrv.nl: tamaño fijo, cover y compresión para tarjetas."""
    if not url_original or not url_original.startswith('http'):
        return None
    if any(
        bad in url_original.lower()
        for bad in ('.svg', 'placeholder', 'default-product')
    ):
        return None
    if 'wsrv.nl' in url_original.lower():
        return url_original
    url_encriptada = quote(url_original, safe='')
    return (
        f'https://wsrv.nl/?url={url_encriptada}'
        f'&w={_WSRV_ANCHO}&h={_WSRV_ALTO}&fit={_WSRV_FIT}'
        f'&output={_WSRV_FORMATO}&q={_WSRV_CALIDAD}'
    )


def buscar_openfoodfacts_texto(query: str) -> str:
    """Búsqueda por texto en OpenFoodFacts."""
    if not query or not query.strip():
        return None
    try:
        url = (
            'https://world.openfoodfacts.org/cgi/search.pl'
            f'?search_terms={quote(query.strip())}&search_simple=1&action=process&json=1'
        )
        res = requests.get(url, headers={'User-Agent': 'LocalisApp/1.0'}, timeout=4)
        if res.status_code != 200:
            return None
        products = (res.json() or {}).get('products') or []
        for prod in products[:5]:
            img = prod.get('image_front_url') or prod.get('image_url')
            if img:
                optimizada = optimizar_url_imagen(img)
                if optimizada:
                    return optimizada
    except Exception:
        pass
    return None


def buscar_openfoodfacts_barcode(codigo: str) -> str:
    """Consulta OpenFoodFacts por EAN/UPC exacto."""
    if not codigo:
        return None
    try:
        url = f'https://world.openfoodfacts.org/api/v0/product/{quote(codigo)}.json'
        res = requests.get(url, headers={'User-Agent': 'LocalisApp/1.0'}, timeout=4)
        if res.status_code != 200:
            return None
        data = res.json()
        if str(data.get('status')) != '1':
            return None
        producto = data.get('product') or {}
        img = (
            producto.get('image_front_url')
            or producto.get('image_url')
            or producto.get('image_front_small_url')
        )
        if img:
            return optimizar_url_imagen(img)
    except Exception:
        pass
    return None


def _consulta_nombre_descripcion(nombre, descripcion):
    """Combina nombre y descripción en una sola consulta de catálogo."""
    partes = []
    for valor in (nombre, descripcion):
        limpio = limpiar_nombre_producto(valor or '')
        if limpio and limpio not in partes:
            partes.append(limpio)
    return ' '.join(partes).strip()


def obtener_url_imagen_automatica(
    nombre=None,
    codigo_barras=None,
    descripcion=None,
    modo_rapido=True,
    **kwargs,
):
    """
    Resolución externa sin descargar al servidor.
    Prioridad: código de barras → nombre+descripción → solo nombre.
    """
    del kwargs

    codigo = normalizar_codigo_barras(codigo_barras)
    if codigo:
        url = buscar_openfoodfacts_barcode(codigo)
        if url:
            return url

    consulta = _consulta_nombre_descripcion(nombre, descripcion)
    if consulta:
        url = buscar_openfoodfacts_texto(consulta)
        if url:
            return url

    if nombre:
        consulta_nombre = limpiar_nombre_producto(nombre)
        if consulta_nombre and consulta_nombre != consulta:
            url = buscar_openfoodfacts_texto(consulta_nombre)
            if url:
                return url

    if not modo_rapido:
        time.sleep(0.2)

    return None

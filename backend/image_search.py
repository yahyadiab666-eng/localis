import re
import time
import requests
from urllib.parse import quote

def limpiar_nombre_producto(nombre: str) -> str:
    """Elimina medidas y ajusta sinónimos de productos locales."""
    if not nombre:
        return ""

    nombre_limpio = nombre.lower().strip()

    # Reemplazos específicos para evitar ambigüedades en búsquedas
    sinonimos = {
        "panela las llaves": "jabon las llaves panela azul",
        "harina pan": "harina pan blanca",
    }

    for clave, reemplazo in sinonimos.items():
        if clave in nombre_limpio:
            nombre_limpio = nombre_limpio.replace(clave, reemplazo)

    # Eliminar unidades de medida (1kg, 500g, 1.5l, etc.)
    nombre_limpio = re.sub(
        r'\b\d+(\.\d+)?\s*(g|gr|kg|ml|l|lt|ltr|oz|lb|unid|v|w|pulg|mm|cm|m|pack)\b',
        '',
        nombre_limpio,
        flags=re.IGNORECASE
    )
    nombre_limpio = re.sub(r'[^\w\s]', ' ', nombre_limpio)
    return " ".join(nombre_limpio.split())


def optimizar_url_imagen(url_original: str) -> str:
    """Pasa la URL por wsrv.nl para servirla en WebP optimizado y evitar bloqueos CORS."""
    if not url_original or not url_original.startswith('http'):
        return None
    
    # Filtrar svgs o imágenes transparentes por defecto
    if any(bad in url_original.lower() for bad in ['.svg', 'placeholder', 'default-product']):
        return None

    url_encriptada = quote(url_original, safe='')
    return f"https://wsrv.nl/?url={url_encriptada}&w=600&output=webp"


def buscar_bing_imagenes(query: str) -> str:
    """Buscador primario ultra confiable basado en Bing Images (sin bloqueo)."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        url = f"https://www.bing.com/images/async?q={quote(query)}&first=1&count=5"
        res = requests.get(url, headers=headers, timeout=4)
        
        if res.status_code == 200:
            urls = re.findall(r'murl&quot;:&quot;(https?://[^&]+)&quot;', res.text)
            for u in urls:
                if u.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')) or 'image' in u.lower():
                    return optimizar_url_imagen(u)
    except Exception:
        pass
    return None


def buscar_openfoodfacts_texto(query: str) -> str:
    """Buscador secundario en catálogo global de víveres/alimentos."""
    try:
        url = f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={quote(query)}&search_simple=1&action=process&json=1"
        res = requests.get(url, headers={"User-Agent": "LocalisApp/1.0"}, timeout=3)
        if res.status_code == 200:
            data = res.json()
            products = data.get('products', [])
            for prod in products[:3]:
                img = prod.get('image_front_url') or prod.get('image_url')
                if img:
                    return optimizar_url_imagen(img)
    except Exception:
        pass
    return None


def buscar_openfoodfacts_barcode(codigo: str) -> str:
    """Consulta OpenFoodFacts por EAN/UPC (más precisa que el nombre)."""
    if not codigo:
        return None
    try:
        url = f"https://world.openfoodfacts.org/api/v0/product/{quote(codigo)}.json"
        res = requests.get(url, headers={"User-Agent": "LocalisApp/1.0"}, timeout=4)
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


def obtener_url_imagen_automatica(nombre: str = None, codigo_barras: str = None, descripcion: str = None, modo_rapido: bool = False, **kwargs) -> str:
    """Búsqueda en cascada: código de barras → nombre. No usa imagen genérica."""
    from backend.utils import normalizar_codigo_barras

    codigo = normalizar_codigo_barras(codigo_barras)
    if codigo:
        url_ean = buscar_openfoodfacts_barcode(codigo)
        if url_ean:
            return url_ean
        url_bing_ean = buscar_bing_imagenes(f"{codigo} producto")
        if url_bing_ean:
            return url_bing_ean

    nombre_limpio = limpiar_nombre_producto(nombre)
    opciones_busqueda = [nombre_limpio]
    if nombre and nombre.strip() and nombre.strip() not in opciones_busqueda:
        opciones_busqueda.append(nombre.strip())
    if descripcion:
        desc = limpiar_nombre_producto(descripcion)
        if desc and desc not in opciones_busqueda:
            opciones_busqueda.append(desc)

    for q in opciones_busqueda:
        if not q:
            continue

        url_bing = buscar_bing_imagenes(f"{q} producto")
        if url_bing:
            return url_bing

        url_off = buscar_openfoodfacts_texto(q)
        if url_off:
            return url_off

        if not modo_rapido:
            time.sleep(0.3)

    return None
"""
Estudio de producto con IA (rembg) para descargas automáticas nuevas.

Solo corre en el hilo de descubrimiento, nunca en la ruta HTTP.
Si rembg no está, tarda de más o no aísla el objeto, retorna None
y el caller persiste la imagen original en lienzo limpio.
"""

from __future__ import annotations

import io
import os
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

from PIL import Image

from backend.images import FONDO_LIENZO, QUALITY, _nombre_archivo_seguro

_LOG = '[Localis IA]'
_MODELO = os.getenv('LOCALIS_IA_MODELO', 'u2netp')
_TIMEOUT_SEG = float(os.getenv('LOCALIS_IA_TIMEOUT_SEG', '18'))
_TIMEOUT_INICIO_SEG = float(os.getenv('LOCALIS_IA_TIMEOUT_INICIO_SEG', '90'))
_MIN_OPACO = 0.03
_LADO_LIENZO = 400

_session = None
_session_lock = threading.Lock()
_modelo_listo = False
_rembg_disponible = None
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='localis-ia')


def ia_estudio_habilitada() -> bool:
    if (os.getenv('LOCALIS_IA_FONDO') or '1').strip().lower() in {
        '0', 'false', 'no', 'off',
    }:
        return False
    return _probar_import_rembg()


def _probar_import_rembg() -> bool:
    global _rembg_disponible
    if _rembg_disponible is not None:
        return _rembg_disponible
    try:
        import rembg  # noqa: F401
        _rembg_disponible = True
    except Exception as error:
        print(f'{_LOG} rembg no disponible, se usa lienzo original: {type(error).__name__}')
        _rembg_disponible = False
    return _rembg_disponible


def _obtener_sesion():
    global _session, _modelo_listo
    if _session is not None:
        return _session
    with _session_lock:
        if _session is not None:
            return _session
        from rembg import new_session

        _session = new_session(_MODELO)
        _modelo_listo = True
        print(f'{_LOG} modelo {_MODELO} listo')
        return _session


def _mascara_detecta_producto(img_rgba) -> bool:
    try:
        alpha = img_rgba.getchannel('A')
    except Exception:
        return False
    bbox = alpha.getbbox()
    if not bbox:
        return False
    ancho, alto = img_rgba.size
    if ancho <= 0 or alto <= 0:
        return False
    bw = bbox[2] - bbox[0]
    bh = bbox[3] - bbox[1]
    if bw * bh < 0.04 * ancho * alto:
        return False
    hist = alpha.histogram()
    opacos = sum(hist[128:])
    return opacos >= _MIN_OPACO * ancho * alto


def _centrar_en_lienzo_limpio(img_rgba, lado=_LADO_LIENZO):
    alpha = img_rgba.getchannel('A')
    bbox = alpha.getbbox()
    if bbox:
        pad_x = max(2, int((bbox[2] - bbox[0]) * 0.06))
        pad_y = max(2, int((bbox[3] - bbox[1]) * 0.06))
        caja = (
            max(0, bbox[0] - pad_x),
            max(0, bbox[1] - pad_y),
            min(img_rgba.width, bbox[2] + pad_x),
            min(img_rgba.height, bbox[3] + pad_y),
        )
        img_rgba = img_rgba.crop(caja)
    copia = img_rgba.copy()
    copia.thumbnail((lado, lado), Image.Resampling.LANCZOS)
    lienzo = Image.new('RGB', (lado, lado), FONDO_LIENZO)
    x = (lado - copia.width) // 2
    y = (lado - copia.height) // 2
    if copia.mode == 'RGBA':
        lienzo.paste(copia, (x, y), copia)
    else:
        lienzo.paste(copia, (x, y))
    return lienzo


def _aislar_bytes_sync(data_bytes: bytes) -> bytes | None:
    from rembg import remove

    sesion = _obtener_sesion()
    recortada = remove(data_bytes, session=sesion, alpha_matting=False)
    if not recortada:
        return None
    img = Image.open(io.BytesIO(recortada))
    img.load()
    if img.mode != 'RGBA':
        img = img.convert('RGBA')
    if not _mascara_detecta_producto(img):
        print(f'{_LOG} recorte descartado: objeto no detectado con claridad')
        return None
    lienzo = _centrar_en_lienzo_limpio(img, _LADO_LIENZO)
    buffer = io.BytesIO()
    lienzo.save(buffer, 'WEBP', quality=QUALITY, method=4)
    return buffer.getvalue()


def aislar_producto_webp(data_bytes: bytes) -> bytes | None:
    """Quita el fondo y devuelve WebP en lienzo #fffefb, o None."""
    if not data_bytes or not ia_estudio_habilitada():
        return None
    timeout = _TIMEOUT_INICIO_SEG if not _modelo_listo else _TIMEOUT_SEG
    try:
        futuro = _executor.submit(_aislar_bytes_sync, data_bytes)
        return futuro.result(timeout=timeout)
    except FuturesTimeout:
        print(f'{_LOG} timeout {timeout:.0f}s; se usa la imagen original')
        return None
    except Exception as error:
        print(f'{_LOG} fallback ({type(error).__name__}): {error}')
        return None


def procesar_descarga_oficial(data_bytes: bytes, prefijo='cat'):
    """
    Intenta WebP de estudio. None si la IA no aplica, para comprimir el original.
    Retorna (bytes, content_type, filename) o None. Nunca lanza.
    """
    try:
        webp = aislar_producto_webp(data_bytes)
        if not webp:
            return None
        filename = _nombre_archivo_seguro(prefijo, 'webp')
        return webp, 'image/webp', filename
    except Exception as error:
        print(f'{_LOG} procesar_descarga omitido: {type(error).__name__}: {error}')
        return None

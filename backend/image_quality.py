"""
Filtro estricto de calidad visual para fotos de catálogo.

Descarta miniaturas, fotos desproporcionadas y recortes borrosos
antes de persistir una URL automática. Las subidas manuales del
comerciante no pasan por este rechazo (deben nacer visibles).
"""

from __future__ import annotations

import io

from PIL import Image, ImageFilter, ImageStat

MIN_LADO_PX = 260
MAX_LADO_PX = 4000
MIN_ASPECTO = 0.50
MAX_ASPECTO = 1.75
MIN_NITIDEZ = 8.0
MAX_BYTES_INSPECCION = 2 * 1024 * 1024
_LOG = '[Localis Calidad]'


def evaluar_imagen_bytes(data, min_lado=MIN_LADO_PX, exigir_fondo_ficha=False):
    """
    Inspecciona bytes con Pillow.
    Retorna dict: ok, motivo, ancho, alto, nitidez.
    """
    if not data or len(data) < 80:
        return _fallo('archivo vacio o demasiado pequeno')
    if len(data) > MAX_BYTES_INSPECCION:
        return _fallo('archivo demasiado pesado para catalogo')
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as error:
        return _fallo(f'no se pudo decodificar ({type(error).__name__})')

    ancho, alto = img.size
    if ancho < min_lado or alto < min_lado:
        return _fallo(f'resolucion baja {ancho}x{alto}', ancho, alto)
    if ancho > MAX_LADO_PX or alto > MAX_LADO_PX:
        return _fallo(f'resolucion extrema {ancho}x{alto}', ancho, alto)
    if min(ancho, alto) <= 0:
        return _fallo('dimensiones invalidas', ancho, alto)
    aspecto = ancho / float(alto)
    if aspecto < MIN_ASPECTO or aspecto > MAX_ASPECTO:
        return _fallo(f'proporcion {aspecto:.2f} fuera de rango', ancho, alto)

    nitidez = _nitidez_bordes(img)
    if nitidez is not None and nitidez < MIN_NITIDEZ:
        return _fallo(
            f'imagen borrosa (nitidez={nitidez:.1f})', ancho, alto, nitidez
        )
    if exigir_fondo_ficha and not _fondo_parece_ficha(img):
        return _fallo('no parece ficha de producto (fondo de escena)', ancho, alto, nitidez)
    return {
        'ok': True,
        'motivo': None,
        'ancho': ancho,
        'alto': alto,
        'nitidez': nitidez,
    }


def metadatos_pasan_umbral(ancho=None, alto=None, min_lado=MIN_LADO_PX):
    if ancho is None or alto is None:
        return None
    try:
        w, h = int(ancho), int(alto)
    except (TypeError, ValueError):
        return None
    if w < min_lado or h < min_lado:
        return False
    if min(w, h) <= 0:
        return False
    aspecto = w / float(h)
    if aspecto < MIN_ASPECTO or aspecto > MAX_ASPECTO:
        return False
    return True


def _fondo_parece_ficha(img):
    """True si al menos dos esquinas son claras (empaque de catálogo, no foto de calle)."""
    try:
        rgb = img.convert('RGB')
        ancho, alto = rgb.size
        if ancho < 40 or alto < 40:
            return False
        pad = max(8, min(ancho, alto) // 25)
        cajas = (
            (0, 0, pad, pad),
            (ancho - pad, 0, ancho, pad),
            (0, alto - pad, pad, alto),
            (ancho - pad, alto - pad, ancho, alto),
        )
        claros = 0
        for caja in cajas:
            media = sum(ImageStat.Stat(rgb.crop(caja)).mean) / 3.0
            if media >= 188:
                claros += 1
        return claros >= 2
    except Exception:
        return False


def _nitidez_bordes(img):
    try:
        muestra = img.convert('L')
        if max(muestra.size) > 480:
            muestra.thumbnail((480, 480))
        bordes = muestra.filter(ImageFilter.FIND_EDGES)
        stat = ImageStat.Stat(bordes)
        return float(stat.mean[0]) if stat.mean else 0.0
    except Exception:
        return None


def _fallo(motivo, ancho=None, alto=None, nitidez=None):
    return {
        'ok': False,
        'motivo': motivo,
        'ancho': ancho,
        'alto': alto,
        'nitidez': nitidez,
    }


def registrar_rechazo(url, motivo):
    if not motivo:
        return
    print(f'{_LOG} rechazada {url}: {motivo}')

"""Extracción rápida de referencias de pago móvil con RapidOCR + ROI central."""

import re
import time

import cv2
import numpy as np

_ocr_engine = None
REFERENCIA_RE = re.compile(r'\d{6}')


def _obtener_motor_ocr():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr_engine = RapidOCR()
    return _ocr_engine


def recortar_roi_central(imagen_bgr, ratio=0.4):
    """Recorta el 40% central de la imagen para acelerar el OCR."""
    alto, ancho = imagen_bgr.shape[:2]
    alto_recorte = max(1, int(alto * ratio))
    ancho_recorte = max(1, int(ancho * ratio))
    y1 = (alto - alto_recorte) // 2
    x1 = (ancho - ancho_recorte) // 2
    return imagen_bgr[y1 : y1 + alto_recorte, x1 : x1 + ancho_recorte]


def extraer_referencia_desde_bytes(data_bytes):
    """
    Procesa un comprobante y retorna (referencia_6_digitos, ms_transcurridos).
    """
    inicio = time.perf_counter()

    if not data_bytes:
        return None, 0.0

    buffer = np.frombuffer(data_bytes, dtype=np.uint8)
    imagen = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if imagen is None:
        return None, (time.perf_counter() - inicio) * 1000

    roi = recortar_roi_central(imagen, ratio=0.4)
    motor = _obtener_motor_ocr()
    resultado, _ = motor(roi)
    transcurrido_ms = (time.perf_counter() - inicio) * 1000

    if not resultado:
        return None, transcurrido_ms

    texto = ''.join(str(fila[1]) for fila in resultado if len(fila) > 1)
    texto = re.sub(r'\D', '', texto)

    coincidencias = REFERENCIA_RE.findall(texto)
    if coincidencias:
        return coincidencias[0], transcurrido_ms

    return None, transcurrido_ms

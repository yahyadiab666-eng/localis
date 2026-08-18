"""Extracción y validación estricta de comprobantes de pago móvil con RapidOCR."""

import re
import time
import unicodedata

import cv2
import numpy as np

from config import PAGO_MOVIL_DEFAULT

_ocr_engine = None
REFERENCIA_RE = re.compile(r'\b(\d{6})\b')
MONTO_RE = re.compile(
    r'(\d{1,3}(?:[.\s]\d{3})*[.,]\d{2}|\d+[.,]\d{2})'
)

BANCO_OFICIAL = PAGO_MOVIL_DEFAULT['banco']
RIF_OFICIAL = re.sub(r'\D', '', PAGO_MOVIL_DEFAULT['cedula_rif'])
TELEFONO_OFICIAL = re.sub(r'\D', '', PAGO_MOVIL_DEFAULT['telefono'])


def _obtener_motor_ocr():
    global _ocr_engine
    if _ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        _ocr_engine = RapidOCR()
    return _ocr_engine


def recortar_roi_central(imagen_bgr, ratio=0.4):
    """Recorta el centro de la imagen para acelerar búsqueda de referencia."""
    alto, ancho = imagen_bgr.shape[:2]
    alto_recorte = max(1, int(alto * ratio))
    ancho_recorte = max(1, int(ancho * ratio))
    y1 = (alto - alto_recorte) // 2
    x1 = (ancho - ancho_recorte) // 2
    return imagen_bgr[y1 : y1 + alto_recorte, x1 : x1 + ancho_recorte]


def _normalizar_texto(texto):
    texto = unicodedata.normalize('NFKD', texto or '')
    texto = ''.join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', texto.lower())


def _ocr_texto(imagen_bgr):
    motor = _obtener_motor_ocr()
    resultado, _ = motor(imagen_bgr)
    if not resultado:
        return ''
    return ' '.join(str(fila[1]) for fila in resultado if len(fila) > 1)


def _parse_monto_ve(valor):
    limpio = re.sub(r'[^\d.,]', '', str(valor or ''))
    if not limpio:
        return None
    if ',' in limpio and '.' in limpio:
        if limpio.rfind(',') > limpio.rfind('.'):
            limpio = limpio.replace('.', '').replace(',', '.')
        else:
            limpio = limpio.replace(',', '')
    elif ',' in limpio:
        partes = limpio.split(',')
        if len(partes[-1]) == 2:
            limpio = ''.join(partes[:-1]).replace('.', '') + '.' + partes[-1]
        else:
            limpio = limpio.replace(',', '.')
    try:
        return round(float(limpio), 2)
    except ValueError:
        return None


def _extraer_referencia(texto):
    solo_digitos = re.sub(r'\D', '', texto or '')
    coincidencias = REFERENCIA_RE.findall(solo_digitos)
    if coincidencias:
        return coincidencias[0]
    coincidencias = REFERENCIA_RE.findall(texto or '')
    return coincidencias[0] if coincidencias else None


def _extraer_montos_candidatos(texto):
    candidatos = []
    for match in MONTO_RE.findall(texto or ''):
        monto = _parse_monto_ve(match)
        if monto is not None and monto > 0:
            candidatos.append(monto)
    return candidatos


def _elegir_monto(candidatos, monto_esperado):
    if not candidatos:
        return None
    return min(candidatos, key=lambda m: abs(m - monto_esperado))


def _contiene_banco(texto_norm):
    return 'caribe' in texto_norm


def _contiene_rif(texto):
    digitos = re.sub(r'\D', '', texto or '')
    return RIF_OFICIAL in digitos


def _contiene_telefono(texto):
    digitos = re.sub(r'\D', '', texto or '')
    return TELEFONO_OFICIAL in digitos or TELEFONO_OFICIAL.lstrip('0') in digitos


def _montos_coinciden(monto_detectado, monto_esperado, tolerancia_rel=0.02):
    if monto_detectado is None or monto_esperado is None:
        return False
    tolerancia = max(1.0, float(monto_esperado) * tolerancia_rel)
    return abs(float(monto_detectado) - float(monto_esperado)) <= tolerancia


def extraer_referencia_desde_bytes(data_bytes):
    """Compatibilidad: retorna (referencia_6_digitos, ms)."""
    resultado = validar_comprobante_pago_movil(data_bytes, monto_esperado_bs=0)
    return resultado.get('referencia'), resultado.get('ms', 0.0)


def validar_comprobante_pago_movil(data_bytes, monto_esperado_bs):
    """
    OCR estricto del comprobante.
    Retorna dict con: ok, referencia, monto_bs, errores, ms, texto_ocr.
    """
    inicio = time.perf_counter()
    errores = []

    if not data_bytes:
        return {
            'ok': False,
            'referencia': None,
            'monto_bs': None,
            'errores': ['Comprobante vacío o ilegible.'],
            'ms': 0.0,
            'texto_ocr': '',
        }

    buffer = np.frombuffer(data_bytes, dtype=np.uint8)
    imagen = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if imagen is None:
        return {
            'ok': False,
            'referencia': None,
            'monto_bs': None,
            'errores': ['No se pudo leer la imagen del comprobante.'],
            'ms': (time.perf_counter() - inicio) * 1000,
            'texto_ocr': '',
        }

    texto_completo = _ocr_texto(imagen)
    roi = recortar_roi_central(imagen, ratio=0.45)
    texto_roi = _ocr_texto(roi)
    texto_raw = f'{texto_completo} {texto_roi}'.strip()
    texto_norm = _normalizar_texto(texto_raw)

    referencia = _extraer_referencia(texto_roi) or _extraer_referencia(texto_raw)
    if not referencia:
        errores.append('No se detectó una referencia válida de 6 dígitos.')

    if not _contiene_banco(texto_norm):
        errores.append(f'El comprobante debe corresponder a {BANCO_OFICIAL}.')

    if not _contiene_rif(texto_raw):
        errores.append(f'El comprobante debe incluir el RIF/Cédula {PAGO_MOVIL_DEFAULT["cedula_rif"]}.')

    if not _contiene_telefono(texto_raw):
        errores.append(
            f'El comprobante debe incluir el teléfono {PAGO_MOVIL_DEFAULT["telefono"]}.'
        )

    candidatos = _extraer_montos_candidatos(texto_raw)
    monto_detectado = _elegir_monto(candidatos, float(monto_esperado_bs or 0))

    if monto_esperado_bs and monto_esperado_bs > 0:
        if monto_detectado is None:
            errores.append('No se pudo leer el monto en bolívares del comprobante.')
        elif not _montos_coinciden(monto_detectado, monto_esperado_bs):
            errores.append(
                f'El monto del comprobante ({monto_detectado:.2f} Bs) no coincide '
                f'con el esperado ({float(monto_esperado_bs):.2f} Bs).'
            )

    transcurrido_ms = (time.perf_counter() - inicio) * 1000
    ok = not errores and bool(referencia)

    return {
        'ok': ok,
        'referencia': referencia,
        'monto_bs': monto_detectado,
        'errores': errores,
        'ms': transcurrido_ms,
        'texto_ocr': texto_raw[:500],
    }

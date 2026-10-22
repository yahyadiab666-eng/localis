"""Extracción y validación estricta de comprobantes de pago móvil con RapidOCR."""

import os
import re
import time
import unicodedata
from datetime import date, datetime

import cv2
import numpy as np

from config import PAGO_MOVIL_DEFAULT

_ocr_engine = None
# Referencia etiquetada ("Referencia: 123456", "Ref 456.789", "Nro 45 6789").
# El grupo captura una tirada que empieza y termina en dígito, para poder unir
# referencias que el OCR separa con puntos o espacios (los recibos reales usan
# separadores de miles). Unir a ciegas sería peligroso: se valida después que
# queden exactamente 6 dígitos y como mucho un separador (ver _referencia_de_etiqueta).
REFERENCIA_ETIQUETADA_RE = re.compile(
    r'(?:referencia|refer|ref\.?|nro\.?|n[uú]mero|operaci[oó]n|comprobante)'
    r'[^\d]{0,15}(\d[\d\s.,-]*\d)',
    re.IGNORECASE,
)
# Token de 6 dígitos aislado: no forma parte de un número, fecha ni monto.
REFERENCIA_TOKEN_RE = re.compile(r'(?<![\d/.,-])(\d{6})(?![\d/.,-])')
# `_D` = dígito, tolerando la confusión O/0 típica del OCR.
_D = r'[0-9Oo]'
# Frontera tras un monto: ni otro dígito, ni una continuación de fecha
# (evita que "21.10.2026" produzca el falso monto 21.10).
_FIN_MONTO = r'(?![0-9Oo])(?![./-]\d)'
MONTO_RE = re.compile(
    rf'({_D}{{1,3}}(?:,{_D}{{3}})+\.{_D}{{2}}{_FIN_MONTO}'      # 1,234.56 (US)
    rf'|{_D}{{1,3}}(?:[.\s]{_D}{{3}})*[.,]{_D}{{2}}{_FIN_MONTO}'  # 1.234,56
    rf'|{_D}+[.,]{_D}{{2}}{_FIN_MONTO}'                          # 500,00
    rf'|{_D}{{1,3}}(?:[.\s]{_D}{{3}})+{_FIN_MONTO})'             # 2.000
)
# Monto con etiqueta, con o sin decimales ("Monto 500 Bs", "Total: 2.000",
# "Bs. 2.OOO,OO"). Anclado a una etiqueta, así que tolerar O/0 aquí es seguro.
MONTO_ETIQUETADO_RE = re.compile(
    r'(?:monto|total|importe|pagado|bs\.?|bol[ií]vares)\s*[:.]?\s*'
    rf'({_D}{{1,3}}(?:,{_D}{{3}})+\.{_D}{{1,2}}'
    rf'|{_D}{{1,3}}(?:[.\s]{_D}{{3}})+(?:[.,]{_D}{{1,2}})?'
    rf'|{_D}+(?:[.,]{_D}{{1,2}})?)'
    rf'{_FIN_MONTO}',
    re.IGNORECASE,
)
# Fechas típicas de comprobantes bancarios: DD/MM/YYYY, YYYY-MM-DD y DD/MM/YY.
FECHAS_RE = (
    re.compile(r'\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b'),
    re.compile(r'\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b'),
    re.compile(r'\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})\b'),
)
# Fechas con nombre de mes: "21 oct 2026", "21 de octubre de 2026",
# "Oct 21, 2026" (capturas de Binance en inglés).
FECHA_MES_DIA_RE = re.compile(
    r'\b(\d{1,2})\s*(?:de\s+)?([a-záéíóú]{3,10})\.?\s*(?:de\s+)?(\d{4})\b',
    re.IGNORECASE,
)
FECHA_MES_EN_RE = re.compile(
    r'\b([a-z]{3,10})\.?\s+(\d{1,2}),?\s+(\d{4})\b',
    re.IGNORECASE,
)
MESES = {
    'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'ago': 8, 'sep': 9, 'set': 9, 'oct': 10, 'nov': 11, 'dic': 12,
    'jan': 1, 'apr': 4, 'aug': 8, 'dec': 12,
}


def _env_bool(nombre, default=True):
    valor = os.environ.get(nombre)
    if valor is None:
        return default
    return valor.strip().lower() not in ('0', 'false', 'no', 'off', '')


def _env_int(nombre, default):
    try:
        return int(os.environ.get(nombre, default))
    except (TypeError, ValueError):
        return default


# Por defecto se EXIGE fecha y se permite hoy o ayer (ventana ~24-48 h).
EXIGIR_FECHA_COMPROBANTE = _env_bool('LOCALIS_COMPROBANTE_EXIGIR_FECHA', True)
MAX_DIAS_COMPROBANTE = max(0, _env_int('LOCALIS_COMPROBANTE_MAX_DIAS', 1))

# Límite de resolución antes de OCR: acota tiempo y memoria sin perder texto.
# Medido con el motor real y capturas de 900x1600 a 1440x3120:
#   2200 px -> hasta 5.671 ms   1800 px -> hasta 3.495 ms (-38 %)
#   1400 px -> empieza a fallar (no detecta en 2 de 4 capturas)
# Por eso 1800 es el punto óptimo: mismo acierto, bastante menos tiempo.
MAX_OCR_DIM = max(600, _env_int('LOCALIS_OCR_MAX_DIM', 1800))


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


def _redimensionar_para_ocr(imagen_bgr, max_dim=None):
    """Limita el lado mayor de la imagen para acotar el coste del OCR."""
    limite = max(600, int(max_dim or MAX_OCR_DIM))
    alto, ancho = imagen_bgr.shape[:2]
    mayor = max(alto, ancho)
    if mayor <= limite:
        return imagen_bgr
    escala = limite / float(mayor)
    return cv2.resize(
        imagen_bgr,
        (max(1, int(ancho * escala)), max(1, int(alto * escala))),
        interpolation=cv2.INTER_AREA,
    )


def calentar_motor_ocr():
    """Carga el modelo OCR en memoria (warm-up de arranque). Nunca lanza."""
    try:
        _obtener_motor_ocr()
        return True
    except Exception as error:
        print(
            f'[Localis OCR] no se pudo precargar el motor: '
            f'{type(error).__name__}: {error}',
            flush=True,
        )
        return False


def _registrar_metricas_ocr(metodo, resultado, duracion_ms, estado_monto=None):
    """Observabilidad best-effort: nunca debe romper una validación."""
    try:
        from backend import metricas

        metricas.registrar_ocr(metodo, resultado, duracion_ms, estado_monto)
    except Exception:
        pass


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
    """Convierte a número un monto escrito en formato venezolano o US.

    Tolera las confusiones típicas del OCR (O por 0, l/I por 1) y resuelve la
    ambigüedad de ``2.000``: en un recibo venezolano son *dos mil*, no dos.
    """
    limpio = str(valor or '')
    # OCR: O/o -> 0 y l/I -> 1 (solo dentro de la cifra que llega aquí).
    limpio = (
        limpio.replace('O', '0')
        .replace('o', '0')
        .replace('l', '1')
        .replace('I', '1')
    )
    limpio = re.sub(r'[^\d.,]', '', limpio)
    if not limpio:
        return None
    # "2.000" / "1.250.000": punto como separador de miles (sin decimales).
    if re.fullmatch(r'\d{1,3}(?:\.\d{3})+', limpio):
        try:
            return round(float(limpio.replace('.', '')), 2)
        except ValueError:
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


def _referencia_de_etiqueta(token):
    """Referencia a partir del texto tras la etiqueta, o ``None``.

    Une separadores ("456.789" y "45 6789" -> "456789") pero **solo** si quedan
    exactamente 6 dígitos y hay como mucho un separador: así no se mezclan dos
    campos distintos (p. ej. "456.789 21/10/2026").
    """
    if not token:
        return None
    limpio = token.strip().strip('.,- ').strip()
    digitos = re.sub(r'\D', '', limpio)
    if len(digitos) != 6:
        return None
    if len(limpio) - 6 > 1:
        return None
    return digitos


def _extraer_referencia(texto):
    """Referencia de 6 dígitos, priorizando la que va junto a una etiqueta.

    Evita confundir montos ("500000,00"), fechas y teléfonos con la referencia.
    """
    if not texto:
        return None
    for match in REFERENCIA_ETIQUETADA_RE.finditer(texto):
        referencia = _referencia_de_etiqueta(match.group(1))
        if referencia:
            return referencia
    for match in REFERENCIA_TOKEN_RE.finditer(texto):
        return match.group(1)
    return None


def _extraer_montos_candidatos(texto):
    candidatos = []
    for match in MONTO_RE.findall(texto or ''):
        monto = _parse_monto_ve(match)
        if monto is not None and monto > 0:
            candidatos.append(monto)
    # Respaldo: montos sin decimales junto a una etiqueta ("Monto 500 Bs").
    for match in MONTO_ETIQUETADO_RE.findall(texto or ''):
        monto = _parse_monto_ve(match)
        if monto is not None and monto > 0:
            candidatos.append(monto)
    # Deduplica sin perder el orden (el OCR puede repetir el mismo monto).
    vistos = set()
    unicos = []
    for monto in candidatos:
        if monto not in vistos:
            vistos.add(monto)
            unicos.append(monto)
    return unicos


def _elegir_monto(candidatos, monto_esperado):
    if not candidatos:
        return None
    return min(candidatos, key=lambda m: abs(m - monto_esperado))


def _parse_fecha(anio, mes, dia):
    try:
        return date(int(anio), int(mes), int(dia))
    except (TypeError, ValueError):
        return None


def _mes_numero(nombre):
    limpio = _normalizar_texto(nombre).replace('.', '').strip()
    return MESES.get(limpio[:3])


def _extraer_fechas(texto):
    """Extrae fechas válidas del texto OCR (numéricas o con nombre de mes)."""
    fechas = []
    for patron in FECHAS_RE:
        for a, b, c in patron.findall(texto or ''):
            if len(a) == 4:  # YYYY-MM-DD
                fecha = _parse_fecha(a, b, c)
            else:  # DD/MM/YYYY o DD/MM/YY
                anio = c
                if len(anio) == 2:
                    anio = ('20' if int(anio) < 70 else '19') + anio
                fecha = _parse_fecha(anio, b, a)
            if fecha:
                fechas.append(fecha)

    # "21 oct 2026", "21 de octubre de 2026" y "Oct 21, 2026".
    for dia, mes, anio in FECHA_MES_DIA_RE.findall(texto or ''):
        numero = _mes_numero(mes)
        if numero:
            fecha = _parse_fecha(anio, numero, dia)
            if fecha:
                fechas.append(fecha)
    for mes, dia, anio in FECHA_MES_EN_RE.findall(texto or ''):
        numero = _mes_numero(mes)
        if numero:
            fecha = _parse_fecha(anio, numero, dia)
            if fecha:
                fechas.append(fecha)

    vistas = set()
    unicas = []
    for fecha in fechas:
        if fecha not in vistas:
            vistas.add(fecha)
            unicas.append(fecha)
    return unicas


def _elegir_fecha(fechas, hoy):
    """Fecha más cercana a hoy y su diferencia en días (hoy - fecha)."""
    if not fechas:
        return None, None
    mejor = min(fechas, key=lambda f: abs((hoy - f).days))
    return mejor, (hoy - mejor).days


def _normalizar_digitos(valor):
    return re.sub(r'\D', '', str(valor or ''))


def _contiene_banco(texto_norm, banco=None):
    nombre = _normalizar_texto(banco or BANCO_OFICIAL)
    if not nombre:
        return False
    if nombre in texto_norm:
        return True
    # "Banco Caribe" -> basta con "caribe" (el OCR puede omitir "Banco").
    palabras = [p for p in nombre.split() if len(p) > 3 and p != 'banco']
    return any(p in texto_norm for p in palabras)


def _contiene_rif(texto, rif=None):
    esperado = _normalizar_digitos(rif or RIF_OFICIAL)
    return bool(esperado) and esperado in _normalizar_digitos(texto)


def _contiene_telefono(texto, telefono=None):
    esperado = _normalizar_digitos(telefono or TELEFONO_OFICIAL)
    if not esperado:
        return False
    digitos = _normalizar_digitos(texto)
    variantes = {
        esperado,
        esperado.lstrip('0'),
        esperado if esperado.startswith('0') else '0' + esperado,
    }
    return any(variante and variante in digitos for variante in variantes)


MONTO_OK = 'ok'
MONTO_INCOMPLETO = 'incompleto'
MONTO_EXCEDIDO = 'excedido'
MONTO_NO_DETECTADO = 'no_detectado'

# Tolerancia (absoluta, relativa) por moneda. En USD (cripto) el importe es
# exacto: se permiten centavos por redondeo, nunca un dólar. En bolívares se
# tolera el redondeo de la tasa (2 %).
TOLERANCIA_MONTO = {
    'USD': (0.02, 0.01),
    'BS': (1.0, 0.02),
}


def tolerancia_monto(monto_esperado, moneda='BS'):
    """Diferencia máxima aceptada para un monto esperado en esa moneda."""
    absoluta, relativa = TOLERANCIA_MONTO.get(moneda, TOLERANCIA_MONTO['BS'])
    return max(absoluta, abs(float(monto_esperado or 0)) * relativa)


def clasificar_monto(monto_detectado, monto_esperado, moneda='BS'):
    """Clasifica el monto: ``ok``, ``incompleto``, ``excedido`` o ``no_detectado``.

    Retorna ``(estado, diferencia)``; la diferencia es detectado - esperado.
    """
    if monto_detectado is None or monto_esperado is None:
        return MONTO_NO_DETECTADO, None
    if float(monto_esperado) <= 0:
        return MONTO_NO_DETECTADO, None
    diferencia = round(float(monto_detectado) - float(monto_esperado), 2)
    if abs(diferencia) <= tolerancia_monto(monto_esperado, moneda):
        return MONTO_OK, diferencia
    if diferencia < 0:
        return MONTO_INCOMPLETO, diferencia
    return MONTO_EXCEDIDO, diferencia


def _mensaje_monto(estado, detectado, esperado, diferencia, unidad):
    """Mensaje accionable y específico para subpago o sobrepago."""
    if estado == MONTO_INCOMPLETO:
        return (
            f'El monto del comprobante ({detectado:.2f} {unidad}) es menor al '
            f'esperado ({esperado:.2f} {unidad}); faltan '
            f'{abs(diferencia):.2f} {unidad}. Completa el pago o contacta a '
            'soporte técnico para recibir ayuda.'
        )
    return (
        f'El monto del comprobante ({detectado:.2f} {unidad}) supera al esperado '
        f'({esperado:.2f} {unidad}) por {diferencia:.2f} {unidad}. Verifica la '
        'cantidad o contacta a soporte técnico para recibir ayuda.'
    )


def extraer_referencia_desde_bytes(data_bytes):
    """Compatibilidad: retorna (referencia_6_digitos, ms)."""
    resultado = validar_comprobante_pago_movil(data_bytes, monto_esperado_bs=0)
    return resultado.get('referencia'), resultado.get('ms', 0.0)


def _texto_pago_movil_incompleto(texto, banco=None, rif=None, telefono=None):
    """True si con este texto el comprobante sería rechazado.

    Se usa para decidir si merece la pena una segunda pasada de OCR sobre el
    recorte central: solo se paga ese coste cuando el resultado iba a fallar.
    """
    if not texto:
        return True
    if not _extraer_referencia(texto):
        return True
    if not _contiene_banco(_normalizar_texto(texto), banco):
        return True
    if not _contiene_rif(texto, rif):
        return True
    if not _contiene_telefono(texto, telefono):
        return True
    if not _extraer_montos_candidatos(texto):
        return True
    if not _extraer_fechas(texto):
        return True
    return False


def evaluar_texto_pago_movil(
    texto_raw,
    monto_esperado_bs,
    ahora=None,
    exigir_fecha=None,
    max_dias=None,
    banco=None,
    rif=None,
    telefono=None,
):
    """Evalúa el TEXTO ya extraído por OCR de un comprobante de pago móvil.

    Separado de :func:`validar_comprobante_pago_movil` para poder probar y
    afinar la lógica de lectura sin depender del motor OCR (rápido y
    determinista). El contrato es el mismo, salvo ``ms``, que lo añade quien
    llama. Nunca lanza.
    """
    errores = []
    texto_norm = _normalizar_texto(texto_raw)

    referencia = _extraer_referencia(texto_raw)
    if not referencia:
        errores.append(
            'No se detectó una referencia válida de 6 dígitos. Escríbela a mano '
            'o contacta a soporte técnico.'
        )

    if not _contiene_banco(texto_norm, banco):
        errores.append(
            f'El comprobante debe corresponder a {banco or BANCO_OFICIAL}.'
        )

    if not _contiene_rif(texto_raw, rif):
        errores.append(
            'El comprobante debe incluir el RIF/Cédula '
            f'{rif or PAGO_MOVIL_DEFAULT["cedula_rif"]}.'
        )

    if not _contiene_telefono(texto_raw, telefono):
        errores.append(
            'El comprobante debe incluir el teléfono '
            f'{telefono or PAGO_MOVIL_DEFAULT["telefono"]}.'
        )

    candidatos = _extraer_montos_candidatos(texto_raw)
    monto_detectado = _elegir_monto(candidatos, float(monto_esperado_bs or 0))

    estado_monto = None
    diferencia_monto = None
    if monto_esperado_bs and monto_esperado_bs > 0:
        estado_monto, diferencia_monto = clasificar_monto(
            monto_detectado, monto_esperado_bs, 'BS'
        )
        if estado_monto == MONTO_NO_DETECTADO:
            errores.append('No se pudo leer el monto en bolívares del comprobante.')
        elif estado_monto != MONTO_OK:
            errores.append(
                _mensaje_monto(
                    estado_monto,
                    monto_detectado,
                    float(monto_esperado_bs),
                    diferencia_monto,
                    'Bs',
                )
            )

    # Antifraude: la fecha del comprobante debe ser reciente (hoy/ayer por defecto).
    exigir_fecha = (
        EXIGIR_FECHA_COMPROBANTE if exigir_fecha is None else bool(exigir_fecha)
    )
    max_dias = MAX_DIAS_COMPROBANTE if max_dias is None else max(0, int(max_dias))
    hoy = ahora or datetime.now().date()
    fecha_comprobante, delta_dias = _elegir_fecha(_extraer_fechas(texto_raw), hoy)

    if exigir_fecha:
        if fecha_comprobante is None:
            errores.append(
                'No se detectó la fecha del comprobante (se espera DD/MM/AAAA).'
            )
        elif not (-1 <= delta_dias <= max_dias):
            errores.append(
                f'La fecha del comprobante '
                f'({fecha_comprobante.strftime("%d/%m/%Y")}) está fuera del rango '
                f'permitido (máximo {max_dias} día(s) de antigüedad).'
            )

    ok = not errores and bool(referencia)
    return {
        'ok': ok,
        'referencia': referencia,
        'monto_bs': monto_detectado,
        'fecha_comprobante': (
            fecha_comprobante.strftime('%Y-%m-%d') if fecha_comprobante else None
        ),
        'errores': errores,
        'texto_ocr': (texto_raw or '')[:500],
        'estado_monto': estado_monto,
        'diferencia_monto': diferencia_monto,
    }


def validar_comprobante_pago_movil(
    data_bytes,
    monto_esperado_bs,
    ahora=None,
    exigir_fecha=None,
    max_dias=None,
    banco=None,
    rif=None,
    telefono=None,
):
    """
    OCR estricto del comprobante.

    ``ahora``: fecha de referencia (``datetime.date``) para validar la fecha
    del comprobante; por defecto hoy.
    ``exigir_fecha``: si es ``True`` y no hay fecha válida, se rechaza. Por
    defecto toma ``LOCALIS_COMPROBANTE_EXIGIR_FECHA`` (1).
    ``max_dias``: antigüedad máxima permitida en días. Por defecto
    ``LOCALIS_COMPROBANTE_MAX_DIAS`` (1: hoy o ayer).
    ``banco``/``rif``/``telefono``: datos destino reales (configuración); si no
    se pasan, se usan los valores por defecto del sistema.

    Retorna dict con: ok, referencia, monto_bs, fecha_comprobante, errores,
    ms, texto_ocr, estado_monto y diferencia_monto.
    """
    inicio = time.perf_counter()

    if not data_bytes:
        return {
            'ok': False,
            'referencia': None,
            'monto_bs': None,
            'fecha_comprobante': None,
            'errores': ['Comprobante vacío o ilegible.'],
            'ms': 0.0,
            'texto_ocr': '',
            'estado_monto': None,
            'diferencia_monto': None,
        }

    buffer = np.frombuffer(data_bytes, dtype=np.uint8)
    imagen = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if imagen is None:
        ms = (time.perf_counter() - inicio) * 1000
        _registrar_metricas_ocr('pago_movil', 'invalido', ms)
        return {
            'ok': False,
            'referencia': None,
            'monto_bs': None,
            'fecha_comprobante': None,
            'errores': ['No se pudo leer la imagen del comprobante.'],
            'ms': ms,
            'texto_ocr': '',
            'estado_monto': None,
            'diferencia_monto': None,
        }

    imagen = _redimensionar_para_ocr(imagen)
    texto_completo = _ocr_texto(imagen)
    texto_raw = texto_completo
    # Segunda pasada por el recorte central SOLO si con la imagen completa falta
    # algún dato clave. Así, un comprobante que ya se lee bien no paga un OCR
    # extra, y uno que iba a ser rechazado tiene una segunda oportunidad con el
    # texto más grande. Antes la segunda pasada solo miraba la referencia, de
    # modo que un comprobante con la referencia visible pero el teléfono o el
    # monto ilegibles se rechazaba sin intentarlo.
    if _texto_pago_movil_incompleto(texto_completo, banco, rif, telefono):
        roi = recortar_roi_central(imagen, ratio=0.45)
        texto_roi = _ocr_texto(roi)
        if texto_roi:
            texto_raw = f'{texto_completo} {texto_roi}'.strip()

    resultado = evaluar_texto_pago_movil(
        texto_raw,
        monto_esperado_bs,
        ahora=ahora,
        exigir_fecha=exigir_fecha,
        max_dias=max_dias,
        banco=banco,
        rif=rif,
        telefono=telefono,
    )
    resultado['ms'] = (time.perf_counter() - inicio) * 1000
    _registrar_metricas_ocr(
        'pago_movil',
        'ok' if resultado.get('ok') else 'rechazado',
        resultado['ms'],
        resultado.get('estado_monto'),
    )
    return resultado


# ==========================================================================
# Binance Pay (2.ª forma de pago)
# ==========================================================================

ORDER_ID_RE = re.compile(r'\b(\d{12,24})\b')
MONTO_USDT_RE = re.compile(r'([\d.,]+)\s*usdt', re.IGNORECASE)


def _extraer_order_id(texto):
    """ID de la orden de Binance (token de 12-24 dígitos), o None."""
    coincidencias = ORDER_ID_RE.findall(texto or '')
    return max(coincidencias, key=len) if coincidencias else None


def evaluar_texto_binance(
    texto_raw,
    monto_usd,
    ahora=None,
    exigir_fecha=None,
    max_dias=None,
):
    """Evalúa el texto OCR de una captura de Binance Pay. Nunca lanza.

    La captura de Binance trae **pocos datos** (monto en USDT, fecha e ID de la
    orden; NO muestra el destinatario), así que el OCR pide: un pago en USDT, el
    **monto ≈ valor del plan en USD**, la **fecha reciente** y un **ID de orden**
    como referencia. El admin verifica ese ID en su Binance antes de aprobar.
    """
    errores = []
    texto_norm = _normalizar_texto(texto_raw)

    if 'usdt' not in texto_norm and 'binance' not in texto_norm:
        errores.append('El comprobante debe ser un pago por Binance (USDT).')

    referencia = _extraer_order_id(texto_raw)
    if not referencia:
        errores.append('No se detectó el ID de la orden de Binance.')

    candidatos = _extraer_montos_candidatos(texto_raw)
    for bruto in MONTO_USDT_RE.findall(texto_raw or ''):
        monto = _parse_monto_ve(bruto)
        if monto is not None and monto > 0:
            candidatos.insert(0, monto)
    monto_detectado = _elegir_monto(candidatos, float(monto_usd or 0))
    estado_monto = None
    diferencia_monto = None
    if monto_usd and float(monto_usd) > 0:
        estado_monto, diferencia_monto = clasificar_monto(
            monto_detectado, float(monto_usd), 'USD'
        )
        if estado_monto == MONTO_NO_DETECTADO:
            errores.append('No se pudo leer el monto en USDT de la captura.')
        elif estado_monto != MONTO_OK:
            errores.append(
                _mensaje_monto(
                    estado_monto,
                    monto_detectado,
                    float(monto_usd),
                    diferencia_monto,
                    'USDT',
                )
            )

    exigir_fecha = (
        EXIGIR_FECHA_COMPROBANTE if exigir_fecha is None else bool(exigir_fecha)
    )
    max_dias = MAX_DIAS_COMPROBANTE if max_dias is None else max(0, int(max_dias))
    hoy = ahora or datetime.now().date()
    fecha_comprobante, delta_dias = _elegir_fecha(_extraer_fechas(texto_raw), hoy)
    if exigir_fecha:
        if fecha_comprobante is None:
            errores.append('No se detectó la fecha de la captura (DD/MM/AAAA).')
        elif not (-1 <= delta_dias <= max_dias):
            errores.append(
                f'La fecha del comprobante '
                f'({fecha_comprobante.strftime("%d/%m/%Y")}) está fuera del rango '
                f'permitido (máximo {max_dias} día(s) de antigüedad).'
            )

    ok = not errores and bool(referencia)
    return {
        'ok': ok,
        'referencia': referencia,
        'monto_usd': monto_detectado,
        'fecha_comprobante': (
            fecha_comprobante.strftime('%Y-%m-%d') if fecha_comprobante else None
        ),
        'errores': errores,
        'texto_ocr': (texto_raw or '')[:500],
        'estado_monto': estado_monto,
        'diferencia_monto': diferencia_monto,
    }


def _texto_binance_incompleto(texto, monto_usd=None):
    """True si falta algún dato clave de la captura de Binance.

    Decide si merece la pena una segunda pasada de OCR sobre el recorte central.
    """
    if not texto:
        return True
    if not _extraer_order_id(texto):
        return True
    if not _extraer_fechas(texto):
        return True
    if monto_usd and float(monto_usd) > 0:
        # Mismo criterio que ``evaluar_texto_binance``: la captura de Binance
        # escribe el monto como "20 USDT", que no lleva decimales.
        if not (_extraer_montos_candidatos(texto) or MONTO_USDT_RE.findall(texto)):
            return True
    return False


def validar_comprobante_binance(
    data_bytes,
    monto_usd,
    ahora=None,
    exigir_fecha=None,
    max_dias=None,
):
    """OCR de una captura de Binance Pay. Mismo contrato que la de pago móvil."""
    inicio = time.perf_counter()
    base = {
        'ok': False,
        'referencia': None,
        'monto_usd': None,
        'fecha_comprobante': None,
        'errores': ['Comprobante vacío o ilegible.'],
        'ms': 0.0,
        'texto_ocr': '',
        'estado_monto': None,
        'diferencia_monto': None,
    }
    if not data_bytes:
        return base

    buffer = np.frombuffer(data_bytes, dtype=np.uint8)
    imagen = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if imagen is None:
        base['errores'] = ['No se pudo leer la imagen de la captura.']
        base['ms'] = (time.perf_counter() - inicio) * 1000
        _registrar_metricas_ocr('binance', 'invalido', base['ms'])
        return base

    imagen = _redimensionar_para_ocr(imagen)
    texto_completo = _ocr_texto(imagen)
    texto_raw = texto_completo
    # Segunda pasada solo si con la imagen completa falta algo clave (ID de la
    # orden, fecha o monto): antes se miraba solo el ID, así que una captura con
    # el ID visible pero el monto ilegible se rechazaba sin intentarlo.
    if _texto_binance_incompleto(texto_completo, monto_usd):
        roi = recortar_roi_central(imagen, ratio=0.45)
        texto_roi = _ocr_texto(roi)
        if texto_roi:
            texto_raw = f'{texto_completo} {texto_roi}'.strip()

    resultado = evaluar_texto_binance(
        texto_raw,
        monto_usd,
        ahora=ahora,
        exigir_fecha=exigir_fecha,
        max_dias=max_dias,
    )
    resultado['ms'] = (time.perf_counter() - inicio) * 1000
    _registrar_metricas_ocr(
        'binance',
        'ok' if resultado.get('ok') else 'rechazado',
        resultado['ms'],
        resultado.get('estado_monto'),
    )
    return resultado

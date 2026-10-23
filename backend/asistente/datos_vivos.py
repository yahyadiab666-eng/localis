"""Datos en vivo (solo lectura) para el asistente.

El asistente sigue siendo INFORMATIVO (no escribe nada), pero ahora puede
CONSULTAR datos reales para responder con precision: precios del catalogo
publico y datos del PROPIO comercio (plan, limites, productos sin foto).

Seguridad: los datos del comercio se piden con el ``usuario_id`` y el
``comercio_id`` de la sesion (nunca con datos del usuario), y
``obtener_comercio_por_usuario`` valida la propiedad. Todo es defensivo: si la
BD falla, devuelve cadena vacia y el asistente responde igual (sin datos).

Las funciones puras (``_keywords``, ``formatear_productos``, ``menciona_comercio``)
se prueban sin red ni BD.
"""

from __future__ import annotations

import logging
import re
import unicodedata

_log = logging.getLogger('localis.asistente.datos')

_STOPWORDS = frozenset(
    'de la el los las un una unos unas y o u que en con por para sin sobre entre '
    'al del lo se su sus mi mis tu te me le les nos es son esta estan este esto '
    'como cuando donde cual cuales mas menos muy no ni si ya pero tambien solo '
    'cada todo toda todos todas hay ser estoy hacer tiene tienen puedo puede '
    'pueden yo cuanto cuanta cuantos cuantas cuesta cuestan vale uno precio '
    'precios costo cuesta sale cuanto donde'.split()
)

# Palabras que sugieren una consulta sobre la cuenta del comercio.
_CLAVES_COMERCIO = (
    'plan', 'planes', 'suscrip', 'limite', 'limites', 'producto', 'productos',
    'importa', 'importacion', 'inventario', 'foto', 'fotos', 'imagen', 'imagenes',
    'venc', 'vencimiento', 'premio', 'puntos', 'destac', 'boost', 'mi cuenta',
    'mi tienda', 'mi comercio',
)

_CLAVES_IMAGENES = ('foto', 'fotos', 'imagen', 'imagenes')

# Hechos canonicos que se inyectan SIEMPRE: son pequenos y evitan que el modelo
# invente precios de planes, destacados o puntos cuando el RAG no los recupera.
HECHOS_CLAVE = (
    'DATOS CLAVE DE LOCALIS (cifras oficiales; usalas tal cual):\n'
    '- Planes (renovacion cada 30 dias): Gratis/Prueba 0 USD (hasta 50 '
    'productos); Basico 10 USD/mes (hasta 100); Pro 15 USD/mes (hasta 300); '
    'Business 35 USD/mes (productos ilimitados).\n'
    '- Pago de planes: Pago Movil o Binance Pay (USDT). Se sube una captura del '
    'comprobante; se verifica con OCR o pasa a revision manual.\n'
    '- Destacados: 3 dias 2 USD; 5 dias 3 USD; 7 dias 4 USD.\n'
    '- Puntos: 1 punto por comercio referido; canjes: 3 puntos = 3 dias, '
    '4 puntos = 5 dias, 5 puntos = 7 dias de destacado.'
)

# Si el mensaje trata de estos temas, NO se busca en el catalogo (lo cubre la
# documentacion) para no inyectar productos irrelevantes.
_CLAVES_NO_CATALOGO = (
    'plan', 'planes', 'suscrip', 'premio', 'puntos', 'destac', 'boost',
    'importa', 'inventario', 'foto', 'imagen', 'sede', 'ubicacion', 'pago',
)


def _normalizar(texto):
    if not isinstance(texto, str):
        return ''
    base = unicodedata.normalize('NFKD', texto)
    base = ''.join(c for c in base if not unicodedata.combining(c))
    return base.lower()


def _keywords(mensaje, maximo=6):
    """Palabras con contenido del mensaje (sin acentos ni stopwords)."""
    tokens = re.findall(r'[a-z0-9]{3,}', _normalizar(mensaje))
    vistas = []
    for token in tokens:
        if token in _STOPWORDS or token in vistas:
            continue
        vistas.append(token)
    return vistas[:maximo]


def menciona_comercio(mensaje):
    """True si el mensaje parece pedir datos del propio comercio."""
    bajo = _normalizar(mensaje)
    return any(clave in bajo for clave in _CLAVES_COMERCIO)


def menciona_imagenes(mensaje):
    bajo = _normalizar(mensaje)
    return any(clave in bajo for clave in _CLAVES_IMAGENES)


def formatear_productos(productos, maximo=6):
    """Lista compacta de productos con precio USD/Bs y tienda."""
    lineas = []
    for producto in (productos or [])[:maximo]:
        nombre = str(producto.get('nombre') or '').strip()
        if not nombre:
            continue
        try:
            usd = float(producto.get('precio_usd') or 0)
        except (TypeError, ValueError):
            usd = 0.0
        try:
            bs = float(producto.get('precio_bs') or 0)
        except (TypeError, ValueError):
            bs = 0.0
        tienda = str(producto.get('comercio_nombre') or '').strip()
        precio = f'{usd:.2f} USD' + (f' (Bs {bs:,.2f})' if bs else '')
        sufijo = f' - en {tienda}' if tienda else ''
        lineas.append(f'- {nombre}: {precio}{sufijo}')
    return '\n'.join(lineas)


def buscar_precios(mensaje, maximo=6):
    """Productos del catalogo que mejor encajan con el mensaje (o '').

    No busca si el mensaje es sobre planes, premios, importacion o imagenes
    (eso lo cubre la documentacion) ni con palabras muy cortas, para no meter
    ruido en la respuesta.
    """
    bajo = _normalizar(mensaje)
    if any(clave in bajo for clave in _CLAVES_NO_CATALOGO):
        return ''
    claves = [clave for clave in _keywords(mensaje) if len(clave) >= 4]
    if not claves:
        return ''
    try:
        from backend.stores import buscar_productos_catalogo
    except Exception:
        return ''
    intentos = [' '.join(claves[:3]), ' '.join(claves[:2])] + claves[:3]
    for consulta in intentos:
        consulta = consulta.strip()
        if not consulta:
            continue
        try:
            productos = buscar_productos_catalogo(palabra_clave=consulta) or []
        except Exception as error:
            _log.warning('busqueda de catalogo fallo: %s', error)
            return ''
        if productos:
            return formatear_productos(productos, maximo=maximo)
    return ''


def tasa_del_dia():
    """Tasa BCV vigente (o None). Nunca lanza."""
    try:
        from backend.stores import obtener_tasa_dolar

        valor = obtener_tasa_dolar()
        return float(valor) if valor else None
    except Exception:
        return None


def _contar_sin_foto(comercio_id):
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                "SELECT COUNT(*) FROM productos WHERE comercio_id = ? "
                "AND COALESCE(imagen_estado, '') <> 'real'",
                (int(comercio_id),),
            )
            fila = cursor.fetchone()
            return int(fila[0]) if fila and fila[0] is not None else 0
    except Exception as error:
        _log.warning('conteo de productos sin foto fallo: %s', error)
        return None


def resumen_comercio(usuario_id, comercio_id, incluir_imagenes=False):
    """Plan, limite, uso y vencimiento del comercio del usuario (o '')."""
    if not usuario_id:
        return ''
    try:
        from backend.plans import es_limite_ilimitado, obtener_plan_por_codigo
        from backend.stores import obtener_comercio_por_usuario
        from backend.subscriptions import (
            contar_productos_comercio,
            obtener_limite_productos_comercio,
        )

        comercio = obtener_comercio_por_usuario(usuario_id, comercio_id)
        if not comercio:
            return ''
        plan_codigo = (comercio.get('plan_tipo') or 'gratis').lower()
        plan = obtener_plan_por_codigo(plan_codigo) or {}
        nombre_plan = plan.get('nombre') or plan_codigo
        limite = obtener_limite_productos_comercio(comercio.get('id'))
        limite_txt = (
            'ilimitado' if es_limite_ilimitado(limite) else str(limite)
        )
        usados = contar_productos_comercio(comercio.get('id'))

        lineas = [
            f'Plan actual: {nombre_plan}',
            f'Productos publicados: {usados} de {limite_txt}',
            f'Estado de pago: {comercio.get("estado_pago") or "desconocido"}',
        ]
        if comercio.get('fecha_vencimiento'):
            lineas.append(f'Vence: {comercio.get("fecha_vencimiento")}')
        if incluir_imagenes:
            sin_foto = _contar_sin_foto(comercio.get('id'))
            if sin_foto is not None:
                lineas.append(f'Productos sin foto real: {sin_foto}')
        return '\n'.join(lineas)
    except Exception as error:
        _log.warning('resumen del comercio fallo: %s', error)
        return ''

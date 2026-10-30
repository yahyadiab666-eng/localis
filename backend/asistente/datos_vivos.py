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

from config import fase_gratuita

_log = logging.getLogger('localis.asistente.datos')

_STOPWORDS = frozenset(
    'de la el los las un una unos unas y o u que en con por para sin sobre entre '
    'al del lo se su sus mi mis tu te me le les nos es son esta estan este esto '
    'como cuando donde cual cuales mas menos muy no ni si ya pero tambien solo '
    'cada todo toda todos todas hay ser estoy hacer tiene tienen puedo puede '
    'pueden yo cuanto cuanta cuantos cuantas cuesta cuestan vale uno precio '
    'precios costo cuesta sale cuanto donde hola buenas buenos dias tardes '
    'noches gracias ayuda favor saludo'.split()
)

# Palabras que sugieren una consulta sobre la cuenta del comercio.
_CLAVES_COMERCIO = (
    'plan', 'planes', 'suscrip', 'limite', 'limites', 'producto', 'productos',
    'importa', 'importacion', 'inventario', 'foto', 'fotos', 'imagen', 'imagenes',
    'venc', 'vencimiento', 'premio', 'puntos', 'destac', 'boost', 'mi cuenta',
    'mi tienda', 'mi comercio',
)

_CLAVES_IMAGENES = ('foto', 'fotos', 'imagen', 'imagenes')
_CLAVES_IMPORTACION = ('importa', 'importacion', 'inventario', 'csv', 'excel', 'archivo')
_CLAVES_PAGO = ('pago', 'pagar', 'comprobante', 'verific', 'revision', 'transferencia', 'binance')
_CLAVES_TIENDA = ('tienda', 'comercio', 'sucursal', 'vende', 'venden', 'donde queda', 'ubicacion', 'local')
_CLAVES_CATEGORIA = ('categoria', 'categorias')

# --- Panel de administración (solo lectura) ---
# La bandeja del panel mezcla dos cosas: SOLICITUDES de los usuarios (soporte y
# reportes) y ALERTAS de revisión de pago. Las preguntas del admin suelen ir
# sobre "cuántos hay pendientes", así que el asistente lee los conteos reales.
_CLAVES_BANDEJA = (
    'bandeja', 'ticket', 'tickets', 'solicitud', 'solicitudes', 'reporte',
    'reportes', 'revision', 'revisiones', 'pendiente', 'pendientes', 'abiert',
    'acept', 'rechaz', 'atender', 'atencion', 'soporte', 'cola',
)
_CLAVES_OPERACION = (
    'comercio', 'comercios', 'tienda', 'tiendas', 'usuario', 'usuarios',
    'producto', 'productos', 'catalogo', 'metricas', 'operacion', 'resumen',
    'cuantos', 'cuantas', 'salud', 'visibles', 'suspendid',
)
_CLAVES_SALUD_PAGOS = (
    'salud', 'pagos', 'confirmad', 'verificad', 'binance', 'comprobante',
)

# Hechos canonicos que se inyectan SIEMPRE: son pequenos y evitan que el modelo
# invente precios de planes, destacados o puntos cuando el RAG no los recupera.
# Este texto es el RESPALDO (sin BD). En cada peticion se prefiere ``hechos_clave()``,
# que deriva las cifras de su fuente real (planes, boosts y referidos) para que el
# asistente nunca diga un precio viejo.
HECHOS_CLAVE = (
    'DATOS CLAVE DE LOCALIS (cifras oficiales; usalas tal cual):\n'
    '- Planes (renovacion cada 30 dias): Gratis/Prueba 0 USD (hasta 50 '
    'productos); Basico 10 USD/mes (hasta 100); Pro 15 USD/mes (hasta 300); '
    'Business 35 USD/mes (productos ilimitados).\n'
    '- Pago de planes: Pago Movil o Binance Pay (USDT). Se sube una captura del '
    'comprobante; se verifica con OCR o pasa a revision manual.\n'
    '- Destacados: 3 dias 2 USD; 5 dias 3 USD; 7 dias 4 USD (con Binance '
    'Pay, 1 USD menos: 1, 2 y 3 USD).\n'
    '- Banner en la portada: 60 USD con Pago Movil o 50 USD con Binance Pay, '
    '30 dias, medida 1200x400 px; maximo 5 banners simultaneos.\n'
    '- Puntos: 1 punto por comercio referido; canjes: 3 puntos = 3 dias, '
    '4 puntos = 5 dias, 5 puntos = 7 dias de destacado.'
)

# Respaldo SIN planes para la fase gratuita (planes ocultos). El respaldo con
# planes (``HECHOS_CLAVE``) NO se borra: se usa cuando ``LOCALIS_PAGOS_HABILITADOS=1``.
HECHOS_CLAVE_GRATIS = (
    'DATOS CLAVE DE LOCALIS (cifras oficiales; usalas tal cual):\n'
    '- Localis es GRATUITO y sin limites: no hay planes de pago ni '
    'suscripciones ni limites de productos.\n'
    '- Destacados: 3 dias 2 USD; 5 dias 3 USD; 7 dias 4 USD (con Binance '
    'Pay, 1 USD menos: 1, 2 y 3 USD).\n'
    '- Banner en la portada: 60 USD con Pago Movil o 50 USD con Binance Pay, '
    '30 dias, medida 1200x400 px; maximo 5 banners simultaneos.\n'
    '- Puntos: 1 punto por comercio referido; canjes: 3 puntos = 3 dias, '
    '4 puntos = 5 dias, 5 puntos = 7 dias de destacado.'
)


def _resumen_planes():
    """Linea con planes, precios y limites REALES (planes -> BD, o constantes)."""
    try:
        from backend.plans import (
            ORDEN_PLANES,
            es_limite_ilimitado,
            obtener_plan_por_codigo,
        )

        partes = []
        for codigo in ORDEN_PLANES:
            plan = obtener_plan_por_codigo(codigo) or {}
            nombre = plan.get('nombre') or codigo
            try:
                precio = float(
                    plan.get('precio')
                    if plan.get('precio') is not None
                    else plan.get('precio_usd') or 0
                )
            except (TypeError, ValueError):
                precio = 0.0
            limite = plan.get('limite_productos')
            capacidad = (
                'productos ilimitados'
                if es_limite_ilimitado(limite)
                else f'hasta {limite} productos'
            )
            partes.append(f'{nombre} {precio:.0f} USD/mes ({capacidad})')
        if not partes:
            return ''
        return (
            'Planes (renovacion cada 30 dias): ' + '; '.join(partes) + '.'
        )
    except Exception:
        return ''


def _resumen_destacados():
    """Linea con los precios REALES de destacados por duracion."""
    try:
        from backend.boosts import (
            DESCUENTO_BINANCE_USD,
            PRECIOS_BOOST_USD,
            precio_con_descuento_binance,
        )

        if not PRECIOS_BOOST_USD:
            return ''
        partes = '; '.join(
            f'{dias} dias {precio:.0f} USD'
            for dias, precio in sorted(PRECIOS_BOOST_USD.items())
        )
        binance = '; '.join(
            f'{dias} dias {precio_con_descuento_binance(precio, "binance"):.0f} USD'
            for dias, precio in sorted(PRECIOS_BOOST_USD.items())
        )
        return (
            f'Destacados: {partes}. Con Binance Pay '
            f'{DESCUENTO_BINANCE_USD:.0f} USD menos: {binance}.'
        )
    except Exception:
        return ''


def _resumen_puntos():
    """Linea con el programa de puntos REAL (referidos/canjes)."""
    try:
        from backend.referidos import CANJES_PUNTOS, PUNTOS_POR_REFERIDO

        canjes = ', '.join(
            f'{tier["puntos"]} puntos = {tier["dias"]} dias'
            for tier in sorted(CANJES_PUNTOS, key=lambda t: t['puntos'])
        )
        sufijo = f'; canjes: {canjes} de destacado.' if canjes else '.'
        return (
            f'Puntos: {PUNTOS_POR_REFERIDO} punto por comercio referido{sufijo}'
        )
    except Exception:
        return ''


def hechos_clave():
    """DATOS CLAVE con las cifras derivadas de su fuente real. Nunca lanza.

    Si alguna fuente no esta disponible, cae al texto de respaldo ``HECHOS_CLAVE``
    para no dejar la respuesta sin datos oficiales.
    """
    try:
        bloques = ['DATOS CLAVE DE LOCALIS (cifras oficiales; usalas tal cual):']
        planes = None
        if not fase_gratuita():
            planes = _resumen_planes()
            if planes:
                bloques.append(f'- {planes}')
            bloques.append(
                '- Pago de planes: Pago Movil o Binance Pay (USDT). Se sube una '
                'captura del comprobante; se verifica con OCR o pasa a revision manual.'
            )
        destacados = _resumen_destacados()
        if destacados:
            bloques.append(f'- {destacados}')
        puntos = _resumen_puntos()
        if puntos:
            bloques.append(f'- {puntos}')
        if fase_gratuita():
            bloques.append(
                '- Localis es GRATUITO: no hay planes ni suscripciones.'
            )
            return '\n'.join(bloques)
        if not planes:
            # Sin la fuente principal no arriesgamos cifras parciales.
            return HECHOS_CLAVE
        return '\n'.join(bloques)
    except Exception:
        return HECHOS_CLAVE_GRATIS if fase_gratuita() else HECHOS_CLAVE

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


def menciona_importacion(mensaje):
    bajo = _normalizar(mensaje)
    return any(clave in bajo for clave in _CLAVES_IMPORTACION)


def menciona_pago(mensaje):
    bajo = _normalizar(mensaje)
    return any(clave in bajo for clave in _CLAVES_PAGO)


def menciona_tienda(mensaje):
    bajo = _normalizar(mensaje)
    return any(clave in bajo for clave in _CLAVES_TIENDA)


def menciona_categoria(mensaje):
    bajo = _normalizar(mensaje)
    return any(clave in bajo for clave in _CLAVES_CATEGORIA)


def menciona_bandeja(mensaje):
    """True si el admin pregunta por solicitudes/tickets de la bandeja."""
    bajo = _normalizar(mensaje)
    return any(clave in bajo for clave in _CLAVES_BANDEJA)


def menciona_operacion(mensaje):
    """True si el admin pregunta por cifras de la operación (comercios, etc.)."""
    bajo = _normalizar(mensaje)
    return any(clave in bajo for clave in _CLAVES_OPERACION)


def menciona_salud_pagos(mensaje):
    bajo = _normalizar(mensaje)
    return any(clave in bajo for clave in _CLAVES_SALUD_PAGOS)


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


def _dias_restantes(valor):
    """Dias hasta una fecha (str ISO, date o datetime), o None. Nunca lanza."""
    try:
        from datetime import date, datetime

        if valor is None:
            return None
        if isinstance(valor, datetime):
            fecha = valor.date()
        elif isinstance(valor, date):
            fecha = valor
        else:
            fecha = datetime.fromisoformat(str(valor)[:10]).date()
        return (fecha - date.today()).days
    except Exception:
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
        usados = contar_productos_comercio(comercio.get('id'))

        if fase_gratuita():
            # Planes ocultos: solo se informan los productos (sin plan ni pago).
            lineas = [f'Productos publicados: {usados}']
            if incluir_imagenes:
                sin_foto = _contar_sin_foto(comercio.get('id'))
                if sin_foto is not None:
                    lineas.append(f'Productos sin foto real: {sin_foto}')
            return '\n'.join(lineas)

        plan_codigo = (comercio.get('plan_tipo') or 'gratis').lower()
        plan = obtener_plan_por_codigo(plan_codigo) or {}
        nombre_plan = plan.get('nombre') or plan_codigo
        limite = obtener_limite_productos_comercio(comercio.get('id'))
        limite_txt = (
            'ilimitado' if es_limite_ilimitado(limite) else str(limite)
        )

        lineas = [
            f'Plan actual: {nombre_plan}',
            f'Productos publicados: {usados} de {limite_txt}',
            f'Estado de pago: {comercio.get("estado_pago") or "desconocido"}',
        ]
        if comercio.get('fecha_vencimiento'):
            lineas.append(f'Vence: {comercio.get("fecha_vencimiento")}')
            restantes = _dias_restantes(comercio.get('fecha_vencimiento'))
            if restantes is not None:
                if restantes < 0:
                    lineas.append(f'Vencido hace {-restantes} dia(s)')
                else:
                    lineas.append(f'Dias restantes: {restantes}')
        if incluir_imagenes:
            sin_foto = _contar_sin_foto(comercio.get('id'))
            if sin_foto is not None:
                lineas.append(f'Productos sin foto real: {sin_foto}')
        return '\n'.join(lineas)
    except Exception as error:
        _log.warning('resumen del comercio fallo: %s', error)
        return ''


def ultima_importacion(comercio_id):
    """Estado de la ultima importacion del comercio (o '')."""
    if not comercio_id:
        return ''
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT job_id, estado, exito, mensaje, filename, creado '
                'FROM import_jobs WHERE comercio_id = ? ORDER BY creado DESC LIMIT 1',
                (int(comercio_id),),
            )
            fila = cursor.fetchone()
        if not fila:
            return 'No hay importaciones registradas.'
        lineas = [
            f'Archivo: {fila[4] or "(sin nombre)"}',
            f'Estado: {fila[1] or "desconocido"}',
        ]
        if fila[3]:
            lineas.append(f'Mensaje: {str(fila[3])[:300]}')
        if fila[5]:
            lineas.append(f'Fecha: {fila[5]}')
        return '\n'.join(lineas)
    except Exception as error:
        _log.warning('ultima importacion fallo: %s', error)
        return ''


def resumen_imagenes(comercio_id, maximo_ejemplos=3):
    """Conteo de productos por estado de imagen y ejemplos sin foto real (o '')."""
    if not comercio_id:
        return ''
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                "SELECT COALESCE(imagen_estado, '') AS estado, COUNT(*) "
                'FROM productos WHERE comercio_id = ? GROUP BY 1',
                (int(comercio_id),),
            )
            conteos = {str(f[0] or 'sin_estado'): int(f[1] or 0) for f in cursor.fetchall()}
            cursor.execute(
                "SELECT nombre FROM productos WHERE comercio_id = ? "
                "AND COALESCE(imagen_estado, '') <> 'real' "
                'ORDER BY id DESC LIMIT ?',
                (int(comercio_id), int(maximo_ejemplos)),
            )
            ejemplos = [str(f[0]) for f in cursor.fetchall() if f and f[0]]
        total = sum(conteos.values())
        reales = conteos.get('real', 0)
        lineas = [f'Productos: {total}; con foto real: {reales}']
        otros = {k: v for k, v in conteos.items() if k != 'real' and v}
        if otros:
            detalle = ', '.join(f'{k}: {v}' for k, v in sorted(otros.items()))
            lineas.append(f'Sin foto real por estado: {detalle}')
        if ejemplos:
            lineas.append('Ejemplos sin foto: ' + '; '.join(ejemplos))
        return '\n'.join(lineas)
    except Exception as error:
        _log.warning('resumen de imagenes fallo: %s', error)
        return ''


def buscar_tiendas(mensaje, maximo=4):
    """Tiendas publicas que mejor encajan con el mensaje (o '')."""
    claves = [clave for clave in _keywords(mensaje) if len(clave) >= 4]
    if not claves:
        return ''
    try:
        from backend.stores import buscar_y_filtrar_comercios
    except Exception:
        return ''
    for consulta in [' '.join(claves[:2])] + claves[:2]:
        consulta = consulta.strip()
        if not consulta:
            continue
        try:
            tiendas = buscar_y_filtrar_comercios(palabra_clave=consulta) or []
        except Exception as error:
            _log.warning('busqueda de tiendas fallo: %s', error)
            return ''
        if tiendas:
            return formatear_tiendas(tiendas, maximo=maximo)
    return ''


def formatear_tiendas(tiendas, maximo=4):
    """Lista compacta de tiendas con ubicacion y contacto."""
    lineas = []
    for tienda in (tiendas or [])[:maximo]:
        nombre = str(tienda.get('nombre') or '').strip()
        if not nombre:
            continue
        ciudad = str(tienda.get('ciudad') or '').strip()
        zona = str(tienda.get('zona') or '').strip()
        ubicacion = ', '.join(p for p in (zona, ciudad) if p)
        telefono = str(tienda.get('telefono') or '').strip()
        extra = []
        if ubicacion:
            extra.append(ubicacion)
        if telefono:
            extra.append(f'WhatsApp {telefono}')
        sufijo = (' - ' + ' | '.join(extra)) if extra else ''
        lineas.append(f'- {nombre}{sufijo}')
    return '\n'.join(lineas)


# ---------------------------------------------------------------------------
# Panel de administración: cifras REALES de la operación (solo lectura)
# ---------------------------------------------------------------------------
# El admin pregunta mucho por "cuántos hay" (solicitudes abiertas, pagos por
# revisar, tiendas suspendidas). Sin estos números el modelo tendería a
# inventarlos, así que se calculan en la BD y se cachean unos segundos: puede
# haber varias preguntas seguidas y no conviene golpear la BD en cada una.
_TTL_OPERACION_SEG = 45


def resumen_bandeja():
    """Solicitudes abiertas y alertas de pago de la bandeja del panel (o '')."""
    try:
        from backend.admin import resumen_bandeja as _resumen

        datos = _resumen() or {}
        abiertas = int(datos.get('abiertas') or 0)
        en_proceso = int(datos.get('en_proceso') or 0)
        aceptadas = int(datos.get('aceptadas') or 0)
        rechazadas = int(datos.get('rechazadas') or 0)
        revisiones = int(datos.get('revisiones_pago') or 0)
        por_tipo = datos.get('por_tipo') or {}
        lineas = [
            f'Solicitudes de usuarios abiertas: {abiertas} '
            f'(en proceso: {en_proceso})',
            f'Aceptadas: {aceptadas}; rechazadas: {rechazadas}',
            f'Alertas de revisión de pago pendientes: {revisiones}',
        ]
        if por_tipo:
            detalle = ', '.join(
                f'{tipo}: {total}' for tipo, total in sorted(por_tipo.items())
            )
            lineas.append(f'Solicitudes por tipo (histórico): {detalle}')
        return '\n'.join(lineas)
    except Exception as error:
        _log.warning('resumen de bandeja fallo: %s', error)
        return ''


def resumen_salud_pagos(dias=30):
    """Salud de la verificación de pagos de los últimos días (o '')."""
    try:
        from backend.admin import salud_pagos as _salud

        datos = _salud(dias) or {}
        return '\n'.join(
            [
                f'Pagos registrados (últimos {datos.get("dias") or dias} días): '
                f'{int(datos.get("total") or 0)}',
                f'Confirmados por Binance: {int(datos.get("confirmados") or 0)}',
                f'Sin verificar (API): {int(datos.get("no_verificados") or 0)}',
                f'No encontrados: {int(datos.get("no_encontrados") or 0)}',
                'Por revisar ahora (bandeja): '
                f'{int(datos.get("pendientes_revision") or 0)}',
            ]
        )
    except Exception as error:
        _log.warning('resumen de salud de pagos fallo: %s', error)
        return ''


def _cifras_operacion():
    """Conteos de comercios, usuarios y productos. Cacheado y defensivo."""
    def _cargar():
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM comercios),
                    (SELECT COUNT(*) FROM comercios
                      WHERE COALESCE(visible, 1) = 1),
                    (SELECT COUNT(*) FROM comercios
                      WHERE COALESCE(estado_pago, '') = 'suspendido'
                         OR COALESCE(visible, 1) = 0),
                    (SELECT COUNT(*) FROM comercios
                      WHERE COALESCE(estado_tienda, 'activa') = 'pre_cuenta'),
                    (SELECT COUNT(*) FROM usuarios),
                    (SELECT COUNT(*) FROM productos)
                """
            )
            fila = cursor.fetchone() or (0,) * 6

        claves = (
            'comercios',
            'comercios_visibles',
            'comercios_inactivos',
            'pre_cuentas',
            'usuarios',
            'productos',
        )
        return {clave: int(valor or 0) for clave, valor in zip(claves, fila)}

    try:
        from backend.runtime_cache import get_or_load

        return get_or_load(
            'asistente:cifras_operacion', _cargar, ttl_seconds=_TTL_OPERACION_SEG
        )
    except Exception as error:
        _log.warning('cifras de operacion fallaron: %s', error)
        return {}


def resumen_operacion():
    """Cifras del panel (comercios, usuarios, productos) para el admin (o '')."""
    datos = _cifras_operacion()
    if not datos:
        return ''
    return '\n'.join(
        [
            f'Comercios registrados: {datos.get("comercios", 0)} '
            f'(visibles: {datos.get("comercios_visibles", 0)}; '
            f'inactivos/suspendidos: {datos.get("comercios_inactivos", 0)}; '
            f'pre-cuentas: {datos.get("pre_cuentas", 0)})',
            f'Usuarios registrados: {datos.get("usuarios", 0)}',
            f'Productos en catálogo: {datos.get("productos", 0)}',
        ]
    )


def categorias_disponibles():
    """Categorias activas del catalogo (o '')."""
    try:
        from backend.db import get_db_connection

        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT nombre FROM categorias WHERE activo = 1 ORDER BY nombre'
            )
            nombres = [str(f[0]) for f in cursor.fetchall() if f and f[0]]
        return ', '.join(nombres)
    except Exception as error:
        _log.warning('categorias fallo: %s', error)
        return ''

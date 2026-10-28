"""Banners B2B de la portada ("alquilar banner") con pago móvil y Binance Pay.

La portada muestra un **carrusel** con los banners B2B activos: se cuentan, se
rotan cada ``ROTACION_SEGUNDOS`` y cada rotación suma una impresión al banner
que se mostró. Cuando no hay ninguno se usa el banner principal configurado
(``configuracion_sistema.banner_principal``), así la portada nunca queda vacía.

El alquiler reutiliza EXACTAMENTE el sistema de pagos de ``Destacados``:

  - Anti-doble-gasto de referencias: ``backend.subscriptions._referencia_ya_usada``
    (tablas ``pagos`` y ``solicitudes_pago``) + índice único en ``solicitudes_pago``.
  - OCR del comprobante (pago móvil venezolano o Binance Pay USDT) en el
    blueprint de la API, igual que mensualidades y destacados.
  - Registro del pago en ``solicitudes_pago`` (``plan_tipo = banner``).
  - Auditoría en la tabla propia ``banners_b2b``.

Precios (USD) por un alquiler de ``DIAS_BANNER`` días en la portada:
  - Pago móvil -> $60.00
  - Binance Pay -> $50.00 (descuento fijo de $10)

Medidas de la imagen: ``MEDIDAS_BANNER`` (1200 x 400 px, proporción 3:1). La
imagen se reencuadra a esas medidas (recorte centrado, sin deformar).
"""

from __future__ import annotations

import io
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
from werkzeug.utils import secure_filename

from backend.db import get_db_connection, using_postgres
from backend.subscriptions import _referencia_ya_usada

logger = logging.getLogger('localis.banners_b2b')

# Precio base (Pago Móvil) y descuento al pagar con Binance Pay (USDT).
PRECIO_BANNER_USD = 60.0
DESCUENTO_BANNER_BINANCE_USD = 10.0

# Días de alquiler que cubre cada pago.
DIAS_BANNER = 30

# Medidas oficiales del banner (3:1). El carrusel las respeta tal cual.
MEDIDAS_BANNER = {'ancho': 1200, 'alto': 400}
RATIO_MINIMO = 1.6
RATIO_MAXIMO = 6.0

# Cuánto dura cada banner en pantalla antes de rotar (segundos).
ROTACION_SEGUNDOS = 60

# Capacidad de la portada: como máximo se alquilan 5 banners a la vez (cada uno
# dura 30 días). Cuando se libera un cupo se puede alquilar otro.
MAX_BANNERS_ACTIVOS = 5
# Tope de banners activos que se sirven a la portada en un solo render.
MAX_BANNERS_PORTADA = MAX_BANNERS_ACTIVOS

# Clave del candado de transacción que serializa el control de cupos en la BD
# (evita que dos pagos simultáneos dejen 6 banners).
CLAVE_LOCK_CUPOS_BANNER = 4747001

_ESTADOS_ACTIVOS = ('activo',)


def _a_int(valor, campo='id'):
    """Entero limpio y estricto. Rechaza ``bool``, ``None`` y texto no numérico."""
    if valor is None or isinstance(valor, bool):
        raise ValueError(f'{campo} inválido.')
    texto = str(valor).strip()
    if not texto:
        raise ValueError(f'{campo} inválido.')
    try:
        return int(texto)
    except (TypeError, ValueError):
        raise ValueError(f'{campo} inválido.')


def sanitizar_referencia(referencia):
    """Deja solo los dígitos de la referencia (sin espacios ni caracteres raros)."""
    return re.sub(r'\D', '', str(referencia or ''))


def referencia_valida(referencia, metodo='pago_movil_ocr'):
    """True si la referencia cumple el formato del método de pago.

    Pago móvil: exactamente 6 dígitos. Binance Pay: 6-24 dígitos (ID de orden).
    """
    ref = sanitizar_referencia(referencia)
    patron = r'^\d{6,24}$' if _es_binance(metodo) else r'^\d{6}$'
    return bool(re.match(patron, ref))


def _es_binance(metodo):
    return str(metodo or '').strip().lower().startswith('binance')


def _ahora_utc():
    return datetime.now(timezone.utc)


def precio_banner(metodo='pago_movil'):
    """Precio USD del alquiler según el método de pago. Función pura."""
    if _es_binance(metodo):
        return round(PRECIO_BANNER_USD - DESCUENTO_BANNER_BINANCE_USD, 2)
    return round(PRECIO_BANNER_USD, 2)


def opciones_banner():
    """Datos de la oferta para el panel (precios, días y medidas)."""
    return {
        'dias': DIAS_BANNER,
        'precio_usd': precio_banner('pago_movil'),
        'precio_binance_usd': precio_banner('binance'),
        'descuento_binance_usd': round(
            PRECIO_BANNER_USD - precio_banner('binance'), 2
        ),
        'ancho': MEDIDAS_BANNER['ancho'],
        'alto': MEDIDAS_BANNER['alto'],
        'rotacion_segundos': ROTACION_SEGUNDOS,
        'maximo': MAX_BANNERS_ACTIVOS,
    }


# ==========================================
# Imagen del banner
# ==========================================


class BannerInvalido(ValueError):
    """Imagen o enlace del banner no válidos (mensaje apto para el usuario)."""


def normalizar_imagen_banner(data_bytes):
    """Reencuadra la imagen a las medidas oficiales y la devuelve en WebP.

    Escala para cubrir 1200x400 y recorta el centro (nunca deforma). Retorna
    ``(bytes, content_type, filename)``. Lanza :class:`BannerInvalido` si la
    imagen no se puede leer o su proporción es inservible para un banner.
    """
    if not data_bytes:
        raise BannerInvalido('No se recibió la imagen del banner.')
    try:
        from PIL import Image

        imagen = Image.open(io.BytesIO(data_bytes))
        imagen.load()
    except Exception as error:
        raise BannerInvalido(
            f'No se pudo leer la imagen del banner ({type(error).__name__}).'
        ) from error

    try:
        ancho, alto = imagen.size
        if ancho <= 0 or alto <= 0:
            raise BannerInvalido('La imagen del banner no tiene un tamaño válido.')
        ratio = ancho / alto
        if ratio < RATIO_MINIMO or ratio > RATIO_MAXIMO:
            raise BannerInvalido(
                'La imagen debe ser panorámica (se recomienda '
                f'{MEDIDAS_BANNER["ancho"]}x{MEDIDAS_BANNER["alto"]} px, '
                'proporción 3:1).'
            )

        destino_ancho = MEDIDAS_BANNER['ancho']
        destino_alto = MEDIDAS_BANNER['alto']

        if imagen.mode in ('RGBA', 'LA', 'P'):
            imagen = imagen.convert('RGBA')
            fondo = Image.new('RGB', imagen.size, (255, 255, 255))
            fondo.paste(imagen, mask=imagen.split()[3])
            imagen = fondo
        elif imagen.mode != 'RGB':
            imagen = imagen.convert('RGB')

        # "Cover": escala al mayor de los dos factores y recorta el centro.
        factor = max(destino_ancho / ancho, destino_alto / alto)
        nuevo = (
            max(destino_ancho, int(round(ancho * factor))),
            max(destino_alto, int(round(alto * factor))),
        )
        imagen = imagen.resize(nuevo, Image.LANCZOS)
        izquierda = max(0, (nuevo[0] - destino_ancho) // 2)
        arriba = max(0, (nuevo[1] - destino_alto) // 2)
        imagen = imagen.crop(
            (izquierda, arriba, izquierda + destino_ancho, arriba + destino_alto)
        )

        buffer = io.BytesIO()
        imagen.save(buffer, 'WEBP', quality=84, method=4)
        prefijo = secure_filename('banner') or 'banner'
        filename = f'{prefijo}_{uuid.uuid4().hex[:8]}.webp'
        return buffer.getvalue(), 'image/webp', filename
    except BannerInvalido:
        raise
    except Exception as error:
        raise BannerInvalido(
            f'No se pudo preparar la imagen del banner '
            f'({type(error).__name__}: {error}).'
        ) from error


def sanitizar_enlace_banner(enlace):
    """Normaliza el enlace del banner. ``None`` si no es http(s) ni una ruta interna.

    Evita ``javascript:`` y otros esquemas peligrosos en el HTML público.
    """
    texto = str(enlace or '').strip()
    if not texto:
        return None
    if len(texto) > 500:
        return None
    if texto.startswith('/') and not texto.startswith('//'):
        return texto
    bajo = texto.lower()
    if bajo.startswith('http://') or bajo.startswith('https://'):
        return texto
    return None


# ==========================================
# Lectura para la portada y el panel
# ==========================================


def listar_banners_activos(limite=MAX_BANNERS_PORTADA):
    """Banners B2B vigentes para el carrusel de la portada. Nunca lanza.

    Devuelve una lista de dicts con ``id``, ``titulo``, ``imagen_url``,
    ``enlace_url``, ``comercio_id`` y ``comercio_nombre``.
    """
    try:
        limite = max(1, int(limite or MAX_BANNERS_PORTADA))
    except (TypeError, ValueError):
        limite = MAX_BANNERS_PORTADA
    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT b.id, b.titulo, b.imagen_url, b.enlace_url,
                       b.comercio_id, b.usuario_id, b.anunciante,
                       c.nombre AS comercio_nombre
                FROM banners_b2b b
                LEFT JOIN comercios c ON c.id = b.comercio_id
                WHERE COALESCE(b.estado, 'activo') = 'activo'
                  AND b.imagen_url IS NOT NULL
                  AND (b.fecha_fin IS NULL OR b.fecha_fin > CURRENT_TIMESTAMP)
                ORDER BY b.id DESC
                LIMIT ?
                """,
                (limite,),
            )
            filas = cursor.fetchall() or []
    except Exception as error:
        logger.error(f'[Localis Banner] no se pudieron listar: {error}')
        return []

    banners = []
    for fila in filas:
        datos = dict(fila) if not isinstance(fila, dict) else fila
        try:
            # El anunciante es el comercio o, si no tiene tienda, el nombre que
            # dejó en su cuenta (``anunciante``).
            nombre = (
                (datos.get('comercio_nombre') or datos.get('anunciante') or '')
                .strip()
            )
            banners.append(
                {
                    'id': int(datos.get('id')),
                    'titulo': (datos.get('titulo') or '').strip(),
                    'imagen_url': datos.get('imagen_url'),
                    'enlace_url': sanitizar_enlace_banner(datos.get('enlace_url')),
                    'comercio_id': datos.get('comercio_id'),
                    'usuario_id': datos.get('usuario_id'),
                    'comercio_nombre': nombre,
                    'anunciante': (datos.get('anunciante') or '').strip(),
                }
            )
        except (TypeError, ValueError):
            continue
    return banners


def contar_banners_activos():
    """Cantidad de banners B2B alquilados y vigentes ahora mismo. Nunca lanza."""
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                SELECT COUNT(*) FROM banners_b2b
                WHERE COALESCE(estado, 'activo') = 'activo'
                  AND imagen_url IS NOT NULL
                  AND (fecha_fin IS NULL OR fecha_fin > CURRENT_TIMESTAMP)
                """
            )
            fila = cursor.fetchone()
            if not fila:
                return 0
            valor = fila[0] if not isinstance(fila, dict) else next(iter(fila.values()))
            return max(0, int(valor or 0))
    except Exception as error:
        logger.error(f'[Localis Banner] no se pudo contar: {error}')
        return 0


def cupos_banners():
    """Cupos de la portada: ``{'maximo', 'ocupados', 'disponibles'}``.

    Es lo que muestra el panel del comercio ("tenemos disponible X banners").
    Nunca lanza: sin BD devuelve todos los cupos libres.
    """
    ocupados = contar_banners_activos()
    disponibles = max(0, MAX_BANNERS_ACTIVOS - ocupados)
    return {
        'maximo': MAX_BANNERS_ACTIVOS,
        'ocupados': ocupados,
        'disponibles': disponibles,
    }


def hay_cupo_banner():
    """True si todavía se puede alquilar un banner (quedan cupos libres)."""
    return contar_banners_activos() < MAX_BANNERS_ACTIVOS


def registrar_impresion(banner_id):
    """Suma una impresión al banner mostrado. Nunca lanza."""
    try:
        banner_id = _a_int(banner_id, 'banner_id')
    except ValueError:
        return False
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'UPDATE banners_b2b SET impresiones = COALESCE(impresiones, 0) + 1 '
                'WHERE id = ?',
                (banner_id,),
            )
            conexion.commit()
            return True
    except Exception as error:
        logger.warning(f'[Localis Banner] impresión no registrada: {error}')
        return False


def estado_banners_anunciante(comercio_id=None, usuario_id=None):
    """Banners de un anunciante (comercio o usuario sin tienda). Nunca lanza.

    Devuelve sus últimos 12 banners con vigencia, fecha de registro, vencimiento
    e impresiones: es lo que se lista en la página del banner y en el perfil.
    """
    resultado = []
    try:
        if comercio_id:
            filtro, valor = 'comercio_id = ?', _a_int(comercio_id, 'comercio_id')
        elif usuario_id:
            filtro, valor = 'usuario_id = ?', _a_int(usuario_id, 'usuario_id')
        else:
            return resultado
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                f"""
                SELECT id, titulo, imagen_url, enlace_url, estado,
                       fecha_inicio, fecha_fin, fecha_registro, impresiones,
                       metodo
                FROM banners_b2b
                WHERE {filtro}
                ORDER BY id DESC
                LIMIT 12
                """,
                (valor,),
            )
            filas = cursor.fetchall() or []
    except Exception as error:
        logger.error(f'[Localis Banner] estado no disponible: {error}')
        return resultado

    ahora = _ahora_utc()
    for fila in filas:
        datos = dict(fila) if not isinstance(fila, dict) else fila
        fin = datos.get('fecha_fin')
        vigente = False
        try:
            if isinstance(fin, datetime):
                momento = fin if fin.tzinfo else fin.replace(tzinfo=timezone.utc)
                vigente = momento > ahora
        except (TypeError, ValueError):
            vigente = False
        resultado.append(
            {
                'id': datos.get('id'),
                'titulo': (datos.get('titulo') or '').strip(),
                'imagen_url': datos.get('imagen_url'),
                'estado': (datos.get('estado') or 'activo'),
                'activo': vigente
                and (datos.get('estado') or 'activo') in _ESTADOS_ACTIVOS,
                'fecha_registro': datos.get('fecha_registro'),
                'fecha_fin': fin,
                'impresiones': int(datos.get('impresiones') or 0),
                'metodo': datos.get('metodo'),
            }
        )
    return resultado


def estado_banners_comercio(comercio_id):
    """Compatibilidad: banners de un comercio (ver ``estado_banners_anunciante``)."""
    return estado_banners_anunciante(comercio_id=comercio_id)


# ==========================================
# Activación del alquiler
# ==========================================


def _es_referencia_duplicada(error):
    """True si el error de BD es una violación de unicidad (código 23505)."""
    try:
        if isinstance(error, psycopg2.errors.UniqueViolation):
            return True
    except Exception:
        pass
    return getattr(error, 'pgcode', None) == '23505'


def referencia_ya_usada_banner(referencia):
    """True si la referencia ya existe en ``banners_b2b``."""
    ref = sanitizar_referencia(referencia)
    if not ref:
        return False
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                'SELECT 1 FROM banners_b2b WHERE referencia_pago = ? LIMIT 1', (ref,)
            )
            return cursor.fetchone() is not None
    except Exception as error:
        logger.error(f'[Localis Banner] no se pudo auditar la referencia: {error}')
        return False


def _registrar_intento_rechazado(comercio_id, referencia, motivo):
    """Audita un alquiler rechazado. Nunca lanza (aislado)."""
    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()
            cursor.execute(
                """
                INSERT INTO logs_auditoria (usuario_id, accion, detalles)
                VALUES (?, 'Banner B2B rechazado', ?)
                """,
                (None, f'comercio={comercio_id} ref={referencia or "-"} motivo={motivo}'),
            )
            conexion.commit()
    except Exception as error:
        logger.error(f'[Localis Banner] no se pudo auditar rechazo: {error}')


def activar_banner_b2b(
    comercio_id=None,
    referencia=None,
    titulo=None,
    imagen_url=None,
    enlace_url=None,
    metodo='pago_movil_ocr',
    comprobante_url=None,
    comprobante_hash=None,
    dias=None,
    usuario_id=None,
    anunciante=None,
):
    """Activa un banner B2B tras validar el pago. Retorna ``(exito, mensaje)``.

    El anunciante es un comercio (``comercio_id``) **o** cualquier usuario sin
    tienda (``usuario_id`` + ``anunciante``). ``metodo`` es ``pago_movil_ocr``
    (por defecto) o ``binance``; cada uno tiene su precio (con descuento en
    Binance) y su formato de referencia. Pago + auditoría + banner se escriben
    en una única transacción: ante cualquier fallo no queda estado inconsistente.
    """
    try:
        comercio_id = _a_int(comercio_id, 'comercio_id') if comercio_id else None
        usuario_id = _a_int(usuario_id, 'usuario_id') if usuario_id else None
        dias = _a_int(dias, 'dias') if dias is not None else DIAS_BANNER
    except ValueError as error:
        return False, str(error)

    if comercio_id is None and usuario_id is None:
        return False, 'No se pudo identificar al anunciante del banner.'

    anunciante = ' '.join(str(anunciante or '').split()).strip()[:120] or None

    if dias <= 0 or dias > 365:
        dias = DIAS_BANNER

    es_binance = _es_binance(metodo)
    metodo = 'binance' if es_binance else 'pago_movil_ocr'
    referencia = sanitizar_referencia(referencia)
    if not referencia_valida(referencia, metodo):
        _registrar_intento_rechazado(comercio_id, referencia, 'referencia_invalida')
        return (
            False,
            'No se detectó un ID de orden válido de Binance.'
            if es_binance
            else 'La referencia de pago móvil debe tener exactamente 6 dígitos.',
        )

    titulo = ' '.join(str(titulo or '').split()).strip()[:120]
    if not titulo:
        # En la portada solo se ve la imagen; el nombre es interno (lista del
        # anunciante y texto alternativo).
        titulo = f'Banner de {anunciante}' if anunciante else 'Banner en la portada'
    if not imagen_url:
        return False, 'No se pudo guardar la imagen del banner. Intenta de nuevo.'

    try:
        if _referencia_ya_usada(referencia) or referencia_ya_usada_banner(referencia):
            _registrar_intento_rechazado(
                comercio_id, referencia, 'referencia_reutilizada'
            )
            return False, 'Esta referencia de pago ya fue registrada en el sistema.'
    except Exception as error:
        logger.error(f'[Localis Banner] no se pudo validar la referencia: {error}')
        return False, 'No se pudo validar la referencia de pago. Intenta de nuevo.'

    precio_usd = precio_banner(metodo)
    inicio = _ahora_utc()
    fin = inicio + timedelta(days=dias)
    enlace = sanitizar_enlace_banner(enlace_url)

    try:
        with get_db_connection() as conexion:
            cursor = conexion.cursor()

            # Cupo de la portada: como máximo MAX_BANNERS_ACTIVOS banners
            # vigentes a la vez. El candado de transacción serializa la
            # comprobación para que dos pagos simultáneos no dejen 6.
            if using_postgres():
                cursor.execute(
                    'SELECT pg_advisory_xact_lock(?)', (CLAVE_LOCK_CUPOS_BANNER,)
                )
            cursor.execute(
                """
                SELECT COUNT(*) FROM banners_b2b
                WHERE COALESCE(estado, 'activo') = 'activo'
                  AND (fecha_fin IS NULL OR fecha_fin > CURRENT_TIMESTAMP)
                """
            )
            fila_cupo = cursor.fetchone()
            if fila_cupo:
                ocupados = fila_cupo[0] if not isinstance(fila_cupo, dict) else next(
                    iter(fila_cupo.values())
                )
                if int(ocupados or 0) >= MAX_BANNERS_ACTIVOS:
                    conexion.rollback()
                    _registrar_intento_rechazado(comercio_id, referencia, 'sin_cupo')
                    return (
                        False,
                        f'No hay banners disponibles: los {MAX_BANNERS_ACTIVOS} '
                        'espacios de la portada están ocupados. Intenta de nuevo '
                        'cuando venza alguno.',
                    )

            # Propiedad inequívoca: el anunciante de la sesión debe existir.
            if comercio_id is not None:
                cursor.execute(
                    'SELECT id FROM comercios WHERE id = ?', (comercio_id,)
                )
                if not cursor.fetchone():
                    conexion.rollback()
                    _registrar_intento_rechazado(
                        comercio_id, referencia, 'comercio_inexistente'
                    )
                    return False, 'El comercio no existe.'
            else:
                cursor.execute('SELECT id FROM usuarios WHERE id = ?', (usuario_id,))
                if not cursor.fetchone():
                    conexion.rollback()
                    _registrar_intento_rechazado(
                        usuario_id, referencia, 'usuario_inexistente'
                    )
                    return False, 'La cuenta del anunciante no existe.'

            cursor.execute(
                """
                INSERT INTO solicitudes_pago
                    (comercio_id, plan_tipo, referencia, fecha_transferencia, estado,
                     comprobante_url, comprobante_hash)
                VALUES (?, ?, ?, ?, 'aprobado', ?, ?)
                """,
                (
                    comercio_id,
                    f'banner_{dias}d',
                    referencia,
                    inicio.strftime('%Y-%m-%d'),
                    comprobante_url,
                    comprobante_hash,
                ),
            )

            cursor.execute(
                """
                INSERT INTO banners_b2b
                    (comercio_id, usuario_id, anunciante, titulo, imagen_url,
                     enlace_url, precio_usd, dias_duracion, referencia_pago,
                     metodo, estado, fecha_inicio, fecha_fin, impresiones)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'activo', ?, ?, 0)
                """,
                (
                    comercio_id,
                    usuario_id,
                    anunciante,
                    titulo,
                    imagen_url,
                    enlace,
                    precio_usd,
                    dias,
                    referencia,
                    metodo,
                    inicio,
                    fin,
                ),
            )
            conexion.commit()
    except Exception as error:
        if _es_referencia_duplicada(error):
            _registrar_intento_rechazado(
                comercio_id, referencia, 'referencia_duplicada'
            )
            return False, 'Esta referencia de pago ya fue utilizada en un banner.'
        logger.error(
            f'[Localis Banner] fallo al activar: {type(error).__name__}: {error}'
        )
        return False, 'No se pudo activar el banner. No se realizó ningún cargo.'

    sufijo = ' · Binance Pay' if es_binance else ''
    return (
        True,
        f'¡Banner alquilado por {dias} días en la portada! '
        f'(${precio_usd:.2f} USD{sufijo})',
    )


def extender_banner_b2b(comercio_id=None, dias=DIAS_BANNER, referencia=None):
    """Extiende el alquiler de un banner (confirmación manual del admin).

    Identifica el banner por su ``referencia`` de pago (sirve también para
    anunciantes sin tienda) o, si no se indica, por el último del comercio.
    Se usa cuando el administrador confirma a mano un pago que no se pudo
    verificar automáticamente. Retorna ``(exito, mensaje)``. Nunca lanza.
    """
    try:
        comercio_id = _a_int(comercio_id, 'comercio_id') if comercio_id else None
        dias = max(1, _a_int(dias, 'dias'))
    except ValueError as error:
        return False, str(error)

    ref = sanitizar_referencia(referencia)
    if not ref and comercio_id is None:
        return False, 'No se sabe qué banner extender.'

    try:
        with get_db_connection(row_factory=sqlite3.Row) as conexion:
            cursor = conexion.cursor()
            if ref:
                cursor.execute(
                    'SELECT id, fecha_fin FROM banners_b2b WHERE referencia_pago = ? '
                    'ORDER BY id DESC LIMIT 1',
                    (ref,),
                )
            else:
                cursor.execute(
                    'SELECT id, fecha_fin FROM banners_b2b WHERE comercio_id = ? '
                    'ORDER BY id DESC LIMIT 1',
                    (comercio_id,),
                )
            fila = cursor.fetchone()
            if not fila:
                return False, 'No hay banners registrados para ese pago.'
            datos = dict(fila) if not isinstance(fila, dict) else fila
            fin = datos.get('fecha_fin')
            ahora = _ahora_utc()
            if isinstance(fin, datetime):
                base = fin if fin.tzinfo else fin.replace(tzinfo=timezone.utc)
                base = max(base, ahora)
            else:
                base = ahora
            nuevo_fin = base + timedelta(days=dias)
            cursor.execute(
                'UPDATE banners_b2b SET estado = ?, fecha_fin = ? WHERE id = ?',
                ('activo', nuevo_fin, datos.get('id')),
            )
            conexion.commit()
        return True, f'Banner extendido {dias} días.'
    except Exception as error:
        logger.error(f'[Localis Banner] no se pudo extender: {error}')
        return False, 'No se pudo extender el banner.'

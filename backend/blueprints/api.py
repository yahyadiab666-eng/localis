"""Blueprint de la API JSON (rutas /api/*).

Extraido de ``main.py`` (Bloque 4). Sus endpoints se consumen por URL literal
desde JS (no por ``url_for``), asi que moverlos no rompe plantillas ni enlaces.

Se importa y registra al FINAL de main.py, despues de que exista el objeto CSRF
(``registrar_csrf``), para que ``exento_csrf`` funcione.
"""

from __future__ import annotations
import logging
logger = logging.getLogger('localis.blueprints.api')

import traceback

from flask import Blueprint, jsonify, request, session

from config import MENSAJE_PAGOS_CONGELADOS, pagos_habilitados

from backend.rate_limit import limite

from backend.admin import confirmar_pago_suscripcion
from backend.analytics import (
    normalizar_tipo,
    producto_pertenece_a_comercio,
    registrar_interaccion,
)
from backend.auth import obtener_usuario_por_id
from backend.boosts import activar_boost_producto, precio_boost
from backend.image_lookup import persistir_imagen_producto_hibrida
from backend.payment_ocr import (
    OCR_SEMAFORO,
    validar_comprobante_binance,
    validar_comprobante_pago_movil,
)
from backend.payments import calcular_hash_comprobante
from backend.plans import MENSAJE_LIMITE_PRODUCTOS, es_limite_ilimitado
from backend.session_comercio import es_usuario_comerciante
from backend.stores import (
    asegurar_slug_producto,
    obtener_comercio_por_usuario,
    obtener_producto_publico,
    obtener_tasa_dolar,
)
from backend.subscriptions import (
    activar_suscripcion_por_comprobante,
    calcular_cotizacion_cambio_plan,
    calcular_monto_pago_plan,
    comprobante_ya_usado,
    contar_productos_comercio,
    obtener_datos_pago_movil,
    obtener_destino_pago_movil,
    obtener_limite_productos_comercio,
    programar_downgrade_plan,
    puede_agregar_producto,
)
from backend.supabase_storage import intentar_subir_bytes
from backend.tasa_bcv import estado_tasa_bcv, obtener_tasa_bcv
from backend.utils import (
    imagen_url_para_persistir,
    normalizar_codigo_barras,
    parsear_entero_form,
    parsear_precio_form,
    url_whatsapp_comercio,
)
from backend.vistas_imagenes import (
    registrar_imagen_manual,
    sincronizar_foto_local_si_aplica,
)
from backend.web_seguridad import (
    admin_requerido_api,
    comercio_sesion_validado,
    exento_csrf,
    login_requerido_api,
)
from utils.images import PLACEHOLDER_PRODUCTO, url_imagen_producto_segura

bp = Blueprint('api', __name__, url_prefix='/api')


def _destino_pago_movil():
    """Banco/RIF/teléfono con los que la UI cobra y el OCR debe validar.

    Nunca lanza: si la configuración falla, el OCR usa sus valores por defecto.
    """
    try:
        datos = obtener_destino_pago_movil() or {}
    except Exception:
        datos = {}
    return {
        'banco': datos.get('banco'),
        'cedula_rif': datos.get('cedula_rif'),
        'telefono': datos.get('telefono'),
    }


def _registrar_revision_pago(
    comercio,
    referencia,
    es_binance,
    confirmado,
    monto_usd,
    ocr=None,
    comprobante_url=None,
    plan_tipo=None,
    boost_dias=None,
    boost_producto_id=None,
):
    """Manda a la bandeja administrativa un pago Binance no confirmado.

    Solo actúa con Binance y cuando la API no confirmó la orden: ``False``
    (no encontrada o monto que no cuadra) o ``None`` (no verificable). Los pagos
    confirmados y los de pago móvil no generan ticket. Nunca lanza.

    ``plan_tipo`` / ``boost_*`` describen qué se estaba pagando, para que el
    admin pueda confirmarlo o suspenderlo desde la propia alerta.
    """
    if not es_binance or confirmado is True:
        return
    try:
        from backend.verificacion_pagos import (
            concepto_boost,
            concepto_plan,
            describir_revision_pago,
            registrar_revision_pago,
        )

        concepto = None
        if plan_tipo:
            concepto = concepto_plan(plan_tipo)
        elif boost_dias and boost_producto_id:
            concepto = concepto_boost(boost_dias, boost_producto_id)

        motivo, mensaje = describir_revision_pago(
            'binance',
            confirmado,
            referencia=referencia,
            monto=monto_usd,
            moneda='USDT',
            fecha_comprobante=(ocr or {}).get('fecha_comprobante'),
            nombre_comercio=(comercio or {}).get('nombre'),
            comercio_id=(comercio or {}).get('id'),
        )
        if not mensaje:
            return
        registrar_revision_pago(
            (comercio or {}).get('id'),
            referencia,
            motivo,
            mensaje,
            correo=session.get('correo') or (comercio or {}).get('owner_email'),
            usuario_id=session.get('usuario_id'),
            comprobante_url=comprobante_url,
            monto=monto_usd,
            concepto=concepto,
        )
    except Exception as error:
        logger.warning(f'[Localis Pagos] no se registró la revisión: {error}')


@bp.route('/tasa-bcv')
def api_tasa_bcv_actual():
    """Tasa vigente en caché (sin forzar red) + metadatos de la caché."""
    return jsonify({'tasa_bcv': obtener_tasa_bcv(), 'cache': estado_tasa_bcv()}), 200


@bp.route('/actualizar-tasa', methods=['POST'])
@limite('10 per minute')
@exento_csrf
def api_actualizar_tasa():
    """Fuerza la sincronizacion manual de la tasa oficial del BCV (solo POST).

    Requiere sesion iniciada. Antes aceptaba GET sin autenticacion, lo que
    permitia dispararlo desde un `<img>`/enlace (abuso de recursos). Devuelve
    200 si la tasa es fresca; 503 si se sirvio desde un respaldo. Nunca
    propaga excepciones a Flask.
    """
    if 'usuario_id' not in session:
        return jsonify({'ok': False, 'error': 'No autorizado.'}), 401
    try:
        from backend.tasa_automatica import sincronizar_tasa_diaria

        # Fuerza la consulta a la web del BCV y **guarda** el valor (2 decimales)
        # como tasa del día, igual que la sincronización automática.
        resultado = sincronizar_tasa_diaria(forzar=True)
        return (
            jsonify(
                {
                    'ok': bool(resultado.get('ok')),
                    'sincronizacion': resultado,
                    'tasa_bcv': obtener_tasa_bcv(),
                }
            ),
            200 if resultado.get('ok') else 503,
        )
    except Exception as error:
        return jsonify({'ok': False, 'error': f'{type(error).__name__}: {error}'}), 500


@bp.route('/producto/<int:producto_id>')
def api_producto(producto_id):
    producto = obtener_producto_publico(producto_id)
    if not producto:
        return jsonify({'error': 'Producto no encontrado'}), 404
    try:
        producto['imagen_url'] = url_imagen_producto_segura(producto)
    except Exception:
        traceback.print_exc()
        producto['imagen_url'] = PLACEHOLDER_PRODUCTO
    # Contacto directo al WhatsApp del comercio con el producto en el mensaje.
    try:
        nombre_producto = (producto.get('nombre') or 'un producto').strip()
        producto['comercio_whatsapp_url'] = url_whatsapp_comercio(
            producto.get('comercio_telefono'),
            f'¡Hola! Vi "{nombre_producto}" en Localis y me interesa.',
        )
    except Exception:
        producto['comercio_whatsapp_url'] = None
    return jsonify(producto)


@bp.route('/me')
@login_requerido_api
def api_me():
    """Perfil JSON del usuario autenticado (rol actual y ``has_store``).

    Endpoint aditivo: no reemplaza ``/perfil`` ni altera respuestas existentes.
    Permite a los clientes saber si deben mostrar interfaces de administración.
    """
    usuario_id = session.get('usuario_id')
    es_admin = bool(session.get('es_admin'))

    usuario = None
    try:
        usuario = obtener_usuario_por_id(usuario_id)
    except Exception as error:
        logger.error(f'[Localis API] /api/me usuario fallo: {type(error).__name__}: {error}')
        usuario = None

    has_store = False
    comercio_id = None
    try:
        has_store = bool(es_usuario_comerciante(usuario_id))
        if has_store:
            comercio = obtener_comercio_por_usuario(usuario_id)
            comercio_id = comercio.get('id') if comercio else None
    except Exception as error:
        logger.error(f'[Localis API] /api/me comercio fallo: {type(error).__name__}: {error}')
        has_store = False

    if es_admin:
        rol = 'admin'
    elif has_store:
        rol = 'comerciante'
    else:
        rol = (usuario or {}).get('rol') or session.get('rol') or 'user'

    return jsonify(
        {
            'id': usuario_id,
            'nombre': (usuario or {}).get('nombre') or session.get('username'),
            'correo': (usuario or {}).get('correo') or session.get('correo'),
            'foto_url': (usuario or {}).get('foto_url') or session.get('foto_url'),
            'rol': rol,
            'es_admin': es_admin,
            'has_store': bool(has_store),
            'comercio_id': comercio_id,
        }
    )


@bp.route('/comercio/<int:comercio_id>/interaccion', methods=['POST'])
@exento_csrf
def api_registrar_interaccion(comercio_id):
    """Beacon público de interés (visitas y clics) ligado al comercio.

    Respuesta 204 sin cuerpo: nunca bloquea ni rompe la navegación del cliente.
    """
    cuerpo = request.get_json(silent=True)
    if not isinstance(cuerpo, dict):
        cuerpo = request.form
    tipo = normalizar_tipo(cuerpo.get('tipo') if hasattr(cuerpo, 'get') else '')
    if not tipo:
        return ('', 204)
    producto_raw = cuerpo.get('producto_id') if hasattr(cuerpo, 'get') else None
    origen = cuerpo.get('origen') if hasattr(cuerpo, 'get') else ''

    producto_id = None
    if producto_raw not in (None, '', 'null', 'None'):
        try:
            producto_id = int(producto_raw)
        except (TypeError, ValueError):
            producto_id = None

    huella = f'{request.remote_addr}|{(request.headers.get("User-Agent") or "")[:40]}'
    registrar_interaccion(
        comercio_id,
        tipo,
        producto_id=producto_id,
        origen=origen,
        huella=huella,
    )
    return ('', 204)


@bp.route('/pwa/instalada', methods=['POST'])
@exento_csrf
def api_pwa_instalada():
    """Beacon público: la PWA se instaló (evento ``appinstalled``).

    Respuesta 204 sin cuerpo: nunca bloquea ni rompe la navegación del cliente.
    """
    from backend import trafico

    trafico.registrar_evento('pwa_instalaciones')
    return ('', 204)


@bp.route('/suscripcion/confirmar-pago', methods=['POST'])
@limite('10 per minute')
@admin_requerido_api
def api_confirmar_pago():
    """Confirmación manual de pago — solo administradores."""
    data = request.get_json(silent=True) or {}
    comercio_id = data.get('comercio_id')
    plan_tipo = data.get('plan_tipo', 'basica')
    meses, error_meses = parsear_entero_form(data.get('meses', 1), default=1, minimo=1)
    if error_meses:
        return jsonify({'ok': False, 'mensaje': error_meses}), 400

    if not comercio_id:
        return jsonify({'ok': False, 'mensaje': 'comercio_id requerido'}), 400

    exito, mensaje = confirmar_pago_suscripcion(comercio_id, plan_tipo, meses)
    return jsonify({'ok': exito, 'mensaje': mensaje}), 200 if exito else 400


@bp.route('/pagos/cotizacion')
@login_requerido_api
def api_cotizacion_pago():
    comercio = comercio_sesion_validado()
    plan_tipo = (request.args.get('plan') or 'basica').lower()
    if comercio:
        cotizacion = calcular_cotizacion_cambio_plan(comercio, plan_tipo)
        if not cotizacion:
            return jsonify({'error': 'Plan no válido.'}), 400
        datos = obtener_datos_pago_movil(plan_tipo)
        return jsonify({'ok': True, **datos, **cotizacion}), 200

    montos = calcular_monto_pago_plan(plan_tipo)
    if not montos:
        return jsonify({'error': 'Plan no válido.'}), 400
    datos = obtener_datos_pago_movil(plan_tipo)
    return jsonify({'ok': True, **datos, **montos}), 200


@bp.route('/pagos/programar-cambio', methods=['POST'])
@limite('10 per minute')
@login_requerido_api
def api_programar_cambio_plan():
    comercio = comercio_sesion_validado()
    if not comercio:
        return jsonify({'error': 'Selecciona un comercio válido en tu cuenta.'}), 403

    # --- RESTAURAR_PAGOS_INICIO ---
    if not pagos_habilitados():
        return jsonify({'error': MENSAJE_PAGOS_CONGELADOS}), 400
    # --- RESTAURAR_PAGOS_FIN ---

    plan_tipo = (
        request.form.get('plan_tipo')
        or (request.get_json(silent=True) or {}).get('plan_tipo')
        or ''
    ).lower()
    cotizacion = calcular_cotizacion_cambio_plan(comercio, plan_tipo)
    if not cotizacion:
        return jsonify({'error': 'Plan no válido.'}), 400
    if cotizacion['tipo_cambio'] != 'downgrade':
        return jsonify(
            {'error': 'Este cambio requiere pago. Usa el flujo de comprobante.'}
        ), 400

    exito, mensaje, datos = programar_downgrade_plan(comercio['id'], plan_tipo)
    if not exito:
        return jsonify({'error': mensaje}), 400

    return jsonify({'ok': True, 'mensaje': mensaje, **(datos or {})}), 200


@bp.route('/productos/crear', methods=['POST'])
@limite('30 per minute')
@login_requerido_api
def api_crear_producto():
    comercio = comercio_sesion_validado()
    if not comercio:
        return jsonify({'error': 'Selecciona un comercio válido en tu cuenta.'}), 403

    tienda_id = comercio['id']
    # --- RESTAURAR_PAGOS_INICIO ---
    if pagos_habilitados():
        limite = obtener_limite_productos_comercio(tienda_id)
        total_productos = contar_productos_comercio(tienda_id)
        if not es_limite_ilimitado(limite) and total_productos >= limite:
            return jsonify({'error': MENSAJE_LIMITE_PRODUCTOS}), 400
        ok_estado, msg_estado = puede_agregar_producto(tienda_id)
        if not ok_estado:
            return jsonify({'error': msg_estado}), 400
    # --- RESTAURAR_PAGOS_FIN ---

    nombre = (request.form.get('nombre') or '').strip()
    descripcion = (request.form.get('descripcion') or '').strip()
    precio_raw = request.form.get('precio_usd')
    codigo_barras = normalizar_codigo_barras(request.form.get('codigo_barras'))
    imagen_archivo = request.files.get('imagen')
    imagen_url_form = request.form.get('imagen_url')

    if not nombre or not nombre.strip():
        return jsonify({'error': 'El nombre es obligatorio.'}), 400

    precio_usd, error_precio = parsear_precio_form(precio_raw)
    if error_precio:
        return jsonify({'error': error_precio}), 400

    aviso_img = None
    imagen_url = None
    try:
        imagen_url, aviso_img = persistir_imagen_producto_hibrida(
            file_storage=imagen_archivo,
            codigo_barras=codigo_barras,
            nombre=nombre,
            descripcion=descripcion,
            comercio_id=tienda_id,
            imagen_url_form=imagen_url_form,
        )
        if aviso_img:
            logger.info(f'[Localis Imagen] api producto aviso: {aviso_img}')
    except Exception as error:
        logger.error(f'[Localis Imagen] api producto hibrido: {error}')
        traceback.print_exc()
        imagen_url = imagen_url_para_persistir(imagen_url_form)

    def _insertar(conexion):
        cursor = conexion.cursor()
        cursor.execute(
            '''
            INSERT INTO productos (comercio_id, nombre, descripcion, precio_usd, codigo_barras, imagen_url)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            ''',
            (tienda_id, nombre, descripcion, precio_usd, codigo_barras, imagen_url),
        )
        fila = cursor.fetchone()
        producto_id = fila[0] if fila else None
        if producto_id:
            try:
                from backend.codigos_publicos import asignar_codigo_producto

                asignar_codigo_producto(cursor, producto_id)
            except Exception as error:
                print(
                    f'[Localis Codigos] producto {producto_id}: '
                    f'{type(error).__name__}: {error}',
                    flush=True,
                )
        return producto_id

    try:
        from backend.db import ejecutar_con_reintentos_bd

        producto_id = ejecutar_con_reintentos_bd(_insertar)
    except Exception as error:
        return jsonify({'error': f'Error al agregar producto: {error}'}), 500

    sincronizar_foto_local_si_aplica(producto_id, imagen_url)
    registrar_imagen_manual(producto_id, imagen_url)
    asegurar_slug_producto(producto_id, nombre)
    return jsonify(
        {
            'ok': True,
            'mensaje': 'Producto agregado con éxito.',
            'producto_id': producto_id,
            'imagen_url': imagen_url,
            **({'aviso': aviso_img} if aviso_img else {}),
        }
    ), 201


@bp.route('/boost/verificar', methods=['POST'])
@limite('10 per minute')
@login_requerido_api
def api_verificar_boost():
    """Valida el pago (móvil o Binance Pay) y activa el destacado de un producto.

    Réplica EXACTA del flujo de mensualidades (``/api/pagos/verificar``):
    comprobante + OCR antifraude + anti-reutilización de referencia/comprobante.
    Con ``metodo_boost=binance`` se valida la captura de Binance Pay y, si las
    llaves están configuradas, se confirma la orden contra la API de Binance.
    """
    comercio = comercio_sesion_validado()
    if not comercio:
        return jsonify({'error': 'Selecciona un comercio válido en tu cuenta.'}), 403

    producto_raw = request.form.get('producto_id')
    try:
        producto_id = int(str(producto_raw).strip())
    except (TypeError, ValueError):
        return jsonify({'error': 'Producto no válido.'}), 400

    dias_raw = request.form.get('dias')
    try:
        dias = int(str(dias_raw).strip())
    except (TypeError, ValueError):
        return jsonify({'error': 'Duración no válida. Elige 3, 5 o 7 días.'}), 400

    precio_usd = precio_boost(dias)
    if not precio_usd:
        return jsonify({'error': 'Duración no válida. Elige 3, 5 o 7 días.'}), 400

    metodo_pago = (
        'binance'
        if (request.form.get('metodo_boost') or '').strip().lower() == 'binance'
        else 'pago_movil'
    )
    es_binance = metodo_pago == 'binance'

    # a) Propiedad inequívoca: el producto debe ser del comercio de la sesión.
    if not producto_pertenece_a_comercio(producto_id, comercio['id']):
        return jsonify({'error': 'El producto no pertenece a tu comercio.'}), 403

    archivo = request.files.get('comprobante')
    if not archivo or not archivo.filename:
        return jsonify({'error': 'Debes adjuntar la captura del comprobante de pago.'}), 400

    from backend.images import (
        ImageProcessingError,
        comprimir_bytes_a_bytes,
        leer_bytes_limitados,
    )

    data_bytes, error_lectura = leer_bytes_limitados(archivo)
    if error_lectura:
        return jsonify({'error': error_lectura}), 400

    hash_comprobante = calcular_hash_comprobante(data_bytes)
    if comprobante_ya_usado(hash_comprobante):
        return jsonify({'error': 'Este comprobante ya fue registrado en el sistema.'}), 400

    try:
        tasa = float(obtener_tasa_dolar() or 1.0)
    except (TypeError, ValueError):
        tasa = 1.0
    monto_bs = round(float(precio_usd) * tasa, 2)

    # b) OCR estricto. Pago móvil: banco nacional, RIF/cédula y teléfono
    #    destino, monto exacto y fecha reciente. Binance: monto en USDT, fecha
    #    reciente e ID de orden.
    with OCR_SEMAFORO:
        if es_binance:
            ocr = validar_comprobante_binance(data_bytes, float(precio_usd))
        else:
            destino = _destino_pago_movil()
            ocr = validar_comprobante_pago_movil(
                data_bytes,
                monto_bs,
                banco=destino.get('banco'),
                rif=destino.get('cedula_rif'),
                telefono=destino.get('telefono'),
            )
    if not ocr.get('ok'):
        return (
            jsonify(
                {
                    'error': ' '.join(ocr.get('errores') or ['Comprobante no válido.']),
                    'ocr_ms': round(ocr.get('ms', 0), 1),
                    'estado_monto': ocr.get('estado_monto'),
                }
            ),
            400,
        )

    referencia = ocr['referencia']

    # b.2) Verificación REAL contra Binance (si la API está configurada). La
    #      captura sola no prueba que el dinero llegó. True=confirmado;
    #      False=no está; None=no se pudo verificar (no bloquea: queda el OCR).
    confirmado = None
    if es_binance:
        from backend.binance_api import verificar_orden_binance

        confirmado = verificar_orden_binance(referencia, float(precio_usd))
        if confirmado is False:
            _registrar_revision_pago(
                comercio,
                referencia,
                es_binance,
                confirmado,
                float(precio_usd),
                ocr=ocr,
                boost_dias=dias,
                boost_producto_id=producto_id,
            )
            return (
                jsonify(
                    {
                        'error': (
                            'No encontramos ese pago en USDT en Binance. Verifica '
                            'que el ID de la orden sea correcto y que el monto sea '
                            'el exacto.'
                        ),
                        'ocr_ms': round(ocr.get('ms', 0), 1),
                    }
                ),
                400,
            )

    comprobante_url = None
    aviso_pago = None
    try:
        comprimido = comprimir_bytes_a_bytes(
            data_bytes,
            prefijo=f'boost_{comercio["id"]}',
            max_dimension=1920,
        )
        payload, content_type, filename = comprimido
        comprobante_url, aviso_pago = intentar_subir_bytes(
            payload,
            filename=filename,
            content_type=content_type,
            carpeta='pagos',
        )
        if aviso_pago:
            logger.info(f'[Localis Storage] comprobante boost en modo hibrido: {aviso_pago}')
    except ImageProcessingError as error:
        return jsonify({'error': str(error)}), 400
    except Exception as error:
        logger.error(f'[Localis Boost] no se pudo subir el comprobante: {error}')

    # c) Activación atómica: auditoría + referencia única + boost del producto.
    #    Se conserva el valor histórico 'pago_movil_ocr' en la tabla boosts.
    exito, mensaje = activar_boost_producto(
        comercio['id'],
        producto_id,
        dias,
        referencia,
        comprobante_url=comprobante_url,
        comprobante_hash=hash_comprobante,
        metodo=('binance' if es_binance else 'pago_movil_ocr'),
    )
    if not exito:
        return jsonify({'error': mensaje, 'ocr_ms': round(ocr.get('ms', 0), 1)}), 400

    # Auditoría: deja constancia de cómo se verificó el pago (API Binance u OCR).
    try:
        from backend.verificacion_pagos import guardar_verificacion

        guardar_verificacion(
            comercio['id'],
            referencia,
            'binance' if es_binance else 'pago_movil_ocr',
            confirmado=confirmado,
            estado_monto=ocr.get('estado_monto'),
            monto=float(precio_usd) if es_binance else ocr.get('monto_bs'),
        )
    except Exception as error:
        logger.warning(f'[Localis Boost] auditoría de verificación omitida: {error}')

    # Un pago no confirmado (API caída, sin monto) queda en la bandeja del admin.
    _registrar_revision_pago(
        comercio,
        referencia,
        es_binance,
        confirmado,
        float(precio_usd),
        ocr=ocr,
        comprobante_url=comprobante_url,
        boost_dias=dias,
        boost_producto_id=producto_id,
    )

    return (
        jsonify(
            {
                'ok': True,
                'mensaje': mensaje,
                'producto_id': producto_id,
                'dias': dias,
                'referencia': referencia,
                'metodo': metodo_pago,
                'monto_usd': float(precio_usd),
                'monto_bs': monto_bs,
                'tasa': tasa,
                'fecha_comprobante': ocr.get('fecha_comprobante'),
                'ocr_ms': round(ocr.get('ms', 0), 1),
                **({'aviso': aviso_pago} if aviso_pago else {}),
            }
        ),
        200,
    )


@bp.route('/pagos/verificar', methods=['POST'])
@limite('10 per minute')
@login_requerido_api
def api_verificar_pago():
    comercio = comercio_sesion_validado()
    if not comercio:
        return jsonify({'error': 'Selecciona un comercio válido en tu cuenta.'}), 403

    # --- RESTAURAR_PAGOS_INICIO ---
    if not pagos_habilitados():
        return jsonify(
            {
                'error': (
                    MENSAJE_PAGOS_CONGELADOS
                    + ' No necesitas subir ningún comprobante de pago.'
                )
            }
        ), 400
    # --- RESTAURAR_PAGOS_FIN ---

    plan_tipo = (request.form.get('plan_tipo') or 'basica').lower()
    cotizacion = calcular_cotizacion_cambio_plan(comercio, plan_tipo)
    if not cotizacion:
        return jsonify({'error': 'Plan no válido para pago.'}), 400

    if cotizacion['tipo_cambio'] == 'downgrade':
        exito, mensaje, datos = programar_downgrade_plan(comercio['id'], plan_tipo)
        if not exito:
            return jsonify({'error': mensaje}), 400
        return jsonify({'ok': True, 'mensaje': mensaje, **(datos or {})}), 200

    archivo = request.files.get('comprobante')
    if not archivo or not archivo.filename:
        return jsonify({'error': 'Debes adjuntar la captura del comprobante de pago.'}), 400

    from backend.images import (
        ImageProcessingError,
        comprimir_bytes_a_bytes,
        leer_bytes_limitados,
    )

    data_bytes, error_lectura = leer_bytes_limitados(archivo)
    if error_lectura:
        return jsonify({'error': error_lectura}), 400

    hash_comprobante = calcular_hash_comprobante(data_bytes)
    if comprobante_ya_usado(hash_comprobante):
        return jsonify(
            {'error': 'Este comprobante ya fue registrado en el sistema.'}
        ), 400

    montos = calcular_monto_pago_plan(plan_tipo, comercio)
    if not montos:
        return jsonify({'error': 'Plan no válido para pago.'}), 400

    metodo = (request.form.get('metodo') or 'pago_movil').strip().lower()
    es_binance = metodo == 'binance'
    with OCR_SEMAFORO:
        if es_binance:
            ocr = validar_comprobante_binance(data_bytes, montos.get('monto_usd'))
        else:
            destino = _destino_pago_movil()
            ocr = validar_comprobante_pago_movil(
                data_bytes,
                montos['monto_bs'],
                banco=destino.get('banco'),
                rif=destino.get('cedula_rif'),
                telefono=destino.get('telefono'),
            )
    if not ocr.get('ok'):
        return jsonify(
            {
                'error': ' '.join(ocr.get('errores') or ['Comprobante no válido.']),
                'ocr_ms': round(ocr.get('ms', 0), 1),
            }
        ), 400

    referencia = ocr['referencia']

    # Verificación REAL contra Binance (si la API está configurada): la captura
    # sola NO prueba que el dinero llegó. True=confirmado; False=no está; None=no
    # se pudo verificar (no bloquea; el OCR sigue siendo el filtro).
    confirmado = None
    if es_binance:
        from backend.binance_api import verificar_orden_binance

        confirmado = verificar_orden_binance(referencia, montos.get('monto_usd'))
        if confirmado is False:
            _registrar_revision_pago(
                comercio,
                referencia,
                es_binance,
                confirmado,
                montos.get('monto_usd'),
                ocr=ocr,
                plan_tipo=plan_tipo,
            )
            return (
                jsonify(
                    {
                        'error': (
                            'No encontramos ese pago en USDT en Binance. Verifica '
                            'que el ID de la orden sea correcto y que el monto sea '
                            'el exacto.'
                        ),
                        'ocr_ms': round(ocr.get('ms', 0), 1),
                    }
                ),
                400,
            )

    aviso_pago = None
    comprobante_url = None

    try:
        comprimido = comprimir_bytes_a_bytes(
            data_bytes,
            prefijo=f'pago_{comercio["id"]}',
            max_dimension=1920,
        )
        payload, content_type, filename = comprimido
        comprobante_url, aviso_pago = intentar_subir_bytes(
            payload,
            filename=filename,
            content_type=content_type,
            carpeta='pagos',
        )
        if aviso_pago:
            logger.info(f'[Localis Storage] comprobante en modo hibrido: {aviso_pago}')
    except ImageProcessingError as error:
        return jsonify({'error': str(error)}), 400

    exito, mensaje, datos = activar_suscripcion_por_comprobante(
        comercio['id'],
        plan_tipo,
        referencia,
        comprobante_url=comprobante_url,
        monto_ocr_bs=ocr.get('monto_bs'),
        comprobante_hash=hash_comprobante,
        metodo=('binance' if es_binance else 'pago_movil_ocr'),
    )

    if not exito:
        return jsonify({'error': mensaje, 'ocr_ms': round(ocr.get('ms', 0), 1)}), 400

    # Auditoría: deja constancia de cómo se verificó el pago (API Binance u OCR).
    try:
        from backend.verificacion_pagos import guardar_verificacion

        guardar_verificacion(
            comercio['id'],
            referencia,
            'binance' if es_binance else 'pago_movil_ocr',
            confirmado=confirmado,
            estado_monto=ocr.get('estado_monto'),
            monto=montos.get('monto_usd') if es_binance else ocr.get('monto_bs'),
        )
    except Exception as error:
        logger.warning(f'[Localis Pagos] auditoría de verificación omitida: {error}')

    # Un pago no confirmado (API caída, sin monto) queda en la bandeja del admin.
    _registrar_revision_pago(
        comercio,
        referencia,
        es_binance,
        confirmado,
        montos.get('monto_usd'),
        ocr=ocr,
        comprobante_url=comprobante_url,
        plan_tipo=plan_tipo,
    )

    respuesta = {
        'ok': True,
        'mensaje': mensaje,
        'referencia': referencia,
        'ocr_ms': round(ocr.get('ms', 0), 1),
        'fecha_comprobante': ocr.get('fecha_comprobante'),
        'estado': datos.get('estado', 'activo'),
        'fecha_vencimiento': datos.get('fecha_vencimiento'),
        'plan_tipo': datos.get('plan_tipo', plan_tipo),
        'monto_usd': datos.get('monto_usd'),
        'monto_bs': datos.get('monto_bs'),
        'tasa': datos.get('tasa'),
        'comprobante_url': comprobante_url,
        **({'aviso': aviso_pago} if aviso_pago else {}),
    }
    return jsonify(respuesta), 200

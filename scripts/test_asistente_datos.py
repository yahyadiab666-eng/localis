#!/usr/bin/env python3
"""Datos en vivo del asistente: funciones puras y saltos seguros. Sin red ni BD."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

_ERRORES = []


def _ok(condicion, mensaje):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    _ERRORES.append(mensaje)
    return False


@contextmanager
def _pagos(valor):
    """Fija LOCALIS_PAGOS_HABILITADOS durante el bloque y restaura al salir."""
    previo = os.environ.get('LOCALIS_PAGOS_HABILITADOS')
    os.environ['LOCALIS_PAGOS_HABILITADOS'] = valor
    try:
        yield
    finally:
        if previo is None:
            os.environ.pop('LOCALIS_PAGOS_HABILITADOS', None)
        else:
            os.environ['LOCALIS_PAGOS_HABILITADOS'] = previo


def _probar_keywords():
    print('\n=== Palabras con contenido ===')
    from backend.asistente.datos_vivos import _keywords

    _ok(
        _keywords('¿Cuanto cuesta el Arroz Mary 1kg?') == ['arroz', 'mary', '1kg'],
        'quita stopwords (cuanto, cuesta, el) y acentos',
    )
    _ok(_keywords('') == [], 'vacio -> lista vacia')
    _ok(_keywords(None) == [], 'None -> lista vacia')


def _probar_menciones():
    print('\n=== Deteccion de tema ===')
    from backend.asistente.datos_vivos import (
        menciona_categoria,
        menciona_comercio,
        menciona_imagenes,
        menciona_importacion,
        menciona_pago,
        menciona_tienda,
    )

    _ok(menciona_comercio('Cuantos productos permite mi plan?'), 'plan -> comercio')
    _ok(not menciona_comercio('cuanto cuesta el arroz'), 'arroz -> NO comercio')
    _ok(menciona_imagenes('por que mi producto no tiene foto'), 'foto -> imagenes')
    _ok(not menciona_imagenes('cuanto cuesta el arroz'), 'arroz -> NO imagenes')
    _ok(menciona_importacion('mi importacion quedo pendiente'), 'importacion')
    _ok(menciona_pago('necesito revisar mi pago movil'), 'pago')
    _ok(menciona_tienda('donde queda la tienda?'), 'tienda')
    _ok(menciona_categoria('que categorias hay?'), 'categoria')


def _probar_tiendas():
    print('\n=== Formato de tiendas ===')
    from backend.asistente.datos_vivos import formatear_tiendas

    texto = formatear_tiendas(
        [
            {'nombre': 'Abastos El Sol', 'ciudad': 'Porlamar', 'zona': 'Centro',
             'telefono': '04121234567'},
            {'nombre': '', 'ciudad': 'X'},
        ]
    )
    _ok('Abastos El Sol - Centro, Porlamar | WhatsApp 04121234567' in texto, 'tienda con ubicacion y contacto')
    _ok(texto.count('\n') == 0, 'ignora tiendas sin nombre')
    _ok(formatear_tiendas([]) == '', 'lista vacia -> cadena vacia')


def _probar_presupuesto():
    print('\n=== Presupuesto diario (desactivado por defecto) ===')
    from backend.asistente import presupuesto

    _ok(presupuesto.disponible() is True, 'desactivado -> siempre disponible')
    presupuesto.registrar(1000)
    _ok(presupuesto.estado()['limite'] == 0, 'limite 0 (desactivado)')


def _probar_observacion():
    print('\n=== Observacion del asistente ===')
    from backend.asistente import observacion

    observacion.registrar_mensaje(True, 100)
    observacion.registrar_mensaje(False, 300)
    observacion.registrar_feedback(True)
    estado = observacion.estado()
    _ok(estado['mensajes'] == 2, 'cuenta mensajes')
    _ok(estado['respuestas_ok'] == 1 and estado['respuestas_error'] == 1, 'cuenta ok/error')
    _ok(estado['ms_promedio'] == 200.0, 'promedio de latencia')
    _ok(estado['feedback_util'] == 1, 'cuenta feedback')


def _probar_venta():
    print('\n=== Modo venta (planes/precios/beneficios) ===')
    from backend.asistente import venta

    _ok('COMPRADOR' in venta.MODO_VENTA_CLIENTE, 'modo venta de comprador')
    _ok('COMERCIANTE' in venta.MODO_VENTA_COMERCIANTE, 'modo venta de comerciante')
    _ok(venta.modo_venta('cliente') == venta.MODO_VENTA_CLIENTE, 'rol cliente -> comprador')
    _ok(venta.modo_venta('invitado') == venta.MODO_VENTA_CLIENTE, 'invitado -> comprador')
    _ok(
        venta.modo_venta('invitado', 'que es localis') == venta.MODO_VENTA_CLIENTE,
        'invitado sin tema de negocio -> comprador',
    )
    _ok(venta.es_tema_comercial('¿Cuanto cuesta el plan Pro?'), 'plan -> venta')
    _ok(venta.es_tema_comercial('Que beneficios tiene Localis?'), 'beneficios -> venta')
    _ok(venta.es_tema_comercial('Es gratis?'), 'es gratis -> venta')
    _ok(venta.es_tema_comercial('Quiero vender en Localis'), 'quiero vender -> venta')
    _ok(not venta.es_tema_comercial('Cuanto cuesta la harina pan?'), 'precio de producto -> NO venta')
    _ok(not venta.es_tema_comercial('Mi importacion fallo'), 'soporte -> NO venta')
    _ok(not venta.es_tema_comercial('vendo plantas'), 'planta -> NO venta (limite de palabra)')
    _ok(venta.es_tema_comercial(None) is False, 'None -> False')

    # Planes ACTIVOS: el comerciante recibe el modo con planes/precios.
    with _pagos('1'):
        _ok(
            venta.modo_venta('comerciante') == venta.MODO_VENTA_COMERCIANTE,
            'planes activos: comerciante -> su modo',
        )
        _ok(
            venta.modo_venta('invitado', 'cuanto cuesta el plan Pro') == venta.MODO_VENTA_COMERCIANTE,
            'planes activos: pregunta por planes -> comerciante',
        )
    # FASE GRATUITA (planes ocultos): el comerciante NO recibe planes.
    with _pagos('0'):
        _ok(
            venta.modo_venta('comerciante') == venta.MODO_VENTA_COMERCIANTE_GRATIS,
            'fase gratuita: comerciante -> modo sin planes',
        )
        _ok(
            'gratis' in venta.MODO_VENTA_COMERCIANTE_GRATIS.lower()
            and '10 USD' not in venta.MODO_VENTA_COMERCIANTE_GRATIS,
            'fase gratuita: destaca que es gratis (sin precios de plan)',
        )


def _probar_reintento_pagos():
    print('\n=== Reintento automatico de pagos (no lanza) ===')
    from backend.asistente import pagos

    _ok(isinstance(pagos._reintento_habilitado(), bool), 'estado del reintento es booleano')
    pagos.reintento_oportunista()  # sin BD -> lista vacia, sin excepcion
    _ok(True, 'reintento_oportunista no lanza sin BD')
    _ok(pagos.asegurar_reintento_activo() is None, 'asegurar no lanza')


def _probar_pagos():
    print('\n=== Soporte de pagos (rutas puras) ===')
    from backend.asistente import pagos

    _ok(pagos.estado_pago(None) == '', 'sin comercio -> cadena vacia')
    _ok(pagos.proponer(None, 'revisar mi pago') is None, 'sin comercio no propone')
    _ok(pagos.proponer(1, 'hola') is None, 'sin claves de pago no propone')
    _ok(pagos.reverificar(None, None)[0] is False, 'sin sesion no ejecuta')
    _ok(pagos.reverificar(1, None)[0] is False, 'sin comercio no ejecuta')


def _probar_reactivacion_pagos():
    print('\n=== Reactivacion de pagos (concepto y guardas) ===')
    from backend.asistente import pagos

    _ok(
        pagos._concepto_de({'concepto': 'plan:pro'}) == {'tipo': 'plan', 'plan_tipo': 'pro'},
        'interpreta concepto de plan',
    )
    _ok(
        pagos._concepto_de({'concepto': 'boost:7:prod:12'})
        == {'tipo': 'boost', 'dias': 7, 'producto_id': 12},
        'interpreta concepto de destacado',
    )
    _ok(pagos._concepto_de({'concepto': 'basura'}) is None, 'concepto raro -> None')
    _ok(pagos._concepto_de(None) is None, 'sin concepto -> None')

    # Ante un error de BD, ``_ya_aplicado`` es conservador (True) para no duplicar
    # activaciones. Se fuerza el fallo de conexion para no depender del entorno.
    from unittest.mock import patch as _patch

    with _patch('backend.db.get_db_connection', side_effect=RuntimeError('sin BD')):
        _ok(
            pagos._ya_aplicado(1, '123456') is True,
            'ante error de BD asume ya aplicado (conservador)',
        )

    # Guardas que no tocan la BD.
    _ok(
        pagos._activar_concepto(1, {'tipo': 'plan', 'plan_tipo': 'gratis'})[0] is False,
        'no reactiva un plan gratis',
    )
    _ok(
        pagos._activar_concepto(1, {'tipo': 'desconocido'})[0] is False,
        'concepto desconocido no reactiva',
    )
    _ok(
        pagos._reverificar_revision(1, {'referencia_pago': ''})[0] is False,
        'sin referencia no reverifica',
    )


def _probar_acciones():
    print('\n=== Acciones confirmadas (whitelist y deteccion) ===')
    from backend.asistente import acciones

    _ok(set(acciones.ACCIONES) == {
        'producto_desactivar', 'producto_activar', 'producto_reintentar_imagen',
        'producto_categoria',
    }, 'whitelist cerrada (4 acciones)')
    _ok(
        acciones.detectar('Desactiva el producto Arroz Mary') == ('producto_desactivar', 'Arroz Mary'),
        'detecta desactivar + nombre',
    )
    _ok(
        acciones.detectar('activa el producto arroz mary')[0] == 'producto_activar',
        'detecta activar',
    )
    _ok(
        acciones.detectar('reintenta la foto del producto arroz')[0] == 'producto_reintentar_imagen',
        'detecta reintentar imagen',
    )
    _ok(acciones.detectar('¿como hago una importacion?') is None, 'no dispara con preguntas')
    _ok(acciones.detectar('cuanto cuesta el arroz') is None, 'no dispara con precios')
    _ok(acciones.detectar('Desactiva el producto') is None, 'sin nombre -> no propone')
    _ok(acciones.detectar(None) is None, 'None no lanza')

    # Deteccion y resolucion de categoria.
    _ok(
        acciones.detectar_categoria('cambia la categoria del producto arroz a bebidas')
        == ('arroz', 'bebidas'),
        'detecta cambio de categoria (orden A)',
    )
    _ok(
        acciones.detectar_categoria('pon la categoria bebidas al producto arroz')
        == ('arroz', 'bebidas'),
        'detecta cambio de categoria (orden B)',
    )
    _ok(acciones._resolver_categoria('Bebidas') == 'bebidas', 'resuelve etiqueta -> clave')
    _ok(acciones._resolver_categoria('bebidas') == 'bebidas', 'resuelve clave directa')
    _ok(acciones._resolver_categoria('inventada') is None, 'categoria inexistente -> None')

    # Rechazos estrictos ANTES de tocar la BD.
    _ok(
        acciones.ejecutar(1, 1, 'borrar_todo', {})[0] is False,
        'rechaza accion fuera de la whitelist',
    )
    _ok(
        acciones.ejecutar(1, 1, 'producto_activar', {'producto_id': 'x'})[0] is False,
        'rechaza producto_id invalido',
    )
    _ok(
        acciones.ejecutar(1, 1, 'producto_categoria', 'no-dict')[0] is False,
        'rechaza parametros que no son dict',
    )
    _ok(
        acciones.ejecutar(1, None, 'producto_activar', {'producto_id': 1})[0] is False,
        'rechaza sin comercio en sesion',
    )
    _ok(
        acciones.ejecutar(None, None, 'producto_desactivar', {'producto_id': 1})[0] is False,
        'rechaza sin usuario',
    )
    _ok(
        acciones.ejecutar(1, 1, 'producto_categoria', {'producto_id': 1, 'categoria': 'xyz'})[0]
        is False,
        'rechaza categoria invalida',
    )


def _probar_formato():
    print('\n=== Formato de precios ===')
    from backend.asistente.datos_vivos import formatear_productos

    texto = formatear_productos(
        [
            {'nombre': 'Arroz Mary 1kg', 'precio_usd': 1.2, 'precio_bs': 66.0,
             'comercio_nombre': 'Abastos El Sol'},
            {'nombre': 'Sin precio', 'precio_usd': 0, 'precio_bs': 0,
             'comercio_nombre': ''},
            {'nombre': '', 'precio_usd': 5, 'comercio_nombre': 'X'},
        ]
    )
    _ok('Arroz Mary 1kg: 1.20 USD (Bs 66.00) - en Abastos El Sol' in texto, 'linea con USD y Bs')
    _ok('Sin precio: 0.00 USD' in texto, 'precio 0 no muestra Bs')
    _ok(texto.count('\n') == 1, 'ignora productos sin nombre')
    _ok(formatear_productos([]) == '', 'lista vacia -> cadena vacia')


def _probar_saltos_seguros():
    print('\n=== No busca catalogo en temas de documentacion ===')
    from backend.asistente.datos_vivos import HECHOS_CLAVE, buscar_precios

    _ok(buscar_precios('cuanto cuesta el plan Pro?') == '', 'plan -> no busca catalogo')
    _ok(buscar_precios('   ') == '', 'vacio -> cadena vacia')
    _ok(buscar_precios('a b c') == '', 'sin palabras utiles -> cadena vacia')
    _ok('10 USD' in HECHOS_CLAVE and '15 USD' in HECHOS_CLAVE, 'hechos clave con precios')
    _ok('35 USD' in HECHOS_CLAVE and 'ilimitados' in HECHOS_CLAVE, 'hechos clave con Business')


def _probar_hechos_clave_dinamicos():
    print('\n=== Hechos clave derivados de su fuente real ===')
    from backend.asistente.datos_vivos import (
        _resumen_destacados,
        _resumen_puntos,
        hechos_clave,
    )

    destacados = _resumen_destacados()
    _ok(
        '2 USD' in destacados and '3 USD' in destacados and '4 USD' in destacados,
        'destacados reales por duracion',
    )
    _ok('puntos' in _resumen_puntos().lower(), 'programa de puntos real')

    # Planes ACTIVOS: los datos clave incluyen planes y precios.
    with _pagos('1'):
        texto = hechos_clave()
        _ok(isinstance(texto, str) and bool(texto), 'planes activos: hechos_clave devuelve texto')
        _ok('10 USD' in texto and '15 USD' in texto, 'planes activos: precios de planes reales')
        _ok('35 USD' in texto and 'ilimitados' in texto, 'planes activos: incluye el plan Business')

    # FASE GRATUITA (planes ocultos): SIN planes, con destacados.
    with _pagos('0'):
        texto_gratis = hechos_clave()
        _ok(isinstance(texto_gratis, str) and bool(texto_gratis), 'fase gratuita: hechos_clave devuelve texto')
        _ok(
            '10 USD' not in texto_gratis and '15 USD' not in texto_gratis
            and '35 USD' not in texto_gratis,
            'fase gratuita: SIN precios de planes en los datos clave',
        )
        _ok('2 USD' in texto_gratis and '4 USD' in texto_gratis, 'fase gratuita: mantiene destacados')


def main() -> int:
    _probar_keywords()
    _probar_menciones()
    _probar_formato()
    _probar_tiendas()
    _probar_saltos_seguros()
    _probar_hechos_clave_dinamicos()
    _probar_presupuesto()
    _probar_observacion()
    _probar_pagos()
    _probar_reactivacion_pagos()
    _probar_venta()
    _probar_reintento_pagos()
    _probar_acciones()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK datos en vivo del asistente')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

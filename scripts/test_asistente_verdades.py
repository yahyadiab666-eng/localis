#!/usr/bin/env python3
"""El asistente no debe decir cosas FALSAS sobre el estado actual de la app.

Sin red ni BD. Este script comprueba que la documentacion que alimenta al RAG
(``docs/asistente-*.md`` y las plantillas publicas) y los "datos clave" que se
inyectan en cada consulta siguen coincidiendo con las CONSTANTES reales del
codigo. La idea: si alguien cambia un precio, un limite, un nombre de boton o
un estado del panel, este test falla antes de que el asistente lo diga mal.

Que verifica:
  1. Planes: precios y limites de ``backend/plans.py`` aparecen en la FAQ.
  2. Destacados: precios (y descuento Binance) de ``backend/boosts.py``.
  3. Banner B2B: precio, descuento, dias y medidas de ``backend/banners_b2b.py``.
  4. Puntos: ``backend/referidos.py``.
  5. Fase gratuita: ni ``hechos_clave()`` ni el RAG mencionan planes ni precios
     de suscripcion.
  6. Panel admin: las acciones y estados reales de la bandeja
     (``backend/admin.py``) estan documentados con su nombre exacto.
  7. Los marcadores de bloque de los documentos estan balanceados.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest.mock import patch

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


def _leer(relativo):
    ruta = RAIZ / relativo
    try:
        return ruta.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return ''


def _sin_acentos(texto):
    """Minusculas, sin acentos y con los espacios colapsados.

    El colapso es importante: los documentos de Markdown parten las frases en
    varias lineas y un "4 puntos = 5\n  dias" no debe contar como otro texto.
    """
    import unicodedata

    base = unicodedata.normalize('NFKD', texto or '')
    plano = ''.join(c for c in base if not unicodedata.combining(c)).lower()
    return re.sub(r'\s+', ' ', plano)


FAQ = _leer('docs/asistente-faq.md')
GUIA = _leer('docs/asistente-app.md')
COMO_FUNCIONA = _leer('templates/como_funciona.html')
DOCS = '\n'.join([FAQ, GUIA, COMO_FUNCIONA])
DOCS_NORM = _sin_acentos(DOCS)


def _probar_planes():
    print('\n=== Planes (fuente: backend/plans.py) ===')
    from backend.plans import ORDEN_PLANES, PLANES, es_limite_ilimitado

    for codigo in ORDEN_PLANES:
        plan = PLANES.get(codigo) or {}
        precio = plan.get('precio_usd')
        limite = plan.get('limite_productos')
        if precio:
            _ok(
                f'{precio:.0f} USD' in FAQ,
                f'la FAQ cita el precio real del plan {codigo} ({precio:.0f} USD)',
            )
        if limite and not es_limite_ilimitado(limite):
            _ok(
                str(limite) in FAQ,
                f'la FAQ cita el limite real del plan {codigo} ({limite})',
            )
    _ok('ilimitad' in FAQ.lower(), 'la FAQ menciona el plan ilimitado')


def _probar_destacados():
    print('\n=== Destacados (fuente: backend/boosts.py) ===')
    from backend.boosts import PRECIOS_BOOST_USD, precio_con_descuento_binance

    for dias, precio in sorted(PRECIOS_BOOST_USD.items()):
        _ok(
            f'{dias} dias' in _sin_acentos(FAQ),
            f'la FAQ lista el destacado de {dias} dias',
        )
        _ok(
            f'{precio:.0f}' in FAQ,
            f'la FAQ cita el precio base del destacado de {dias} dias '
            f'({precio:.0f} USD)',
        )
        binance = precio_con_descuento_binance(precio, 'binance')
        _ok(
            f'{binance:.0f} USD' in FAQ,
            f'la FAQ cita el precio con Binance de {dias} dias ({binance:.0f} USD)',
        )


def _probar_banner():
    print('\n=== Banner B2B (fuente: backend/banners_b2b.py) ===')
    from backend.banners_b2b import (
        DIAS_BANNER,
        MAX_BANNERS_ACTIVOS,
        MEDIDAS_BANNER,
        PRECIO_BANNER_USD,
        precio_banner,
    )

    binance = precio_banner('binance')
    _ok(
        f'{PRECIO_BANNER_USD:.0f} USD' in DOCS,
        f'la documentacion cita el precio del banner ({PRECIO_BANNER_USD:.0f} USD)',
    )
    _ok(
        f'{binance:.0f} USD' in DOCS,
        f'la documentacion cita el precio con Binance ({binance:.0f} USD)',
    )
    _ok(
        f'{DIAS_BANNER} dias' in DOCS_NORM,
        f'la documentacion cita la duracion real ({DIAS_BANNER} dias)',
    )
    patron_medida = (
        rf"{MEDIDAS_BANNER['ancho']}\s*[x×]\s*{MEDIDAS_BANNER['alto']}"
    )
    _ok(
        re.search(patron_medida, DOCS_NORM) is not None,
        f'la documentacion cita la medida real del banner '
        f"({MEDIDAS_BANNER['ancho']}x{MEDIDAS_BANNER['alto']})",
    )
    _ok(
        str(MAX_BANNERS_ACTIVOS) in DOCS_NORM,
        f'la documentacion cita el maximo de banners ({MAX_BANNERS_ACTIVOS})',
    )


def _probar_puntos():
    print('\n=== Puntos (fuente: backend/referidos.py) ===')
    from backend.referidos import CANJES_PUNTOS, PUNTOS_POR_REFERIDO

    _ok(
        f'{PUNTOS_POR_REFERIDO} punto' in _sin_acentos(FAQ),
        'la FAQ cita los puntos por comercio referido',
    )
    for tier in CANJES_PUNTOS:
        patron = f"{tier['puntos']} puntos = {tier['dias']} dias"
        _ok(
            patron in _sin_acentos(FAQ),
            f'la FAQ cita el canje real "{patron}"',
        )


def _probar_fase_gratuita():
    print('\n=== Fase gratuita: nada de planes ===')
    from backend.asistente import datos_vivos, conocimiento

    with patch.dict(os.environ, {'LOCALIS_PAGOS_HABILITADOS': '0'}, clear=False):
        # El indice se construye UNA vez por worker (documentado); para probar
        # ambos modos en el mismo proceso hay que reconstruirlo.
        conocimiento._INDICE = None
        hechos = datos_vivos.hechos_clave()
        _ok('GRATUITO' in hechos.upper(), 'hechos_clave declara que Localis es gratuito')
        _ok('USD/mes' not in hechos, 'hechos_clave no da precios de suscripcion')
        _ok('Basico' not in hechos and 'Básico' not in hechos,
            'hechos_clave no nombra planes')

        contexto = conocimiento.contexto_para('cuanto cuesta el plan basico', k=6)
        _ok(
            '10 USD' not in contexto and 'Básico' not in contexto,
            'el RAG no devuelve precios de planes en la fase gratuita',
        )


def _probar_planes_activos():
    print('\n=== Planes activos: si se dicen, tienen que ser reales ===')
    from backend.asistente import datos_vivos, conocimiento
    from backend.plans import PLANES

    with patch.dict(os.environ, {'LOCALIS_PAGOS_HABILITADOS': '1'}, clear=False):
        conocimiento._INDICE = None
        hechos = datos_vivos.hechos_clave()
        precio_basico = PLANES['basica']['precio_usd']
        _ok(
            f'{precio_basico:.0f} USD' in hechos,
            'hechos_clave usa el precio real del plan Basico',
        )
        _ok('150' not in hechos, 'hechos_clave no inventa un precio de plan')

        contexto = conocimiento.contexto_para('cuanto cuesta el plan basico', k=6)
        _ok(
            '10 USD' in contexto,
            'el RAG recupera el precio del plan cuando los pagos estan activos',
        )


def _probar_admin():
    print('\n=== Panel admin: nombres reales de la bandeja ===')
    from backend.admin import (
        ACCIONES_SOLICITUD,
        _ETIQUETAS_ESTADO_SOLICITUD,
        acciones_ticket,
    )

    for clave, etiqueta in ACCIONES_SOLICITUD.items():
        _ok(
            etiqueta.lower() in _sin_acentos(DOCS),
            f'la etiqueta real "{etiqueta}" ({clave}) esta documentada',
        )
    for estado, etiqueta in _ETIQUETAS_ESTADO_SOLICITUD.items():
        _ok(
            etiqueta.lower() in _sin_acentos(DOCS),
            f'el estado real "{etiqueta}" ({estado}) esta documentado',
        )

    # Whitelist por tipo: soporte no puede suspender tiendas y un reporte de
    # tienda no puede ocultar productos.
    soporte = acciones_ticket(
        {'tipo': 'soporte', 'estado': 'pendiente', 'referencia_id': 7}
    )
    claves = {accion['accion'] for accion in soporte}
    _ok('suspender_tienda' not in claves, 'soporte no ofrece suspender tiendas')
    _ok('ocultar_producto' not in claves, 'soporte no ofrece ocultar productos')
    _ok('aceptar' in claves and 'rechazar' in claves,
        'soporte ofrece aceptar y rechazar')

    reporte_tienda = acciones_ticket(
        {'tipo': 'reportar_tienda', 'estado': 'pendiente', 'referencia_id': 3}
    )
    _ok(
        'suspender_tienda' in {a['accion'] for a in reporte_tienda},
        'un reporte de tienda ofrece suspender la tienda',
    )
    reporte_articulo = acciones_ticket(
        {'tipo': 'reportar_articulo', 'estado': 'pendiente', 'referencia_id': 4}
    )
    _ok(
        'ocultar_producto' in {a['accion'] for a in reporte_articulo},
        'un reporte de articulo ofrece ocultar el producto',
    )
    # Lo atendido/rechazado se archiva (se elimina): no existen acciones de
    # reapertura ni estados "Aceptada"/"Rechazada" en la bandeja.
    _ok(
        'reabrir' not in ACCIONES_SOLICITUD,
        'no se ofrece reabrir: lo cerrado se archiva',
    )
    _ok(
        acciones_ticket({'tipo': 'soporte', 'estado': 'resuelto'}) == [],
        'una solicitud ya cerrada no ofrece acciones',
    )
    _ok(
        'archivar' in ACCIONES_SOLICITUD['aceptar'].lower()
        and 'archivar' in ACCIONES_SOLICITUD['rechazar'].lower(),
        'aceptar y rechazar dejan claro que archivan',
    )
    _ok(
        acciones_ticket({'tipo': 'revision_pago', 'estado': 'pendiente'}) == [],
        'las alertas de pago no usan las acciones de solicitud',
    )


def _probar_marcadores():
    print('\n=== Marcadores de bloque balanceados ===')
    documentos = ['docs/asistente-app.md', 'docs/asistente-faq.md',
                  'templates/como_funciona.html']
    pares = [('PLANES_INICIO', 'PLANES_FIN'),
             ('GRATIS_INICIO', 'GRATIS_FIN'),
             ('RESTAURAR_PAGOS_INICIO', 'RESTAURAR_PAGOS_FIN')]
    for relativo in documentos:
        texto = _leer(relativo)
        if not texto:
            continue
        for inicio, fin in pares:
            abre = len(re.findall(re.escape(inicio), texto))
            cierra = len(re.findall(re.escape(fin), texto))
            _ok(
                abre == cierra,
                f'{relativo}: {abre} {inicio} / {cierra} {fin}',
            )


def _probar_deteccion_admin():
    print('\n=== Deteccion de temas del admin ===')
    from backend.asistente import datos_vivos

    _ok(
        datos_vivos.menciona_bandeja('cuantas solicitudes hay pendientes'),
        'detecta preguntas por las solicitudes',
    )
    _ok(
        datos_vivos.menciona_bandeja('que hay en la bandeja'),
        'detecta preguntas por la bandeja',
    )
    _ok(
        not datos_vivos.menciona_bandeja('hola, buenos dias'),
        'un saludo no dispara la bandeja',
    )
    _ok(
        datos_vivos.menciona_operacion('cuantos comercios hay'),
        'detecta preguntas por comercios',
    )
    _ok(
        datos_vivos.menciona_operacion('cuantos usuarios registrados'),
        'detecta preguntas por usuarios',
    )
    _ok(
        datos_vivos.menciona_salud_pagos('como esta la salud de pagos'),
        'detecta preguntas por la salud de pagos',
    )


def main() -> int:
    _probar_planes()
    _probar_destacados()
    _probar_banner()
    _probar_puntos()
    _probar_fase_gratuita()
    _probar_planes_activos()
    _probar_admin()
    _probar_marcadores()
    _probar_deteccion_admin()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK el asistente dice la verdad sobre el estado actual de la app')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

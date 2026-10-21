"""Trafico persistido + reporte: verificacion SIN red ni BD.

Comprueba la clasificacion de peticiones, deduplicacion de unicos, exclusion de
bots, deteccion de la PWA (instalaciones y aperturas) y que el reporte degrada
sin BD/SMTP en vez de romper.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend import trafico  # noqa: E402

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def _reset():
    trafico._pendientes.clear()
    trafico._unicos.clear()
    trafico._dia_actual = None


def _claves():
    return {clave: valor for (_fecha, clave), valor in trafico._pendientes.items()}


def main() -> int:
    print('=== Gating por LOCALIS_TRAFICO ===')
    os.environ['LOCALIS_TRAFICO'] = '0'
    _ok(not trafico.habilitado(), 'LOCALIS_TRAFICO=0 desactiva')
    os.environ['LOCALIS_TRAFICO'] = '1'
    _ok(trafico.habilitado(), 'LOCALIS_TRAFICO=1 activa')

    print('\n=== Clasificacion de peticiones ===')
    _reset()
    trafico.registrar_peticion('/', 200, user_agent='Mozilla', remote_addr='1.1.1.1')
    trafico.registrar_peticion('/static/js/x.js', 200, user_agent='Mozilla', remote_addr='1.1.1.1')
    trafico.registrar_peticion('/health/live', 200, user_agent='Mozilla', remote_addr='1.1.1.1')
    trafico.registrar_peticion('/metrics', 200, user_agent='Mozilla', remote_addr='1.1.1.1')
    trafico.registrar_peticion('/api/tienda/1', 200, user_agent='Mozilla', remote_addr='1.1.1.1')
    c = _claves()
    _ok(c.get('peticiones') == 5, f"peticiones=5 -> {c.get('peticiones')}")
    _ok(c.get('peticiones_app') == 2, f"peticiones_app=2 (/, /api) -> {c.get('peticiones_app')}")

    print('\n=== Unicos: sin bots, sin duplicados ===')
    _reset()
    trafico.registrar_peticion('/', 200, user_agent='Mozilla', remote_addr='2.2.2.2')
    trafico.registrar_peticion('/', 200, user_agent='Mozilla', remote_addr='2.2.2.2')
    trafico.registrar_peticion('/', 200, user_agent='Mozilla', remote_addr='3.3.3.3')
    trafico.registrar_peticion('/', 200, user_agent='Googlebot/2.1', remote_addr='4.4.4.4')
    c = _claves()
    _ok(c.get('visitas_unicas') == 2, f"unicos=2 (2 humanos, 1 bot) -> {c.get('visitas_unicas')}")

    print('\n=== PWA: instalaciones y aperturas ===')
    _reset()
    trafico.registrar_peticion('/', 200, user_agent='Mozilla', remote_addr='5.5.5.5', query='source=pwa')
    trafico.registrar_peticion('/', 200, user_agent='Mozilla', remote_addr='5.5.5.5')
    trafico.registrar_evento('pwa_instalaciones')
    trafico.registrar_evento('pwa_instalaciones')
    c = _claves()
    _ok(c.get('pwa_aperturas') == 1, f"aperturas_pwa=1 -> {c.get('pwa_aperturas')}")
    _ok(c.get('pwa_instalaciones') == 2, f"instalaciones_pwa=2 -> {c.get('pwa_instalaciones')}")

    print('\n=== Cambio de dia: la misma huella vuelve a contar ===')
    real_hoy = trafico._hoy
    base = _dt.date(2026, 1, 1)
    try:
        trafico._hoy = lambda: base
        _reset()
        trafico.registrar_peticion('/', 200, user_agent='Mozilla', remote_addr='6.6.6.6')
        trafico._hoy = lambda: base + _dt.timedelta(days=1)
        trafico.registrar_peticion('/', 200, user_agent='Mozilla', remote_addr='6.6.6.6')
        fechas = sorted({f for (f, k) in trafico._pendientes if k == 'visitas_unicas'})
        _ok(fechas == ['2026-01-01', '2026-01-02'], f'cuenta en ambos dias -> {fechas}')
    finally:
        trafico._hoy = real_hoy

    print('\n=== Volcado oportunista: respeta el intervalo ===')
    _reset()
    trafico._ultimo_intento_volcado = 0.0
    trafico._pendientes[('2026-01-01', 'peticiones')] = 1
    llamadas = []
    original_volcar = trafico.volcar
    trafico.volcar = lambda: (llamadas.append(1), 1)[1]
    try:
        trafico.volcar_oportunista()  # intervalo vencido -> dispara
        trafico.volcar_oportunista()  # dentro del intervalo -> no dispara
    finally:
        trafico.volcar = original_volcar
    _ok(len(llamadas) == 1, f'dispara 1 de 2 llamadas -> {len(llamadas)}')

    print('\n=== Degradacion (sin tocar la BD) ===')
    _reset()
    from backend.reporte_trafico import construir_reporte, enviar_reporte_admin
    import backend.reporte_trafico as rt

    vacio = {
        'disponible': False,
        'dias': 1,
        'por_dia': [],
        'totales_periodo': {},
        'totales_historicos': {},
    }
    resumen_original = trafico.resumen_trafico
    marca_original = rt._leer_marca
    correo_original = rt.correo_configurado
    trafico.resumen_trafico = lambda dias=1: dict(vacio, dias=dias)
    rt._leer_marca = lambda: None
    rt.correo_configurado = lambda: False
    try:
        rep = construir_reporte(dias=1)
        _ok(
            rep.get('disponible') is False and 'no hay datos' in rep['texto'].lower(),
            'reporte degrada sin datos (aviso, no lanza)',
        )
        ok, detalle, _texto = enviar_reporte_admin()
        _ok((not ok) and 'SMTP' in detalle, f'sin SMTP no envia y avisa -> {detalle}')
    finally:
        trafico.resumen_trafico = resumen_original
        rt._leer_marca = marca_original
        rt.correo_configurado = correo_original

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK trafico + reporte: clasificacion, unicos, PWA y degradacion')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

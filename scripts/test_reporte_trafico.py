"""Reporte de tráfico: orquestación y planificador, SIN red ni BD.

Con dobles (espías) verifica que ``enviar_reporte_admin`` compone y envía a todos
los admins, respeta la idempotencia diaria y avisa si falta SMTP; y que el
planificador interno solo envía tras la hora objetivo y una vez al día.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import backend.reporte_trafico as rt  # noqa: E402

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def _probar_contenido():
    """Comprueba el contenido del informe con datos simulados (sin red ni BD)."""
    fake = {
        'disponible': True,
        'por_dia': [
            {'fecha': '2026-10-22', 'claves': {
                'peticiones': 1000, 'peticiones_app': 400, 'peticiones_bots': 50,
                'visitas_unicas': 37, 'pwa_instalaciones': 2, 'pwa_aperturas': 5}},
            {'fecha': '2026-10-23', 'claves': {
                'peticiones': 300, 'peticiones_app': 120, 'peticiones_bots': 10,
                'visitas_unicas': 15}},
            {'fecha': '2026-10-24', 'claves': {
                'peticiones': 5, 'peticiones_app': 5, 'peticiones_bots': 0,
                'visitas_unicas': 3}},
        ],
        'totales_periodo': {},
        'totales_historicos': {'peticiones': 5000},
    }
    original_resumen = rt.trafico.resumen_trafico
    original_ahora = rt._ahora_utc
    rt.trafico.resumen_trafico = lambda dias=1: fake
    rt._ahora_utc = lambda: _dt.datetime(2026, 10, 23, 13, 5, tzinfo=_dt.timezone.utc)
    try:
        texto = rt.construir_reporte(dias=1)['texto']
    finally:
        rt.trafico.resumen_trafico = original_resumen
        rt._ahora_utc = original_ahora

    _ok('--- Ayer (2026-10-22, día completo) ---' in texto, 'incluye seccion Ayer completa')
    _ok('Visitantes únicos (aprox.): 37' in texto, 'unicos de ayer correctos')
    _ok('Tráfico de la app (humanos, sin bots): 350' in texto, 'humanos ayer = app-bots')
    _ok('--- Hoy (2026-10-23, en curso) ---' in texto, 'Hoy marcado como en curso')
    _ok('en hora de Venezuela' in texto, 'aclara zona horaria')
    _ok(
        '--- Totales del período (1 día(s)) ---\n'
        '  Peticiones totales (incl. salud y estáticos): 300' in texto,
        'totales del periodo solo de hoy (no el dia futuro)',
    )


def main() -> int:
    _probar_contenido()

    enviados = []
    estado = {'correo': True, 'marca': None}

    originales = {
        'construir_reporte': rt.construir_reporte,
        '_leer_marca': rt._leer_marca,
        '_guardar_marca': rt._guardar_marca,
        'enviar_correo': rt.enviar_correo,
        'correo_configurado': rt.correo_configurado,
        'ADMIN_EMAILS': rt.ADMIN_EMAILS,
        '_ahora_utc': rt._ahora_utc,
        'iniciar_reporte_diario': rt.iniciar_reporte_diario,
    }

    rt.construir_reporte = lambda dias=1: {
        'texto': 'REPORTE-TEST',
        'datos': {},
        'disponible': True,
    }
    rt._leer_marca = lambda: estado['marca']

    def _guardar_marca(valor):
        estado['marca'] = valor
        return True

    def _enviar(destino, asunto, cuerpo):
        enviados.append((destino, asunto, cuerpo))
        return True, 'ok'

    rt._guardar_marca = _guardar_marca
    rt.enviar_correo = _enviar
    rt.correo_configurado = lambda: estado['correo']
    rt.ADMIN_EMAILS = ('a@x.com', 'b@x.com')

    try:
        print('=== enviar_reporte_admin: envía a todos los admins ===')
        estado['marca'] = None
        ok, detalle, texto = rt.enviar_reporte_admin()
        _ok(ok and len(enviados) == 2, f'envía a 2 admins -> {detalle}')
        _ok(texto == 'REPORTE-TEST', 'devuelve el texto del reporte')
        _ok(estado['marca'] is not None, 'guarda la marca del día')

        print('\n=== Idempotencia diaria ===')
        enviados.clear()
        ok2, detalle2, _ = rt.enviar_reporte_admin()
        _ok(
            (not ok2) and 'ya enviado' in detalle2 and not enviados,
            f'no reenvía el mismo día -> {detalle2}',
        )

        print('\n=== Sin SMTP: avisa y no envía ===')
        estado['marca'] = None
        estado['correo'] = False
        enviados.clear()
        ok3, detalle3, _ = rt.enviar_reporte_admin()
        _ok(
            (not ok3) and 'SMTP' in detalle3 and not enviados,
            f'avisa sin SMTP -> {detalle3}',
        )
        estado['correo'] = True

        print('\n=== Planificador: hora objetivo y marca ===')
        base = _dt.datetime(2026, 6, 1, 5, 0, tzinfo=_dt.timezone.utc)
        os.environ['LOCALIS_REPORTE_DIARIO'] = '1'
        os.environ['LOCALIS_REPORTE_HORA_UTC'] = '13'
        rt._ahora_utc = lambda: base  # 05 UTC: antes de la hora
        estado['marca'] = None
        enviados.clear()
        rt.quizas_enviar_reporte_diario()
        _ok(len(enviados) == 0, 'antes de la hora objetivo no envía')

        rt._ahora_utc = lambda: base.replace(hour=14)  # 14 UTC: ya toca
        rt.quizas_enviar_reporte_diario()
        _ok(len(enviados) == 2, f'después de la hora envía -> {len(enviados)}')

        enviados.clear()
        rt.quizas_enviar_reporte_diario()
        _ok(len(enviados) == 0, 'si ya se envió hoy no reenvía')

        print('\n=== Planificador: arranque perezoso (throttle) ===')
        llamadas = []
        rt.iniciar_reporte_diario = lambda: llamadas.append(1)
        rt._iniciado = False
        rt._ultimo_intento_arranque = 0.0
        rt.asegurar_reporte_diario_activo()
        rt.asegurar_reporte_diario_activo()
        _ok(len(llamadas) == 1, f'arranca 1 de 2 veces -> {len(llamadas)}')
    finally:
        for nombre, valor in originales.items():
            setattr(rt, nombre, valor)
        os.environ.pop('LOCALIS_REPORTE_HORA_UTC', None)

    if FALLOS:
        print(f'\nFALLOS: {len(FALLOS)}')
        return 1
    print('\nOK reporte de tráfico: envío, idempotencia, SMTP y planificador')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

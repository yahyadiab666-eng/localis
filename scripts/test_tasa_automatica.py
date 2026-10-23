"""Tasa de cambio automática (BCV) — verificación SIN red ni BD.

Comprueba, con la red y la BD simuladas:

* El **redondeo a 2 decimales** y el formato con el que se guarda.
* Que la sincronización **guarda** la tasa oficial del BCV.
* Que si el BCV falla **no se degrada** la tasa anterior.
* Que el interruptor ``LOCALIS_TASA_AUTO_BCV=0`` la desactiva (modo manual).
* Que el manifiesto de Android **declara** los permisos de ubicación (regresión:
  sin ellos el botón "usar mi ubicación actual" no funciona en la app).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend import tasa_automatica as ta  # noqa: E402

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def _reset_estado():
    ta._estado.update(
        {'dia': None, 'ok': False, 'en_curso': False, 'ultimo_intento': None, 'ultimo': None}
    )


def _parchear(bcv, guardados):
    ta.obtener_tasa_bcv = bcv
    ta._tasa_actual_db = lambda: 36.50
    ta._leer_config = lambda clave: None

    def _guardar(tasa, fuente=None):
        guardados.append((tasa, fuente))
        return ta._texto_tasa(tasa)

    ta._guardar_tasa = _guardar


def _bcv_ok(forzar_actualizacion=False):
    return {'ok': True, 'tasa': 884.7496, 'fuente': 'bcv', 'error': None}


def _bcv_fallo(forzar_actualizacion=False):
    return {'ok': False, 'tasa': None, 'fuente': 'fallback', 'error': 'timeout'}


def main() -> int:
    print('=== Redondeo a 2 decimales ===')
    _ok(ta._texto_tasa(884.7496) == '884.75', f"884.7496 -> {ta._texto_tasa(884.7496)}")
    _ok(ta._texto_tasa(36.5) == '36.50', f"36.5 -> {ta._texto_tasa(36.5)}")
    _ok(ta._texto_tasa(36.999) == '37.00', f"36.999 -> {ta._texto_tasa(36.999)}")
    _ok(ta._texto_tasa('  12,5 '.replace(',', '.')) == '12.50', 'texto numérico tolerado')

    print('\n=== Sincronización: guarda la tasa del BCV redondeada ===')
    os.environ.pop('LOCALIS_TASA_AUTO_BCV', None)
    guardados = []
    _parchear(_bcv_ok, guardados)
    _reset_estado()
    resultado = ta.sincronizar_tasa_diaria(forzar=True)
    _ok(resultado.get('ok') is True, f"ok ({resultado.get('motivo')})")
    _ok(resultado.get('motivo') == 'actualizada', 'motivo actualizada')
    _ok(resultado.get('tasa') == 884.75, f"tasa redondeada ({resultado.get('tasa')})")
    _ok(len(guardados) == 1 and guardados[0][0] == 884.7496, 'se guardó el valor del BCV')
    _ok(guardados and guardados[0][1] == 'bcv', 'se registra la fuente')

    print('\n=== Sin forzar y ya al día: no repite ===')
    guardados.clear()
    resultado = ta.sincronizar_tasa_diaria()
    _ok(resultado.get('ok') is True and resultado.get('motivo') == 'ya_al_dia', 'no repite la consulta')
    _ok(not guardados, 'no vuelve a guardar')

    print('\n=== Si el BCV falla: no se degrada la tasa ===')
    guardados = []
    _parchear(_bcv_fallo, guardados)
    _reset_estado()
    resultado = ta.sincronizar_tasa_diaria(forzar=True)
    _ok(resultado.get('ok') is False, 'ok=False')
    _ok(resultado.get('motivo') == 'bcv_no_disponible', f"motivo ({resultado.get('motivo')})")
    _ok(not guardados, 'NO se guarda nada (se conserva la anterior)')

    print('\n=== Interruptor apagado: modo manual ===')
    os.environ['LOCALIS_TASA_AUTO_BCV'] = '0'
    guardados = []
    _parchear(_bcv_ok, guardados)
    _reset_estado()
    resultado = ta.sincronizar_tasa_diaria(forzar=True)
    _ok(ta.tasa_automatica_habilitada() is False, 'habilitada() -> False')
    _ok(resultado.get('ok') is False and resultado.get('motivo') == 'deshabilitada', 'no hace nada')
    _ok(not guardados, 'no consulta ni guarda')
    os.environ.pop('LOCALIS_TASA_AUTO_BCV', None)
    _ok(ta.tasa_automatica_habilitada() is True, 'vuelve a habilitarse por defecto')

    print('\n=== asegurar_tasa_del_dia nunca lanza ===')
    os.environ['LOCALIS_TASA_AUTO_BCV'] = '0'  # sin hilo ni red en la prueba
    crash = False
    try:
        ta.asegurar_tasa_del_dia()
    except Exception as error:
        crash = True
        print(f'    lanzó: {error}')
    _ok(not crash, 'no lanza')
    os.environ.pop('LOCALIS_TASA_AUTO_BCV', None)

    print('\n=== Manifiesto Android: permisos de ubicación declarados ===')
    manifiesto = RAIZ / 'android' / 'app' / 'src' / 'main' / 'AndroidManifest.xml'
    texto = manifiesto.read_text(encoding='utf-8') if manifiesto.exists() else ''
    _ok('ACCESS_FINE_LOCATION' in texto, 'declara ACCESS_FINE_LOCATION')
    _ok('ACCESS_COARSE_LOCATION' in texto, 'declara ACCESS_COARSE_LOCATION')

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK tasa automática: redondeo 2 decimales, guardado y permisos de ubicación')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Trafico persistido en BD (requiere DATABASE_URL; se omite sin ella).

Verifica que:
- La tabla ``metricas_diarias`` existe (init_db) y el UPSERT acumula deltas.
- ``trafico.volcar()`` persiste los deltas pendientes.
- ``resumen_trafico`` agrega por periodo y por histórico.
- La marca de idempotencia del reporte se guarda y se lee.

Crea datos sandbox ``__test_trafico__*`` y los limpiar al final.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass

from backend import trafico  # noqa: E402

CLAVE_TEST = '__test_trafico__'
CLAVE_TEST2 = '__test_trafico__pwa'
FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def _limpiar():
    from backend.db import get_db_connection

    with get_db_connection() as c:
        cur = c.cursor()
        cur.execute("DELETE FROM metricas_diarias WHERE clave LIKE '__test_trafico__%'")
        c.commit()


def main() -> int:
    from database import DATABASE_URL, init_db

    if not DATABASE_URL:
        print('  SKIP  DATABASE_URL no configurada: prueba de trafico en BD omitida.')
        return 0

    _ok(bool(init_db()), 'init_db() OK (crea metricas_diarias)')

    hoy = datetime.now(timezone.utc).date().isoformat()
    try:
        _limpiar()

        print('=== UPSERT acumula deltas ===')
        _ok(trafico._escribir([((hoy, CLAVE_TEST), 5)]), 'primer volcado (5)')
        _ok(trafico._escribir([((hoy, CLAVE_TEST), 3)]), 'segundo volcado (3)')
        datos = trafico.resumen_trafico(dias=1)
        _ok(datos.get('disponible') is True, 'resumen disponible')
        _ok(
            datos['totales_periodo'].get(CLAVE_TEST) == 8,
            f"periodo={datos['totales_periodo'].get(CLAVE_TEST)} (esperado 8)",
        )
        _ok(
            datos['totales_historicos'].get(CLAVE_TEST) == 8,
            f"historico={datos['totales_historicos'].get(CLAVE_TEST)} (esperado 8)",
        )
        dias_con = [d for d in datos['por_dia'] if CLAVE_TEST in d['claves']]
        _ok(bool(dias_con) and dias_con[0]['claves'][CLAVE_TEST] == 8, 'desglose por dia correcto')

        print('\n=== volcar() persiste lo pendiente ===')
        trafico._pendientes.clear()
        trafico._dia_actual = None
        trafico.registrar_evento(CLAVE_TEST2, 4)
        volcadas = trafico.volcar()
        _ok(volcadas >= 1, f'volcar() volcó {volcadas} clave(s)')
        datos = trafico.resumen_trafico(dias=1)
        _ok(
            datos['totales_periodo'].get(CLAVE_TEST2) == 4,
            f"volcado e2e={datos['totales_periodo'].get(CLAVE_TEST2)} (esperado 4)",
        )

        print('\n=== Marca de idempotencia ===')
        import backend.reporte_trafico as rt

        original = rt._leer_marca()
        try:
            _ok(rt._guardar_marca('2026-01-01'), 'guardar marca')
            _ok(rt._leer_marca() == '2026-01-01', 'leer marca devuelve el valor')
        finally:
            if original is None:
                from backend.db import get_db_connection

                with get_db_connection() as c:
                    cur = c.cursor()
                    cur.execute(
                        "DELETE FROM configuracion_sistema WHERE clave = ?",
                        (rt._CLAVE_MARCA,),
                    )
                    c.commit()
            else:
                rt._guardar_marca(original)
    finally:
        _limpiar()

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK trafico en BD: UPSERT, volcado, resumen e idempotencia')
    return 0


if __name__ == '__main__':
    sys.exit(main())

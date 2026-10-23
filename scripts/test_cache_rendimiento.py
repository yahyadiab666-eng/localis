"""Refuerzo de rendimiento — verificación SIN red ni BD.

Comprueba:
  * La caché en memoria tiene **límite de tamaño** (no crece sin control).
  * La caché corta de tienda/catálogo **evita repetir la consulta** y se puede
    apagar (TTL 0 = consulta directa).
  * La geocodificación de sedes va **en segundo plano** y no se duplica.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend import runtime_cache  # noqa: E402

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def _probar_cache_limite():
    print('=== Caché en memoria: TTL y límite de tamaño ===')
    runtime_cache.invalidate()
    llamadas = {'n': 0}

    def loader():
        llamadas['n'] += 1
        return llamadas['n']

    a = runtime_cache.get_or_load('k1', loader, ttl_seconds=60)
    b = runtime_cache.get_or_load('k1', loader, ttl_seconds=60)
    _ok(a == b == 1, f'cachea el valor y no repite el loader (a={a} b={b})')

    runtime_cache.invalidate('k')
    c = runtime_cache.get_or_load('k1', loader, ttl_seconds=60)
    _ok(c == 2, 'invalidate(prefix) fuerza recarga')

    # TTL 0 -> siempre recarga
    runtime_cache.invalidate()
    d = runtime_cache.get_or_load('k2', loader, ttl_seconds=0)
    e = runtime_cache.get_or_load('k2', loader, ttl_seconds=0)
    _ok(d != e, 'TTL 0 no cachea')

    print('\n=== Límite de entradas (no crece sin control) ===')
    os.environ['LOCALIS_CACHE_MAX_ENTRIES'] = '50'
    runtime_cache.invalidate()
    for i in range(500):
        runtime_cache.get_or_load(f'bulk:{i}', lambda i=i: i, ttl_seconds=3600)
    est = runtime_cache.estadisticas()
    _ok(
        est['entradas'] <= est['maximo'],
        f"no supera el máximo ({est['entradas']} <= {est['maximo']})",
    )
    os.environ.pop('LOCALIS_CACHE_MAX_ENTRIES', None)
    runtime_cache.invalidate()


def _probar_cache_listados():
    print('\n=== Caché corta de tienda y catálogo ===')
    from backend import stores

    contador = {'n': 0}

    def fake(**kwargs):
        contador['n'] += 1
        return [{'id': contador['n']}]

    original = stores.buscar_y_filtrar_productos
    stores.buscar_y_filtrar_productos = fake
    runtime_cache.invalidate()
    try:
        stores._CACHE_TIENDA_SEG = 20
        stores.productos_tienda_publica(7)
        stores.productos_tienda_publica(7)
        _ok(contador['n'] == 1, f'tienda: 2 llamadas -> 1 consulta (n={contador["n"]})')

        contador['n'] = 0
        stores._CACHE_TIENDA_SEG = 0
        stores.productos_tienda_publica(7)
        stores.productos_tienda_publica(7)
        _ok(contador['n'] == 2, f'tienda con caché apagada: consulta directa (n={contador["n"]})')

        contador['n'] = 0
        stores._CACHE_CATALOGO_SEG = 10
        runtime_cache.invalidate()
        stores.buscar_productos_catalogo('cafe', None)
        stores.buscar_productos_catalogo('cafe', None)
        _ok(contador['n'] == 1, f'catálogo: 2 búsquedas -> 1 consulta (n={contador["n"]})')
    finally:
        stores.buscar_y_filtrar_productos = original
        stores._CACHE_TIENDA_SEG = 20
        stores._CACHE_CATALOGO_SEG = 10
        runtime_cache.invalidate()


def _probar_geocodificacion_segundo_plano():
    print('\n=== Geocodificación en segundo plano (no bloquea y no se duplica) ===')
    from backend import ubicaciones

    ejecutadas = []

    def fake(comercio_id):
        time.sleep(0.4)
        ejecutadas.append(comercio_id)

    original = ubicaciones._geocodificar_comercio
    ubicaciones._geocodificar_comercio = fake
    os.environ['LOCALIS_GEOCODIFICACION'] = '1'
    ubicaciones._geo_en_curso.clear()
    try:
        inicio = time.perf_counter()
        ubicaciones.geocodificar_comercio_en_segundo_plano(99)
        ubicaciones.geocodificar_comercio_en_segundo_plano(99)  # duplicada: se ignora
        transcurrido = time.perf_counter() - inicio
        _ok(transcurrido < 0.2, f'no bloquea al llamar ({transcurrido:.3f}s)')

        time.sleep(0.9)
        _ok(ejecutadas == [99], f'se ejecutó una sola vez ({ejecutadas})')
        _ok(99 not in ubicaciones._geo_en_curso, 'libera la marca al terminar')
    finally:
        ubicaciones._geocodificar_comercio = original
        os.environ.pop('LOCALIS_GEOCODIFICACION', None)
        ubicaciones._geo_en_curso.clear()

    print('\n=== Con la geocodificación apagada no lanza hilo ===')
    os.environ['LOCALIS_GEOCODIFICACION'] = '0'
    try:
        ubicaciones.geocodificar_comercio_en_segundo_plano(123)
        _ok(123 not in ubicaciones._geo_en_curso, 'no arranca nada')
    finally:
        os.environ.pop('LOCALIS_GEOCODIFICACION', None)


def _probar_diagnostico_cacheado():
    print('\n=== Diagnóstico de BD cacheado (6 consultas pesadas) ===')
    import database as db
    from database import diagnosticar_postgresql

    contador = {'n': 0}
    original = db._diagnosticar_postgresql_real

    def fake():
        contador['n'] += 1
        return {'ok': True, 'llamada': contador['n']}

    db._diagnosticar_postgresql_real = fake
    db._diag_cache.update({'valor': None, 'ts': 0.0})
    os.environ['LOCALIS_DIAG_CACHE_SEG'] = '30'
    try:
        a = diagnosticar_postgresql()
        b = diagnosticar_postgresql()
        _ok(a is b and contador['n'] == 1, f'cachea el diagnóstico (n={contador["n"]})')

        c = diagnosticar_postgresql(forzar=True)
        _ok(c['llamada'] == 2 and contador['n'] == 2, 'forzar=True recalcula')

        os.environ['LOCALIS_DIAG_CACHE_SEG'] = '0'
        diagnosticar_postgresql()
        diagnosticar_postgresql()
        _ok(contador['n'] == 4, f'TTL 0 recalcula siempre (n={contador["n"]})')
    finally:
        db._diagnosticar_postgresql_real = original
        db._diag_cache.update({'valor': None, 'ts': 0.0})
        os.environ.pop('LOCALIS_DIAG_CACHE_SEG', None)

    print('\n=== Keepalive de conexiones: interruptor ===')
    os.environ['LOCALIS_DB_KEEPALIVE'] = '0'
    _ok(db.programar_keepalive_bd() is False, 'desactivable con LOCALIS_DB_KEEPALIVE=0')
    os.environ.pop('LOCALIS_DB_KEEPALIVE', None)


def main() -> int:
    _probar_cache_limite()
    _probar_cache_listados()
    _probar_geocodificacion_segundo_plano()
    _probar_diagnostico_cacheado()

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK refuerzo: caché con tope, listados cacheados y geocodificación en 2º plano')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

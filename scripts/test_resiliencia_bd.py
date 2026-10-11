#!/usr/bin/env python3
"""Resiliencia de la base de datos ante conexiones zombie (sin BD real).

Valida el blindaje contra el bug de "productos vacíos tras inactividad":

  1. Clasificación de errores: una desconexión (InterfaceError / OperationalError
     de red) es transitoria y reintentable.
  2. Health check: ``_verificar_conexion_pg`` detecta conexiones muertas.
  3. ``get_db_connection`` descarta una conexión zombie y entrega una nueva.
  4. ``listar_portada_hibrida`` reintenta ante desconexión y NO devuelve vacío.

Ejecución:
    python scripts/test_resiliencia_bd.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import psycopg2

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

_ERRORES = []


class _OperationalConPgcode(psycopg2.OperationalError):
    """OperationalError con pgcode (los reales lo traen; el de prueba no)."""

    pgcode = '55P03'


def _ok(condicion, mensaje):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    _ERRORES.append(mensaje)
    return False


class _FakeCursor:
    def __init__(self, sano=True):
        self._sano = sano

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, *args, **kwargs):
        if not self._sano:
            raise psycopg2.InterfaceError('connection already closed')

    def fetchone(self):
        return (1,)


class _FakeConn:
    def __init__(self, sano=True, closed=0):
        self.closed = closed
        self.autocommit = False
        self._sano = sano

    def cursor(self, *args, **kwargs):
        return _FakeCursor(self._sano)

    def close(self):
        self.closed = 1


class _FakePool:
    def __init__(self, conexiones):
        self._cola = list(conexiones)
        self.putconn_calls = []

    def getconn(self):
        return self._cola.pop(0)

    def putconn(self, conn, close=False):
        self.putconn_calls.append((conn, close))


def _probar_clasificacion_errores():
    print('\n=== 1. Clasificación de errores de conexión ===')
    import database

    conexion = psycopg2.OperationalError('server closed the connection unexpectedly')
    _ok(database.es_error_conexion_bd(conexion), 'OperationalError de red = desconexión')
    _ok(database.es_error_bd_transitorio(conexion), 'desconexión es transitoria/reintentable')

    cerrada = psycopg2.InterfaceError('connection already closed')
    _ok(database.es_error_conexion_bd(cerrada), 'InterfaceError = desconexión')
    _ok(database.es_error_bd_transitorio(cerrada), 'InterfaceError es reintentable')

    timeout = _OperationalConPgcode('canceling statement due to lock timeout')
    _ok(database.es_error_bd_transitorio(timeout), 'lock timeout sigue siendo transitorio')
    _ok(not database.es_error_conexion_bd(timeout), 'lock timeout no se marca como desconexión')

    logico = ValueError('dato inválido')
    _ok(not database.es_error_conexion_bd(logico), 'error lógico no es desconexión')
    _ok(not database.es_error_bd_transitorio(logico), 'error lógico no es transitorio')


def _probar_health_check():
    print('\n=== 2. Health check de conexión ===')
    import database

    _ok(database._verificar_conexion_pg(_FakeConn(sano=True)), 'conexión viva responde al ping')
    _ok(not database._verificar_conexion_pg(_FakeConn(sano=False)), 'conexión rota falla el ping')
    _ok(not database._verificar_conexion_pg(_FakeConn(closed=1)), 'conexión cerrada = no utilizable')
    _ok(not database._verificar_conexion_pg(None), 'conexión None = no utilizable')


def _probar_reconexion_automatica():
    print('\n=== 3. get_db_connection descarta zombie y reconecta ===')
    import database

    zombie = _FakeConn(sano=False)
    sana = _FakeConn(sano=True)
    pool_falso = _FakePool([zombie, sana])

    originales = {
        '_obtener_pool': database._obtener_pool,
        '_preparar_conexion_pg': database._preparar_conexion_pg,
        '_connection_pool': database._connection_pool,
        'DB_HEALTHCHECK_ENABLED': database.DB_HEALTHCHECK_ENABLED,
        'DATABASE_URL': database.DATABASE_URL,
    }
    try:
        database._obtener_pool = lambda: pool_falso
        database._preparar_conexion_pg = lambda conn, read_only=False: None
        database._connection_pool = pool_falso
        database.DB_HEALTHCHECK_ENABLED = True
        database.DATABASE_URL = 'postgresql://localhost/localis_test'

        conexion = database.get_db_connection()
        _ok(conexion._conn is sana, 'se entrega la conexión sana tras descartar la zombie')
        _ok(
            any(conn is zombie and close for conn, close in pool_falso.putconn_calls),
            'la conexión zombie se descarta (putconn close=True)',
        )
    finally:
        database._obtener_pool = originales['_obtener_pool']
        database._preparar_conexion_pg = originales['_preparar_conexion_pg']
        database._connection_pool = originales['_connection_pool']
        database.DB_HEALTHCHECK_ENABLED = originales['DB_HEALTHCHECK_ENABLED']
        database.DATABASE_URL = originales['DATABASE_URL']


def _probar_portada_reintenta():
    print('\n=== 4. listar_portada_hibrida reintenta ante desconexión ===')
    from backend import stores

    intentos = {'n': 0}

    def _once(limit, offset=0):
        intentos['n'] += 1
        if intentos['n'] == 1:
            raise psycopg2.OperationalError('server closed the connection unexpectedly')
        return ([{'id': 1, 'nombre': 'Producto'}], False)

    original = stores._listar_portada_hibrida_once
    original_sleep = stores.time.sleep
    try:
        stores._listar_portada_hibrida_once = _once
        stores.time.sleep = lambda *_: None
        productos, hay_mas = stores.listar_portada_hibrida(limit=10)
    finally:
        stores._listar_portada_hibrida_once = original
        stores.time.sleep = original_sleep

    _ok(intentos['n'] == 2, 'se reintentó exactamente una vez tras la desconexión')
    _ok(productos and productos[0].get('nombre') == 'Producto',
        'la portada devuelve productos (no una lista vacía)')
    _ok(hay_mas is False, 'hay_mas coherente')


def main():
    print('Pruebas de resiliencia de BD (bug de productos vacíos tras inactividad)')
    _probar_clasificacion_errores()
    _probar_health_check()
    _probar_reconexion_automatica()
    _probar_portada_reintenta()
    print('')
    if _ERRORES:
        print(f'RESULTADO: {len(_ERRORES)} fallo(s)')
        for error in _ERRORES:
            print(f'  - {error}')
        return 1
    print('RESULTADO: todas las pruebas pasaron')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

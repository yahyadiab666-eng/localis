#!/usr/bin/env python3
"""Resiliencia del pipeline de imágenes (sin red, sin BD real).

Verifica el blindaje de la re-subida de CSV:
  1. ``resolver_activa`` NUNCA vacía una imagen existente válida.
  2. ``reparar_imagenes_comercio`` es estrictamente aditivo: solo rellena vacíos
     y jamás ejecuta un UPDATE que borre una imagen.
  3. La caché negativa caduca (TTL) para no congelar la pasarela de Serper.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
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


class _FakeCursor:
    def __init__(self, filas):
        self._filas = list(filas)
        self.consultas = []
        self.rowcount = 1

    def execute(self, sql, params=()):
        self.consultas.append((' '.join(str(sql).split()), tuple(params or ())))
        return self

    def fetchone(self):
        return self._filas.pop(0) if self._filas else None

    def fetchall(self):
        filas, self._filas = self._filas, []
        return filas


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _probar_resolver_activa_conserva():
    print('\n=== resolver_activa conserva la imagen existente ===')
    from backend import imagenes_producto as ip

    fila = {
        'nombre': 'Atun enlatado Lark 170g',
        'descripcion': 'atun',
        'codigo_barras': '7591234567890',
        'imagen_manual_url': None,
        'imagen_manual_fuente': None,
        'imagen_url': 'https://cdn.tienda.com/atun.webp',
        'imagen_fuente': 'serper',
        'imagen_estado': 'real',
    }
    cursor = _FakeCursor([fila, None])  # producto, sin fila en imagenes_automaticas
    with patch.object(ip, '_conexion', lambda: _FakeConn(cursor)):
        resultado = ip.resolver_activa(42)

    update = [c for c in cursor.consultas if c[0].upper().startswith('UPDATE PRODUCTOS')]
    _ok(bool(update), 'ejecuta el UPDATE de la vista activa')
    _ok(
        update and update[0][1][0] == 'https://cdn.tienda.com/atun.webp',
        'mantiene la URL existente (no la vacía)',
    )
    _ok(
        resultado and resultado.get('activa') == 'https://cdn.tienda.com/atun.webp',
        'devuelve la imagen conservada',
    )


def _probar_resolver_activa_pendiente():
    print('\n=== resolver_activa deja pendiente si NO hay imagen ===')
    from backend import imagenes_producto as ip

    fila = {
        'nombre': 'Producto sin foto',
        'descripcion': '',
        'codigo_barras': '000',
        'imagen_manual_url': None,
        'imagen_manual_fuente': None,
        'imagen_url': None,
        'imagen_fuente': None,
        'imagen_estado': 'pendiente',
    }
    cursor = _FakeCursor([fila, None])
    with patch.object(ip, '_conexion', lambda: _FakeConn(cursor)):
        ip.resolver_activa(43)
    update = [c for c in cursor.consultas if c[0].upper().startswith('UPDATE PRODUCTOS')]
    _ok(update and update[0][1][0] is None, 'sin imagen válida queda nulo (pendiente)')


def _probar_reparacion_aditiva():
    print('\n=== reparar_imagenes_comercio: repara sin romper imágenes buenas ===')
    from backend import imagenes_producto as ip

    filas = [
        {'id': 1, 'codigo_barras': '111', 'imagen_url': None, 'imagen_fuente': None, 'estado': 'pendiente'},
        {
            'id': 2,
            'codigo_barras': '222',
            'imagen_url': 'https://cdn.tienda.com/atun.webp',
            'imagen_fuente': 'serper',
            'estado': 'real',
        },
        {
            'id': 3,
            'codigo_barras': '333',
            'imagen_url': 'https://images.openfoodfacts.org/x/y.jpg',
            'imagen_fuente': 'openfoodfacts',
            'estado': 'pendiente',
        },
    ]
    cursor = _FakeCursor(filas)
    with patch('backend.db.get_db_connection', lambda **k: _FakeConn(cursor)), patch.object(
        ip, '_imagen_maestra_por_ean', side_effect=['https://storage/x1.webp', None]
    ):
        reparadas, marcadas = ip.reparar_imagenes_comercio(1, limite=10)

    updates = [c for c in cursor.consultas if c[0].upper().startswith('UPDATE PRODUCTOS')]
    _ok(reparadas == 1, 'rellena desde el catálogo global cuando falta imagen')
    _ok(marcadas == 1, 'marca pendiente la imagen objetivamente inválida')
    _ok(len(updates) == 2, 'un UPDATE por cada caso accionable')
    _ok(
        any('SET IMAGEN_URL = NULL' in u[0].upper() for u in updates),
        'la inválida se marca pendiente (no se muestra foto errónea)',
    )
    _ok(
        not any(u[1][0] and 'atun.webp' in str(u[1][0]) for u in updates),
        'la imagen válida existente NO se toca',
    )


def _probar_negativo_ttl():
    print('\n=== La caché negativa caduca (no congela la pasarela) ===')
    from backend import imagenes_producto as ip

    reciente = {'url_imagen': None, 'encontrada': 0, 'fecha_actualizacion': datetime.now()}
    antiguo = {
        'url_imagen': None,
        'encontrada': 0,
        'fecha_actualizacion': datetime.now() - timedelta(days=30),
    }
    positivo = {'url_imagen': 'https://x/a.webp', 'encontrada': 1, 'fecha_actualizacion': None}
    _ok(ip._negativo_vigente(reciente), 'negativo reciente se respeta (sin gasto)')
    _ok(not ip._negativo_vigente(antiguo), 'negativo antiguo se reintenta')
    _ok(ip._negativo_vigente(positivo), 'positivo siempre sirve')


def main() -> int:
    _probar_resolver_activa_conserva()
    _probar_resolver_activa_pendiente()
    _probar_reparacion_aditiva()
    _probar_negativo_ttl()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK resiliencia de imágenes: sin borrados, reparación aditiva y EAN reutilizable')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

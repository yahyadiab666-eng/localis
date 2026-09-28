#!/usr/bin/env python3
"""Logos de marca vs. fotografías reales en la asociación de imágenes.

Invariantes que protege este parche:

  1. Un logo (``…/imagenes/marcas/…``, ``/static/uploads/marcas/…`` o archivo
     ``marca_*``) NUNCA se considera foto de producto verificada.
  2. El catálogo maestro ni guarda ni reutiliza logos como imagen canónica de un
     EAN.
  3. La reconciliación de estados marca los logos como ``'logo'`` (nunca
     ``'real'``), incluidos los que viven en Storage.
  4. El guardado en el catálogo no ejecuta DDL si el índice único ya existe.

Sin BD ni red: usa cursores simulados.
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

_ERRORES = []

LOGO_STORAGE = (
    'https://abc.supabase.co/storage/v1/object/public/imagenes/marcas/'
    'marca_samsung.webp'
)
LOGO_LOCAL = '/static/uploads/marcas/marca_xiaomi.webp'
FOTO_PRODUCTO = (
    'https://abc.supabase.co/storage/v1/object/public/imagenes/productos/'
    'auto_telefono_abc123.webp'
)
FOTO_LOCAL = '/static/uploads/productos/manual_1_a.webp'


def _ok(condicion, mensaje):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    _ERRORES.append(mensaje)
    return False


class _CursorFalso:
    """Cursor mínimo para probar SQL sin BD."""

    def __init__(self, filas=None):
        self._filas = list(filas or [])
        self._resultado = None
        self.ejecutadas = []
        self.rowcount = 0

    def execute(self, sql, params=()):
        self.ejecutadas.append((str(sql), params))
        self._resultado = self._filas.pop(0) if self._filas else None
        return self

    def fetchone(self):
        return self._resultado

    def fetchall(self):
        return []


def _probar_helper():
    print('\n=== Identificación de logos ===')
    from backend.activos_verificados import es_url_logo_marca

    _ok(es_url_logo_marca(LOGO_STORAGE), 'Storage /imagenes/marcas/ -> logo')
    _ok(es_url_logo_marca(LOGO_LOCAL), '/static/uploads/marcas/ -> logo')
    _ok(
        es_url_logo_marca('https://x.supabase.co/storage/v1/object/public/'
                          'imagenes/marcas/logo_favicon.webp'),
        'archivo marca_* en otro bucket -> logo',
    )
    _ok(not es_url_logo_marca(FOTO_PRODUCTO), 'Storage /productos/ NO es logo')
    _ok(not es_url_logo_marca(FOTO_LOCAL), 'upload de producto NO es logo')
    _ok(not es_url_logo_marca(None), 'None no es logo')


def _probar_asset_verificado():
    print('\n=== Asset verificado excluye logos ===')
    from backend.activos_verificados import es_asset_verificado

    _ok(not es_asset_verificado(LOGO_STORAGE), 'logo Storage nunca verificado')
    _ok(not es_asset_verificado(LOGO_LOCAL), 'logo local nunca verificado')
    _ok(es_asset_verificado(FOTO_PRODUCTO), 'foto de producto Storage sí verificada')
    _ok(es_asset_verificado(FOTO_LOCAL), 'upload de producto sí verificado')


def _probar_catalogo_maestro():
    print('\n=== Catálogo maestro rechaza logos ===')
    from backend.catalogo_maestro import _url_maestro_valida

    _ok(not _url_maestro_valida(LOGO_STORAGE), 'no guarda/reutiliza logo Storage')
    _ok(not _url_maestro_valida(LOGO_LOCAL), 'no guarda/reutiliza logo local')
    _ok(bool(_url_maestro_valida(FOTO_PRODUCTO)), 'sí acepta foto de producto')


def _probar_candidato_ean():
    print('\n=== Búsqueda por EAN descarta logos ===')
    from backend.imagenes_producto import _candidato_ean_valido

    ean = '7591234567890'
    candidato_logo = {
        'url': LOGO_STORAGE,
        'dominio': 'abc.supabase.co',
        'titulo': f'Producto EAN {ean}',
        'contexto': ean,
    }
    candidato_foto = {
        'url': FOTO_PRODUCTO,
        'dominio': 'abc.supabase.co',
        'titulo': f'Telefono EAN {ean}',
        'contexto': ean,
    }
    _ok(
        not _candidato_ean_valido(candidato_logo, ean, ['telefono']),
        'un logo con el EAN en el contexto se descarta',
    )
    _ok(
        _candidato_ean_valido(candidato_foto, ean, ['telefono']),
        'una foto real con el EAN se acepta',
    )


def _probar_reconciliacion():
    print('\n=== Reconciliación: logos nunca real ===')
    from database import _reconciliar_estados_imagenes

    cursor = _CursorFalso()
    _reconciliar_estados_imagenes(cursor)
    sql = cursor.ejecutadas[-1][0] if cursor.ejecutadas else ''
    _ok('%/imagenes/marcas/%' in sql, 'la reconciliación cubre Storage /imagenes/marcas/')
    _ok("'/static/uploads/marcas/%' THEN 'logo'" in sql.replace('  ', ' '), 'cubre marcas local')
    pos_logo = sql.find("'%/imagenes/marcas/%'")
    pos_real = sql.find("THEN 'real'")
    _ok(0 <= pos_logo < pos_real, 'la rama de logo precede a la de real')


def _probar_ddl_hot_path():
    print('\n=== Sin DDL si el índice ya existe ===')
    import backend.catalogo_maestro as cm

    cm._INDICE_UNICO_VERIFICADO = False
    cursor_existente = _CursorFalso(filas=[(1,)])
    cm._asegurar_indice_unico_codigo(cursor_existente)
    hay_ddl = any('CREATE UNIQUE INDEX' in sql for sql, _ in cursor_existente.ejecutadas)
    _ok(not hay_ddl, 'índice existente -> no ejecuta CREATE UNIQUE INDEX')

    cm._INDICE_UNICO_VERIFICADO = False
    cursor_ausente = _CursorFalso(filas=[None])
    cm._asegurar_indice_unico_codigo(cursor_ausente)
    hay_ddl2 = any('CREATE UNIQUE INDEX' in sql for sql, _ in cursor_ausente.ejecutadas)
    _ok(hay_ddl2, 'índice ausente -> sí crea el índice una vez')


def main() -> int:
    _probar_helper()
    _probar_asset_verificado()
    _probar_catalogo_maestro()
    _probar_candidato_ean()
    _probar_reconciliacion()
    _probar_ddl_hot_path()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK logos aislados de las fotos reales (estado, catálogo maestro y camino caliente)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

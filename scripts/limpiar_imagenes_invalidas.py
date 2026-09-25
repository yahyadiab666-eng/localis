#!/usr/bin/env python3
"""
Limpia URLs de imagen **objetivamente inválidas** de los caches globales.

Motivo: `imagenes_automaticas` y `catalogo_maestro_imagenes` pueden guardar URLs
externas no mostrables (p. ej. OpenFoodFacts/Wikimedia). Esas URLs se re-aplican
a los productos vía `resolver_activa` y reaparecen tras cada reparación.

Este script:
  - `imagenes_automaticas`: pone en nulo las inválidas (permite re-resolver).
  - `catalogo_maestro_imagenes`: elimina las entradas inválidas.
  - `productos`: deja pendientes las que apunten a una URL inválida.

Solo toca URLs rechazadas por la política de presentación; las válidas jamás se
modifican. Dry-run por defecto; con ``--apply`` aplica.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass


def _es_invalida(url, fuente=None):
    from backend.activos_verificados import es_asset_generado
    from backend.utils import url_imagen_catalogo_valida

    texto = str(url or '').strip()
    if not texto:
        return False
    if texto.startswith('/static/uploads/'):
        return False
    if es_asset_generado(texto, fuente):
        return True
    if texto.lower().startswith('http'):
        return not bool(url_imagen_catalogo_valida(texto))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()

    from backend.db import get_db_connection

    with get_db_connection(row_factory=True) as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            "SELECT clave, url_imagen FROM imagenes_automaticas "
            "WHERE url_imagen IS NOT NULL AND TRIM(CAST(url_imagen AS TEXT)) <> ''"
        )
        auto_invalidas = [dict(f) for f in cursor.fetchall() if _es_invalida(dict(f).get('url_imagen'))]

        cursor.execute(
            "SELECT codigo_barras, url_imagen FROM catalogo_maestro_imagenes "
            "WHERE url_imagen IS NOT NULL AND TRIM(CAST(url_imagen AS TEXT)) <> ''"
        )
        maestro_invalidas = [dict(f) for f in cursor.fetchall() if _es_invalida(dict(f).get('url_imagen'))]

        cursor.execute(
            "SELECT id, imagen_url, imagen_fuente FROM productos "
            "WHERE imagen_url IS NOT NULL AND TRIM(CAST(imagen_url AS TEXT)) <> ''"
        )
        prod_invalidas = [
            dict(f)
            for f in cursor.fetchall()
            if _es_invalida(dict(f).get('imagen_url'), dict(f).get('imagen_fuente'))
        ]

        print('=== Limpieza de URLs inválidas en caches ===')
        print(f'imagenes_automaticas inválidas: {len(auto_invalidas)}')
        for f in auto_invalidas[:10]:
            print(f"  {f['clave']} -> {str(f['url_imagen'])[:70]}")
        print(f'catalogo_maestro inválidas: {len(maestro_invalidas)}')
        print(f'productos con URL inválida: {len(prod_invalidas)}')
        for f in prod_invalidas[:10]:
            print(f"  id={f['id']} -> {str(f['imagen_url'])[:70]}")

        if not args.apply:
            print('\nDry-run: nada se modificó. Repite con --apply.')
            return 0

        if auto_invalidas:
            claves = [f['clave'] for f in auto_invalidas if f.get('clave') is not None]
            for inicio in range(0, len(claves), 200):
                lote = claves[inicio : inicio + 200]
                ph = ', '.join('?' for _ in lote)
                cursor.execute(
                    f'UPDATE imagenes_automaticas SET url_imagen = NULL, encontrada = 0 '
                    f'WHERE clave IN ({ph})',
                    tuple(lote),
                )
        if maestro_invalidas:
            codigos = [f['codigo_barras'] for f in maestro_invalidas if f.get('codigo_barras') is not None]
            for inicio in range(0, len(codigos), 200):
                lote = codigos[inicio : inicio + 200]
                ph = ', '.join('?' for _ in lote)
                cursor.execute(
                    f'DELETE FROM catalogo_maestro_imagenes WHERE codigo_barras IN ({ph})',
                    tuple(lote),
                )
        if prod_invalidas:
            ids = [int(f['id']) for f in prod_invalidas]
            for inicio in range(0, len(ids), 200):
                lote = ids[inicio : inicio + 200]
                ph = ', '.join('?' for _ in lote)
                cursor.execute(
                    f"UPDATE productos SET imagen_url = NULL, imagen_fuente = NULL, "
                    f"imagen_estado = 'pendiente' WHERE id IN ({ph})",
                    tuple(lote),
                )
        conexion.commit()
        print('\nLimpieza aplicada.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

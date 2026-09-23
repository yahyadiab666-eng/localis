#!/usr/bin/env python3
"""
Diagnóstico del conector Serper.dev (Google Images).

Ejecuta una consulta de prueba REAL y explica el resultado:

  - ``ok``            -> HTTP 200, credenciales válidas.
  - ``clave_invalida``-> HTTP 401/403: la API key no es válida.
  - ``cuota``         -> HTTP 429: sin créditos / límite alcanzado.
  - ``red``           -> no se pudo contactar a Serper.
  - ``http_error``    -> otro código HTTP devuelto por Serper.

No persiste nada ni modifica el estado de cuota/cooldown del runtime.

Uso::

    python scripts/diagnostico_serper.py
"""

from __future__ import annotations

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env')
except Exception:
    pass


def main():
    print('=== Diagnóstico Serper.dev (Google Images) ===')
    from backend import serper_images

    estado = serper_images.estado_configuracion()
    print(
        f"{estado['variable']} configurada: {estado['configurada']} "
        f"(len={estado['key_longitud']})"
    )
    print(
        f"Endpoint: {estado['endpoint']}  gl={estado['gl']} hl={estado['hl']} "
        f"concurrentes_max={estado['concurrentes_max']}"
    )

    informe = serper_images.diagnosticar()
    if informe['ok']:
        print('\nRESULTADO: OK — HTTP 200. Credenciales válidas.')

        imagenes = serper_images.buscar_imagenes('laptop lenovo', limite=3)
        print(f'Ejemplo de búsqueda: {len(imagenes)} resultado(s)')
        for item in imagenes:
            print(f"  - {item['dominio']} {item['ancho']}x{item['alto']} {item['url'][:80]}")
        return 0

    print('\nRESULTADO: FALLO')
    print(f"  status   : {informe['status']}")
    print(f"  categoría: {informe['categoria']}")
    print(f"  mensaje  : {informe['mensaje']}")
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Diagnóstico estricto de red hacia Supabase (DNS, HTTP directo, SDK).

Uso:
  python scripts/diagnostico_supabase_red.py
  python scripts/diagnostico_supabase_red.py --sin-sdk
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=True)

    parser = argparse.ArgumentParser(description='Diagnóstico de red Supabase para Localis')
    parser.add_argument(
        '--sin-sdk',
        action='store_true',
        help='Omitir prueba del SDK de Python (solo DNS + HTTP httpx)',
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Salida JSON en lugar de texto legible',
    )
    args = parser.parse_args()

    from backend.supabase_connectivity import (
        diagnosticar_conectividad_supabase,
        imprimir_diagnostico_conectividad,
    )

    informe = diagnosticar_conectividad_supabase(probar_sdk=not args.sin_sdk)

    if args.json:
        print(json.dumps(informe, indent=2, ensure_ascii=False))
    else:
        imprimir_diagnostico_conectividad(informe)
        print('')
        print('--- Informe completo ---')
        for clave in (
            'url',
            'host',
            'config_ok',
            'capa_fallo',
            'recomendacion',
            'advertencias_config',
            'errores_config',
            'dns',
            'http_rest',
            'http_storage',
            'sdk_storage',
        ):
            if clave in informe and informe[clave] is not None:
                print(f'{clave}: {informe[clave]}')

    return 0 if informe.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(main())

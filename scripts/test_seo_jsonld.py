"""Datos estructurados (SEO JSON-LD): verificacion SIN red ni BD.

Comprueba que ``backend/seo.py`` genera JSON válido con los tipos esperados y
que tolera datos incompletos sin lanzar.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend import seo  # noqa: E402

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def main() -> int:
    base = 'https://localis.onrender.com/'
    comercio = {
        'id': 7,
        'slug': 'panaderia-la-esquina',
        'nombre': 'Panadería La Esquina',
        'descripcion': 'Pan recién hecho',
        'telefono': '04141234567',
        'ciudad': 'Maracaibo',
        'zona': 'Bella Vista',
        'direccion': 'Av. 5 con Calle 8',
    }
    producto = {'id': 3, 'nombre': 'Pan canilla', 'precio_usd': 1.2, 'imagen_url': '/static/x.png'}

    print('=== Tienda + producto ===')
    crudo = seo.json_ld_store(comercio, base, producto)
    datos = json.loads(crudo)  # lanza si no es JSON válido
    tipos = [d.get('@type') for d in datos]
    _ok('Store' in tipos and 'Product' in tipos, f'tipos {tipos}')
    tienda = next(d for d in datos if d.get('@type') == 'Store')
    _ok(
        tienda['url'] == base.rstrip('/') + '/tienda/panaderia-la-esquina',
        f"url tienda {tienda['url']}",
    )
    _ok(tienda.get('telephone') == '04141234567', 'telefono incluido')
    _ok(
        tienda.get('address', {}).get('addressLocality') == 'Maracaibo',
        'dirección (ciudad) incluida',
    )
    prod = next(d for d in datos if d.get('@type') == 'Product')
    _ok(prod.get('offers', {}).get('price') == 1.2, 'precio en la oferta')
    _ok(
        prod['offers']['priceCurrency'] == 'USD',
        'moneda USD',
    )
    _ok(prod.get('image', '').startswith('https://'), 'imagen absoluta')

    print('\n=== Sin producto: solo tienda ===')
    datos2 = json.loads(seo.json_ld_store(comercio, base))
    _ok(len(datos2) == 1 and datos2[0]['@type'] == 'Store', 'un solo Store')

    print('\n=== Tolera datos incompletos (nunca lanza) ===')
    crash = False
    for malo in ({}, None, {'nombre': ''}, {'id': None}):
        try:
            json.loads(seo.json_ld_store(malo, base))
        except Exception as error:
            crash = True
            print(f'    fallo con {malo!r}: {error}')
    _ok(not crash, 'datos incompletos -> JSON válido (no lanza)')

    print('\n=== WebSite con SearchAction ===')
    web = json.loads(seo.json_ld_website(base))
    _ok(web['@type'] == 'WebSite', 'tipo WebSite')
    _ok(
        web['potentialAction']['target']['urlTemplate'].endswith('/?q={search_term_string}'),
        'caja de búsqueda',
    )

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK SEO JSON-LD: tienda, producto, sitio y tolerancia a datos incompletos')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

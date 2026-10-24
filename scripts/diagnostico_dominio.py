#!/usr/bin/env python3
"""
Estado del dominio propio: vencimiento, aviso y comprobaciones técnicas.

Sirve para saber **cuánto falta para renovar** (NIC.ve no avisa ni renueva solo)
y para verificar que el dominio resuelve, responde y tiene TLS.

Uso::

    python scripts/diagnostico_dominio.py
    python scripts/diagnostico_dominio.py --dominio localis.com.ve
"""

from __future__ import annotations

import argparse
import socket
import ssl
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass


def _tls(host):
    """Certificado TLS: emisor y días para vencer (o el error)."""
    try:
        contexto = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=15) as bruto:
            with contexto.wrap_socket(bruto, server_hostname=host) as tls:
                cert = tls.getpeercert()
        vence = datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y %Z').replace(
            tzinfo=timezone.utc
        )
        dias = (vence - datetime.now(timezone.utc)).days
        emisor = dict(x[0] for x in (cert.get('issuer') or []) if x)
        return f'ok, vence en {dias} día(s) (emisor: {emisor.get("organizationName") or emisor.get("commonName") or "?"})'
    except Exception as error:
        return f'{type(error).__name__}: {error}'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dominio', default='', help='dominio a revisar')
    args = parser.parse_args()

    from backend.dominio import estado

    datos = estado()
    dominio = (args.dominio or datos['dominio'] or '').strip()

    print('== vencimiento del dominio (aviso preventivo) ==')
    if not datos['dominio'] and not args.dominio:
        print('   (sin configurar) define LOCALIS_DOMINIO y LOCALIS_DOMINIO_VENCE')
        print('   ejemplo: LOCALIS_DOMINIO=localis.com.ve LOCALIS_DOMINIO_VENCE=2027-10-24')
    else:
        print(f"   dominio : {datos['dominio'] or dominio}")
        print(f"   vence   : {datos['vence'] or '(sin fecha configurada)'}")
        if datos['dias'] is not None:
            print(f"   faltan  : {datos['dias']} día(s)")
        print(f"   aviso   : {datos['aviso'] or '(todavía no toca avisar)'}")

    if dominio:
        print()
        print(f'== comprobaciones técnicas de {dominio} ==')
        try:
            direccion = socket.gethostbyname(dominio)
            print(f'   DNS     : resuelve a {direccion}')
        except Exception as error:
            print(f'   DNS     : no resuelve ({type(error).__name__})')
        print(f'   TLS     : {_tls(dominio)}')
        try:
            import httpx

            r = httpx.get(f'https://{dominio}/health/live', timeout=25, follow_redirects=True)
            print(f'   HTTP    : /health/live -> {r.status_code} {r.text[:90]}')
        except Exception as error:
            print(f'   HTTP    : error {type(error).__name__}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
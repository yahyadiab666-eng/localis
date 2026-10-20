"""Verifica el rate limiting: trafico normal pasa, el exceso devuelve 429.

Requiere BD (importa main). No modifica datos.
"""

from __future__ import annotations

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


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    return bool(cond)


def main() -> int:
    from database import DATABASE_URL

    if not DATABASE_URL:
        print('  SKIP  DATABASE_URL no configurada: prueba de rate limit omitida.')
        return 0

    from main import app

    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    cliente = app.test_client()
    fallos = []

    print('=== Paginas normales no se limitan ===')
    codigos = [cliente.get('/').status_code for _ in range(20)]
    fallos.append(not _ok(all(c == 200 for c in codigos), f'20 GET / -> {set(codigos)}'))

    print('=== Endpoint sensible se limita (10/min) ===')
    codigos = [cliente.post('/api/actualizar-tasa').status_code for _ in range(15)]
    fallos.append(not _ok(codigos[0] == 401, f'primer POST -> {codigos[0]} (401 esperado)'))
    fallos.append(not _ok(429 in codigos, f'exceso -> 429 presente en {codigos}'))

    print('\n=== RESULTADO ===')
    total = sum(1 for f in fallos if f)
    if total:
        print(f'FALLOS: {total}')
        return 1
    print('OK rate limiting: paginas libres y endpoint sensible con 429')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

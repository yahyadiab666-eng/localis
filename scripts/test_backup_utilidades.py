"""Utilidades de respaldo: parseo de URL y retencion. SIN red ni BD."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
if str(RAIZ / 'scripts') not in sys.path:
    sys.path.insert(0, str(RAIZ / 'scripts'))

import backup_bd  # noqa: E402

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def main() -> int:
    print('=== parsear_url ===')
    datos = backup_bd.parsear_url('postgresql://usuario:p%40ss@host.example:6543/localisdb')
    _ok(datos['host'] == 'host.example', f"host={datos['host']}")
    _ok(datos['port'] == 6543, f"port={datos['port']}")
    _ok(datos['user'] == 'usuario', f"user={datos['user']}")
    _ok(datos['password'] == 'p@ss', f"password desescapada={datos['password']!r}")
    _ok(datos['dbname'] == 'localisdb', f"dbname={datos['dbname']}")

    print('=== podar (retencion) ===')
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        for i in range(5):
            f = base / f'localis_2026010{i}_000000.dump'
            f.write_bytes(b'x')
            os.utime(f, (time.time() + i, time.time() + i))
        eliminados = backup_bd.podar(base, 2)
        restantes = sorted(p.name for p in base.glob('localis_*.dump'))
        _ok(len(restantes) == 2, f'quedan 2 respaldos: {restantes}')
        _ok(len(eliminados) == 3, f'se podaron 3: {eliminados}')

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK utilidades de respaldo: URL y retencion')
    return 0


if __name__ == '__main__':
    sys.exit(main())

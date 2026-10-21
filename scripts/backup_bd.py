"""Respaldo logico de PostgreSQL con ``pg_dump`` y retencion.

Uso:
    python scripts/backup_bd.py                 # respalda en ./backups/
    python scripts/backup_bd.py --out D:\\backups --keep 7
    python scripts/backup_bd.py --list          # lista respaldos existentes
    python scripts/backup_bd.py --dry-run       # muestra el comando sin ejecutar

Por defecto respalda SOLO el esquema ``public`` (los datos de la app). En
Supabase, un dump de TODA la base incluye schemas gestionados (auth, storage,
realtime, vault, graphql) que NO son restaurables sin superusuario; el drill de
restore falla con "permission denied". Supabase protege esos schemas con sus
propios backups/PITR. Usa ``--schema=""`` solo si sabes lo que haces.

Usa la ``DATABASE_URL`` del entorno (.env o panel). La contrasena NO se pasa en
la linea de comandos: se inyecta por ``PGPASSWORD``.

Para restaurar y hacer el *drill*: ver ``docs/backups.md`` y
``scripts/restore_bd.py``.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

try:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=False)
except Exception:
    pass


def parsear_url(url):
    """Descompone una URL de PostgreSQL en partes (sin exponer la clave)."""
    partes = urlparse(url or '')
    return {
        'host': partes.hostname or 'localhost',
        'port': partes.port or 5432,
        'user': unquote(partes.username or ''),
        'password': unquote(partes.password or ''),
        'dbname': (partes.path or '/postgres').lstrip('/') or 'postgres',
    }


def podar(directorio, keep):
    """Conserva los ``keep`` respaldos mas nuevos y borra el resto."""
    archivos = sorted(
        Path(directorio).glob('localis_*.dump'),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    eliminados = []
    for viejo in archivos[max(1, keep):]:
        viejo.unlink()
        eliminados.append(viejo.name)
    return eliminados


def _construir_comando(pg_dump, datos, archivo, esquema='public'):
    comando = [
        pg_dump,
        '--format=custom',
        '--no-owner',
        '--no-privileges',
        '--host', str(datos['host']),
        '--port', str(datos['port']),
        '--username', datos['user'],
        '--dbname', datos['dbname'],
    ]
    # Por defecto SOLO el esquema de la app (public). Un dump de TODA la base en
    # Supabase incluye schemas gestionados (auth, storage, realtime, vault,
    # graphql) que NO se pueden restaurar sin superusuario: el drill de restore
    # fallaba con "permission denied". Supabase ya protege esos schemas con sus
    # propios backups/PITR.
    if esquema:
        comando += ['--schema', str(esquema)]
    comando += ['--file', str(archivo)]
    return comando


def _listar(destino):
    archivos = sorted(Path(destino).glob('localis_*.dump'))
    if not archivos:
        print(f'Sin respaldos en {destino}')
        return
    print(f'Respaldos en {destino}:')
    for f in archivos:
        print(f'  {f.name}  ({f.stat().st_size / 1024:.0f} KB)')


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Respaldo logico de PostgreSQL (pg_dump, formato custom)'
    )
    parser.add_argument('--out', default=str(RAIZ / 'backups'))
    parser.add_argument('--keep', type=int, default=7, help='respaldos a conservar')
    parser.add_argument(
        '--schema',
        default='public',
        help="esquema a respaldar (por defecto 'public'; vacio = todos los esquemas)",
    )
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args(argv)

    destino = Path(args.out)

    if args.list:
        _listar(destino)
        return 0

    url = (os.environ.get('DATABASE_URL') or '').strip()
    if not url:
        print('ERROR: DATABASE_URL no esta definida (cargala en .env o en el entorno).')
        return 2

    datos = parsear_url(url)
    pg_dump = shutil.which('pg_dump')

    destino.mkdir(parents=True, exist_ok=True)
    sello = datetime.now().strftime('%Y%m%d_%H%M%S')
    archivo = destino / f'localis_{sello}.dump'
    comando = _construir_comando(pg_dump or 'pg_dump', datos, archivo, args.schema)

    if args.dry_run:
        print('DRY-RUN: se ejecutaria:')
        print('  ' + ' '.join(comando))
        if not pg_dump:
            print('  (aviso: pg_dump no esta en el PATH; en produccion instalalo)')
        return 0

    if not pg_dump:
        print('ERROR: no se encontro pg_dump en el PATH.')
        print('  Windows:   https://www.postgresql.org/download/windows/')
        print('  Debian:    sudo apt-get install postgresql-client')
        print('  macOS:     brew install libpq && brew link --force libpq')
        print('Alternativa gestionada (sin pg_dump): activa PITR/backups en Supabase.')
        print('Ver docs/backups.md.')
        return 3

    entorno = dict(os.environ)
    if datos['password']:
        entorno['PGPASSWORD'] = datos['password']

    print(f'Respaldando {datos["host"]}/{datos["dbname"]} (esquema={args.schema or "todos"}) -> {archivo} ...')
    try:
        subprocess.run(comando, env=entorno, check=True)
    except Exception as error:
        print(f'ERROR: pg_dump fallo: {error}')
        return 4

    print(f'OK: {archivo.name} ({archivo.stat().st_size / 1024:.0f} KB)')
    for nombre in podar(destino, args.keep):
        print(f'  podado (retencion {args.keep}): {nombre}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

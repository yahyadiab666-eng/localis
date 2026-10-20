"""Restaura un respaldo en PostgreSQL (drill de restore o recuperacion).

PELIGRO: por defecto usa ``pg_restore --clean --if-exists``, que **borra y
recrea** los objetos de la base destino. Por eso:
  - exige ``--yes`` explicitamente;
  - **rechaza** el host del proyecto de PRODUCCION salvo ``--force-prod``.

Uso (drill en staging):
    python scripts/restore_bd.py backups/localis_YYYYMMDD_HHMMSS.dump \
        --database-url "$STAGING_DATABASE_URL" --yes

Recuperacion en produccion (con maxima precaucion):
    python scripts/restore_bd.py <archivo> --yes --force-prod
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
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

HOST_PRODUCCION = 'wesnnnvoavprgqcczzsg'  # proyecto Supabase de produccion


def parsear_url(url):
    partes = urlparse(url or '')
    return {
        'host': partes.hostname or 'localhost',
        'port': partes.port or 5432,
        'user': unquote(partes.username or ''),
        'password': unquote(partes.password or ''),
        'dbname': (partes.path or '/postgres').lstrip('/') or 'postgres',
    }


def _construir_comando(pg_restore, datos, archivo, limpiar=True):
    cmd = [
        pg_restore,
        '--no-owner',
        '--no-privileges',
        '--host', str(datos['host']),
        '--port', str(datos['port']),
        '--username', datos['user'],
        '--dbname', datos['dbname'],
    ]
    if limpiar:
        cmd += ['--clean', '--if-exists']
    cmd.append(str(archivo))
    return cmd


def main(argv=None):
    parser = argparse.ArgumentParser(description='Restaura un respaldo pg_dump (custom)')
    parser.add_argument('archivo', help='ruta del .dump a restaurar')
    parser.add_argument('--database-url', default='', help='destino (por defecto DATABASE_URL)')
    parser.add_argument('--yes', action='store_true', help='confirma la operacion destructiva')
    parser.add_argument('--force-prod', action='store_true', help='permite el host de produccion')
    parser.add_argument('--no-clean', action='store_true', help='no borra objetos antes de restaurar')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)

    archivo = Path(args.archivo)
    if not archivo.exists():
        print(f'ERROR: no existe el archivo {archivo}')
        return 2

    url = (args.database_url or os.environ.get('DATABASE_URL') or '').strip()
    if not url:
        print('ERROR: define --database-url o DATABASE_URL.')
        return 2

    if HOST_PRODUCCION in url and not args.force_prod:
        print('RECHAZADO: el destino apunta al proyecto de PRODUCCION.')
        print('Usa staging para el drill, o agrega --force-prod si de verdad vas a restaurar en prod.')
        return 3

    datos = parsear_url(url)
    pg_restore = shutil.which('pg_restore')
    comando = _construir_comando(pg_restore or 'pg_restore', datos, archivo, not args.no_clean)

    if args.dry_run:
        print('DRY-RUN: se ejecutaria:')
        print('  ' + ' '.join(comando))
        return 0

    if not pg_restore:
        print('ERROR: no se encontro pg_restore en el PATH (instala postgresql-client).')
        return 4

    if not args.yes:
        print('ATENCION: esta operacion BORRA y recrea objetos en:')
        print(f'  {datos["host"]}/{datos["dbname"]}')
        print('Repite con --yes para confirmar.')
        return 5

    entorno = dict(os.environ)
    if datos['password']:
        entorno['PGPASSWORD'] = datos['password']

    print(f'Restaurando {archivo.name} -> {datos["host"]}/{datos["dbname"]} ...')
    try:
        subprocess.run(comando, env=entorno, check=True)
    except Exception as error:
        print(f'ERROR: pg_restore fallo: {error}')
        return 6
    print('OK: restauracion completada. Verifica con: /health/ready')
    return 0


if __name__ == '__main__':
    sys.exit(main())

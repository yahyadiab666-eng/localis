"""Upsert de URLs de prueba en catalogo_maestro_imagenes y verificación de lectura."""
import os
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
load_dotenv(os.path.join(ROOT, '.env'), override=False)

from backend.catalogo_maestro import (
    imagen_maestro_por_codigo,
    sembrar_catalogo_maestro_imagenes,
)


def main():
    print('Purgando URLs artificiales del catálogo maestro...')
    sembrar_catalogo_maestro_imagenes()
    for codigo in ('7591001000011', '7591001000035'):
        url = imagen_maestro_por_codigo(codigo)
        print(f'leer {codigo}:', repr(url or ''))


if __name__ == '__main__':
    main()

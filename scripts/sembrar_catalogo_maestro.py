"""Políticas Storage del catálogo maestro. No rellena productos al arrancar."""
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
    print('Asegurando políticas Storage (sin relleno masivo de productos)...')
    sembrar_catalogo_maestro_imagenes(rellenar=False)
    for codigo in ('7591001000011', '7591001000035'):
        url = imagen_maestro_por_codigo(codigo)
        print(f'leer {codigo}:', repr(url or ''))


if __name__ == '__main__':
    main()

"""Pruebas de decodificación, encabezados y validación del importador CSV."""
import io
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.inventory_import import (
    cargar_archivo_inventario,
    detectar_mapeo_columnas,
    leer_encabezados_inventario,
    recortar_mensaje_importacion,
    validar_inventario_previo,
    _decodificar_csv,
)


def _archivo(nombre, data):
    return SimpleNamespace(filename=nombre, stream=io.BytesIO(data))


def _ok(condicion, mensaje):
    if not condicion:
        raise AssertionError(mensaje)
    print('OK', mensaje)


def main():
    utf8 = 'nombre,precio\nCafé,1.5\nPan,2\n'.encode('utf-8')
    data, ext, err = cargar_archivo_inventario(_archivo('inv.csv', utf8))
    _ok(err is None and ext == 'csv', 'CSV UTF-8 se lee')
    encabezados, err = leer_encabezados_inventario(data, ext)
    _ok(err is None and encabezados == ['nombre', 'precio'], 'encabezados UTF-8')
    mapeo, meta, err = detectar_mapeo_columnas(encabezados)
    _ok(err is None and 'nombre' in mapeo and 'precio' in mapeo, 'mapeo obligatorio')
    valido, msg, _ = validar_inventario_previo(data, ext, encabezados, mapeo, meta)
    _ok(valido and msg is None, 'validación UTF-8')

    latin1 = 'nombre;precio\nCafé ñandú;3,50\n'.encode('latin-1')
    data, ext, err = cargar_archivo_inventario(_archivo('inv.csv', latin1))
    _ok(err is None, 'CSV Latin-1 se lee')
    texto = _decodificar_csv(data)
    _ok(texto and 'ñandú' in texto, 'decodifica Latin-1')
    encabezados, err = leer_encabezados_inventario(data, ext)
    _ok(err is None, 'encabezados Latin-1 / ;')
    mapeo, meta, err = detectar_mapeo_columnas(encabezados)
    _ok(err is None, 'mapeo con delimitador ;')
    valido, msg, _ = validar_inventario_previo(data, ext, encabezados, mapeo, meta)
    _ok(valido, 'validación Latin-1')

    raros = 'producto,costo\nAgua,1\n'.encode('utf-8')
    data, ext, err = cargar_archivo_inventario(_archivo('inv.csv', raros))
    encabezados, err = leer_encabezados_inventario(data, ext)
    mapeo, meta, err = detectar_mapeo_columnas(encabezados)
    _ok(err is None and mapeo['nombre'] == 'producto', 'sinónimos de cabecera')

    sin_precio = 'nombre,stock\nPan,4\n'.encode('utf-8')
    data, ext, err = cargar_archivo_inventario(_archivo('inv.csv', sin_precio))
    encabezados, err = leer_encabezados_inventario(data, ext)
    mapeo, meta, err = detectar_mapeo_columnas(encabezados)
    _ok(err and 'Precio' in err, 'rechaza cabecera sin precio')

    binario = b'\x00\x01\x02nombre,precio\n'
    data, ext, err = cargar_archivo_inventario(_archivo('inv.csv', binario))
    _ok(err and 'binario' in err.lower(), 'rechaza CSV con NUL')

    utf16 = 'nombre,precio\nLeche,4\n'.encode('utf-16')
    data, ext, err = cargar_archivo_inventario(_archivo('inv.csv', utf16))
    _ok(err is None, 'CSV UTF-16 no se rechaza como binario')
    encabezados, err = leer_encabezados_inventario(data, ext)
    _ok(err is None and encabezados[:1] == ['nombre'], f'encabezados UTF-16 {encabezados}')

    vacio, _, err = cargar_archivo_inventario(_archivo('inv.csv', b''))
    _ok(err and 'vacío' in err.lower(), 'archivo vacío')

    xlsx_falso, _, err = cargar_archivo_inventario(_archivo('foto.png', b'123'))
    _ok(err and '.csv' in err, 'rechaza extensión no soportada')

    recortado = recortar_mensaje_importacion('x' * 5000)
    _ok(len(recortado) <= 1400, 'mensaje de flash recortado')

    from backend.image_lookup import (
        preparar_mapa_imagenes_importacion,
        programar_asociacion_imagenes_inventario,
    )
    from backend.inventory_import import _imagen_final_importacion

    mapa = preparar_mapa_imagenes_importacion(
        [
            {
                'nombre': 'Harina',
                'codigo_barras': '7590000040110',
                'imagen_url': None,
            }
        ],
        snapshot_imagenes={},
    )
    _ok(mapa == {}, 'preparar_mapa de importación no inventa fotos')
    url = _imagen_final_importacion(None, '7590000040110', {}, mapa or {})
    _ok(url is None, 'INSERT CSV sin foto manual queda vacío (API diferida)')

    hilo = programar_asociacion_imagenes_inventario(0)
    _ok(hilo.daemon, 'asociación de imágenes corre en hilo daemon')

    print('Todas las pruebas de importación CSV pasaron.')


if __name__ == '__main__':
    main()

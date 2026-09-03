#!/usr/bin/env python3
"""
Verifica que la cascada de imagenes priorice el codigo de barras
y solo use nombre + descripcion como respaldo.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# Nutella 350g — EAN estable en Open Food Facts
EAN_NUTELLA = '3017620422003'
URL_CODIGO = (
    'https://images.openfoodfacts.org/images/products/'
    '301/762/042/2003/front_en.400.jpg'
)
URL_NOMBRE = (
    'https://images.openfoodfacts.org/images/products/'
    '000/000/000/0000/underwood-wine.jpg'
)
AVISO_CSV = (
    'La asociación automática de imágenes puede contener errores o no ser exacta'
)


def _ok(condicion, mensaje, errores):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    errores.append(mensaje)
    return False


def _probar_score(errores):
    print('\n=== Score anti falso positivo ===')
    from backend.image_manager import _score_hit_nombre, _tokens_nombre

    tokens = _tokens_nombre('Diablitos Underwood 115g')
    vino = {
        'product_name': 'Underwood Rose Wine',
        'brands': 'Underwood',
    }
    pate = {
        'product_name': 'Diablitos Underwood',
        'brands': 'Underwood',
    }
    _ok(len(tokens) >= 2, f'tokens nombre={tokens}', errores)
    _ok(
        _score_hit_nombre(tokens, vino) == 0,
        'Underwood vino NO puntua para Diablitos Underwood',
        errores,
    )
    _ok(
        _score_hit_nombre(tokens, pate) >= 2,
        'Diablitos Underwood si coincide con el pate',
        errores,
    )


def _probar_orden_mocks(errores):
    print('\n=== Orden de cascada (mocks) ===')
    from backend.image_manager import _descubrir_y_persistir_oficial

    URL_STORAGE = (
        'https://wesnnnvoavprgqcczzsg.supabase.co/storage/v1/object/public/'
        'imagenes/productos/cat_mock.webp'
    )
    llamadas = []

    def fake_codigo(codigo, familia):
        llamadas.append(('codigo', codigo, familia))
        return URL_CODIGO

    def fake_nombre(nombre, familia, descripcion=None, **_kwargs):
        llamadas.append(('nombre', nombre, descripcion))
        return URL_NOMBRE

    with patch(
        'backend.image_manager._buscar_codigo_en_fuentes', side_effect=fake_codigo
    ), patch(
        'backend.image_manager._buscar_nombre_en_fuentes', side_effect=fake_nombre
    ), patch(
        'backend.image_manager._espejar_en_storage', return_value=URL_STORAGE
    ), patch(
        'backend.image_manager.guardar_imagen_maestro', return_value=None
    ):
        url = _descubrir_y_persistir_oficial(
            EAN_NUTELLA,
            nombre='Diablitos Underwood 115g',
            descripcion='pate de carne',
            categoria='Alimentos',
        )

    tipos = [item[0] for item in llamadas]
    print(f'  llamadas={llamadas}')
    _ok(url == URL_STORAGE, 'con EAN se espeja a Storage (no OFF cruda)', errores)
    _ok(tipos[:1] == ['codigo'], 'el primer intento es por codigo de barras', errores)
    _ok('nombre' not in tipos, 'con EAN exitoso NO se busca por nombre', errores)

    llamadas.clear()

    def fake_codigo_vacio(codigo, familia):
        llamadas.append(('codigo', codigo, familia))
        return None

    with patch(
        'backend.image_manager._buscar_codigo_en_fuentes',
        side_effect=fake_codigo_vacio,
    ), patch(
        'backend.image_manager._buscar_nombre_en_fuentes', side_effect=fake_nombre
    ), patch(
        'backend.image_manager._espejar_en_storage', return_value=URL_STORAGE
    ), patch(
        'backend.image_manager.guardar_imagen_maestro', return_value=None
    ):
        url_respaldo = _descubrir_y_persistir_oficial(
            '0000000000000',
            nombre='Harina PAN 1kg',
            descripcion='harina de maiz precocida',
            categoria='Alimentos',
        )

    tipos = [item[0] for item in llamadas]
    print(f'  llamadas_respaldo={llamadas}')
    _ok(tipos == ['codigo', 'nombre'], 'si el EAN falla, luego nombre+descripcion', errores)
    _ok(url_respaldo == URL_STORAGE, 'respaldo por nombre se espeja a Storage', errores)
    _ok(
        llamadas[1][2] == 'harina de maiz precocida',
        'el respaldo recibe la descripcion',
        errores,
    )

    llamadas.clear()

    def fail_si_codigo(*_args, **_kwargs):
        errores.append('sin codigo no debe consultarse EAN de fuentes')
        return None

    with patch(
        'backend.image_manager._buscar_codigo_en_fuentes',
        side_effect=fail_si_codigo,
    ), patch(
        'backend.image_manager._buscar_nombre_en_fuentes', side_effect=fake_nombre
    ), patch(
        'backend.image_manager._espejar_en_storage', return_value=URL_STORAGE
    ):
        url_solo_nombre = _descubrir_y_persistir_oficial(
            None,
            nombre='Harina PAN',
            descripcion='harina de maiz',
            categoria='Alimentos',
        )

    _ok(url_solo_nombre == URL_STORAGE, 'sin EAN espeja nombre a Storage', errores)
    _ok(
        llamadas and llamadas[0][0] == 'nombre',
        'sin EAN el primer (y unico) paso es nombre',
        errores,
    )

    with patch(
        'backend.image_manager._buscar_codigo_en_fuentes', side_effect=fake_codigo
    ), patch(
        'backend.image_manager._buscar_nombre_en_fuentes', side_effect=fake_nombre
    ), patch(
        'backend.image_manager._espejar_en_storage', return_value=None
    ), patch(
        'backend.image_manager.guardar_imagen_maestro', return_value=None
    ):
        url_sin_espejo = _descubrir_y_persistir_oficial(
            EAN_NUTELLA,
            nombre='Nutella',
            categoria='Alimentos',
        )
    _ok(
        url_sin_espejo is None,
        'si el espejo falla NO se persiste URL OFF cruda',
        errores,
    )


def _probar_ean_en_vivo(errores):
    print('\n=== EAN en vivo vs nombre tramposo ===')
    from backend.image_manager import descubrir_imagen_catalogo
    from backend.utils import imagen_url_almacenada

    url = descubrir_imagen_catalogo(
        codigo_barras=EAN_NUTELLA,
        nombre='Diablitos Underwood 115g',
        descripcion='pate de carne enlatado',
        categoria='Alimentos',
    )
    print(f'  url_ean={url!r}')
    _ok(bool(imagen_url_almacenada(url)), 'EAN Nutella devolvio URL Storage/local', errores)
    texto = str(url or '').lower()
    _ok(
        'storage/v1/object/public' in texto or '/static/uploads/' in texto,
        'la URL persistida es Storage o upload local (no OFF cruda)',
        errores,
    )
    _ok(
        'openfoodfacts.org' not in texto,
        'no se persistio host Open Food Facts crudo',
        errores,
    )
    _ok(
        'underwood' not in texto and 'wine' not in texto and 'rose' not in texto,
        'no se asocio el vino Underwood',
        errores,
    )


def _probar_generico_sin_foto(errores):
    print('\n=== Genericos no inventan foto ===')
    from backend.image_manager import descubrir_imagen_catalogo

    url = descubrir_imagen_catalogo(nombre='Martillo', categoria='Ferretería')
    print(f'  url_martillo={url!r}')
    _ok(url is None, 'Martillo generico no persiste foto de calle', errores)


def _probar_aviso_csv(errores):
    print('\n=== Aviso CSV en UI ===')
    html = (RAIZ / 'templates' / 'comercio.html').read_text(encoding='utf-8')
    css = (RAIZ / 'static' / 'css' / 'responsive.css').read_text(encoding='utf-8')
    _ok('aviso-csv-imagenes' in html, 'bloque de aviso en el importador CSV', errores)
    _ok(AVISO_CSV in html, 'texto de disclaimer presente', errores)
    _ok('editar o subir la imagen manualmente' in html.lower(), 'invita a correccion manual', errores)
    _ok('.aviso-csv-imagenes' in css, 'estilos del aviso CSV', errores)


def _probar_subida_manual(errores):
    print('\n=== Subida manual hibrida ===')
    from PIL import Image, ImageDraw
    from werkzeug.datastructures import FileStorage

    from backend.image_lookup import persistir_imagen_producto_hibrida
    from backend.utils import imagen_url_almacenada

    img = Image.new('RGB', (900, 600), (20, 90, 60))
    ImageDraw.Draw(img).rectangle((80, 80, 820, 520), fill=(245, 158, 11))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=88)
    archivo = FileStorage(
        stream=io.BytesIO(buf.getvalue()),
        filename='manual-cascada.jpg',
        content_type='image/jpeg',
    )
    url, aviso = persistir_imagen_producto_hibrida(
        file_storage=archivo,
        codigo_barras=EAN_NUTELLA,
        nombre='Producto con EAN pero foto propia',
        comercio_id='cascada',
    )
    print(f'  url_manual={url!r} aviso={aviso!r}')
    _ok(bool(imagen_url_almacenada(url)), 'manual deja URL persistible', errores)
    _ok(
        'openfoodfacts' not in str(url or '').lower(),
        'la foto manual no se sustituye por el EAN',
        errores,
    )


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(RAIZ / '.env', override=True)
    errores = []
    _probar_score(errores)
    _probar_orden_mocks(errores)
    _probar_ean_en_vivo(errores)
    _probar_generico_sin_foto(errores)
    _probar_aviso_csv(errores)
    _probar_subida_manual(errores)

    print('\n=== RESULTADO ===')
    if errores:
        print('FALLO cascada barcode:')
        for item in errores:
            print(f'  - {item}')
        return 1
    print(
        'OK cascada: EAN primero, espejo Storage obligatorio, '
        'sin OFF cruda, sin falso positivo Underwood, aviso CSV, subida manual.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

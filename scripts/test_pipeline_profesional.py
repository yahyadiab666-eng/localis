#!/usr/bin/env python3
"""Pruebas del pipeline profesional de imágenes (sin red ni base de datos).

Verifica:
  - Búsqueda/validación/filtros de seguridad (marcas, dominios, relevancia).
  - Validación de calidad (descarta pequeñas, planas y de aspecto extremo).
  - Procesamiento a fondo blanco puro (rembg con respaldo local).
  - Orquestación: almacenamiento y actualización de productos.imagen_url.
  - Migración: Barcode Spider desactivado por defecto.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

_ERRORES = []


def _ok(condicion, mensaje):
    if condicion:
        print(f'  OK  {mensaje}')
        return True
    print(f'  FALLO  {mensaje}')
    _ERRORES.append(mensaje)
    return False


def _imagen_sintetica(lado=900, plano=False):
    from PIL import Image, ImageDraw

    img = Image.new('RGB', (lado, lado), (255, 255, 255))
    if not plano:
        d = ImageDraw.Draw(img)
        d.rounded_rectangle(
            [lado * 0.35, lado * 0.30, lado * 0.65, lado * 0.85],
            radius=40,
            fill=(200, 30, 40),
        )
        d.rectangle([lado * 0.45, lado * 0.15, lado * 0.55, lado * 0.32], fill=(180, 20, 30))
        d.ellipse([lado * 0.40, lado * 0.50, lado * 0.60, lado * 0.70], fill=(240, 220, 40))
    buffer = io.BytesIO()
    img.save(buffer, 'PNG')
    return buffer.getvalue()


def _bytes_webp(lado=800):
    from PIL import Image

    img = Image.new('RGB', (lado, lado), (255, 255, 255))
    buffer = io.BytesIO()
    img.save(buffer, 'WEBP')
    return buffer.getvalue()


def main() -> int:
    import services.professional_image_pipeline as P

    print('=== Filtros de seguridad ===')
    _ok(P._dominio_bloqueado('https://images.google.com/x.jpg'), 'bloquea Google Images')
    _ok(P._dominio_bloqueado('https://http2.mlstatic.com/x.jpg'), 'bloquea CDN de marketplace')
    _ok(P._dominio_bloqueado('https://www.shutterstock.com/x.jpg'), 'bloquea bancos de stock')
    _ok(not P._dominio_bloqueado('https://max.com/x.jpg'), 'no confunde max.com con x.com')
    _ok(P._url_imagen_valida('https://www.farmatodo.com.ve/prod/x.jpg'), 'permite fuente confiable')

    print('\n=== Protección de imágenes ===')
    _ok(P._imagen_puede_reemplazarse(None), 'reemplaza vacío')
    _ok(P._imagen_puede_reemplazarse('/static/img/placeholder-producto.svg'), 'reemplaza placeholder')
    _ok(P._imagen_puede_reemplazarse('https://cdn.barcodespider.test/e.jpg'), 'reemplaza API vieja')
    _ok(
        not P._imagen_puede_reemplazarse(
            'https://x.supabase.co/storage/v1/object/public/imagenes/productos/a.webp'
        ),
        'conserva Storage',
    )
    _ok(not P._imagen_puede_reemplazarse('/static/uploads/productos/a.webp'), 'conserva subida manual')

    print('\n=== Inferencias ===')
    _ok(P._inferir_presentacion('Coca-Cola 2L') == '2L', 'detecta presentación 2L')
    _ok(P._inferir_presentacion('Harina 1kg', None) == '1kg', 'detecta presentación 1kg')
    _ok(P._off_url_alta_res('.../front.1.400.jpg') == '.../front.1.jpg', 'deriva variante de alta resolución')

    print('\n=== Validación de calidad ===')
    ok, motivo, meta = P.validar_calidad(_imagen_sintetica())
    _ok(ok and meta.get('ancho') == 900, f'imagen de estudio válida ({motivo})')
    ok_p, _, _ = P.validar_calidad(_imagen_sintetica(lado=900, plano=True))
    _ok(not ok_p, 'descarta imagen plana (logo/banner)')
    ok_s, _, _ = P.validar_calidad(_imagen_sintetica(lado=200))
    _ok(not ok_s, 'descarta imagen bajo el mínimo de resolución')

    print('\n=== Fondo blanco puro (respaldo sin rembg) ===')
    from PIL import Image

    with patch.object(P, '_quitar_fondo_rembg', side_effect=RuntimeError('simulado')):
        procesada, content_type = P.procesar_fondo_blanco(_imagen_sintetica())
    _ok(bool(procesada) and content_type == 'image/webp', 'procesa con respaldo local')
    if procesada:
        img = Image.open(io.BytesIO(procesada))
        _ok(img.size == (800, 800), f'lienzo final {img.size}')
        _ok(img.convert('RGB').getpixel((3, 3)) == (255, 255, 255), 'fondo blanco puro #FFFFFF')

    print('\n=== Orquestación (red/BD/Storage simulados) ===')
    producto = {
        'id': 7,
        'nombre': 'Harina P.A.N. 1kg',
        'descripcion': 'maíz blanco',
        'codigo_barras': '7702084137520',
        'imagen_url': None,
    }
    capturado = {}
    with patch.object(P, '_leer_producto', return_value=dict(producto)), patch.object(
        P, 'buscar_candidatos', return_value=[P.Candidato(url='https://fuente.test/x.jpg', fuente='test')]
    ), patch.object(P, '_evaluar_candidato', return_value=(_bytes_webp(), {'ancho': 800})), patch.object(
        P, '_almacenar_imagen', return_value=('/static/uploads/productos/auto_test.webp', 'local')
    ), patch.object(
        P, '_actualizar_imagen', side_effect=lambda pid, url, f: capturado.update(update=(pid, url, f)) or True
    ), patch.object(P, '_log_pipeline', lambda *a, **k: None):
        resultado = P.procesar_producto(7, categoria='Alimentos')

    _ok(resultado.ok and resultado.url, 'procesa y vincula imagen')
    _ok(capturado.get('update', (None,))[0] == 7, 'actualiza el producto correcto')
    _ok('profesional_test' in (capturado.get('update', (None, None, ''))[2] or ''), 'etiqueta la fuente profesional')

    capturado.clear()
    producto['imagen_url'] = (
        'https://x.supabase.co/storage/v1/object/public/imagenes/productos/manual.webp'
    )
    with patch.object(P, '_leer_producto', return_value=dict(producto)), patch.object(
        P, '_actualizar_imagen', side_effect=lambda *a: capturado.update(update=a) or True
    ):
        resultado = P.procesar_producto(7)
    _ok(not resultado.ok and resultado.motivo == 'imagen_manual_conservada', 'no pisa foto manual')
    _ok('update' not in capturado, 'no actualiza cuando hay foto manual')

    print('\n=== Fuentes multifuente y atajo de velocidad ===')
    _ok(callable(getattr(P, '_buscar_vtex', None)), 'fuente VTEX (catálogo local) disponible')
    _ok(callable(getattr(P, '_buscar_mercadolibre', None)), 'fuente Mercado Libre disponible')
    _ok(
        P._url_imagen_valida('https://http2.mlstatic.com/x.jpg', confiable=True),
        'acepta ML como fuente confiable',
    )
    _ok(
        not P._url_imagen_valida('https://http2.mlstatic.com/x.jpg'),
        'rechaza ML en scraping genérico',
    )

    llamadas = {'rembg': 0}

    def _rembg_no_llamar(_data):
        llamadas['rembg'] += 1
        raise RuntimeError('no debería llamarse para fondo limpio')

    original_rembg = P._quitar_fondo_rembg
    P._quitar_fondo_rembg = _rembg_no_llamar
    try:
        procesada_rapida, _ct = P.procesar_fondo_blanco(_imagen_sintetica())
    finally:
        P._quitar_fondo_rembg = original_rembg
    _ok(bool(procesada_rapida) and llamadas['rembg'] == 0, 'fondo limpio evita rembg (rápido)')

    print('\n=== Código legacy de Barcode Spider purgado ===')
    ruta_legado = RAIZ / 'services' / 'smart_image_pipeline.py'
    _ok(not ruta_legado.exists(), 'services/smart_image_pipeline.py eliminado')
    import importlib.util

    _ok(
        importlib.util.find_spec('services.smart_image_pipeline') is None,
        'el módulo legacy ya no es importable',
    )

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        return 1
    print('OK pipeline profesional de imágenes')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

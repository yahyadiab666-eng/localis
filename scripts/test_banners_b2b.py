"""Banners B2B de la portada: precios, medidas y utilidades (sin BD ni red).

Comprueba que el alquiler cobra lo que debe (Pago Móvil $60 / Binance Pay $50),
que la imagen se reencuadra EXACTAMENTE a 1200 x 400 px sin deformarse, que los
enlaces peligrosos se rechazan y que el concepto del pago se puede reactivar
desde el panel admin.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from backend import banners_b2b  # noqa: E402
from backend.boosts import precio_boost  # noqa: E402
from backend.verificacion_pagos import concepto_banner, parsear_concepto  # noqa: E402

FALLOS = []


def _ok(cond, mensaje):
    print(('  OK  ' if cond else '  FALLO  ') + mensaje)
    if not cond:
        FALLOS.append(mensaje)
    return bool(cond)


def _imagen_bytes(ancho, alto, formato='PNG'):
    from PIL import Image

    imagen = Image.new('RGB', (ancho, alto), (245, 158, 11))
    buffer = io.BytesIO()
    imagen.save(buffer, formato)
    return buffer.getvalue()


def main() -> int:
    print('=== Precios del alquiler ===')
    _ok(banners_b2b.precio_banner('pago_movil') == 60.0, 'pago móvil -> 60 USD')
    _ok(banners_b2b.precio_banner('binance') == 50.0, 'binance -> 50 USD')
    _ok(banners_b2b.precio_banner('BINANCE') == 50.0, 'método sin distinguir mayúsculas')
    _ok(banners_b2b.precio_banner() == 60.0, 'sin método -> pago móvil')
    oferta = banners_b2b.opciones_banner()
    _ok(
        oferta['precio_usd'] == 60.0
        and oferta['precio_binance_usd'] == 50.0
        and oferta['descuento_binance_usd'] == 10.0,
        'oferta expone ambos precios y el descuento',
    )
    _ok(
        oferta['ancho'] == 1200 and oferta['alto'] == 400,
        'medidas oficiales 1200 x 400',
    )
    _ok(oferta['dias'] == 30 and oferta['rotacion_segundos'] == 60, '30 días y rotación de 60 s')
    _ok(
        banners_b2b.DIAS_BANNER == 30 and oferta['maximo'] == 5,
        'alquiler de 30 días y máximo 5 banners',
    )
    _ok(
        banners_b2b.MAX_BANNERS_ACTIVOS == 5
        and banners_b2b.MAX_BANNERS_PORTADA == 5,
        'la portada sirve como máximo 5 banners',
    )
    cupos = banners_b2b.cupos_banners()
    _ok(
        cupos['maximo'] == 5 and 0 <= cupos['disponibles'] <= 5,
        'cupos disponibles entre 0 y 5',
    )
    _ok(
        cupos['ocupados'] + cupos['disponibles'] == 5,
        'ocupados + disponibles = capacidad total',
    )

    print('\n=== Destacados con descuento de Binance ===')
    _ok(precio_boost(3) == 2.0 and precio_boost(3, 'binance') == 1.0, '3 días: 2 -> 1 USD')
    _ok(precio_boost(5) == 3.0 and precio_boost(5, 'binance') == 2.0, '5 días: 3 -> 2 USD')
    _ok(precio_boost(7) == 4.0 and precio_boost(7, 'binance') == 3.0, '7 días: 4 -> 3 USD')
    _ok(precio_boost(4, 'binance') is None, 'duración inválida -> None')

    print('\n=== Referencias de pago ===')
    _ok(banners_b2b.referencia_valida('123456') is True, 'pago móvil: 6 dígitos')
    _ok(banners_b2b.referencia_valida('12345') is False, 'pago móvil: 5 dígitos rechazados')
    _ok(
        banners_b2b.referencia_valida('459421234567', 'binance') is True,
        'binance: ID de orden válido',
    )
    _ok(banners_b2b.sanitizar_referencia(' 12-34 56 ') == '123456', 'normaliza a solo dígitos')

    print('\n=== Enlaces del banner ===')
    _ok(
        banners_b2b.sanitizar_enlace_banner('https://tutienda.com/promo')
        == 'https://tutienda.com/promo',
        'https permitido',
    )
    _ok(banners_b2b.sanitizar_enlace_banner('/tienda/12') == '/tienda/12', 'ruta interna permitida')
    _ok(banners_b2b.sanitizar_enlace_banner('javascript:alert(1)') is None, 'javascript rechazado')
    _ok(banners_b2b.sanitizar_enlace_banner('//evil.com') is None, 'URL relativa a host ajeno rechazada')
    _ok(banners_b2b.sanitizar_enlace_banner('') is None, 'vacío -> None')

    print('\n=== Imagen del banner: medidas exactas ===')
    payload, tipo, nombre = banners_b2b.normalizar_imagen_banner(_imagen_bytes(1000, 600))
    from PIL import Image

    with Image.open(io.BytesIO(payload)) as resultado:
        _ok(resultado.size == (1200, 400), f'reencuadra a 1200x400 (obtenido {resultado.size})')
    _ok(tipo == 'image/webp' and nombre.endswith('.webp'), 'sube como WebP')
    _ok(len(payload) > 0, 'devuelve bytes')

    try:
        banners_b2b.normalizar_imagen_banner(_imagen_bytes(500, 500))
        _ok(False, 'imagen cuadrada rechazada')
    except banners_b2b.BannerInvalido:
        _ok(True, 'imagen cuadrada rechazada')

    try:
        banners_b2b.normalizar_imagen_banner(b'no-es-una-imagen')
        _ok(False, 'bytes inválidos rechazados')
    except banners_b2b.BannerInvalido:
        _ok(True, 'bytes inválidos rechazados')

    print('\n=== Concepto del pago (reactivación desde el admin) ===')
    _ok(concepto_banner(30) == 'banner:30', 'concepto del banner')
    _ok(parsear_concepto('banner:30') == {'tipo': 'banner', 'dias': 30}, 'parsea banner')
    _ok(parsear_concepto('banner:x') is None, 'días no numéricos -> None')
    _ok(parsear_concepto('banner') is None, 'sin días -> None')

    print('\n=== Sin BD no rompe la portada ===')
    _ok(banners_b2b.listar_banners_activos() == [], 'sin BD devuelve lista vacía')
    _ok(banners_b2b.estado_banners_comercio(1) == [], 'estado del panel degrada a lista vacía')

    print('\n=== RESULTADO ===')
    if FALLOS:
        print(f'FALLOS: {len(FALLOS)}')
        return 1
    print('OK Banners B2B: precios, medidas de imagen y conceptos de pago')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

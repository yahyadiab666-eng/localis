#!/usr/bin/env python3
"""Anti-invención: la importación nunca fabrica imágenes.

Genera 2.000 productos sintéticos (sin código de barras, sin índice maestro y
sin red) y verifica que NINGUNO reciba un asset fabricado (placeholder de
categoría, monograma o tarjeta): los que no tienen foto verificada quedan con
``imagen_url=None`` (estado neutro). Comprueba además que el clasificador de
categorías y los formatos masivos siguen funcionando y que el reporte es honesto.
"""

from __future__ import annotations

import sys
import time
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


def _fabricados(productos):
    from backend.activos_verificados import es_asset_generado

    return [
        p for p in productos
        if es_asset_generado(p.get('imagen_url'), p.get('imagen_fuente'))
    ]


def _con_imagen(productos):
    return [p for p in productos if p.get('imagen_url')]


def _probar_clasificador():
    print('\n=== Clasificador universal por categoría ===')
    from backend.categorias_producto import clasificar_categoria

    casos = {
        'Refresco Pepsi 1.5L': 'bebidas',
        'Celular Samsung Galaxy A15': 'tecnologia',
        'Taladro percutor 650W': 'ferreteria',
        'Harina de maíz P.A.N. 1kg': 'alimentos',
        'Shampoo anticaspa 375ml': 'belleza',
        'Camisa manga larga': 'ropa',
        'Cuaderno universitario 100 hojas': 'papeleria',
        'Pañal desechable talla M': 'bebes',
        'Llanta radial 195/65': 'automotriz',
        'Balón de fútbol No. 5': 'deportes',
        'Alimento para perro 2kg': 'mascotas',
        'Rompecabezas infantil 100 piezas': 'juguetes',
        'Tabletas de ibuprofeno 400mg': 'salud',
        'Detergente en polvo 1kg': 'hogar',
        'Producto raro sin pistas': 'otros',
    }
    for nombre, esperado in casos.items():
        obtenido = clasificar_categoria(nombre=nombre)
        _ok(obtenido == esperado, f'{nombre!r} -> {obtenido} (esperado {esperado})')


def _generar_productos(total):
    ejemplos = [
        ('Refresco Cola 2L', 'bebidas'),
        ('Celular 128GB', 'tecnologia'),
        ('Taladro 650W', 'ferreteria'),
        ('Harina de maíz 1kg', 'alimentos'),
        ('Shampoo 375ml', 'belleza'),
        ('Camisa manga larga', 'ropa'),
        ('Cuaderno 100 hojas', 'papeleria'),
        ('Pañal talla M', 'bebes'),
        ('Llanta 195/65', 'automotriz'),
        ('Balón de fútbol', 'deportes'),
        ('Alimento para gato 2kg', 'mascotas'),
        ('Ibuprofeno 400mg', 'salud'),
        ('Detergente en polvo', 'hogar'),
        ('Repuesto genérico', 'otros'),
    ]
    productos = []
    for indice in range(total):
        nombre, categoria_esperada = ejemplos[indice % len(ejemplos)]
        productos.append(
            {
                'nombre': f'{nombre} lote {indice}',
                'descripcion': '',
                'marca': '',
                'categoria': '',
                'codigo_barras': None,
                'imagen_url': None,
                '_esperada': categoria_esperada,
            }
        )
    return productos


def _probar_sin_invencion():
    print('\n=== Sin invención: 2.000 productos, índice vacío ===')
    from backend.catalogo_maestro_index import IndiceMaestro
    from backend.inventory_import import asignar_imagenes_instantaneas

    total = 2000
    productos = _generar_productos(total)
    indice_vacio = IndiceMaestro()

    inicio = time.perf_counter()
    with patch('backend.catalogo_maestro_index.obtener_indice', return_value=indice_vacio):
        sin_imagen = asignar_imagenes_instantaneas(
            productos, snapshot_imagenes={}, categoria=None
        )
    duracion = time.perf_counter() - inicio

    _ok(sin_imagen == total, f'los {total} quedan sin foto verificada ({sin_imagen})')
    _ok(duracion < 5.0, f'procesa {total} en {duracion * 1000:.0f} ms')
    _ok(not _con_imagen(productos), f'cero imágenes inventadas ({len(_con_imagen(productos))})')
    _ok(not _fabricados(productos), 'cero assets fabricados (placeholder/monograma/tarjeta)')
    _ok(
        all(p.get('imagen_estado') == 'pendiente' for p in productos),
        'todos marcados como pendientes (estado neutro)',
    )
    _ok(
        all(p.get('categoria_inferida') for p in productos),
        'la categoría se infiere para la UI aunque no haya imagen',
    )

    aciertos = sum(
        1 for p in productos if p.get('categoria_inferida') == p.get('_esperada')
    )
    precision = aciertos / total
    _ok(precision >= 0.9, f'precisión de categoría {precision:.1%} (>= 90%)')


def _probar_master_verificado():
    print('\n=== Solo se acepta una foto verificada del catálogo maestro ===')
    from backend.catalogo_maestro_index import IndiceMaestro
    from backend.inventory_import import asignar_imagenes_instantaneas

    indice = IndiceMaestro()
    indice.por_codigo = {
        '7591234567890': 'https://x.supabase.co/storage/v1/object/public/imagenes/productos/real.webp'
    }
    indice.por_nombre = {}

    productos = [
        {'nombre': 'Producto real', 'descripcion': '', 'marca': '',
         'codigo_barras': '7591234567890', 'imagen_url': None},
        {'nombre': 'Sin foto', 'descripcion': '', 'marca': '',
         'codigo_barras': '0000000000000', 'imagen_url': None},
    ]
    with patch('backend.catalogo_maestro_index.obtener_indice', return_value=indice):
        asignar_imagenes_instantaneas(productos, {}, None)

    _ok(
        productos[0]['imagen_url'] and productos[0]['imagen_estado'] == 'real',
        'la foto verificada del maestro se conserva',
    )
    _ok(productos[1]['imagen_url'] is None, 'lo que no está verificado queda vacío')


def _probar_descarte_generado():
    print('\n=== Un asset fabricado entrante se descarta ===')
    from backend.catalogo_maestro_index import IndiceMaestro
    from backend.inventory_import import asignar_imagenes_instantaneas

    productos = [
        {'nombre': 'Con placeholder', 'descripcion': '', 'marca': '',
         'codigo_barras': None, 'imagen_url': '/static/img/placeholder-alimentos.svg',
         'imagen_fuente': 'placeholder_categoria'},
        {'nombre': 'Con monograma', 'descripcion': '', 'marca': '',
         'codigo_barras': None, 'imagen_url': '/static/uploads/marcas/acme.png',
         'imagen_fuente': 'logo_monograma'},
    ]
    with patch('backend.catalogo_maestro_index.obtener_indice', return_value=IndiceMaestro()):
        asignar_imagenes_instantaneas(productos, {}, None)
    _ok(
        all(p['imagen_url'] is None for p in productos),
        'los assets fabricados se descartan al importar',
    )


def _probar_formatos_masivos():
    print('\n=== Formatos masivos CSV/XLSX/XLS sin inventar ===')
    from types import SimpleNamespace

    from backend.inventory_import import (
        asignar_imagenes_instantaneas,
        cargar_archivo_inventario,
        detectar_mapeo_columnas,
        iter_lotes_productos,
        leer_encabezados_inventario,
    )
    from backend.catalogo_maestro_index import IndiceMaestro

    encabezados_cols = ['nombre', 'precio', 'categoria', 'marca']
    muestras = [
        ('Refresco Cola 2L', 2.0, 'Bebidas', 'Cola'),
        ('Celular 128GB', 180.0, 'Tecnología', 'MarcaX'),
        ('Taladro 650W', 45.0, 'Ferretería', 'MarcaY'),
        ('Harina de maíz 1kg', 1.5, 'Alimentos', 'PAN'),
        ('Shampoo 375ml', 4.2, 'Cuidado Personal', 'MarcaZ'),
        ('Camisa manga larga', 9.9, 'Ropa', 'MarcaW'),
        ('Cuaderno 100 hojas', 1.1, 'Papelería', 'MarcaV'),
        ('Pañal talla M', 8.0, 'Bebés', 'MarcaU'),
        ('Llanta 195/65', 60.0, 'Automotriz', 'MarcaT'),
        ('Balón de fútbol', 12.0, 'Deportes', 'MarcaS'),
    ]

    def _csv(total):
        lineas = [','.join(encabezados_cols)]
        for i in range(total):
            nombre, precio, categoria, marca = muestras[i % len(muestras)]
            lineas.append(f'{nombre} lote {i},{precio},{categoria},{marca}')
        return ('\n'.join(lineas) + '\n').encode('utf-8')

    def _xlsx(total):
        import openpyxl

        libro = openpyxl.Workbook()
        hoja = libro.active
        hoja.append(encabezados_cols)
        for i in range(total):
            hoja.append(list(muestras[i % len(muestras)]))
        buffer = __import__('io').BytesIO()
        libro.save(buffer)
        return buffer.getvalue()

    def _xls(total):
        try:
            import xlwt
        except ImportError:
            return None
        libro = xlwt.Workbook()
        hoja = libro.add_sheet('Inv')
        for col, titulo in enumerate(encabezados_cols):
            hoja.write(0, col, titulo)
        for i in range(total):
            nombre, precio, categoria, marca = muestras[i % len(muestras)]
            hoja.write(i + 1, 0, nombre)
            hoja.write(i + 1, 1, precio)
            hoja.write(i + 1, 2, categoria)
            hoja.write(i + 1, 3, marca)
        buffer = __import__('io').BytesIO()
        libro.save(buffer)
        return buffer.getvalue()

    casos = [
        ('inventario.csv', 'csv', _csv(2000), 2000),
        ('inventario.xlsx', 'xlsx', _xlsx(300), 300),
        ('inventario.xls', 'xls', _xls(300), 300),
    ]

    for nombre_archivo, extension, contenido, esperado in casos:
        if contenido is None:
            print(f'  OMITIDO  {extension} (xlwt no instalado)')
            continue
        archivo = SimpleNamespace(filename=nombre_archivo, stream=__import__('io').BytesIO(contenido))
        datos, ext, error = cargar_archivo_inventario(archivo)
        if error:
            _ok(False, f'{extension}: {error}')
            continue
        encabezados, error = leer_encabezados_inventario(datos, ext)
        mapeo, meta, error = detectar_mapeo_columnas(encabezados)
        if error:
            _ok(False, f'{extension} mapeo: {error}')
            continue

        inicio = time.perf_counter()
        productos = []
        for lote in iter_lotes_productos(datos, ext, encabezados, mapeo, meta):
            productos.extend(lote)
        with patch('backend.catalogo_maestro_index.obtener_indice', return_value=IndiceMaestro()):
            asignar_imagenes_instantaneas(productos, snapshot_imagenes={}, categoria=None)
        duracion = time.perf_counter() - inicio

        _ok(len(productos) == esperado, f'{extension}: {len(productos)} productos leídos')
        _ok(not _fabricados(productos), f'{extension}: 0 assets fabricados')
        _ok(duracion < 5.0, f'{extension}: {len(productos)} en {duracion * 1000:.0f} ms')
        categorias = {p.get('categoria_inferida') for p in productos} if productos else set()
        _ok(len(categorias) >= 5, f'{extension}: {len(categorias)} categorías inferidas')


def _probar_reporte_honesto():
    print('\n=== Reporte honesto (solo imágenes reales) ===')
    from backend.estado_imagenes import construir_reporte_importacion

    _mensaje, meta_sin = construir_reporte_importacion(2000, 0, 2000)
    _ok(meta_sin['estado_imagenes'] == 'sin_reales', 'sin reales -> estado "sin_reales"')

    _mensaje2, meta_parcial = construir_reporte_importacion(2000, 12, 1988)
    _ok(meta_parcial['estado_imagenes'] == 'parcial', 'mezcla -> estado "parcial"')
    _ok(meta_parcial['imagenes_reales'] == 12, 'reporta el número de imágenes reales')

    _mensaje3, meta_ok = construir_reporte_importacion(50, 50, 0)
    _ok(meta_ok['estado_imagenes'] == 'completo', 'todas reales -> estado "completo"')


def _probar_logos_oficiales():
    print('\n=== Logos: solo arte oficial, nunca inventado ===')
    from backend.marca_logo import logo_instantaneo, resolver_logo_marca
    from backend.activos_verificados import es_asset_generado, es_asset_verificado

    _ok(logo_instantaneo('Altunsa') is None, 'logo_instantaneo ya no fabrica monogramas')
    url, fuente = resolver_logo_marca('MarcaSinArteOficialQwerty', permitir_red=False)
    _ok(url is None and fuente is None, 'sin red y sin arte oficial -> (None, None)')

    _ok(es_asset_generado('/static/uploads/marcas/x.png', 'logo_monograma'), 'monograma = fabricado')
    _ok(es_asset_generado('/static/img/placeholder-otros.svg'), 'placeholder = fabricado')
    _ok(
        es_asset_verificado('https://x.supabase.co/storage/v1/object/public/imagenes/productos/a.webp'),
        'foto de Storage = verificada',
    )
    _ok(
        es_asset_verificado('/static/uploads/productos/manual_1_a.webp'),
        'subida manual = verificada',
    )
    _ok(
        not es_asset_verificado('/static/uploads/genericos/producto_a.png', 'tarjeta_producto'),
        'tarjeta generada = NO verificada',
    )
    _ok(
        not es_asset_verificado('https://sitio-ajeno.example/foto.jpg'),
        'URL externa no confiable = NO verificada',
    )


def _probar_consulta_estructurada():
    print('\n=== Consulta limpia marca + modelo (estructurados) ===')
    from backend.consulta_producto import consulta_estructurada

    queries = consulta_estructurada(
        'Licuadora Oster 2 Velocidades Original Unidad', categoria='tecnologia'
    )
    _ok(queries and 'oster' in queries[0].lower(), f'usa la marca: {queries[:1]}')
    _ok(
        all('unidad' not in q.lower() and 'original' not in q.lower() for q in queries),
        'descarta relleno (unidad, original)',
    )

    queries_tal = consulta_estructurada(
        'Taladro percutor Bosch GSB 550 500W', marca='Bosch', categoria='ferreteria'
    )
    _ok(
        any('gsb' in q.lower() and '550' in q.lower() for q in queries_tal),
        f'extrae el modelo exacto: {queries_tal[:1]}',
    )

    _ok(
        consulta_estructurada('Pan canilla integral', categoria='alimentos') == [],
        'un producto no estructurado no usa esta vía',
    )


def main() -> int:
    _probar_clasificador()
    _probar_sin_invencion()
    _probar_master_verificado()
    _probar_descarte_generado()
    _probar_formatos_masivos()
    _probar_reporte_honesto()
    _probar_logos_oficiales()
    _probar_consulta_estructurada()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK anti-invención: cero assets fabricados, solo fotos verificadas o estado neutro')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Cobertura visual universal: ningún producto queda sin imagen.

Genera 2.000 productos sintéticos de múltiples categorías (sin código de barras,
sin índice maestro y sin red) y verifica que la asignación instantánea entregue
a TODOS una imagen de placeholder profesional de su categoría, en memoria y en
milisegundos. Además comprueba que cada SVG referenciado exista en disco.
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

    _ok(
        clasificar_categoria(nombre='Componente X', categoria_hint='Ferretería') == 'ferreteria',
        'respeta la categoría declarada en el archivo',
    )

    from backend.categorias_producto import imagen_para_categoria

    _ok(
        imagen_para_categoria('Tecnología') == '/static/img/placeholder-tecnologia.svg',
        'mapea categoría declarada "Tecnología"',
    )
    _ok(
        imagen_para_categoria('Cuidado Personal') == '/static/img/placeholder-belleza.svg',
        'mapea "Cuidado Personal" -> belleza',
    )


def _generar_productos(total):
    import random

    ejemplos = [
        ('Refresco Cola 2L', 'bebidas'),
        ('Jugo de naranja 1L', 'bebidas'),
        ('Celular 128GB', 'tecnologia'),
        ('Audífonos inalámbricos', 'tecnologia'),
        ('Taladro 650W', 'ferreteria'),
        ('Juego de destornilladores', 'ferreteria'),
        ('Harina de maíz 1kg', 'alimentos'),
        ('Arroz blanco 1kg', 'alimentos'),
        ('Aceite vegetal 1L', 'alimentos'),
        ('Shampoo 375ml', 'belleza'),
        ('Crema dental 90g', 'belleza'),
        ('Camisa manga larga', 'ropa'),
        ('Zapatos deportivos', 'ropa'),
        ('Cuaderno 100 hojas', 'papeleria'),
        ('Pañal talla M', 'bebes'),
        ('Llanta 195/65', 'automotriz'),
        ('Balón de fútbol', 'deportes'),
        ('Alimento para gato 2kg', 'mascotas'),
        ('Rompecabezas 100 piezas', 'juguetes'),
        ('Ibuprofeno 400mg', 'salud'),
        ('Detergente en polvo', 'hogar'),
        ('Lámpara LED', 'hogar'),
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


def _probar_cobertura_masiva():
    print('\n=== Cobertura masiva (2.000 productos, índice vacío) ===')
    from backend.catalogo_maestro_index import IndiceMaestro
    from backend.inventory_import import asignar_imagenes_instantaneas

    total = 2000
    productos = _generar_productos(total)
    indice_vacio = IndiceMaestro()

    inicio = time.perf_counter()
    with patch('backend.catalogo_maestro_index.obtener_indice', return_value=indice_vacio):
        nuevos = asignar_imagenes_instantaneas(productos, snapshot_imagenes={}, categoria=None)
    duracion = time.perf_counter() - inicio

    _ok(nuevos == total, f'los {total} usan placeholder de categoría ({nuevos})')
    _ok(duracion < 5.0, f'procesa {total} en {duracion * 1000:.0f} ms')

    sin_imagen = [p for p in productos if not p.get('imagen_url')]
    _ok(not sin_imagen, f'cero productos sin imagen ({len(sin_imagen)})')

    reales = [p for p in productos if p.get('imagen_estado') == 'real']
    pendientes = [p for p in productos if p.get('imagen_estado') == 'pendiente']
    _ok(not reales, f'sin imágenes reales con índice vacío ({len(reales)})')
    _ok(len(pendientes) == total, f'todos marcados como pendientes ({len(pendientes)})')

    malas = [
        p
        for p in productos
        if not str(p.get('imagen_url') or '').startswith('/static/img/placeholder-')
    ]
    _ok(not malas, f'todos usan placeholder limpio ({len(malas)} no)')

    # Cada SVG referenciado debe existir en disco.
    faltantes = set()
    for producto in productos:
        rel = str(producto['imagen_url']).lstrip('/')
        if not (RAIZ / rel).is_file():
            faltantes.add(producto['imagen_url'])
    _ok(not faltantes, f'todos los SVG existen en disco ({sorted(faltantes)[:3]})')

    # Exactitud de categoría (muestra completa por coincidencia de nombre).
    aciertos = 0
    for producto in productos:
        inferida = producto.get('categoria_inferida')
        if inferida == producto.get('_esperada'):
            aciertos += 1
    precision = aciertos / total
    _ok(precision >= 0.95, f'precisión de categoría {precision:.1%} (>= 95%)')

    resumen = {}
    for producto in productos:
        cat = producto['categoria_inferida']
        resumen[cat] = resumen.get(cat, 0) + 1
    _ok(
        len(resumen) >= 10,
        f'distribuye en {len(resumen)} categorías distintas',
    )
    _ok(
        all(not str(c).startswith('sin') for c in resumen),
        'ninguna categoría queda como "sin imagen"',
    )


def _probar_formatos_masivos():
    print('\n=== Formatos masivos CSV/XLSX/XLS con cobertura 100% ===')
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

        vacios = [p for p in productos if not p.get('imagen_url')]
        _ok(len(productos) == esperado, f'{extension}: {len(productos)} productos leídos')
        _ok(not vacios, f'{extension}: 0 sin imagen')
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


def _probar_respaldo_marca():
    print('\n=== Respaldo visual por marca (logo/monograma) ===')
    from unittest.mock import patch

    from backend.catalogo_maestro_index import IndiceMaestro
    from backend.inventory_import import asignar_imagenes_instantaneas
    from backend.marca_logo import (
        iniciales_marca,
        logo_instantaneo,
        monograma_png,
        resolver_logo_marca,
    )

    _ok(iniciales_marca('Fama de América') == 'FA', 'iniciales de marca multi-palabra')
    _ok(iniciales_marca('Altunsa') == 'AL', 'iniciales de marca simple')
    png = monograma_png('Altunsa')
    _ok(png[:8] == b'\x89PNG\r\n\x1a\n', 'monograma PNG válido')
    url = logo_instantaneo('Altunsa')
    _ok(bool(url) and url.startswith('/static/uploads/marcas/'), 'monograma local disponible')
    ruta = RAIZ / url.lstrip('/')
    _ok(ruta.is_file() and ruta.stat().st_size > 0, 'archivo de monograma en disco')

    url2, fuente2 = resolver_logo_marca('Altunsa', permitir_red=False)
    _ok(bool(url2) and fuente2 == 'logo_monograma', 'resolver cae a monograma sin red')

    productos = [
        {'nombre': 'Detergente Alta Espuma', 'descripcion': '', 'marca': 'Altunsa',
         'categoria': '', 'codigo_barras': None, 'imagen_url': None},
        {'nombre': 'Café molido', 'descripcion': '', 'marca': 'Fama de América',
         'categoria': '', 'codigo_barras': None, 'imagen_url': None},
    ]
    with patch('backend.catalogo_maestro_index.obtener_indice', return_value=IndiceMaestro()):
        asignar_imagenes_instantaneas(productos, {}, 'Alimentos')
    _ok(
        all(p['imagen_estado'] == 'logo' and p['imagen_url'].startswith('/static/uploads/marcas/')
            for p in productos),
        'la importación asigna logo/monograma de marca (nunca placeholder genérico)',
    )


def _probar_catalogo_panaderia():
    print('\n=== Cobertura visual: catálogo de panadería (244 productos) ===')
    from unittest.mock import patch

    from backend.catalogo_maestro_index import IndiceMaestro
    from backend.inventory_import import asignar_imagenes_instantaneas

    base = [
        ('Pan Canilla', ''), ('Pan Frances', ''), ('Pan de Jamon', 'Plumrose'),
        ('Torta de Chocolate', ''), ('Cachito de Jamon', ''), ('Pan de Queso', ''),
        ('Croissant', ''), ('Dona Glaseada', ''), ('Pan Integral', ''),
        ('Pan Campesino', ''), ('Torta Tres Leches', ''), ('Quesillo', ''),
        ('Palmera', ''), ('Rosca de Reyes', ''), ('Pan de Leche', 'Mavesa'),
        ('Baguette', ''), ('Pan de Ajo', ''), ('Empanada de Queso', ''),
        ('Tequeños', ''), ('Pan de Maiz', 'PAN'), ('Galletas de Mantequilla', 'Quaker'),
        ('Chocolatina', 'Ferrero'), ('Malta', 'Polar'), ('Jugo de Naranja', 'Polar'),
        ('Refresco Cola', 'Pepsi'), ('Chicle', 'Colgate'), ('Cafe Molido', 'Nestle'),
    ]
    productos = []
    for i in range(244):
        nombre, marca = base[i % len(base)]
        productos.append({
            'nombre': f'{nombre} {i + 1}',
            'descripcion': 'producto de panaderia' if marca else '',
            'marca': marca,
            'categoria': '',
            'codigo_barras': None,
            'imagen_url': None,
        })
    with patch('backend.catalogo_maestro_index.obtener_indice', return_value=IndiceMaestro()):
        asignar_imagenes_instantaneas(productos, {}, 'Alimentos')

    vacios = [p for p in productos if not p.get('imagen_url')]
    _ok(not vacios, f'0 tarjetas sin identidad visual ({len(vacios)})')
    estados = {}
    for p in productos:
        estados[p['imagen_estado']] = estados.get(p['imagen_estado'], 0) + 1
    print('  estados:', estados)
    _ok(estados.get('logo', 0) > 0, f'{estados.get("logo", 0)} con logo/monograma de marca')
    _ok(estados.get('pendiente', 0) > 0, f'{estados.get("pendiente", 0)} con imagen de categoría')
    faltantes = set()
    for p in productos:
        rel = str(p['imagen_url']).lstrip('/')
        if rel.startswith('static') and not (RAIZ / rel).is_file():
            faltantes.add(p['imagen_url'])
    _ok(not faltantes, f'todos los assets existen ({sorted(faltantes)[:3]})')


def main() -> int:
    _probar_clasificador()
    _probar_cobertura_masiva()
    _probar_formatos_masivos()
    _probar_reporte_honesto()
    _probar_respaldo_marca()
    _probar_catalogo_panaderia()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK cobertura universal: 100% de productos con imagen de categoría')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

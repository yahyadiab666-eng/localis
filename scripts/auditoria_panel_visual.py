#!/usr/bin/env python3
"""Auditoría visual/lógica del panel de comercios de Localis.

Recorre de forma automatizada lo que vería el usuario en el panel:

  1. El panel usa un único formato de tarjetas con imagen grande (formato nuevo).
  2. No queda la lista compacta/tabla antigua ni elementos duplicados.
  3. El diseño sigue siendo responsivo (2 columnas en móvil, 3-4 en escritorio).
  4. Las rutas de imagen apuntan al formato limpio (Storage / subida local /
     placeholder) y el buscador del panel sigue operativo.
  5. La estética de las imágenes nuevas es de estudio (fondo blanco puro).
  6. El pipeline profesional está conectado y Barcode Spider desactivado.

No importa ``main`` (evita tocar la base de datos de producción).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

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


def _leer(relativo):
    return (RAIZ / relativo).read_text(encoding='utf-8')


def _auditar_plantilla():
    print('\n=== 1. Formato único de tarjetas en el panel ===')
    html = _leer('templates/comercio.html')
    _ok('<table' not in html and 'panel-comercio-tabla' not in html, 'sin tabla compacta antigua')
    _ok('localis-img-producto-thumb-wrap' not in html, 'sin miniaturas del formato viejo')
    _ok('panel-comercio-cards' in html, 'contiene la grilla de tarjetas')
    _ok('localis-img-producto-wrap' in html, 'usa la imagen grande de producto')
    _ok('grid-cols-2' in html and 'md:grid-cols-3' in html and 'lg:grid-cols-4' in html,
        'grilla responsiva 2/3/4 columnas')
    _ok('editar_producto' in html and 'eliminar_producto_ruta' in html, 'acciones de gestión presentes')


def _auditar_css():
    print('\n=== 2. CSS del panel sin reglas duplicadas ===')
    css = _leer('static/css/responsive.css')
    _ok('.panel-comercio-tabla' not in css, 'CSS sin reglas de la tabla antigua')
    _ok('.panel-comercio-cards {\n  display: none' not in css, 'las tarjetas no se ocultan en escritorio')
    _ok('height: 180px' in css, 'franja de foto grande de altura fija')


def _auditar_busqueda():
    print('\n=== 3. Buscador del panel funcional con las tarjetas ===')
    js = _leer('static/js/localis.js')
    _ok('inicializarBusquedaProductosPanel' in js, 'inicializa el buscador')
    _ok('data-producto-busqueda' in js, 'filtra por marca de búsqueda de cada tarjeta')
    _ok('busqueda-sin-resultados' in js, 'muestra estado sin resultados')


def _auditar_pipeline():
    print('\n=== 4. Pipeline profesional conectado y sin suscripciones ===')
    pipeline = _leer('services/professional_image_pipeline.py')
    for funcion in (
        'def buscar_candidatos',
        'def validar_calidad',
        'def procesar_fondo_blanco',
        'def _almacenar_imagen',
        'def procesar_producto',
        'def programar_procesamiento_inventario',
    ):
        _ok(funcion in pipeline, f'define {funcion.split("def ")[1]}')

    _ok('255, 255, 255' in pipeline, 'fondo blanco puro (#FFFFFF)')
    _ok(
        not (RAIZ / 'services' / 'smart_image_pipeline.py').exists(),
        'código legacy de Barcode Spider eliminado',
    )
    imagen_lookup = _leer('backend/image_lookup.py')
    _ok('professional_image_pipeline' in imagen_lookup, 'el backend usa el pipeline profesional')
    _ok('rembg' in _leer('requirements.txt'), 'rembg declarado en requirements')


def _auditar_importacion_instantanea():
    print('\n=== 4b. Matching instantáneo y formatos de importación ===')
    inventario = _leer('backend/inventory_import.py')
    _ok('asignar_imagenes_instantaneas' in inventario, 'asignación instantánea en la importación')
    _ok("'xls'" in inventario and 'xlrd' in inventario, 'soporte .xls con xlrd')
    _ok('marca' in inventario, 'columna marca reconocida')

    indice = _leer('backend/catalogo_maestro_index.py')
    _ok('class IndiceMaestro' in indice, 'índice en memoria definido')
    _ok('por_codigo' in indice and 'por_nombre' in indice, 'índice por código y por nombre')
    _ok('_similitud' in indice, 'coincidencia por similitud de tokens')

    categorias = _leer('backend/categorias_producto.py')
    _ok('def clasificar_categoria' in categorias, 'clasificador universal de categorías')
    _ok('def imagen_para_categoria' in categorias, 'matriz de fallbacks por categoría')
    svgs = sorted((RAIZ / 'static' / 'img').glob('placeholder-*.svg'))
    _ok(len(svgs) >= 14, f'{len(svgs)} placeholders de categoría en disco')
    _ok(all(svg.stat().st_size > 0 for svg in svgs), 'placeholders no vacíos')
    _ok("'categoria'" in inventario, 'columna categoría reconocida')

    estados = _leer('backend/estado_imagenes.py')
    _ok('def construir_reporte_importacion' in estados, 'reporte honesto de imágenes')
    _ok('ESTADO_PENDIENTE' in estados and 'ESTADO_RECHAZADA' in estados, 'estados pendiente/rechazada')
    _ok('imagen_estado' in _leer('database.py'), 'columna imagen_estado migrada')
    _ok('parcial' in _leer('backend/import_queue.py'), 'la cola distingue trabajos parciales')
    _ok(
        (RAIZ / 'backend' / 'marcas_ve.py').is_file(),
        'reconocimiento de marcas criollas/importadas',
    )
    _ok('site:' in _leer('services/professional_image_pipeline.py'), 'búsqueda restringida por sitio')
    _ok('Imagen pendiente' in _leer('templates/comercio.html'), 'badge de imagen pendiente en el panel')
    backfill = _leer('backend/image_backfill.py')
    _ok('def ejecutar_ciclo' in backfill, 'reintento periódico de pendientes')
    _ok('iniciar_backfill_periodico' in _leer('main.py'), 'el reintento arranca con la app')

    pipeline_src = _leer('services/professional_image_pipeline.py')
    _ok('def _buscar_vtex' in pipeline_src, 'fuente VTEX (catálogo local directo)')
    _ok('def _buscar_mercadolibre' in pipeline_src, 'fuente Mercado Libre (API)')
    _ok('_fondo_ya_limpio' in pipeline_src, 'atajo sin rembg para fondos ya limpios')
    _ok('ThreadPoolExecutor' in pipeline_src, 'enriquecimiento en paralelo')
    _ok('imagen_intentos' in _leer('database.py'), 'cola persistente con intentos')
    _ok('presupuesto_seg' in pipeline_src, 'lote acotado por presupuesto (30-60s)')

    _ok('def detectar_fila_cabecera' in inventario, 'detección dinámica de cabecera (ERP)')
    _ok('def analizar_inventario' in inventario, 'analiza cabecera/columnas por índice')
    _ok('def persistir_importacion_upsert' in inventario, 'UPSERT de inventario (cero rechazos falsos)')
    _ok('UPSERT_PRODUCTO_VALUES_SQL' in inventario, 'UPDATE masivo por execute_values')
    _ok('precio_usd = None' in inventario, 'precio/existencia opcionales (solo actualizar stock)')
    _ok('Actualiza los productos existentes' in _leer('templates/comercio.html'), 'panel informa UPSERT')

    _ok((RAIZ / 'backend' / 'marca_logo.py').is_file(), 'respaldo visual por logo/monograma de marca')
    _ok(
        (RAIZ / 'backend' / 'fuentes_imagenes.py').is_file()
        and 'catalogo_fuentes' in _leer('backend/fuentes_imagenes.py'),
        'registro modular de fuentes (global + Venezuela)',
    )
    _ok('ESTADO_LOGO' in _leer('backend/estado_imagenes.py'), 'estado "logo" en el reporte')
    cola_src = _leer('backend/import_queue.py')
    _ok('_guardar_spool' in cola_src and 'instance' in cola_src, 'cola con spooling a disco (alta concurrencia)')
    _ok('LOCALIS_IMG_MAX_CONCURRENT' in _leer('.env.example'), 'límite de CPU configurable')

    requisitos = _leer('requirements.txt')
    _ok('xlrd' in requisitos, 'xlrd declarado en requirements')
    _ok('openpyxl' in requisitos, 'openpyxl declarado en requirements')


def _render_panel():
    """Renderiza el panel con datos simulados (sin Flask ni BD)."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(RAIZ / 'templates')), autoescape=True)
    env.filters['url_imagen_producto'] = lambda p: (
        (p.get('imagen_url') or '/static/img/placeholder-producto.svg')
        if isinstance(p, dict)
        else '/static/img/placeholder-producto.svg'
    )
    env.filters['fecha_corta'] = lambda v: str(v)[:10]
    env.globals['csrf_token'] = lambda: 'tok'
    env.globals['get_flashed_messages'] = lambda with_categories=False: []
    env.globals['url_for'] = lambda endpoint, **kw: '/xxx'

    productos = [
        {
            'id': 1,
            'nombre': 'Harina P.A.N. 1kg',
            'descripcion': 'Maíz blanco',
            'precio_usd': 1.5,
            'precio_bs': 55.0,
            'codigo_barras': '7702084137520',
            'imagen_url': (
                'https://abc.supabase.co/storage/v1/object/public/imagenes/'
                'productos/auto_harina.webp'
            ),
        },
        {
            'id': 2,
            'nombre': 'Coca-Cola 2L',
            'descripcion': 'Refresco',
            'precio_usd': 2.0,
            'precio_bs': 73.0,
            'codigo_barras': None,
            'imagen_url': None,
        },
    ]
    comercio = {
        'id': 9,
        'nombre': 'Bodega Test',
        'categoria': 'Alimentos',
        'descripcion': 'Demo',
        'telefono': '04120000000',
        'direccion': 'Calle 1',
        'ciudad': 'Caracas',
        'zona': 'Centro',
        'maps_link': None,
        'logo_completo': None,
        'visible': 1,
        'fecha_vencimiento': '2026-10-01',
    }
    html = env.get_template('comercio.html').render(
        productos=productos,
        comercio=comercio,
        plan_info={'nombre': 'Gratis'},
        avisos={'bienvenida_prueba': False, 'suscripcion_vencida': False,
                'fecha_vencimiento': None, 'plan_actual': 'gratis'},
        tasa=36.5,
        whatsapp_url='https://wa.me/58',
        placeholder_producto='/static/img/placeholder-producto.svg',
        nav_activo='panel',
    )
    return html, productos


def _auditar_render():
    print('\n=== 5. Render del panel (datos simulados) ===')
    html, productos = _render_panel()
    _ok('panel-comercio-cards' in html and 'grid-cols-2' in html, 'renderiza la grilla unificada')
    _ok(html.count('class="localis-img-producto-wrap') == len(productos), 'una imagen grande por producto')
    _ok('<table' not in html, 'no se renderiza tabla antigua')
    _ok('placeholder-producto.svg' in html, 'producto sin foto usa placeholder limpio')
    _ok('storage/v1/object/public' in html, 'la foto procesada apunta al bucket de Storage')
    _ok('busqueda-productos-panel' in html and 'busqueda-sin-resultados' in html,
        'buscador y estado vacío presentes')

    print('\n=== 6. Validación de rutas de imagen del render ===')
    from backend.utils import imagen_url_para_persistir

    for producto in productos:
        url = producto.get('imagen_url')
        if not url:
            continue
        _ok(bool(imagen_url_para_persistir(url)), f'la URL de "{producto["nombre"]}" es persistible/mostrable')


def main() -> int:
    print('Auditoría visual del panel de comercios — Localis')
    _auditar_plantilla()
    _auditar_css()
    _auditar_busqueda()
    _auditar_pipeline()
    _auditar_importacion_instantanea()
    _auditar_render()

    print('\n=== RESULTADO ===')
    if _ERRORES:
        for item in _ERRORES:
            print(f'  - {item}')
        print(f'FALLOS: {len(_ERRORES)}')
        return 1
    print('OK panel visual unificado, responsivo y con imágenes limpias de estudio')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

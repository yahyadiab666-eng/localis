"""Importador inteligente de inventario (CSV / Excel) con detección flexible de columnas."""

import csv
import io
import os
import re
import traceback
import unicodedata

import openpyxl

from config import MAX_UPLOAD_BYTES
from backend.utils import (
    EXPR_CODIGO_BARRAS,
    imagen_url_almacenada,
    imagen_url_para_persistir,
    normalizar_codigo_barras,
)

SINONIMOS_COLUMNA = {
    'nombre': [
        'nombre',
        'producto',
        'articulo',
        'artículo',
        'descripcion',
        'descripción',
        'item',
        'denominacion',
        'denominación',
        'concepto',
    ],
    'precio': [
        'precio',
        'costo',
        'pvp',
        'precio usd',
        'precio_usd',
        'precio bs',
        'precio_bs',
        'precio venta',
        'valor',
        'monto',
        'importe',
        'precio unitario',
    ],
    'stock': [
        'cantidad',
        'stock',
        'existencia',
        'existencias',
        'inventario',
        'qty',
        'unidades',
        'disponible',
    ],
    'descripcion': [
        'detalle',
        'observaciones',
        'notas',
        'descripcion larga',
        'descripción larga',
        'info adicional',
    ],
    'codigo_barras': [
        'codigo barras',
        'codigo_barras',
        'codigo de barras',
        'barcode',
        'ean',
        'sku',
        'codigo',
        'referencia',
    ],
    'imagen_url': [
        'imagen url',
        'imagen_url',
        'url imagen',
        'imagen',
        'foto',
        'fotos',
        'image',
        'photo',
        'picture',
        'img',
        'link imagen',
        'url foto',
        'foto url',
    ],
    'marca': [
        'marca',
        'brand',
        'fabricante',
        'marca producto',
        'marca del producto',
    ],
    'categoria': [
        'categoria',
        'rubro',
        'seccion',
        'departamento',
        'linea',
        'familia',
        'tipo producto',
    ],
}

ETIQUETAS_COLUMNA = {
    'nombre': 'Nombre del producto (nombre, producto, artículo, descripción…)',
    'precio': 'Precio (precio, costo, PVP, precio_usd, precio_bs…)',
    'stock': 'Cantidad / stock (cantidad, stock, existencia, inventario…)',
    'marca': 'Marca (marca, brand, fabricante…)',
    'categoria': 'Categoría (categoría, rubro, sección, departamento…)',
}

CAMPOS_OBLIGATORIOS = ('nombre', 'precio')
_MAX_ERRORES_VALIDACION = 20
_UMBRAL_COINCIDENCIA = 50

MAX_IMPORT_FILE_BYTES = min(
    int(os.getenv('MAX_IMPORT_FILE_BYTES', str(MAX_UPLOAD_BYTES))),
    MAX_UPLOAD_BYTES,
)
IMPORT_BATCH_SIZE = int(os.getenv('IMPORT_BATCH_SIZE', '500'))
LOG_PREFIX = '[Localis CSV]'


def _modo_bajas():
    """Cómo tratar productos que ya no vienen en el archivo.

    ``desactivar`` (por defecto): los marca inactivos (no aparecen en público).
    ``eliminar``: los borra. ``off``: no hace nada.
    """
    valor = str(os.getenv('LOCALIS_CSV_BAJAS', 'desactivar') or 'desactivar').strip().lower()
    if valor in ('eliminar', 'delete', 'borrar'):
        return 'eliminar'
    if valor in ('off', '0', 'no', 'nada', 'ninguno'):
        return 'off'
    return 'desactivar'
_MAX_FLASH_CHARS = 1400

INSERT_PRODUCTO_SQL = """
    INSERT INTO productos (
        comercio_id, nombre, descripcion, precio_usd,
        codigo_barras, imagen_url, imagen_fuente, imagen_estado, stock
    )
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# Variante para `execute_values` (inserción multi-fila en una sola sentencia).
INSERT_PRODUCTO_VALUES_SQL = """
    INSERT INTO productos (
        comercio_id, nombre, descripcion, precio_usd,
        codigo_barras, imagen_url, imagen_fuente, imagen_estado, stock
    )
    VALUES %s
"""


class ErrorImportacionInventario(Exception):
    """Error controlado durante importación masiva (mensaje amigable para el comercio)."""


def normalizar_encabezado(texto):
    if texto is None:
        return ''
    normalizado = unicodedata.normalize('NFKD', str(texto))
    normalizado = ''.join(c for c in normalizado if not unicodedata.combining(c))
    normalizado = normalizado.lower().strip()
    normalizado = re.sub(r'[_\-./\\]+', ' ', normalizado)
    normalizado = re.sub(r'\s+', ' ', normalizado)
    return normalizado


def _puntuacion_coincidencia(encabezado_norm, sinonimo):
    if not encabezado_norm or not sinonimo:
        return 0
    if encabezado_norm == sinonimo:
        return 100
    if encabezado_norm.startswith(f'{sinonimo} '):
        return 85
    palabras = encabezado_norm.split()
    if sinonimo in palabras:
        return 75
    if encabezado_norm.endswith(f' {sinonimo}'):
        return 70
    if sinonimo in encabezado_norm:
        return 55
    return 0


def detectar_mapeo_columnas(encabezados):
    """
    Detecta columnas por sinónimos en la primera fila.
    Retorna (mapeo, meta, error).
    """
    encabezados_originales = [
        str(h).strip()
        for h in (encabezados or [])
        if h is not None and str(h).strip()
    ]
    if not encabezados_originales:
        return None, None, 'El archivo no tiene encabezados en la primera fila.'

    encabezados_norm = {
        orig: normalizar_encabezado(orig) for orig in encabezados_originales
    }
    usados = set()
    mapeo = {}
    meta = {'precio_en_bs': False}

    orden_campos = (
        'nombre',
        'precio',
        'stock',
        'descripcion',
        'codigo_barras',
        'imagen_url',
        'marca',
        'categoria',
    )

    for campo in orden_campos:
        mejor_encabezado = None
        mejor_puntaje = 0

        for original, normalizado in encabezados_norm.items():
            if original in usados:
                continue
            for sinonimo in SINONIMOS_COLUMNA.get(campo, []):
                puntaje = _puntuacion_coincidencia(normalizado, sinonimo)
                if puntaje > mejor_puntaje:
                    mejor_puntaje = puntaje
                    mejor_encabezado = original

        if mejor_puntaje >= _UMBRAL_COINCIDENCIA and mejor_encabezado:
            mapeo[campo] = mejor_encabezado
            usados.add(mejor_encabezado)
            if campo == 'precio':
                norm_precio = encabezados_norm[mejor_encabezado]
                if any(
                    token in norm_precio
                    for token in ('bs', 'bolivar', 'bolívares', 'ves')
                ):
                    meta['precio_en_bs'] = True

    faltantes = [
        ETIQUETAS_COLUMNA[campo]
        for campo in CAMPOS_OBLIGATORIOS
        if campo not in mapeo
    ]
    if faltantes:
        detectadas = ', '.join(f'«{h}»' for h in encabezados_originales)
        return (
            None,
            None,
            'No pudimos reconocer columnas obligatorias en la primera fila. '
            f'Falta: {"; ".join(faltantes)}. '
            f'Columnas encontradas en tu archivo: {detectadas}. '
            'Corrige los encabezados e intenta de nuevo.',
        )

    return mapeo, meta, None


def _extension_archivo(archivo):
    if not archivo or not getattr(archivo, 'filename', ''):
        return None
    return archivo.filename.rsplit('.', 1)[-1].lower()


def recortar_mensaje_importacion(mensaje):
    """Evita flashes enormes que rompen la cookie de sesión (500 al redirigir)."""
    texto = str(mensaje or '').strip() or 'No se pudo completar la importación.'
    if len(texto) <= _MAX_FLASH_CHARS:
        return texto
    sufijo = '\n… (mensaje recortado).'
    return texto[: max(0, _MAX_FLASH_CHARS - len(sufijo))].rstrip() + sufijo


def _registrar_fallo_importacion(etapa, exc):
    print(f'{LOG_PREFIX} FALLO etapa={etapa} {type(exc).__name__}: {exc}')
    traceback.print_exc()


def _abrir_libro_xls(data):
    """Abre un libro .xls (formato binario viejo) con xlrd. Retorna (libro, error)."""
    try:
        import xlrd
    except ImportError:
        return None, (
            'Para leer archivos .xls falta la librería xlrd. '
            'Guarda el archivo como .xlsx o CSV e intenta de nuevo.'
        )
    try:
        return xlrd.open_workbook(file_contents=data), None
    except Exception as exc:
        _registrar_fallo_importacion('abrir_xls', exc)
        return None, (
            'El archivo .xls no se pudo leer. Ábrelo y guárdalo como .xlsx o CSV '
            'e intenta de nuevo.'
        )


def _valor_celda_xls(celda):
    if celda is None:
        return None
    try:
        return celda.value
    except Exception:
        return None


def cargar_archivo_inventario(archivo):
    """Lee el archivo subido con límite de tamaño. Retorna (bytes, extension, error)."""
    if not archivo or not getattr(archivo, 'filename', ''):
        return None, None, 'No se adjuntó ningún archivo.'

    extension = _extension_archivo(archivo)
    if extension not in {'csv', 'xlsx', 'xls'}:
        return None, None, 'El archivo debe tener extensión .csv, .xlsx o .xls.'

    try:
        stream = getattr(archivo, 'stream', archivo)
        if hasattr(stream, 'seek'):
            stream.seek(0)

        data = stream.read(MAX_IMPORT_FILE_BYTES + 1)
    except Exception as exc:
        _registrar_fallo_importacion('leer_stream', exc)
        return None, None, 'No se pudo leer el archivo subido. Intenta de nuevo.'

    if not data:
        return None, None, 'El archivo está vacío.'

    if len(data) > MAX_IMPORT_FILE_BYTES:
        max_mb = MAX_IMPORT_FILE_BYTES // (1024 * 1024)
        return (
            None,
            None,
            f'El archivo supera el tamaño máximo permitido ({max_mb} MB). '
            'Divide el inventario en archivos más pequeños e intenta de nuevo.',
        )

    es_utf16 = data.startswith(b'\xff\xfe') or data.startswith(b'\xfe\xff')
    if extension == 'csv' and not es_utf16 and b'\x00' in data[:65536]:
        return (
            None,
            None,
            'El archivo no es un CSV de texto válido (parece binario o Excel). '
            'En Excel usa «Guardar como → CSV UTF-8» e intenta de nuevo.',
        )

    return data, extension, None


def _detectar_delimitador_csv(muestra):
    texto = muestra or ''
    primera_linea = next((linea for linea in texto.splitlines() if linea.strip()), '')
    if not primera_linea:
        return ','
    try:
        if len(primera_linea) >= 8 and (',' in primera_linea or ';' in primera_linea):
            dialecto = csv.Sniffer().sniff(primera_linea, delimiters=',;\t|')
            return dialecto.delimiter
    except (csv.Error, TypeError, ValueError):
        pass
    if primera_linea.count(';') >= primera_linea.count(',') and ';' in primera_linea:
        return ';'
    if '\t' in primera_linea:
        return '\t'
    if '|' in primera_linea:
        return '|'
    return ','


_ENCODINGS_CSV = ('utf-8-sig', 'utf-8', 'cp1252', 'latin-1', 'iso-8859-1')
_MENSAJE_CODIFICACION_CSV = (
    'No se pudo leer la codificación del archivo CSV. '
    'Guárdalo como UTF-8 o Latin-1 (Excel: «CSV UTF-8») e intenta de nuevo.'
)


def _decodificar_csv(data):
    if not data:
        return None
    if data.startswith(b'\xff\xfe') or data.startswith(b'\xfe\xff'):
        try:
            texto = data.decode('utf-16')
            if '\x00' not in texto:
                return texto
        except UnicodeDecodeError:
            pass
    if b'\x00' in data[:65536]:
        return None
    for encoding in _ENCODINGS_CSV:
        try:
            texto = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if '\x00' in texto:
            continue
        return texto
    return None


def leer_encabezados_inventario(data, extension):
    """Lee solo la primera fila de encabezados. Retorna (encabezados, error)."""
    try:
        if extension == 'xlsx':
            wb = openpyxl.load_workbook(
                io.BytesIO(data), read_only=True, data_only=True
            )
            try:
                hoja = wb.active
                primera = next(hoja.iter_rows(min_row=1, max_row=1, values_only=True), None)
                if not primera:
                    return None, 'El archivo Excel está vacío.'
                encabezados = [str(v or '').strip() for v in primera]
                while encabezados and not encabezados[-1]:
                    encabezados.pop()
                if not encabezados:
                    return None, 'El archivo Excel no tiene encabezados en la primera fila.'
                return encabezados, None
            finally:
                wb.close()

        if extension == 'xls':
            libro, error_xls = _abrir_libro_xls(data)
            if error_xls:
                return None, error_xls
            hoja = libro.sheet_by_index(0)
            if hoja.nrows == 0:
                return None, 'El archivo Excel está vacío.'
            primera = hoja.row_values(0)
            encabezados = [str(v or '').strip() for v in primera]
            while encabezados and not encabezados[-1]:
                encabezados.pop()
            if not encabezados:
                return None, 'El archivo Excel no tiene encabezados en la primera fila.'
            return encabezados, None

        contenido = _decodificar_csv(data)
        if contenido is None:
            return None, _MENSAJE_CODIFICACION_CSV

        delimitador = _detectar_delimitador_csv(contenido[:4096])
        reader = csv.reader(io.StringIO(contenido), delimiter=delimitador)
        primera = next(reader, None)
        if not primera:
            return None, 'El archivo CSV está vacío.'
        encabezados = [str(h or '').strip() for h in primera if str(h or '').strip()]
        if not encabezados:
            return None, 'El archivo CSV no tiene encabezados en la primera fila.'
        return encabezados, None
    except csv.Error as exc:
        _registrar_fallo_importacion('encabezados_csv', exc)
        return None, (
            'El CSV no se pudo interpretar. Revisa la primera fila '
            '(delimitadores, comillas y caracteres especiales) y guárdalo como CSV UTF-8.'
        )
    except Exception as exc:
        _registrar_fallo_importacion('leer_encabezados', exc)
        return None, (
            'No se pudieron leer los encabezados del archivo. '
            'Verifica que sea un CSV o Excel válido e intenta de nuevo.'
        )


def _fila_tiene_datos(fila_dict):
    return any(str(v or '').strip() for v in fila_dict.values())


def _celda_vacia(valor):
    if valor is None:
        return True
    texto = str(valor).strip()
    return not texto or texto.lower() in ('none', 'null', 'nan', 'n/a', '-')


# ---------------------------------------------------------------------------
# Detección dinámica de cabecera (soporta reportes ERP con preámbulo y
# encabezados desalineados de las columnas de datos, típicos de .xls viejos).
# ---------------------------------------------------------------------------
_MAX_FILAS_CABECERA = 20


def _valor_fila_excel(celda):
    if celda is None:
        return None
    return celda.value if hasattr(celda, 'value') else celda


def _matriz_inicial(data, extension, max_filas=_MAX_FILAS_CABECERA):
    """Primeras ``max_filas`` filas del archivo como listas de valores."""
    if extension == 'xls':
        libro, error = _abrir_libro_xls(data)
        if error:
            return []
        hoja = libro.sheet_by_index(0)
        return [hoja.row_values(i) for i in range(min(hoja.nrows, max_filas))]
    if extension == 'xlsx':
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            hoja = wb.active
            matriz = []
            for indice, fila in enumerate(hoja.iter_rows()):
                if indice >= max_filas:
                    break
                matriz.append([_valor_fila_excel(c) for c in fila])
            return matriz
        finally:
            wb.close()
    contenido = _decodificar_csv(data)
    if contenido is None:
        return []
    delimitador = _detectar_delimitador_csv(contenido[:4096])
    lector = csv.reader(io.StringIO(contenido), delimiter=delimitador)
    matriz = []
    for indice, fila in enumerate(lector):
        if indice >= max_filas:
            break
        matriz.append(fila)
    return matriz


def _campos_en_fila(celdas):
    campos = set()
    for celdas_texto in celdas:
        texto = normalizar_encabezado(celdas_texto)
        if not texto:
            continue
        for campo, sinonimos in SINONIMOS_COLUMNA.items():
            if campo in campos:
                continue
            if any(
                _puntuacion_coincidencia(texto, sinonimo) >= _UMBRAL_COINCIDENCIA
                for sinonimo in sinonimos
            ):
                campos.add(campo)
    return campos


def detectar_fila_cabecera(matriz):
    """Fila de cabecera con más palabras clave. Retorna (indice, celdas, campos)."""
    mejor_indice = None
    mejor_campos = set()
    for indice, fila in enumerate((matriz or [])[:_MAX_FILAS_CABECERA]):
        campos = _campos_en_fila(fila)
        if len(campos) > len(mejor_campos):
            mejor_indice = indice
            mejor_campos = campos
    if mejor_indice is None or len(mejor_campos) < 2:
        return None, None, set()
    return mejor_indice, matriz[mejor_indice], mejor_campos


def _mapear_columnas_por_indice(encabezados, matriz, indice_cabecera):
    """{campo: índice de columna} con alineación exacta y, si el reporte está
    desalineado (típico ERP), emparejado por orden de columnas con datos."""
    filas_datos = (matriz or [])[indice_cabecera + 1 :]
    columnas_con_datos = set()
    for fila in filas_datos:
        for col, valor in enumerate(fila):
            if str(valor or '').strip():
                columnas_con_datos.add(col)

    etiquetas = {
        col: normalizar_encabezado(valor)
        for col, valor in enumerate(encabezados)
        if str(valor or '').strip()
    }

    orden_campos = (
        'codigo_barras', 'nombre', 'precio', 'stock',
        'descripcion', 'marca', 'categoria', 'imagen_url',
    )
    mapeo = {}
    etiquetas_usadas = set()

    # 1) Coincidencia exacta si la propia columna del encabezado tiene datos.
    for campo in orden_campos:
        for col in sorted(etiquetas):
            if col in etiquetas_usadas:
                continue
            if col not in columnas_con_datos:
                continue
            if any(
                _puntuacion_coincidencia(etiquetas[col], sinonimo) >= _UMBRAL_COINCIDENCIA
                for sinonimo in SINONIMOS_COLUMNA.get(campo, [])
            ):
                mapeo[campo] = col
                etiquetas_usadas.add(col)
                break

    # 2) Emparejado por orden para etiquetas que no coinciden con su columna.
    campos_restantes = [c for c in orden_campos if c not in mapeo]
    etiquetas_restantes = [
        (col, etiquetas[col]) for col in sorted(etiquetas) if col not in etiquetas_usadas
    ]
    asignaciones = []
    for campo in campos_restantes:
        for indice_etiqueta, (col, texto) in enumerate(etiquetas_restantes):
            if any(
                _puntuacion_coincidencia(texto, sinonimo) >= _UMBRAL_COINCIDENCIA
                for sinonimo in SINONIMOS_COLUMNA.get(campo, [])
            ):
                asignaciones.append(campo)
                etiquetas_restantes.pop(indice_etiqueta)
                break

    columnas_libres = sorted(c for c in columnas_con_datos if c not in mapeo.values())
    for campo, col in zip(asignaciones, columnas_libres):
        mapeo[campo] = col
    return mapeo


def analizar_inventario(data, extension):
    """Detecta cabecera y mapea columnas por índice.

    Retorna ``(indice_cabecera, encabezados, {campo: col}, error)``.
    """
    matriz = _matriz_inicial(data, extension)
    if not matriz:
        return None, None, None, 'El archivo está vacío o no se pudo leer.'
    indice, encabezados, campos = detectar_fila_cabecera(matriz)
    if indice is not None and {'nombre'} <= campos and (
        {'precio', 'stock'} & campos or {'codigo_barras'} & campos
    ):
        mapeo = _mapear_columnas_por_indice(encabezados, matriz, indice)
        if 'nombre' in mapeo:
            encabezados_limpios = [str(v or '').strip() for v in encabezados]
            return indice, encabezados_limpios, mapeo, None

    # Compatibilidad: cabecera clásica en la primera fila no vacía.
    for indice, fila in enumerate(matriz[:5]):
        celdas = [str(v or '').strip() for v in fila]
        if not any(celdas):
            continue
        mapeo_clasico, _meta, error = detectar_mapeo_columnas([c for c in celdas if c])
        if not error:
            columnas = {c: i for i, c in enumerate(celdas) if c}
            mapeo_indice = {
                campo: columnas[nombre]
                for campo, nombre in mapeo_clasico.items()
                if nombre in columnas
            }
            return indice, [c for c in celdas if c], mapeo_indice, None
    return None, None, None, (
        'No pudimos reconocer una fila de encabezados con columnas de '
        'producto (Código, Descripción, Costo/Precio, Existencia). '
        'Revisa el archivo e intenta de nuevo.'
    )


def _iter_filas_valores(data, extension, inicio):
    """Genera (numero_fila, valores) a partir de ``inicio`` (índice 0-based)."""
    if extension == 'xls':
        libro, error = _abrir_libro_xls(data)
        if error:
            raise ErrorImportacionInventario(error)
        hoja = libro.sheet_by_index(0)
        for indice in range(inicio, hoja.nrows):
            yield indice + 1, hoja.row_values(indice)
        return

    if extension == 'xlsx':
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            hoja = wb.active
            for indice, fila in enumerate(hoja.iter_rows()):
                if indice < inicio:
                    continue
                yield indice + 1, [_valor_fila_excel(c) for c in fila]
        finally:
            wb.close()
        return

    contenido = _decodificar_csv(data)
    if contenido is None:
        raise ErrorImportacionInventario(_MENSAJE_CODIFICACION_CSV)
    delimitador = _detectar_delimitador_csv(contenido[:4096])
    lector = csv.reader(io.StringIO(contenido), delimiter=delimitador)
    for indice, fila in enumerate(lector):
        if indice < inicio:
            continue
        yield indice + 1, fila


def iter_filas_inventario_enumeradas(data, extension, encabezados, columnas=None, fila_inicio=None):
    """Generador (numero_fila, fila_dict).

    - Modo clásico: datos desde la fila 2 (Excel) o equivalente CSV.
    - Modo ERP (``columnas`` = {campo: índice}): salta el preámbulo hasta
      ``fila_inicio`` y construye la fila por **índice de columna**, soportando
      encabezados desalineados de los reportes heredados.
    """
    if columnas is not None:
        inicio = 0 if fila_inicio is None else int(fila_inicio) + 1
        for numero, valores in _iter_filas_valores(data, extension, inicio):
            fila = {}
            for campo, col in columnas.items():
                valor = valores[col] if (col is not None and col < len(valores)) else None
                fila[campo] = valor
            if _fila_tiene_datos(fila):
                yield numero, fila
        return

    if extension == 'xlsx':
        wb = openpyxl.load_workbook(
            io.BytesIO(data), read_only=True, data_only=True
        )
        try:
            hoja = wb.active
            numero_fila = 1
            for row in hoja.iter_rows(min_row=2):
                numero_fila += 1
                if not any(
                    _valor_celda_excel(celda) not in (None, '')
                    for celda in row
                ):
                    continue
                fila = {}
                for idx, encabezado in enumerate(encabezados):
                    if not encabezado:
                        continue
                    celda = row[idx] if idx < len(row) else None
                    fila[encabezado] = _valor_celda_excel(celda)
                if _fila_tiene_datos(fila):
                    yield numero_fila, fila
        finally:
            wb.close()
        return

    if extension == 'xls':
        libro, error_xls = _abrir_libro_xls(data)
        if error_xls:
            raise ErrorImportacionInventario(error_xls)
        hoja = libro.sheet_by_index(0)
        for indice in range(1, hoja.nrows):
            fila_celdas = hoja.row(indice)
            if not any(
                _valor_celda_xls(celda) not in (None, '') for celda in fila_celdas
            ):
                continue
            fila = {}
            for idx, encabezado in enumerate(encabezados):
                if not encabezado:
                    continue
                celda = fila_celdas[idx] if idx < len(fila_celdas) else None
                fila[encabezado] = _valor_celda_xls(celda)
            if _fila_tiene_datos(fila):
                yield indice + 1, fila
        return

    contenido = _decodificar_csv(data)
    if contenido is None:
        raise ErrorImportacionInventario(_MENSAJE_CODIFICACION_CSV)

    delimitador = _detectar_delimitador_csv(contenido[:4096])
    try:
        reader = csv.DictReader(io.StringIO(contenido), delimiter=delimitador)
        for indice, fila in enumerate(reader, start=2):
            fila_limpia = {
                str(clave).strip(): valor
                for clave, valor in fila.items()
                if clave is not None and str(clave).strip()
            }
            if _fila_tiene_datos(fila_limpia):
                yield indice, fila_limpia
    except csv.Error as exc:
        _registrar_fallo_importacion('parsear_csv', exc)
        raise ErrorImportacionInventario(
            'El CSV no se pudo interpretar. Revisa delimitadores, comillas '
            'y caracteres especiales. Guárdalo como CSV UTF-8 e intenta de nuevo.'
        ) from exc


def iter_filas_inventario(data, extension, encabezados, columnas=None, fila_inicio=None):
    """Generador de filas {encabezado: valor} sin cargar todo el inventario en RAM."""
    for _, fila in iter_filas_inventario_enumeradas(
        data, extension, encabezados, columnas=columnas, fila_inicio=fila_inicio
    ):
        yield fila


def _valor_celda_excel(celda):
    """Lee valor o hipervínculo de una celda Excel (las fotos suelen ir como HYPERLINK)."""
    if celda is None:
        return None
    hyper = getattr(celda, 'hyperlink', None)
    if hyper is not None:
        destino = getattr(hyper, 'target', None) or getattr(hyper, 'display', None)
        if destino:
            return str(destino)
    return celda.value


def _obtener_valor_celda(fila, columna):
    if columna is None:
        return None
    if columna in fila:
        return fila[columna]
    columna_lower = str(columna).lower()
    for clave, valor in fila.items():
        if str(clave).lower() == columna_lower:
            return valor
    return None


def _parsear_numero(valor):
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto or texto.lower() == 'none':
        return None

    limpio = re.sub(r'[^\d,.\-]', '', texto)
    if not limpio:
        return None

    if ',' in limpio and '.' in limpio:
        if limpio.rfind(',') > limpio.rfind('.'):
            limpio = limpio.replace('.', '').replace(',', '.')
        else:
            limpio = limpio.replace(',', '')
    elif ',' in limpio:
        partes = limpio.split(',')
        if len(partes[-1]) == 2:
            limpio = ''.join(partes[:-1]).replace('.', '') + '.' + partes[-1]
        else:
            limpio = limpio.replace(',', '.')

    try:
        return float(limpio)
    except ValueError:
        return None


def _parsear_entero(valor):
    numero = _parsear_numero(valor)
    if numero is None:
        return 0
    return max(0, int(round(numero)))


def diagnosticar_fila_obligatoria(fila, mapeo, meta, numero_fila, tasa_dolar=1.0):
    """Detecta celdas vacías o inválidas en campos obligatorios. Retorna lista de mensajes."""
    errores = []
    nombre_raw = _obtener_valor_celda(fila, mapeo.get('nombre'))
    if _celda_vacia(nombre_raw):
        errores.append(
            f'Fila {numero_fila}: el nombre del producto está vacío (campo obligatorio).'
        )

    precio_val = _obtener_valor_celda(fila, mapeo.get('precio'))
    if _celda_vacia(precio_val):
        errores.append(
            f'Fila {numero_fila}: el precio está vacío (campo obligatorio).'
        )
    else:
        precio_raw = _parsear_numero(precio_val)
        if precio_raw is None:
            errores.append(
                f'Fila {numero_fila}: el precio «{precio_val}» no es un número válido.'
            )
        elif precio_raw < 0:
            errores.append(
                f'Fila {numero_fila}: el precio no puede ser negativo.'
            )

    return errores


def validar_inventario_previo(
    data, extension, encabezados, mapeo, meta, tasa_dolar=1.0
):
    """
    Validación previa sin escribir en PostgreSQL.
    Retorna (valido, mensaje_error, meta_validacion).
    """
    errores = []
    filas_con_datos = 0
    filas_validas = 0

    try:
        for numero_fila, fila in iter_filas_inventario_enumeradas(
            data, extension, encabezados
        ):
            filas_con_datos += 1
            errores_fila = diagnosticar_fila_obligatoria(
                fila, mapeo, meta, numero_fila, tasa_dolar=tasa_dolar
            )
            if errores_fila:
                errores.extend(errores_fila)
                continue
            if parsear_fila_inventario(
                fila, mapeo, meta, tasa_dolar=tasa_dolar, imagen_default=None
            ):
                filas_validas += 1
    except ErrorImportacionInventario:
        raise
    except Exception as exc:
        _registrar_fallo_importacion('validacion_filas', exc)
        raise ErrorImportacionInventario(
            'No se pudieron leer las filas del archivo. '
            'Revisa la codificación y el formato e intenta de nuevo.'
        ) from exc

    if filas_con_datos == 0:
        return False, 'El archivo no contiene filas de datos debajo de los encabezados.', None

    if errores:
        visibles = errores[:_MAX_ERRORES_VALIDACION]
        mensaje = (
            'El archivo tiene errores en campos obligatorios. '
            'Ningún cambio fue aplicado a tu inventario.\n'
            + '\n'.join(visibles)
        )
        restantes = len(errores) - len(visibles)
        if restantes > 0:
            mensaje += f'\n… y {restantes} error(es) adicional(es).'
        return False, mensaje, {'errores': errores, 'filas_validas': filas_validas}

    if filas_validas == 0:
        return False, (
            'No se encontraron filas válidas con nombre y precio. '
            'Revisa que los datos no estén vacíos y que el precio use formato numérico.'
        ), None

    return True, None, {'filas_validas': filas_validas, 'filas_con_datos': filas_con_datos}


def parsear_fila_inventario(fila, mapeo, meta, tasa_dolar=1.0, imagen_default=None):
    """Convierte una fila del archivo en dict de producto o None si es inválida.

    El **precio es opcional**: si el archivo solo actualiza existencias
    (reportes de inventario/ERP), ``precio_usd`` queda en ``None`` y el upsert
    conserva el precio existente. Solo el nombre (o el código) es obligatorio.
    """
    nombre_raw = _obtener_valor_celda(fila, mapeo.get('nombre'))
    nombre = str(nombre_raw or '').strip()
    if not nombre or nombre.lower() == 'none':
        return None

    precio_usd = None
    if mapeo.get('precio'):
        precio_raw = _parsear_numero(_obtener_valor_celda(fila, mapeo.get('precio')))
        if precio_raw is not None and precio_raw >= 0:
            if meta.get('precio_en_bs'):
                tasa = float(tasa_dolar or 1.0)
                if tasa <= 0:
                    tasa = 1.0
                precio_usd = round(precio_raw / tasa, 2)
            else:
                precio_usd = round(precio_raw, 2)

    descripcion_col = mapeo.get('descripcion')
    descripcion = ''
    if descripcion_col:
        descripcion = str(_obtener_valor_celda(fila, descripcion_col) or '').strip()
        if descripcion.lower() == 'none':
            descripcion = ''

    codigo_barras = None
    if mapeo.get('codigo_barras'):
        codigo_barras = normalizar_codigo_barras(
            _obtener_valor_celda(fila, mapeo['codigo_barras'])
        )

    imagen_url = None
    if mapeo.get('imagen_url'):
        imagen_url = imagen_url_para_persistir(
            _obtener_valor_celda(fila, mapeo['imagen_url'])
        )

    stock = None
    if mapeo.get('stock'):
        stock = _parsear_entero(_obtener_valor_celda(fila, mapeo['stock']))

    marca = ''
    if mapeo.get('marca'):
        marca = str(_obtener_valor_celda(fila, mapeo['marca']) or '').strip()
        if marca.lower() == 'none':
            marca = ''

    categoria = ''
    if mapeo.get('categoria'):
        categoria = str(_obtener_valor_celda(fila, mapeo['categoria']) or '').strip()
        if categoria.lower() == 'none':
            categoria = ''

    return {
        'nombre': nombre,
        'descripcion': descripcion,
        'precio_usd': precio_usd,
        'codigo_barras': codigo_barras,
        'imagen_url': imagen_url,
        'marca': marca,
        'categoria': categoria,
        'stock': stock,
    }


def contar_productos_validos(data, extension, encabezados, mapeo, meta, tasa_dolar=1.0, imagen_default=None):
    total = 0
    for fila in iter_filas_inventario(data, extension, encabezados):
        if parsear_fila_inventario(
            fila, mapeo, meta, tasa_dolar=tasa_dolar, imagen_default=imagen_default
        ):
            total += 1
    return total


def iter_lotes_productos(
    data,
    extension,
    encabezados,
    mapeo,
    meta,
    tasa_dolar=1.0,
    imagen_default=None,
    batch_size=None,
    columnas=None,
    fila_inicio=None,
):
    """Genera lotes de productos parseados para inserción por bloques."""
    tamano = batch_size or IMPORT_BATCH_SIZE
    lote = []

    for fila in iter_filas_inventario(
        data, extension, encabezados, columnas=columnas, fila_inicio=fila_inicio
    ):
        parsed = parsear_fila_inventario(
            fila, mapeo, meta, tasa_dolar=tasa_dolar, imagen_default=imagen_default
        )
        if not parsed:
            continue
        lote.append(parsed)
        if len(lote) >= tamano:
            yield lote
            lote = []

    if lote:
        yield lote


def _snapshot_imagenes_por_codigo(cursor, comercio_id):
    """
    Mapa codigo_barras normalizado → imagen_url existente antes de reemplazo masivo.
    Coincidencia estricta vía EXPR_CODIGO_BARRAS (PostgreSQL / Excel / CSV).
    """
    cursor.execute(
        f"""
        SELECT {EXPR_CODIGO_BARRAS} AS codigo_key, imagen_url
        FROM productos
        WHERE comercio_id = ?
          AND codigo_barras IS NOT NULL
          AND TRIM(BOTH FROM CAST(codigo_barras AS TEXT)) <> ''
        """,
        (int(comercio_id),),
    )
    snapshot = {}
    for fila in cursor.fetchall():
        if isinstance(fila, dict):
            clave_raw = fila.get('codigo_key')
            imagen_raw = fila.get('imagen_url')
        else:
            clave_raw, imagen_raw = fila[0], fila[1]
        clave = normalizar_codigo_barras(clave_raw)
        if not clave:
            continue
        imagen = imagen_url_almacenada(imagen_raw)
        if imagen:
            snapshot[clave] = imagen
    return snapshot


def _imagen_final_importacion(
    imagen_csv,
    codigo_barras,
    snapshot_imagenes,
    mapa_maestro=None,
    nombre=None,
    descripcion=None,
):
    """URL para INSERT: solo CSV, snapshot del comercio o catálogo maestro local.

    Nunca devuelve un asset generado (placeholder/monograma/tarjeta).
    """
    del nombre, descripcion
    from backend.activos_verificados import es_asset_verificado

    nueva = imagen_url_para_persistir(imagen_csv)
    if nueva and es_asset_verificado(nueva):
        return nueva
    codigo = normalizar_codigo_barras(codigo_barras)
    candidato = None
    if codigo and codigo in snapshot_imagenes:
        candidato = snapshot_imagenes[codigo]
    elif codigo and mapa_maestro:
        candidato = mapa_maestro.get(codigo)
    return candidato if es_asset_verificado(candidato) else None


def asignar_imagenes_instantaneas(productos, snapshot_imagenes=None, categoria=None):
    """Asigna imagen **solo** si es un asset verificado (sin inventar nada).

    Orden:
      1. URL del propio archivo (CSV/Excel), si es verificada.
      2. Foto previa del comercio (snapshot) por código de barras.
      3. Catálogo maestro por código de barras o por nombre/marca.

    Si no hay asset verificado, el producto queda **sin imagen**
    (``imagen_url=None``, estado ``pendiente``): la interfaz muestra un estado
    neutro y el motor seguirá buscando en segundo plano. Nunca se fabrica un
    placeholder, monograma ni tarjeta. Retorna cuántos quedaron sin imagen.
    """
    from backend.activos_verificados import es_asset_generado, es_asset_verificado

    snapshot = snapshot_imagenes or {}
    indice = None
    try:
        from backend.catalogo_maestro_index import obtener_indice

        indice = obtener_indice()
    except Exception as exc:
        print(f'{LOG_PREFIX} índice maestro no disponible: {type(exc).__name__}: {exc}')

    try:
        from backend.categorias_producto import clasificar_categoria
    except Exception as exc:
        print(f'{LOG_PREFIX} clasificador de categorías no disponible: {exc}')

        def clasificar_categoria(nombre=None, descripcion=None, marca=None, categoria_hint=None):
            return 'otros'

    reales = 0
    sin_imagen = 0
    descartados = 0
    por_categoria = {}

    def _descartar_generado(prod):
        if prod.get('imagen_url') and es_asset_generado(
            prod.get('imagen_url'), prod.get('imagen_fuente')
        ):
            prod['imagen_url'] = None
            prod['imagen_fuente'] = None
            return True
        return False

    for prod in productos:
        url = prod.get('imagen_url')
        if url and es_asset_verificado(url, prod.get('imagen_fuente')):
            prod['imagen_fuente'] = prod.get('imagen_fuente') or 'archivo'
            prod['imagen_estado'] = 'real'
            reales += 1
            continue
        if _descartar_generado(prod):
            descartados += 1

        codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
        url_snapshot = snapshot.get(codigo) if codigo else None
        if url_snapshot and es_asset_verificado(url_snapshot):
            prod['imagen_url'] = url_snapshot
            prod['imagen_fuente'] = 'comercio'
            prod['imagen_estado'] = 'real'
            reales += 1
            continue

        if indice is not None:
            url_maestro, origen = indice.buscar(
                codigo=codigo,
                nombre=prod.get('nombre'),
                marca=prod.get('marca'),
            )
            if url_maestro and es_asset_verificado(url_maestro):
                prod['imagen_url'] = url_maestro
                prod['imagen_fuente'] = f'maestro_{origen}'
                prod['imagen_estado'] = 'real'
                reales += 1
                continue

        cat = clasificar_categoria(
            nombre=prod.get('nombre'),
            descripcion=prod.get('descripcion'),
            marca=prod.get('marca'),
            categoria_hint=prod.get('categoria') or categoria,
        )
        prod['categoria_inferida'] = cat
        por_categoria[cat] = por_categoria.get(cat, 0) + 1

        # Sin asset verificado: NO se inventa nada (la UI muestra estado neutro).
        prod['imagen_url'] = None
        prod['imagen_fuente'] = None
        prod['imagen_estado'] = 'pendiente'
        sin_imagen += 1

    print(
        f'{LOG_PREFIX} imágenes verificadas: total={len(productos)} '
        f'reales={reales} sin_imagen={sin_imagen} '
        f'generados_descartados={descartados} '
        f'maestro_codigos={len(indice.por_codigo) if indice else 0} '
        f'maestro_nombres={len(indice.por_nombre) if indice else 0} '
        f'categorias={por_categoria}'
    )
    return sin_imagen


def _tuplas_insercion(comercio_id, lote, snapshot_imagenes=None, mapa_maestro=None):
    snapshot_imagenes = snapshot_imagenes or {}
    mapa_maestro = mapa_maestro or {}
    tuplas = []
    for prod in lote:
        url = prod.get('imagen_url')
        if not url:
            url = _imagen_final_importacion(
                prod.get('imagen_url'),
                prod.get('codigo_barras'),
                snapshot_imagenes,
                mapa_maestro=mapa_maestro,
            )
        estado = prod.get('imagen_estado')
        if not estado:
            estado = 'pendiente' if (not url or 'placeholder' in str(url).lower()) else 'real'
        # Precio/stock son opcionales en inventarios ERP: un NULL explícito rompe
        # la restricción NOT NULL de la BD. Se normaliza a 0.0 / 0 (cero rechazos).
        precio = prod.get('precio_usd')
        try:
            precio = float(precio) if precio is not None else 0.0
        except (TypeError, ValueError):
            precio = 0.0
        stock = prod.get('stock')
        try:
            stock = int(stock) if stock is not None else 0
        except (TypeError, ValueError):
            stock = 0
        tuplas.append(
            (
                comercio_id,
                prod['nombre'],
                prod['descripcion'],
                precio,
                prod['codigo_barras'],
                url,
                prod.get('imagen_fuente'),
                estado,
                stock,
            )
        )
    return tuplas


# Variante de UPDATE masivo (UPSERT) para `execute_values`.
UPSERT_PRODUCTO_VALUES_SQL = """
    UPDATE productos
    SET nombre = v.nombre,
        descripcion = v.descripcion,
        precio_usd = v.precio_usd,
        codigo_barras = v.codigo_barras,
        imagen_url = v.imagen_url,
        imagen_fuente = v.imagen_fuente,
        imagen_estado = v.imagen_estado,
        stock = v.stock,
        activo = 1
    FROM (VALUES %s) AS v(
        id, nombre, descripcion, precio_usd, codigo_barras,
        imagen_url, imagen_fuente, imagen_estado, stock
    )
    WHERE productos.id = v.id
"""


def _hay_columna_activo():
    """True si ``productos.activo`` existe (cacheado). Aislamiento defensivo."""
    try:
        from backend.stores import _activo_disponible

        return _activo_disponible()
    except Exception:
        return False


def _cargar_existentes_comercio(comercio_id):
    """Productos actuales del comercio indexados por código y por nombre."""
    from backend.db import get_db_connection
    from backend.utils import normalizar_nombre_producto

    col_activo = 'COALESCE(activo, 1) AS activo' if _hay_columna_activo() else '1 AS activo'
    with get_db_connection() as conexion:
        cursor = conexion.cursor()
        cursor.execute(
            f"""
            SELECT id, codigo_barras, nombre, descripcion, precio_usd, stock,
                   imagen_url, imagen_fuente, imagen_estado, {col_activo}
            FROM productos
            WHERE comercio_id = ?
            """,
            (int(comercio_id),),
        )
        filas = cursor.fetchall()

    por_codigo = {}
    por_nombre = {}
    todos = []
    for fila in filas:
        registro = fila if isinstance(fila, dict) else {
            'id': fila[0], 'codigo_barras': fila[1], 'nombre': fila[2],
            'descripcion': fila[3], 'precio_usd': fila[4], 'stock': fila[5],
            'imagen_url': fila[6], 'imagen_fuente': fila[7], 'imagen_estado': fila[8],
            'activo': (fila[9] if len(fila) > 9 else 1),
        }
        registro.setdefault('activo', 1)
        todos.append(registro)
        codigo = normalizar_codigo_barras(registro.get('codigo_barras'))
        if codigo and codigo not in por_codigo:
            por_codigo[codigo] = registro
        nombre = normalizar_nombre_producto(registro.get('nombre'))
        if nombre and nombre not in por_nombre:
            por_nombre[nombre] = registro
    return {
        'por_codigo': por_codigo,
        'por_nombre': por_nombre,
        'todos': todos,
        'total': len(filas),
    }


def _buscar_existente(prod, existentes):
    from backend.utils import normalizar_nombre_producto

    codigo = normalizar_codigo_barras(prod.get('codigo_barras'))
    if codigo and codigo in existentes['por_codigo']:
        return existentes['por_codigo'][codigo]
    nombre = normalizar_nombre_producto(prod.get('nombre'))
    if nombre and nombre in existentes['por_nombre']:
        return existentes['por_nombre'][nombre]
    return None


def productos_nuevos(productos, existentes):
    """Subconjunto de productos que no existen aún en el comercio."""
    return [p for p in productos if _buscar_existente(p, existentes) is None]


def _texto_norm(valor):
    return ' '.join(str(valor or '').split()).strip().lower()


def _a_float(valor):
    try:
        return float(valor) if valor is not None and str(valor).strip() != '' else None
    except (TypeError, ValueError):
        return None


def _a_int(valor):
    try:
        return int(float(valor)) if valor is not None and str(valor).strip() != '' else None
    except (TypeError, ValueError):
        return None


def _difiere(nuevo, actual, normalizador=None):
    """True si el valor del CSV viene informado y difiere del actual."""
    if nuevo is None or (isinstance(nuevo, str) and not nuevo.strip()):
        return False
    norm = normalizador or _texto_norm
    return norm(nuevo) != norm(actual)


def _registro_sin_cambios(registro, prod):
    """True si la fila del CSV es idéntica al producto ya guardado.

    Evita recalcular/actualizar filas que no cambiaron al re-subir el mismo
    catálogo. Un producto **inactivo** que reaparece en el archivo siempre se
    considera cambiado (para reactivarlo).
    """
    from backend.utils import normalizar_codigo_barras

    activo = registro.get('activo')
    activo = 1 if activo is None else int(activo)
    if activo != 1:
        return False
    if _difiere(prod.get('nombre'), registro.get('nombre')):
        return False
    if _difiere(prod.get('descripcion'), registro.get('descripcion')):
        return False
    if _difiere(prod.get('precio_usd'), registro.get('precio_usd'), _a_float):
        return False
    if _difiere(prod.get('stock'), registro.get('stock'), _a_int):
        return False
    if _difiere(prod.get('codigo_barras'), registro.get('codigo_barras'), normalizar_codigo_barras):
        return False
    if _difiere(prod.get('imagen_url'), registro.get('imagen_url'), lambda v: str(v or '').strip()):
        return False
    return True


def persistir_importacion_upsert(comercio_id, productos, categoria=None, existentes=None):
    """UPSERT: actualiza productos existentes (precio/stock/imagen) e inserta nuevos.

    Cero rechazos falsos: si el archivo solo trae existencias o precios, los
    campos ausentes conservan su valor actual. Las filas **idénticas** ya
    guardadas no se tocan (se cuentan como ``omitidos``). Los productos del
    comercio que ya **no vienen** en el archivo se dan de baja según
    ``LOCALIS_CSV_BAJAS``. Retorna ``(insertados, actualizados, omitidos,
    bajas)``.
    """
    from backend.db import ejecutar_con_reintentos_bd, get_db_connection

    existentes = existentes or _cargar_existentes_comercio(comercio_id)
    nuevos = []
    actualizaciones = []
    for prod in productos:
        registro = _buscar_existente(prod, existentes)
        if registro is None:
            nuevos.append(prod)
        else:
            actualizaciones.append((registro, prod))

    snapshot_imagenes = {
        codigo: reg.get('imagen_url')
        for codigo, reg in existentes['por_codigo'].items()
        if reg.get('imagen_url')
    }
    if categoria is None:
        try:
            from backend.catalogo_maestro_index import categoria_de_comercio

            categoria = categoria_de_comercio(comercio_id)
        except Exception:
            categoria = None
    if nuevos:
        try:
            asignar_imagenes_instantaneas(nuevos, snapshot_imagenes, categoria)
        except Exception as exc:
            print(f'{LOG_PREFIX} aviso imágenes de productos nuevos: {exc}')

    def _operacion(conexion):
        cursor = conexion.cursor()
        cursor.execute('SELECT pg_advisory_xact_lock(?)', (int(comercio_id),))
        ejecutar_lote = getattr(cursor, 'execute_values', None)
        activo_ok = _hay_columna_activo()
        sql_upsert = UPSERT_PRODUCTO_VALUES_SQL
        if not activo_ok:
            sql_upsert = sql_upsert.replace('        activo = 1\n', '')

        insertados = 0
        for inicio in range(0, len(nuevos), IMPORT_BATCH_SIZE):
            lote = nuevos[inicio : inicio + IMPORT_BATCH_SIZE]
            tuplas = _tuplas_insercion(comercio_id, lote, snapshot_imagenes)
            if callable(ejecutar_lote):
                ejecutar_lote(INSERT_PRODUCTO_VALUES_SQL, tuplas, page_size=IMPORT_BATCH_SIZE)
            else:
                cursor.executemany(INSERT_PRODUCTO_SQL, tuplas)
            insertados += len(lote)

        filas_update = []
        omitidos = 0
        for registro, prod in actualizaciones:
            # Filas idénticas a lo ya guardado: no se tocan (ni UPDATE ni hash).
            if _registro_sin_cambios(registro, prod):
                omitidos += 1
                continue
            precio = prod.get('precio_usd')
            if precio is None:
                precio = registro.get('precio_usd')
            stock = prod.get('stock')
            if stock is None:
                stock = registro.get('stock')
            imagen_nueva = prod.get('imagen_url')
            filas_update.append(
                (
                    registro.get('id'),
                    prod.get('nombre') or registro.get('nombre'),
                    prod.get('descripcion') or registro.get('descripcion') or '',
                    float(precio) if precio is not None else 0.0,
                    prod.get('codigo_barras') or registro.get('codigo_barras'),
                    imagen_nueva or registro.get('imagen_url'),
                    'archivo' if imagen_nueva else registro.get('imagen_fuente'),
                    'real' if imagen_nueva else (registro.get('imagen_estado') or 'pendiente'),
                    int(stock) if stock is not None else 0,
                )
            )

        actualizados = 0
        for inicio in range(0, len(filas_update), IMPORT_BATCH_SIZE):
            lote = filas_update[inicio : inicio + IMPORT_BATCH_SIZE]
            if callable(ejecutar_lote):
                ejecutar_lote(sql_upsert, lote, page_size=IMPORT_BATCH_SIZE)
            else:
                set_activo = ', activo = 1' if activo_ok else ''
                for fila in lote:
                    cursor.execute(
                        f"""
                        UPDATE productos
                        SET nombre = ?, descripcion = ?, precio_usd = ?,
                            codigo_barras = ?, imagen_url = ?, imagen_fuente = ?,
                            imagen_estado = ?, stock = ?{set_activo}
                        WHERE id = ?
                        """,
                        fila[1:] + (fila[0],),
                    )
            actualizados += len(lote)

        # --- Bajas: productos del comercio que YA NO vienen en el archivo ---
        bajas = 0
        from backend.utils import normalizar_codigo_barras as _ncb
        from backend.utils import normalizar_nombre_producto as _nnp

        existentes_todos = existentes.get('todos') or []
        por_codigo_ids = {}
        por_nombre_ids = {}
        for reg in existentes_todos:
            if reg.get('id') is None:
                continue
            codigo_reg = _ncb(reg.get('codigo_barras'))
            if codigo_reg:
                por_codigo_ids.setdefault(codigo_reg, set()).add(int(reg['id']))
            nombre_reg = _nnp(reg.get('nombre'))
            if nombre_reg:
                por_nombre_ids.setdefault(nombre_reg, set()).add(int(reg['id']))

        ids_presentes = set()
        for prod in productos:
            codigo_prod = _ncb(prod.get('codigo_barras'))
            if codigo_prod and codigo_prod in por_codigo_ids:
                ids_presentes.update(por_codigo_ids[codigo_prod])
            nombre_prod = _nnp(prod.get('nombre'))
            if nombre_prod and nombre_prod in por_nombre_ids:
                ids_presentes.update(por_nombre_ids[nombre_prod])

        ids_baja = [
            int(reg['id'])
            for reg in existentes_todos
            if reg.get('id') is not None
            and int(reg['id']) not in ids_presentes
            and (reg.get('activo') is None or int(reg['activo']) == 1)
        ]
        modo_bajas = _modo_bajas()
        if ids_baja and activo_ok and modo_bajas == 'desactivar':
            for inicio in range(0, len(ids_baja), IMPORT_BATCH_SIZE):
                lote_ids = ids_baja[inicio : inicio + IMPORT_BATCH_SIZE]
                placeholders = ', '.join('?' for _ in lote_ids)
                cursor.execute(
                    f'UPDATE productos SET activo = 0 WHERE id IN ({placeholders})',
                    tuple(lote_ids),
                )
                bajas += len(lote_ids)
        elif ids_baja and activo_ok and modo_bajas == 'eliminar':
            for inicio in range(0, len(ids_baja), IMPORT_BATCH_SIZE):
                lote_ids = ids_baja[inicio : inicio + IMPORT_BATCH_SIZE]
                placeholders = ', '.join('?' for _ in lote_ids)
                cursor.execute(
                    f'DELETE FROM productos WHERE id IN ({placeholders})',
                    tuple(lote_ids),
                )
                bajas += len(lote_ids)

        if insertados == 0 and actualizados == 0 and omitidos == 0 and bajas == 0:
            raise ErrorImportacionInventario(
                'No se encontraron filas válidas para importar. '
                'Revisa que el archivo tenga nombres/descripciones de producto.'
            )
        return insertados, actualizados, omitidos, bajas

    return ejecutar_con_reintentos_bd(_operacion)


def mensaje_error_importacion(exc):
    """Traduce excepciones técnicas a mensajes amigables para el comercio."""
    if isinstance(exc, ErrorImportacionInventario):
        return str(exc)
    if isinstance(exc, RuntimeError):
        return str(exc)

    exc_name = type(exc).__module__ + '.' + type(exc).__name__
    if exc_name in ('psycopg2.pool.PoolError', 'psycopg2.PoolError'):
        return (
            'El servidor está procesando muchas importaciones a la vez. '
            'Espera unos segundos e intenta de nuevo.'
        )
    if exc_name.startswith('psycopg2') and type(exc).__name__ == 'OperationalError':
        return (
            'Hubo una sobrecarga temporal al guardar tu inventario. '
            'Ningún cambio fue aplicado. Intenta de nuevo en unos segundos.'
        )
    try:
        from psycopg2 import OperationalError
        from psycopg2.pool import PoolError

        if isinstance(exc, PoolError):
            return (
                'El servidor está procesando muchas importaciones a la vez. '
                'Espera unos segundos e intenta de nuevo.'
            )
        if isinstance(exc, OperationalError):
            return (
                'Hubo una sobrecarga temporal al guardar tu inventario. '
                'Ningún cambio fue aplicado. Intenta de nuevo en unos segundos.'
            )
    except ImportError:
        pass

    if isinstance(exc, MemoryError):
        return (
            'El archivo es demasiado grande para procesarlo en este momento. '
            'Reduce el tamaño o divide el inventario en varios archivos.'
        )
    if isinstance(exc, csv.Error) or isinstance(exc, UnicodeError):
        return (
            'El CSV no se pudo leer. Revisa la codificación (UTF-8 o Latin-1), '
            'los delimitadores y que no haya caracteres binarios.'
        )
    return (
        'No se pudo completar la importación. '
        'Tus productos anteriores no fueron modificados. '
        'Verifica el archivo e intenta de nuevo.'
    )


# Compatibilidad con pruebas y llamadas legacy
def leer_filas_inventario(archivo):
    data, extension, error = cargar_archivo_inventario(archivo)
    if error:
        return None, None, error
    encabezados, error = leer_encabezados_inventario(data, extension)
    if error:
        return None, None, error
    filas = list(iter_filas_inventario(data, extension, encabezados))
    return filas, encabezados, None


def procesar_filas_inventario(filas, encabezados, tasa_dolar=1.0, imagen_default=None):
    mapeo, meta, error = detectar_mapeo_columnas(encabezados)
    if error:
        return None, error

    productos = []
    for fila in filas:
        parsed = parsear_fila_inventario(
            fila, mapeo, meta, tasa_dolar=tasa_dolar, imagen_default=imagen_default
        )
        if parsed:
            productos.append(parsed)

    if not productos:
        return None, (
            'No se encontraron filas válidas con nombre y precio. '
            'Revisa que los datos no estén vacíos y que el precio use formato numérico.'
        )

    return productos, None

# Imágenes de producto (Localis)

Hay **dos caminos**, sin mezclarlos.

## 1. Foto manual (prioridad, costo API = 0)

Si el comerciante sube un archivo desde el dispositivo, se comprime y se guarda en el bucket Supabase `imagenes` (`productos/…`) o, si Storage no está disponible, en `/static/uploads/productos/`.

No se consulta ninguna API externa.

## 2. Pipeline profesional automático (gratuito, local)

Módulo: `services/professional_image_pipeline.py`.

Se activa **solo** cuando el producto queda sin foto definitiva (alta o CSV). El request HTTP no espera: corre en un hilo daemon.

1. **Registro automático / Serper.dev (Google Images)**: una sola consulta por producto (EAN primero; si no, nombre + descripción) con el conector oficial `backend/serper_images.py`.
2. **Búsqueda web**: `[Nombre] + [Marca] + [Presentación] + "venezuela"` (Bing/DuckDuckGo + Serper).
3. **Validación de fuente y calidad**: prioriza marcas oficiales, Farmatodo, Locatel y distribuidores nacionales; descarta imágenes pequeñas, borrosas, planas o con aspecto de logo/banner.
4. **Procesamiento local (rembg)**: recorta el fondo y entrega un lienzo cuadrado **blanco puro (#FFFFFF)** de 800×800, estilo estudio.
5. **Almacenamiento**: sube el WebP a Supabase Storage (`productos/auto_*.webp`) y actualiza `productos.imagen_url`.

> **Barcode Spider, UPCitemdb y Barcode Lookup fueron ELIMINADOS del repositorio**: no funcionan para el mercado venezolano y añadían dependencia de pago. El único pipeline de imágenes es `services/professional_image_pipeline.py`.

### Variables de entorno (opcionales)

```
LOCALIS_IMG_PIPELINE=1
LOCALIS_IMG_MIN_SIDE=320
LOCALIS_IMG_MAX_CANDIDATOS=8
LOCALIS_IMG_BLUR_MIN=35
LOCALIS_IMG_LADO_FINAL=800
LOCALIS_IMG_MAX_CONCURRENT=1
LOCALIS_REMBG_MODEL=u2net
```

La primera ejecución descarga el modelo de `rembg` (~176 MB) al disco del worker.

## 3. Importación masiva asíncrona (CSV / Excel)

Módulo: `backend/import_queue.py`.

Subir un catálogo **no bloquea** la petición HTTP:

- La ruta `POST /comercio/productos/cargar-csv` valida comercio/plan/archivo y **encola** el trabajo.
- Responde `HTTP 202 Accepted` con un `job_id`; el panel hace *polling* en `GET /comercio/productos/importacion/<job_id>`.
- Cola `queue.Queue` acotada (`LOCALIS_IMPORT_QUEUE_MAX`) y pool fijo de hilos daemon (`LOCALIS_IMPORT_WORKERS`).
- Cada job corre con contexto de Flask (`app.app_context()`); las filas se insertan por lotes y con *advisory lock* por comercio, de modo que importaciones del mismo comercio se serializan y las de comercios distintos avanzan en paralelo.
- Si la cola está llena: `HTTP 503` con mensaje de reintento (no se acumula memoria).

```
LOCALIS_IMPORT_QUEUE_MAX=8
LOCALIS_IMPORT_WORKERS=2
LOCALIS_IMPORT_JOB_TTL_SEC=3600
```

> **Escala:** la cola y el estado de los trabajos viven en memoria del worker de Gunicorn. Mantén `WEB_CONCURRENCY=1` (valor por defecto) para que el *polling* siempre encuentre el trabajo; varios usuarios se atienden en paralelo con los hilos daemon (`LOCALIS_IMPORT_WORKERS`). Si necesitas varios procesos, usa un broker compartido (Redis + RQ/Celery).

## 4. Catálogo maestro indexado (asignación en microsegundos)

Módulo: `backend/catalogo_maestro_index.py`.

Durante una importación masiva **no** se hace web scraping ni `rembg` por fila. El catálogo maestro (Supabase) se carga **una vez** y se indexa en memoria:

- `por_codigo`: EAN/UPC normalizado → URL (coincidencia O(1), la más común).
- `por_nombre`: tokens de nombre + marca normalizados (sin tildes, unidades ni palabras vacías) → URL.
- Respaldo por **similitud de tokens (Jaccard ≥ 0.6)** para variaciones como “Harina P.A.N.” vs “Harina de Maíz P.A.N.”.

Así, asignar la imagen de cada fila es un `dict.get` (~0.2–0.5 µs por búsqueda, verificado con 20.000 búsquedas).

Para cada fila, la importación resuelve en este orden:

1. URL del propio archivo (si el comercio la trae).
2. Foto previa del comercio (snapshot) por código de barras.
3. Catálogo maestro por **código de barras**.
4. Catálogo maestro por **nombre/marca**.
5. **Placeholder profesional de la categoría inferida de la fila** (`/static/img/placeholder-*.svg`, fondo blanco) → la UI nunca queda vacía.

### Cobertura universal (cero productos huérfanos)

Módulo: `backend/categorias_producto.py`. Clasificador universal **sin listas de productos**: una matriz de términos genéricos por categoría infiere la categoría/subcategoría a partir de `nombre + descripción + marca` (o de la columna `categoria`/`rubro` del archivo si viene). Categorías: `alimentos, bebidas, tecnologia, hogar, ferreteria, belleza, ropa, salud, juguetes, mascotas, deportes, automotriz, bebes, papeleria, otros`.

**Garantía:** tras la asignación instantánea, ningún producto queda con `imagen_url` vacío; si por cualquier motivo faltara, se fuerza el placeholder limpio. Verificado con 2.000 productos de 15 categorías: 100% con imagen, ~0,4 ms/producto.

### Éxito estricto (sin falsos positivos)

Cada producto guarda su estado en `productos.imagen_estado`:

- `real`: `imagen_url` apunta a una foto real (Storage/local).
- `pendiente`: aún no se ha buscado la foto real (placeholder de categoría).
- `rechazada`: se intentó y ninguna candidata superó la validación.

Módulo: `backend/estado_imagenes.py`. El reporte de la importación **solo cuenta imágenes reales** y nunca declara "completo" si quedan pendientes:

- `completo`: todas las imágenes son reales.
- `parcial`: catálogo cargado con fotos reales + pendientes.
- `sin_reales`: ninguna foto real todavía.

El trabajo asíncrono se marca `parcial` (no `completado`) cuando quedan pendientes, y el panel muestra un badge **"Imagen pendiente"**. El motor reintenta en segundo plano (con búsqueda ampliada y por sitio) y actualiza a `real` cuando la consigue.

**Reintento periódico:** `backend/image_backfill.py` arranca con la app y cada `LOCALIS_IMG_BACKFILL_INTERVALO` segundos procesa un lote en paralelo con un presupuesto de `LOCALIS_IMG_BACKFILL_PRESUPUESTO` (30-60 s), de modo que nada queda "sin imagen" para siempre sin saturar 1 CPU. Al inicio, `init_db` **reconcilia** `imagen_estado` con la URL real (corrige filas antiguas).

**Cola persistente (sin rendirse):** cada producto guarda `imagen_intentos` y `imagen_ultimo_intento`. El motor prioriza los nunca intentados (y los menos intentados), **nunca marca "real" un placeholder** y sigue reintentando hasta conseguir la foto real.

**Ejemplo real medido:** comercio con 51 productos → pasó de 28 fotos reales / 23 pendientes a **48 reales / 3 pendientes** en 2 ciclos (~50 s cada uno), con 0 imágenes vacías y 0 falsos positivos.

Los productos que caen en el placeholder se procesan **en segundo plano** (hilo único, semáforo=1) con el pipeline profesional (búsqueda + rembg + Storage) y, al guardarse, se cachean en el catálogo maestro con nombre/marca para que la **próxima** importación los resuelva al instante.

### Global (marcas internacionales) y registro modular

- `backend/fuentes_imagenes.py`: **registro modular** con todas las fuentes (catálogo maestro, VTEX, Mercado Libre, Serper.dev, Bing, DuckDuckGo, `site:` retail, favicon de marca, Simple Icons, monograma) y **dominios confiables** (Venezuela + marcas globales: Samsung, LG, Philips, Bosch, Makita, Nestlé, Coca-Cola, Altunsa…).
- **VTEX regional:** además de Locatel (VE), se consultan distribuidores regionales con API pública y **imagen directa** (Carulla, Olímpica, Plaza Vea, Jumbo AR), lo que cubre marcas globales y productos de importación que no están en catálogos venezolanos.
- **Motor de búsqueda humana (`_consultas_busqueda`):** genera hasta 8 variantes combinando `[código]`, `[nombre]`, `[marca]`, `[categoría]`, `"venezuela"`, sinónimos locales y eliminando gramajes/unidades. Si una variante no rinde, se prueban las demás en paralelo.
- **Buscadores API:** `SERPER_API_KEY` (Serper.dev / Google Images, conector oficial) y opcionalmente `SERPAPI_KEY`, `BRAVE_SEARCH_API_KEY` y `BING_SEARCH_V7_KEY`; se activan solo si existen.
- **Anti-bloqueo (`backend/http_client.py`):** rotación de User-Agents de navegadores reales, cabeceras completas (`Accept-Language`, `Sec-Fetch-*`…), reintentos con **backoff exponencial**, **proxy HTTP(S)** (`LOCALIS_HTTP_PROXY`) y **pool de proxies públicos dinámicos** opt-in (`LOCALIS_HTTP_PROXY_PUBLICO=1`) para saltar bloqueos de IP del datacenter.
- **Motor secundario HTML (`_buscar_brave_html`):** cuando Bing/DuckDuckGo bloquean, se scrapea **Brave Search en HTML plano** y de cada ficha de producto se extrae el `og:image` (imagen de estudio).
- **Logos oficiales vectoriales:** `logo_wikidata` obtiene el logo corporativo de **Wikidata/Wikimedia Commons** (SVG), con desambiguación por descripción; luego Simple Icons y favicon de alta resolución. El monograma limpio es el último recurso.
- **Búsqueda persistente por escenarios (`nivel`):** si el escenario inicial no rinde, los reintentos en segundo plano exploran **consultas nuevas** (más genéricas: sin medidas, por marca, por categoría) hasta agotarlas, sin repetir siempre lo mismo y sin dar por buena una foto que no aparezca.
- **Rastreo `og:image`:** de las páginas de producto que devuelve Bing Images se extrae la imagen de estudio (`og:image`).
- **Descargas robustas:** reintentos con backoff y **múltiples variantes de tamaño** (`.400`, `.full`, sin tamaño) para CDN intermitentes.
- **Filtro de relevancia por título:** los candidatos de buscadores/VTEX/ML deben compartir tokens con el producto (evita asignar fotos de otro artículo).
- `backend/marca_logo.py`: **respaldo visual por marca** solo tras agotar la búsqueda real.
- Los estados son `real`, `logo`, `pendiente`, `rechazada`; el reporte informa foto real / logo de marca / pendientes. Los `logo`/`pendiente` siguen reintentándose en segundo plano.

**Medición real (catálogo ERP de 225 productos, muestra de 20):** la tasa de **imagen real** subió de ~5% a **55%** solo con catálogos directos (VTEX regional + Open Facts + variantes + reintentos), sin buscadores web (bloqueados desde el entorno de prueba). Con `SERPAPI_KEY`/`BRAVE_SEARCH_API_KEY` o desde la red de producción, la cobertura sube más.

### Alta concurrencia (cientos de usuarios)

- `backend/import_queue.py`: cola acotada (`LOCALIS_IMPORT_QUEUE_MAX`, por defecto 200) con **spooling a disco** (`instance/cola_import/`): los archivos no viven en RAM, así se absorben cientos de importaciones concurrentes sin agotar memoria. Responde `HTTP 202` y el panel hace *polling*.
- **Límites de CPU configurables**: `LOCALIS_IMG_MAX_CONCURRENT` (rembg serializado), `LOCALIS_IMG_TRABAJADORES` (productos en paralelo), `LOCALIS_IMG_PARALELO` (búsquedas), `LOCALIS_IMPORT_WORKERS` (imports).
- **Justicia entre comercios**: el backfill rota comercios y prioriza los productos con menos intentos.
- Pruebas: `scripts/test_concurrencia.py` (120 imports concurrentes, 2.000 productos en 8 hilos, rembg nunca en paralelo).

```
LOCALIS_MAESTRO_INDEX_TTL_SEC=600
LOCALIS_MAESTRO_INDEX_MAX=100000
LOCALIS_MAESTRO_SIMILITUD_MIN=0.6
# Relleno real en segundo plano (acotado por tiempo para no saturar 1 CPU)
LOCALIS_IMG_CSV_MAX=2000
LOCALIS_IMG_CSV_BUDGET_SEC=600
LOCALIS_IMG_PARALELO=4
LOCALIS_IMG_TRABAJADORES=4
LOCALIS_IMG_CACHE_TTL_SEC=3600
LOCALIS_IMG_SITIOS=2
LOCALIS_IMG_VTEX_HOSTS=
MELI_ACCESS_TOKEN=
LOCALIS_MARCA_LOGO_TTL_SEC=86400
LOCALIS_IMG_MAX_CONCURRENT=1
LOCALIS_IMPORT_QUEUE_MAX=200
LOCALIS_IMPORT_WORKERS=2
# Cola persistente en segundo plano (lotes de 30-60 s)
LOCALIS_IMG_BACKFILL_LOTE=20
LOCALIS_IMG_BACKFILL_PRESUPUESTO=50
LOCALIS_IMG_BACKFILL_INTERVALO=90
```

## 5. Formatos soportados, parser ERP y UPSERT

`backend/inventory_import.py` lee **CSV** (UTF-8, UTF-8 BOM, UTF-16, Latin-1/CP1252/ISO-8859-1), **XLSX** (openpyxl) y **XLS** (xlrd). Los CSV se leen con detección de delimitador (`,`, `;`, tab, `|`).

**Parser de .xls heredados (ERP venezolanos):** `analizar_inventario` escanea las primeras 20 filas, detecta la fila de cabecera por palabras clave (`Código`, `Descripción`, `Costo`/`Precio`, `Existencia`/`Cantidad`, `Marca`, `Categoría`…) y mapea las columnas por **índice**, soportando reportes con preámbulo (membrete/empresa) y **encabezados desalineados** de las columnas de datos (p. ej. `Costo` en la columna 8 pero los valores en la 10). También ignora filas alternas vacías.

**UPSERT (cero rechazos falsos):** una importación **no reemplaza** el inventario. Si el producto ya existe (por **código de barras** o por **nombre**), se actualizan sus campos (precio, existencia, descripción, imagen) y se insertan solo los nuevos. **El precio es opcional**: un reporte que solo trae existencias actualiza el stock y conserva el precio, sin rechazar el archivo. Verificado con el ERP real `ReporteGeneral.Xls` (225 productos): primera importación 225 nuevos; reimportación **225 actualizados, 0 nuevos**; actualización de stock/precio por lote aplicada en BD.

La inserción/actualización usa **`execute_values`** (multi-fila, lotes de 500): **2.000 productos pasaron de ~214 s a ~10 s** contra Supabase, sin bloquear la petición HTTP (responde `202`).

## Qué no hace el sistema

- No hay llamadas a APIs de códigos de barras globales.
- El listado público **no** dispara APIs (evita saturar el VPS).

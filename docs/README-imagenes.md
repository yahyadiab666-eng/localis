# Imágenes de producto (Localis)

Hay **dos caminos**, sin mezclarlos.

## 1. Foto manual (prioridad, costo API = 0)

Si el comerciante sube un archivo desde el dispositivo, se comprime y se guarda en el bucket Supabase `imagenes` (`productos/…`) o, si Storage no está disponible, en `/static/uploads/productos/`.

No se consulta ninguna API externa.

## 2. Pipeline profesional automático (gratuito, local)

Módulo: `services/professional_image_pipeline.py`.

Se activa **solo** cuando el producto queda sin foto definitiva (alta o CSV). El request HTTP no espera: corre en un hilo daemon.

1. **EAN/UPC**: Open Food/Beauty/Products Facts (gratuitos, sin clave).
2. **Búsqueda web**: `[Nombre] + [Marca] + [Presentación] + "venezuela"` (Bing/DuckDuckGo).
3. **Validación de fuente y calidad**: prioriza marcas oficiales, Farmatodo, Locatel y distribuidores nacionales; descarta imágenes pequeñas, borrosas, planas o con aspecto de logo/banner.
4. **Procesamiento local (rembg)**: recorta el fondo y entrega un lienzo cuadrado **blanco puro (#FFFFFF)** de 800×800, estilo estudio.
5. **Almacenamiento**: sube el WebP a Supabase Storage (`productos/auto_*.webp`) y actualiza `productos.imagen_url`.

> **Barcode Spider, UPCitemdb y Barcode Lookup fueron eliminados**: no funcionan para el mercado venezolano y añadían dependencia de pago. `services/smart_image_pipeline.py` queda solo como capa de compatibilidad (`buscar_por_ean`/`buscar_por_nombre` devuelven `None`).

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

## Qué no hace el sistema

- No hay llamadas a APIs de códigos de barras globales.
- El listado público **no** dispara APIs (evita saturar el VPS).

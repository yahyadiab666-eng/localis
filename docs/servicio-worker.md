# Servicio "worker": sacar el trabajo pesado de CPU (plan)

> **Estado: ACTIVO** (reparto ya en producción). Fase 1 (OCR) y Fase 2 (imágenes)
> implementadas y encendidas. Aquí quedan los IDs, las variables y los números
> medidos para referencia.

## Despliegue actual (verificado)

| | Servicio WEB | Servicio WORKER |
|---|---|---|
| Nombre | `localis` | `localis-worker` |
| ID | `srv-dbdcbvvb9u1s7396u1eg` | `srv-dbdr21e7bikc73857dc0` |
| URL | https://localis.onrender.com | https://localis-worker.onrender.com |
| Repo / rama | `localis` / `main_limpia` | igual |
| Región / plan | `virginia` / `0.5c-512mb` | igual |
| Arranque | `gunicorn --config gunicorn.conf.py main:app` | igual |
| autoDeploy | sí | sí (sigue los commits) |

### Variables propias de cada uno

**WEB**

```
LOCALIS_WORKER=0
LOCALIS_IMG_PIPELINE_PESADO=0     # no hace búsqueda web + rembg
LOCALIS_IMG_BACKFILL=0            # el backfill lo hace el worker
LOCALIS_OCR_SERVICE_URL=https://localis-worker.onrender.com
LOCALIS_OCR_TOKEN=<token compartido>
```

**WORKER** (el resto de variables son las mismas que el web)

```
LOCALIS_WORKER=1                  # arranca el trabajo pesado al iniciar
LOCALIS_IMG_PIPELINE=1
LOCALIS_IMG_PIPELINE_PESADO=1
LOCALIS_IMG_BACKFILL=1
LOCALIS_OCR_WARMUP=1              # precarga el modelo (pagos rápidos)
LOCALIS_REPORTE_DIARIO=0          # el informe diario lo manda solo el web
LOCALIS_TRAFICO=0                 # sus llamadas de OCR no ensucian metricas_diarias
LOCALIS_METRICS=0
LOCALIS_OCR_SERVICE_URL=          # vacío: el OCR lo procesa él
LOCALIS_OCR_TOKEN=<el mismo token>
```

### Números medidos (producción)

| Métrica | WEB | WORKER |
|---|---|---|
| Memoria RSS | **186 MB** | **256 MB** (con OCR precargado) |
| Hilos de fondo | informe diario, recordatorios, reintento de pagos, keepalive BD, tasa | backfill de imágenes, reintento de pagos, keepalive BD, tasa |
| Cuenta tráfico | sí | **no** |
| Hace OCR | no (delega) | sí |
| Hace rembg (imágenes) | no | sí |

Latencia y capacidad del WEB (medido desde una sola máquina; indicativo):

| Prueba | Resultado |
|---|---|
| Portada, secuencial | ~0,8–1,1 s (incluye latencia de red del cliente) |
| Portada, 10 concurrentes | 3,4 req/s · mediana 2,5 s |
| Portada, 20 concurrentes | 3,9 req/s · mediana 4,9 s |
| Endpoint JSON, 10 concurrentes | 3,7 req/s · mediana 2,6 s |
| 1 consulta al asistente | ~1,0 s |

Lectura: el web **no escala con la concurrencia** porque el trabajo por petición
es **CPU** (render de la plantilla + gzip ≈ 250 ms) sobre **0,5 vCPU**; al subir
la concurrencia sube la latencia, no el caudal. La BD ya no es el cuello
(~73 ms) y el OCR/imágenes ya no compiten.

### Cómo se comunican (importante)

- **OCR**: la web llama al worker por HTTP **solo** cuando un usuario sube un
  comprobante (pagar plan o destacar). No depende de la carga: es fijo. Si el
  worker falla, la web hace el OCR **local** (y tras 3 fallos, cortacircuitos 60 s).
- **Imágenes**: la web **no** pide nada; deja el producto como `pendiente` y el
  worker lo drena solo consultando la **BD** (backfill cada ~90 s).


Localis corre hoy en **un solo servicio** de Render (`WEB_CONCURRENCY=1` +
varios hilos). Ese único proceso comparte CPU entre:

1. Las peticiones web (páginas, API).
2. El **OCR** de comprobantes (rapidocr/onnxruntime) — CPU pura.
3. El **pipeline de imágenes** (rembg + onnxruntime + scraping) — el mayor
   consumidor de CPU.
4. Hilos de fondo (backfill, reportes, reintentos).

La idea de un **segundo servicio** solo se justifica si carga con el **trabajo
pesado** (OCR e imágenes), no solo con el asistente (que es ligero: casi todo su
tiempo es esperar a OpenRouter).

### Capacidad estimada (con las suposiciones a la vista)

El web es **CPU-bound** (render + gzip ≈ 250 ms por página sobre 0,5 vCPU ≈ 3,8 req/s).

| Usuarios navegando a la vez* | Peticiones/s necesarias | ¿Aguanta 0,5 vCPU? |
|---|---|---|
| 50 | ~3,3 | **sí, al límite** |
| 100 | ~6,7 | no (latencia ~5 s) |
| 200 | ~13 | no |

\* suponiendo que cada usuario carga una página cada ~15 s.

Como el límite es CPU, subir de plan **escala casi lineal**:

| Plan | Caudal estimado | Usuarios navegando |
|---|---|---|
| 0,5 vCPU (hoy) | ~3,8 req/s | **~50** |
| 1 vCPU | ~8 req/s | **~100–120** |
| 2 vCPU | ~15 req/s | **~200–250** |

El **asistente** tiene su propio tope: **2 consultas simultáneas** al LLM (~1 s
cada una → ~120/min); el excedente recibe "ocupado" (se puede subir con
`LOCALIS_ASISTENTE_MAX_CONCURRENTES`). El **OCR y las imágenes ya no cuentan**
(viven en el worker).

Palanca barata de CPU sin subir de plan: bajar `LOCALIS_CATALOGO_PAGINA`
(menos tarjetas por página = menos render). No se puede cachear el HTML tal cual
porque lleva el token CSRF de cada sesión.

## Fase 1 — OCR remoto (LISTO, apagado por defecto)

Ya está implementado y **no cambia nada** mientras `LOCALIS_OCR_SERVICE_URL` esté
vacía: el OCR corre local como siempre. Cuando quieras moverlo:

1. Crea un **segundo servicio web** en Render con este mismo repo y el mismo
   comando de arranque (`gunicorn --config gunicorn.conf.py main:app`).
   *Nota: Render no ofrece workers HTTP privados en planes bajos, así que es un
   servicio web más; su URL no se comparte.*
2. Define en **ambos** servicios el token compartido: `LOCALIS_OCR_TOKEN` (o usa
   el `LOCALIS_CRON_TOKEN` que ya existe).
3. En el **web** añade: `LOCALIS_OCR_SERVICE_URL=https://<worker>.onrender.com`.
4. En el **worker** deja `LOCALIS_OCR_SERVICE_URL` **vacía** (así procesa él).

El endpoint es `POST /tareas/ocr/<pago-movil|binance>` (protegido por token).
El web envía la captura y recibe el mismo JSON que producía el OCR local.

**Salvaguardas** (`backend/ocr_remoto.py`):
- Si el worker falla o tarda, **cae al OCR local** (los pagos nunca se rompen).
- **Cortacircuitos**: tras 3 fallos seguidos deja de intentar el remoto 60 s, para
  no penalizar cada pago con un timeout.
- Timeout configurable (`LOCALIS_OCR_REMOTO_TIMEOUT_SEG`, por defecto 12 s).

## Fase 2 — Pipeline de imágenes (LISTO, apagado por defecto)

El contenedor **WEB** se queda solo con lo barato: en la importación y en el alta
de producto asigna fotos del **catálogo maestro** (sin CPU) y deja el resto como
`pendiente`. El **WORKER** hace la búsqueda web + rembg (pipeline profesional) y
también el backfill.

| Contenedor | Variables |
|---|---|
| **WEB** | `LOCALIS_WORKER=0`, `LOCALIS_IMG_PIPELINE_PESADO=0`, `LOCALIS_IMG_BACKFILL=0`, `LOCALIS_OCR_SERVICE_URL=<worker>` |
| **WORKER** | `LOCALIS_WORKER=1`, `LOCALIS_IMG_PIPELINE_PESADO=1`, `LOCALIS_IMG_BACKFILL=1` (sin URL de OCR) |

Con `LOCALIS_WORKER=1` el worker **arranca el backfill de imágenes y el reintento
de pagos al iniciar**, sin depender de que lleguen peticiones (un worker con poco
tráfico no dispararía el arranque perezoso).

**Consecuencia a tener en cuenta**: si el worker está caído, las fotos nuevas **no
se generan** (quedan `pendiente` y el catálogo muestra el marcador). El sitio y el
resto siguen funcionando.

**Ahorro de RAM en el web**: si `LOCALIS_OCR_SERVICE_URL` está definida, el web
**no precarga** el modelo de OCR (libera memoria del contenedor de 512 MB).

## Coste y recomendación

### Estado real medido (verificado en producción)

- **Render**: región `virginia`, plan **`0.5c-512mb`** → **0,5 vCPU y 512 MB**.
- **Supabase**: `aws-0-us-east-1.pooler…` → **us-east-1 (N. Virginia)** → **misma
  región que Render** ✅. Ya están alineadas.
- **Latencia de BD medida**: **~59 ms** (rtt ~2,6 ms) → **ya NO es el cuello**
  (el ~0,7 s histórico ya no aplica).
- **Memoria**: ~265 MB de 512 MB en uso (el stack de OCR/rembg es lo que más pesa).

**El cuello real es la CPU: 0,5 vCPU.** Con 1 worker y varios hilos, el trabajo de
Python queda muy limitado, y cualquier pico de OCR/rembg roba CPU a las peticiones
web (que sí son ligeras: esperan a la BD y a OpenRouter).

### Recomendación (actualizada)

1. **Subir de plan en el MISMO servicio** (más CPU y RAM): es la vía más directa y
   **no añade un segundo contenedor**. Con 0,5 vCPU casi cualquier pico compite.
2. **Solo si aun así no basta**, sacar el trabajo pesado a un worker: la Fase 1
   (OCR remoto) ya está lista y apagada; la Fase 2 (imágenes) es más compleja.
3. Alinear la región **ya está hecho**: se descarta como palanca.

Alternativa sin servidor extra y sin subir de plan: reducir trabajo de CPU
(menos concurrencia de OCR, backfill de imágenes más espaciado), a costa de
resolver pagos/imágenes más lento.


# Servicio "worker": sacar el trabajo pesado de CPU (plan)

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


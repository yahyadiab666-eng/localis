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

## Fase 2 — Pipeline de imágenes (pendiente, más complejo)

El backfill de imágenes (`backend/image_backfill.py`) es un hilo que ya lee de la
BD y trabaja en segundo plano: es el mejor candidato a moverse, pero escribe en
la BD y en Supabase Storage y comparte estado con la importación. Requiere:

- Un proceso de worker dedicado (no web) para el backfill, o un cron que lo
  dispare por lotes.
- Decidir si la importación (que hoy enriquece imágenes en el request) también se
  mueve.

No se implementa todavía: es el cambio de mayor riesgo y conviene medir primero
si la CPU es realmente el cuello (hoy el cuello medido es la **latencia de BD**,
~0,7 s por ida y vuelta).

## Coste y recomendación

- El segundo servicio **cuesta** (es otro contenedor). Como el asistente es
  ligero y el OCR tiene poco volumen (solo pagos), el ahorro de CPU puede no
  compensar.
- **Recomendación**: mantener **1 servicio** y activar la Fase 1 solo si notas
  lentitud durante picos de pagos, o la Fase 2 si el backfill de imágenes está
  compitiendo con las visitas.
- Alternativa sin segundo servidor: alinear la **región de Render con la de
  Supabase** (la mejora de mayor impacto medida, ×5-10) y/o subir de plan para
  tener más CPU en el mismo contenedor.

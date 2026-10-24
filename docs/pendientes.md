# Pendientes con especificación (historial de decisiones)

> Creado el **2026-10-24**, al cerrar la sesión de capacidad + imágenes.
> Cada bloque tiene: **por qué** (con la medición que lo respalda), **la
> especificación concreta**, **el coste**, **cómo verificarlo** y **la decisión
> pendiente**. Así, cuando retomemos el tema, no hay que reconstruir nada.

## Estado de partida (medido)

| Pieza | Estado |
|---|---|
| Web | `localis`, **2 instancias** `0.5c-512mb` (escalado manual) |
| Worker | `localis-worker`, 1 instancia `0.5c-512mb` (OCR + pipeline de imágenes) |
| **Coste Render** | **$21/mes** (web $14 + worker $7) + OpenRouter ~$0,1 + Supabase Free $0 |
| Caché de HTML anónimo | activa (`LOCALIS_CACHE_HTML_SEG=15`), HTML + cuerpo gzip cacheados |
| **Capacidad medida** | portada **~190 req/s**, tienda **~157-180 req/s**, **0 errores** (300 concurrentes) |
| Antes (1 instancia) | 26 req/s y **502 masivos** al forzar (18-26 %) |
| Imágenes | 214 reales / 14 pendientes; **216/216 con miniatura**; 83 → 4 externas |

> Auditoría de la caché: cabecera `X-Localis-HTML: hit|miss|off:<motivo>|error`.
> Generador de carga: `scripts/loadgen_server.py` (se crea/borra como servicio temporal).

---

## 1. OCR y pagos

### 1.1 Comprobante asíncrono — **código, $0 (la más importante)**
**Por qué:** el OCR corre **en la misma petición** (`backend/blueprints/api.py`
llama a `ocr_remoto.validar_comprobante_*` y devuelve `ocr_ms`), con
`LOCALIS_OCR_MAX_CONCURRENTES=2` y **~1-3 s por comprobante** (RapidOCR, CPU).
En una ráfaga (p. ej. renovaciones de fin de mes) la cola crece: el comerciante
**espera** y cada petición ocupa un hilo del web → el sitio entero se resiente.

**Especificación**
- `POST /api/pagos/comprobante` responde **202** con `{'estado':'recibido','job_id':…}`.
- El OCR corre en **hilo de fondo** (patrón ya existente:
  `backend/imagenes_producto.py::programar_descubrimiento_producto`).
- El estado vive en **BD** (`solicitudes_pago`/`pagos`: añadir `ocr_estado`,
  `ocr_intento`, `ocr_ms` si faltan) → sobrevive reinicios y es compartido entre
  instancias (los pagos están congelados por `LOCALIS_PAGOS_HABILITADOS`).
- El panel del comerciante hace *polling* (patrón `import_jobs`) y muestra
  "verificando…" → resultado; si falla, mensaje claro y bandeja de revisión manual
  (ya existe).
- **Idempotencia**: mantener `calcular_hash_comprobante` + `comprobante_ya_usado`.

**Verificar:** subir 10 comprobantes a la vez → respuesta <300 ms y resultado <10 s.
**Riesgo de no hacerlo:** el pago es el momento más sensible; si no ve "pagado", se va.

### 1.2 Worker a `1c-2g` — **$25/mes (hoy $7, +$18)**
**Por qué:** el worker tiene **512 MB** y carga **rembg (~176 MB de modelo) +
RapidOCR + Python** (`LOCALIS_OCR_WARMUP=1`). Con ambos activos el **OOM es un
riesgo real**; y con 0,5 vCPU el OCR da ~20-40 comprobantes/min.
**Especificación:** plan del worker → `1c-2g` (2 vCPU / **2 GB**) = 4× RAM (mata
el OOM) y ~2× CPU.
**Cuándo:** al activar pagos o al primer comercio con importaciones grandes diarias.
**Verificar:** `/health/ready` del worker + Render → Metrics (RAM y CPU).

### 1.3 Servicio de OCR propio — **fase futura, +$25/mes**
**Por qué:** si los pagos dominan la CPU del worker, competirán con el pipeline
de imágenes (semáforos separados, pero **CPU compartida**).
**Especificación:** tercer servicio `localis-ocr` (`1c-2g`) y
`LOCALIS_OCR_SERVICE_URL` apuntando a él; el worker se queda con imágenes.
`backend/ocr_remoto.py` ya es un cliente remoto, así que es sobre todo config.
**Cuándo:** solo si el OCR se vuelve el cuello.

### 1.4 Medir el OCR de verdad — **bloqueado por pagos congelados**
**Especificación:** `scripts/medir_ocr.py`: envía N comprobantes sintéticos al
worker (`/tareas/ocr/...` con `LOCALIS_OCR_TOKEN`) y reporta latencia p50/p95,
comprobantes/minuto y RAM. Se ejecuta cuando se activen los pagos.

---

## 2. Redis (Key Value) — **$10/mes**

### 2.1 Caché compartida (multiplica la caché de HTML)
**Por qué (medido):** la caché de HTML es **por proceso**: con 2 instancias la
misma URL **alterna** `hit`/`miss` (verificado con `X-Localis-HTML`). Compartida
pasaría de ~50 % a ~100 % de aciertos → ~2× menos trabajo por petición.
**Especificación**
- Render Key Value **256 MB**, misma región (Virginia).
- `backend/runtime_cache.py`: backend Redis **opcional**
  (`LOCALIS_CACHE_REDIS_URL`) con **respaldo a memoria** si falta o falla
  (nunca romper).
- Migrar por orden de impacto: (1) `html:*`, (2) datos de tienda/catálogo,
  (3) `config:*`.
- Las claves **deben incluir `host`** (el HTML lleva la URL canónica en el JSON-LD).
- 256 MB sobra: la tienda pesa ~730 KB por entrada con TTL de 15-60 s.
**Verificar:** con 2 instancias, `X-Localis-HTML` debe dar `hit` casi siempre
(no alternar) y medir con el generador.

### 2.2 Rate limit compartido
**Por qué:** `storage_uri='memory://'` en `backend/rate_limit.py` → el límite
efectivo **se multiplica por el número de instancias** (2 → 1200 req/min reales).
**Especificación:** mover el storage de Flask-Limiter a Redis cuando exista.

### 2.3 Cola de importaciones (fase futura)
Hoy el estado vive en `import_jobs` (BD) ✅. Con Redis + RQ/Celery se podría
repartir el trabajo entre varios workers. **Solo si el volumen lo exige.**

---

## 3. Dominio y CDN

### 3.1 `localis.com.ve` — **bloqueado por NIC.ve (activación de la cuenta)**
- **Libre**: confirmado por WHOIS (protocolo, puerto 43). NIC.ve es el **único**
  registrador (`registrar: NIC-VE`; no hay revendedores).
- **Precio**: 15.889,41 Bs/año (~$17,9 con la tasa del sistema). Si sale
  **"PREMIUM - BAJO RESERVA"**, ~$35,8.
- **Trámite**: cuenta en NIC.ve; su formulario falla por captcha/JS. Soporte:
  `soporte@nic.ve`, **0800-CONATEL**, oficina en Las Mercedes (Caracas). La cuenta
  quedó **pendiente de activación** (el login dice *"El usuario no existe o no está
  activo"*).
- ⚠️ **Al registrar hay que declarar los nameservers** (`nsset`) → **crear la zona
  en Cloudflare antes** para tener los 2 NS.
- **Renovación manual**: el aviso automático ya existe
  (`LOCALIS_DOMINIO`, `LOCALIS_DOMINIO_VENCE`) → reporte diario a 60/30/7 días.
- **Plan B**: `localisapp.com` (libre por RDAP, ~$10-13, renovación automática).

### 3.2 Cloudflare delante
- **Receta completa**: `docs/cloudflare.md` (3 reglas de caché + protección).
- **Prerrequisito ya cumplido**: una visita anónima **no escribe sesión** ni lleva
  token CSRF.
- **Efecto esperado**: el pico muere en el borde; el origen solo ve *miss*.
- **Nota**: `Vary: Cookie` sigue apareciendo en rutas que no tocan la sesión (lo
  añade el borde) → no bloquea con la regla "bypass si hay cookie".

### 3.3 Cambiar la URL base (una variable)
- Ya unificado: `LOCALIS_SITIO_URL` (`config.SITIO_URL`) alimenta correos,
  asistente y enlaces.
- **Falta (nativo)**: `capacitor.config.json` (`server.url`) + `www/index.html`
  → **recompilar el APK** y subir `versionCode`. **No cambiar `appId`.**

---

## 4. Mediciones y métricas

### 4.1 Separar mis pruebas del reporte de tráfico — **$0**
**Por qué (medido):** las mediciones sumaron ~63.000 peticiones a "hoy" (el 98 %
del histórico "humano"). El reporte es correcto: el generador se clasifica como
humano por su *user-agent*.
**Especificación:** `scripts/loadgen_server.py` añade `X-Localis-Loadgen: 1`;
`backend/trafico.py` **ignora** las peticiones con esa cabecera.

### 4.2 Medir 1 instancia (¿ahorrar $7?) — **decisión de gasto**
Con la caché, 1 instancia daría ~95 req/s (~1.400 usuarios) frente a ~190 (~2.800).
**Contra:** sin segunda instancia cada despliegue es una ventana de error y un
fallo del proceso deja la app caída. **Recomendación: quedarse con 2.**

---

## 5. Registro de lo hecho (para no repetirlo)

| Cambio | Commit | Efecto medido |
|---|---|---|
| Web a 2 instancias + candados de BD en correos | `9ac56f6` | 26 → 65 req/s; correos sin duplicar |
| HTML público sin sesión ni token CSRF (`/api/asistente/csrf`) | `76b77b9` | fuera `Set-Cookie` y `Vary: Cookie` |
| Caché de HTML anónimo + cabecera `X-Localis-HTML` | `25377d0`, `b669b56` | instrumentación que cazó el fallo siguiente |
| **Arreglo** `app.session_cookie_name` (no existe en esta versión de Flask) | `c8b75c1` | la caché pasó de **nunca** activarse a funcionar |
| Caché del cuerpo gzip (el gzip por petición era el cuello) | `db010c3` | portada 178 → ~200 req/s |
| Caché de la resolución del comercio | `3bbfdd9` | **tienda: 6-26 → 157-180 req/s, 0 errores** |
| Miniaturas propias 400 px (−83 % bytes) + espejo de imágenes externas | `1bebb7b`… | 216/216 con miniatura; 83 → 4 externas |
| Caché de 1 año en Storage (`cache-control`) | `7c6d373` | CDN de Supabase cacheando |
| Aviso de vencimiento de dominio + URL base en una sola fuente | `bb036f1` | `LOCALIS_SITIO_URL`, `LOCALIS_DOMINIO_VENCE` |
| `docs/cloudflare.md` y `docs/dominio.md` | — | recetas listas para ejecutar |

---

## 6. Reglas aprendidas (para futuras sesiones)

1. **Medir, no suponer**: cada afirmación de capacidad de este documento salió de
   un generador en la nube (el local no vale: paga TLS por petición).
2. **Instrumentar antes de optimizar**: el fallo que dejaba la caché muerta
   (un atributo de Flask que no existe) lo cazó una **cabecera de diagnóstico**,
   no una lectura de código.
3. **Probar en la condición del CI**: el job "sin red" corre **sin Postgres**; si
   una ruta nueva toca la BD en un camino que antes no la tocaba, el CI se pone
   rojo (pasó con los candados de correo).
4. **Con N instancias**, cualquier "comprobar y actuar" necesita candado en BD
   (`pg_try_advisory_xact_lock`), y el límite de **20 conexiones por proceso** es
   el primer techo al subir instancias (pooler de Supabase en 6543).
5. **`HEAD` miente** al auditar caché de Storage: usar `GET` o `/object/info`.
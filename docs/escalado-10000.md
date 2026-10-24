# Plan de escalado hasta 10.000 usuarios simultáneos

> Estado de partida **medido** (no estimado): ver `docs/servicio-worker.md` y la
> sección de imágenes de `AGENTS.md`. Resumen: `0.5c-512mb` con 1 proceso servía
> **~26 req/s** sin errores (a 40-51 req/s aparecían **502** del 18-26 %);
> `2c-4g` con 2 procesos llegó a **106 req/s**. El cuello no era la BD
> (RTT 2-8 ms) sino el **GIL con un solo worker**.

## 0. Qué significa "10.000 usuarios"

Es la pregunta que decide todo. Tres lecturas muy distintas:

| Lectura | A qué equivale | ¿Hace falta este plan? |
|---|---|---|
| **10.000 registrados** (la mayoría desconectados) | ~10-30 req/s de media | **No**: ya se aguanta |
| **10.000 usuarios activos a la vez** (todos navegando en el pico) | **500-1.000 req/s** | **Sí** (objetivo de este documento) |
| 10.000 peticiones en el mismo segundo | ~10.000 req/s | No es realista para un marketplace; exigiría otro diseño |

Supuesto de conversión usado: una persona navegando pide **una página cada
10-20 s** → **0,05-0,1 req/s** por usuario.
`10.000 × 0,05-0,1 = 500-1.000 req/s` en el pico.

## 1. Estado actual (tras la Fase 5 aplicada)

| Pieza | Ahora |
|---|---|
| Web | `localis` en `0.5c-512mb`, **2 instancias** (manual scaling, +$7/mes) |
| Worker | `localis-worker` en `0.5c-512mb`, 1 instancia (OCR + pipeline de imágenes) |
| BD | Supabase Postgres 17, misma región que Render (RTT 2-8 ms), `pool_max=20` **por proceso** |
| Imágenes | Supabase Storage + Smart CDN; miniaturas propias de 400 px (−83 % de bytes) |
| Cachés | en memoria por proceso (`runtime_cache`) |
| CDN | el de Render (estáticos). **No hay CDN propio ni dominio propio** |
| Techo estimado | **~50 req/s** sostenidos (2 × 26) |

## 2. Las fases, en orden de impacto

### Fase A. CDN propio + HTML anónimo cacheable — *el salto grande*
Es la única palanca que multiplica de verdad, porque evita que el pico **llegue**
al origen.

1. **Dominio propio** (solo lo puede hacer el dueño): comprar/poseer un dominio
   (p. ej. `localis.com.ve`) y crear la zona en Cloudflare (plan gratis).
2. **Cloudflare delante** de Render: registro `A/CNAME` hacia `localis.onrender.com`
   (proxy activado, naranja) y el dominio propio añadido en Render
   (`/v1/services/{id}/custom-domains`) para que emita el TLS.
3. ~~**Cachear el HTML anónimo**~~ **HECHO** (falta el CDN): el token CSRF ya no
   se incrusta en las páginas públicas — el widget lo pide aparte en
   `GET /api/asistente/csrf` — y una visita anónima **no escribe la sesión**
   (sin `Set-Cookie` ni `Vary: Cookie`), así que el HTML es idéntico para todos
   los anónimos y cacheable. Falta añadir `Cache-Control: public, s-maxage=60` a
   las vistas públicas cuando exista Cloudflare.
4. **Reglas de caché** en Cloudflare: HTML anónimo 60 s en el borde, estáticos
   1 año (`/static/*` ya manda `max-age=604800`).

**Efecto:** si 9 de cada 10 visitas anónimas las sirve el borde, el origen sólo
recibe el 10 %: 500-1.000 req/s de entrada → **50-100 req/s en el origen**.
**Coste:** dominio (~$10-15/año) + Cloudflare gratis.
**Paso a paso exacto (con las reglas de caché listas):** `docs/cloudflare.md`.

> Mientras no haya dominio, el CDN de Render ya cubre los estáticos; sin dominio
> no se puede activar Cloudflare.

### Fase B. Caché de datos compartida (Redis) — *barato y habilita lo demás*
Render Key Value 256 MB = **$10/mes**. Sirve para:
- Caché de datos (portada/tienda/búsqueda) **compartida** entre instancias (hoy
  cada proceso tiene la suya: menos aciertos cuantos más procesos).
- **Rate limit** compartido (hoy el límite efectivo se multiplica por el número
  de instancias).
- **Cola de importaciones** (hoy en `import_jobs`, que ya es compartida ✅; con
  Redis se puede repartir el trabajo entre varios workers).
- *Single-flight* por clave (evitar que N procesos hagan la misma consulta).

### Fase C. Escala horizontal — *coste lineal y conocido*
- `0.5c-512mb` = **$7/mes por instancia**; `1c-2g` = $25; el autoescalado exige
  Pro (**$25/mes** de workspace, y ahí el ancho de banda incluido sube a 25 GB).
- Ruta barata: **N instancias de `0.5c`** (manual scaling funciona en cualquier
  plan). Para 50-100 req/s en el origen: **2-4 instancias** (=$14-28/mes).
- ⚠️ **Límite duro: conexiones a la BD.** El pool es de **20 por proceso**:
  4 instancias = 80 conexiones simultáneas, y eso roza el tope de Supabase.
  - **Acción obligatoria antes de pasar de 3 instancias:** usar el **pooler
    (PgBouncer, modo transacción, puerto 6543)** de Supabase, o bajar
    `pool_max` por instancia. A verificar en modo transacción: nada de estado de
    sesión (`SET`, `prepared statements` con nombre, locks de sesión). El código
    ya usa `pg_try_advisory_xact_lock` (por transacción) ✅.

### Fase D. Base de datos — *cuando la BD sea el cuello*
- Índices: ya hay `pg_trgm` + 13 índices GIN ✅.
- **Réplicas de lectura** para las consultas públicas (Supabase Pro).
- Mover métricas/logs a tablas aparte o a otro almacén (hoy `metricas_diarias`
  crece sin parar).
- Vigilar: conexiones, `pg_stat_statements`, consultas > 100 ms.

### Fase E. Sacar el trabajo pesado del web — *ya hecho*
OCR e imágenes ya viven en `localis-worker`. Al crecer: más instancias del
**worker** (o colas con Redis), nunca en el web.

### Fase F. Observabilidad y protección
- Cloudflare: WAF, *rate limiting* por IP, *bot fight*, absorción de DDoS.
- Alertas: p95 de latencia, tasa de 5xx, saturación de CPU/memoria, conexiones BD.
- **Test de carga por release** (el generador ya existe: `scripts/loadgen_server.py`
  y el control `/self` para saber si el cuello es el generador).

## 3. Objetivo por fase (con el supuesto de 0,05-0,1 req/s por usuario)

| Fase | req/s en el origen | Usuarios activos | Coste/mes |
|---|---|---|---|
| Hoy (2 × 0.5c) | ~50 | 500-1.000 | $14 (web) + $7 (worker) |
| A (CDN + HTML cacheable) | ~50 de entrada → 10 % al origen | **5.000-10.000** | +dominio (~$1/mes) |
| B (Redis) | igual, pero más margen y sin desincronizar | igual | +$10 |
| C (4 instancias) | ~100 sostenidos en el origen | picos de miles con login | +$14 |
| D (réplicas) | cuando la BD apriete | — | según Supabase |

**Conclusión honesta:** el salto a 10.000 **no lo da el plan de Render** (2c-4g
cuesta $85 y sólo llega a 106 req/s en el origen). Lo da **cachear el HTML
anónimo en el borde** + varias instancias baratas + Redis, y eso cuesta
**~$25-40/mes**, no $85-200.

## 4. Lo que sólo puede hacer el dueño (bloqueantes)

1. **Dominio propio** + zona en Cloudflare (sin esto, Fase A no existe).
2. Añadir el dominio al servicio en Render (yo puedo hacerlo por API una vez
   exista el registro DNS).
3. Decidir si se pasa a **workspace Pro** ($25/mes) para tener autoescalado y
   25 GB de ancho de banda incluidos.

## 5. Verificación (cómo saber que cada fase funciona)

1. **Capacidad**: `scripts/loadgen_server.py` desde un servicio temporal en la
   misma región, con el control `/self` para descartar que el cuello sea el
   generador (el error clásico: medir desde una máquina local y creer que el
   servidor es lento).
2. **Caché**: `cf-cache-status` en el HTML y en las imágenes (`HIT` esperado);
   para objetos de Storage, auditar con `GET` o `/object/info` (**`HEAD` miente**).
3. **Duplicación de estado con N instancias**: ya se blindaron los correos
   diarios y los recordatorios con candados de BD
   (`pg_try_advisory_xact_lock`). Cualquier tarea "comprobar y actuar" nueva
   necesita el mismo tratamiento.
4. **Deriva de datos**: `python scripts/test_supabase.py` y
   `scripts/reparar_imagenes_rotas.py` (dry-run) como chequeo periódico.

## 6. Riesgos conocidos a vigilar

| Riesgo | Mitigación |
|---|---|
| Conexiones a la BD (20/proceso) | pooler de Supabase antes de la 4.ª instancia |
| Cachés por proceso (aciertos ÷ N) | Redis (Fase B) |
| Rate limit se multiplica por instancia | Redis (Fase B) |
| CSRF en HTML cacheado | sacar el token del HTML anónimo (Fase A.3) |
| Tasa BCV / reintentos de pago repetidos | candado de BD (mismo patrón ya aplicado) |
| Coste de Supabase (egreso, transformaciones) | miniaturas propias ✅; vigilar Usage |
| Ancho de banda de Render | 5 GB incluidos; subir a Pro (25 GB) si se acerca |
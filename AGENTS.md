# AGENTS.md — Localis

Marketplace venezolano. Monolito **Flask (Python 3)** + Jinja2 server-rendered.
No hay bundler para la web: `templates/` y `static/` se sirven directamente.
`android/` es una **TWA de Capacitor** aparte (no forma parte del loop de Python).

## Git

- **Todos los commits se hacen en `main_limpia`** (rama de referencia y de despliegue).
  Trabaja, comitea y haz `push` directamente a `origin/main_limpia`; **no** abras
  ramas nuevas para features/fixes salvo petición explícita. Las ramas antiguas
  (`main`, `mejora/noche-autonoma`) ya están fusionadas y no se usan.
- Antes de commitear: `ruff check .` (el CI lo corre primero) y
  `python scripts/run_tests.py --solo-sin-red`.
- Mensajes de commit en **español** y estilo Conventional Commits
  (`feat(...)`, `fix(...)`, `docs(...)`, `perf(...)`), como el resto del historial.

## Comandos

```bash
pip install -r requirements.txt        # Python
npm install                            # solo para Android/TWA

python main.py                         # dev: init_db() + app.run(0.0.0.0:PORT, def. 10000)
gunicorn --config gunicorn.conf.py main:app   # prod (Procfile -> main:app)

python init_db.py                      # crea/migra esquema (idempotente, aditivo)
python scripts/test_<nombre>.py         # "tests" (ver abajo)
```

Sí hay CI y linter, y **fallan el build si no pasan**:

```bash
ruff check .                             # lint (ruff.toml: solo E9+F; el CI lo corre ANTES de los tests)
python scripts/run_tests.py --solo-sin-red   # lo mismo que ejecuta el CI
```

`.github/workflows/ci.yml` corre en cada push/PR dos trabajos: *«Compilar + pruebas
sin red»* (instala `requirements-dev.txt`, `ruff check .`, luego el runner) e
*«Integración (PostgreSQL efímero)»* (`python scripts/run_tests.py` completo con
un Postgres de servicio). **Corre `ruff check .` antes de commitear**: el lint va
primero, así que un error de lint tumba el CI en segundos sin llegar a los tests.
Sigue sin haber `pytest`: los "tests" son los `scripts/test_*.py`.

## Base de datos (gotchas reales)

- **Solo PostgreSQL** vía psycopg2; `DATABASE_URL` es obligatoria (el arranque falla sin ella). No hay SQLite de producción.
- Escribe SQL con marcadores **`?`**, no `%s`. La capa de conexión lo adapta en `_adapt_sql` (`database.py`): además de `?` → `%s`, reescribe sintaxis SQLite heredada (`ON CONFLICT`, `excluded.`→`EXCLUDED.`, `datetime('now')`→`CURRENT_TIMESTAMP`, `strftime`→`TO_CHAR`). Usa `database.get_db_connection` / `backend.db.get_db_connection`.
- `init_db()` es aditivo: crea tablas, añade columnas, siembra `categorias`/`planes`, reconcilia `imagen_estado` e índices. Toma un advisory lock, así que varios workers pueden arrancar a la vez. Esquema de referencia en `schema.sql`.
- `BaseDatosNoDisponible` es un error **recuperable** (arranque en frío/pool), no fatal; preserva ese trato en el manejo de errores.
- **Los `id` NO son consecutivos y no es un bug**: son secuencias `SERIAL`, que no
  devuelven valores en rollback/reintento ni al borrar filas (la importación en
  espejo y las bajas del admin borran productos). Por eso **lo visible es
  `codigo_publico`** (`PROD-/COM-/USR-`, contador en `contadores_publicos`) y el
  panel admin muestra ese código, no el id. Diagnóstico:
  `python scripts/diagnostico_ids.py [--apply]` (solo reajusta contadores
  atrasados; nunca renumera ids).

## Tests

Cada `scripts/test_*.py` es un **script autónomo** con `main()` y un reportero `_ok()`; se ejecutan uno a uno y devuelven código de salida (no hay runner que los descubra). Inserta la raíz del repo en `sys.path`, así que corre desde cualquier lugar.

- Muchos requieren `DATABASE_URL` real y/o red: crean datos sandbox `__...__` y limpian al final (p. ej. `test_importacion_formatos.py` escribe en la BD).
- Verificación rápida y **sin BD ni red**: `test_clasificador_categorias.py`,
  `test_cobertura_universal.py`, `test_asistente_verdades.py` (contrasta la
  documentación del asistente con las constantes reales: planes, destacados,
  banner, puntos y las acciones/estados de la bandeja).
- Diagnóstico: `diagnostico_*.py` y `auditoria_*.py` (algunos aceptan `--apply`; sin el flag son dry-run).

## Estado en memoria del worker (no romper)

Estos viven **por proceso**: cola de importación y `job_id` (`backend/import_queue.py`), índice del catálogo maestro (`backend/catalogo_maestro_index.py`), cachés diarias y backfill de imágenes. Por eso `WEB_CONCURRENCY=1` es el valor por defecto en `gunicorn.conf.py`: **no lo subas** sin un broker compartido (Redis/RQ), o el polling de importaciones fallará.

## Configuración

- `.env` se carga con `load_dotenv(override=False)`: el entorno del panel/Render **gana** sobre `.env`. La verdad documentada está en `.env.example` (léelo antes de tocar features).
- Producción se detecta por `RENDER`/`RENDER_EXTERNAL_URL`/`FLASK_ENV=production`; allí `LOCALIS_SECRET_KEY` es obligatoria (`config.py`).
- Casi todo se activa por variables `LOCALIS_*` (pagos: `LOCALIS_PAGOS_HABILITADOS`; bajas CSV: `LOCALIS_CSV_BAJAS`; imágenes: `LOCALIS_IMG_PIPELINE` + `SERPER_API_KEY` obligatoria para el pipeline real). No hardcodees valores: usa el switch.

## Arquitectura que no se ve en los nombres

- **Imágenes**: manual del comerciante > catálogo maestro en memoria (por EAN O(1); por nombre con Jaccard ≥ 0.6) > placeholder por categoría. El pipeline de scraping está en `services/professional_image_pipeline.py`; **corre en segundo plano**, nunca en el request. Detalles en `docs/README-imagenes.md`.
- **Categorías**: `backend/categorias_producto.py` es la **única fuente de verdad**; enum `CLAVES_CATEGORIA` y fallback `otros`. La matriz de términos usa sufijo `*` para raíces (prefijo) y palabra exacta/plural sin `*`. `clasificar_categoria` nunca lanza y ante ambigüedad devuelve `otros`. No inventes nombres de categoría.
- **Pagos opcionales: Destacados y Banners B2B**. Única fuente de precios: `backend/boosts.py` (destacado de producto: 3/5/7 días → $2/$3/$4; con Binance Pay **$1 menos** → $1/$2/$3, siempre vía `precio_boost(dias, metodo)`) y `backend/banners_b2b.py` (banner de la portada: **$60 pago móvil / $50 Binance Pay**, 30 días, **máximo 5 banners simultáneos** —con candado `pg_advisory_xact_lock` en el alta—, imagen reencuadrada a **1200×400** antes de subirla). El banner tiene **página propia** `/comercio/banner` (`templates/banner_b2b.html`, accesible desde el **perfil del usuario** y desde la barra del panel): la puede ver cualquiera y alquilarla **cualquier usuario, tenga o no tienda** (el anunciante es `comercio_id` o `usuario_id` + `anunciante`). Ambos cobran con el mismo flujo que las mensualidades: OCR del comprobante + `solicitudes_pago` con referencia única. El carrusel de `templates/cliente.html` reemplaza al banner estático, **muestra solo la imagen** (sin título ni textos encima; el nombre es interno y opcional, se usa en la lista y como `alt`), rota cada `LOCALIS_BANNER_ROTACION_SEG` (60 s) y suma `banners_b2b.impresiones` con `POST /api/banner/impresion`; sin banners activos se muestra el `banner_principal` del admin.
- **Importación CSV/Excel**: `backend/inventory_import.py` (parser ERP con cabecera dinámica) + `backend/import_queue.py` (202 + polling). UPSERT por EAN o nombre; el precio es opcional.
- **Ubicaciones**: cada comercio declara **sedes** con **nombre propio + enlace de Google Maps** (`backend/ubicaciones.py`); ya **no** se escriben `direccion`/`ciudad`/`zona` a mano. Esos datos se **derivan del enlace**: si es corto (`maps.app.goo.gl/…`) se resuelve la redirección y las coordenadas se geocodifican al revés con OpenStreetMap/Nominatim (`backend/geo_ubicacion.py`) para rellenar `ciudad`/`zona`. La primera sede es la **principal** y alimenta las columnas históricas de `comercios`. Se apaga con `LOCALIS_GEOCODIFICACION=0` (nunca lanza). La **búsqueda por ubicación** consulta las columnas del comercio **y** `sucursales` (nombre, dirección, ciudad, zona, enlace), así que no se pierden ni comercios antiguos ni nuevos. Backfill de datos existentes: `scripts/diagnostico_ubicaciones_geocodificacion.py [--apply]`. La geocodificación corre **en segundo plano** (nunca en el request).
- **Capacidad/rendimiento (medido)**: la región ya está alineada (Render virginia ↔ Supabase us-east-1, **RTT 2-8 ms**), así que el cuello dejó de ser la BD y pasó a ser **la CPU del proceso**. Medido con el generador en la nube: `0.5c-512mb` con 1 proceso servía **~26 req/s** sin errores y a 40-51 req/s empezaban los **502 (18-26 %)**; `2c-4g` con 2 procesos llegó a **106 req/s**. Por eso el web corre con **2 instancias** (`numInstances=2` por escalado manual: $7/mes por instancia y disponible en cualquier plan) y `WEB_CONCURRENCY=1` dentro de cada una, con `ProxyFix` y límite de 600 req/min por IP en `/` y `/tienda/<slug>`. Las páginas públicas usan **caché corta en memoria** (`LOCALIS_CACHE_TIENDA_SEG=20`, `LOCALIS_CACHE_CATALOGO_SEG=10`, portada 15 s, `LOCALIS_CACHE_MAX_ENTRIES`). El OCR tiene tope (`LOCALIS_OCR_MAX_CONCURRENTES=2`) y vive en el worker.
  - **Con varias instancias**, lo que es por proceso se multiplica: cachés con menos aciertos, rate limit ×N, métricas. Las importaciones ya son compartidas (`import_jobs`) y los correos diarios/recordatorios usan **candado de BD** (`pg_try_advisory_xact_lock`) para no enviarse dos veces.
  - **Límite duro**: el pool es de **20 conexiones por proceso**; 4 instancias = 80 conexiones → usar el **pooler de Supabase (puerto 6543, modo transacción)** antes de pasar de 3 instancias.
  - **Caché de imágenes**: se suben a Storage con `cache-control` de un año y el catálogo usa **miniaturas propias** de 400 px (−83 % de bytes).
  - **HTML público listo para CDN**: una visita anónima ya **no escribe la sesión** (ni `Set-Cookie` ni `Vary: Cookie`) porque el token CSRF del widget se pide aparte (`GET /api/asistente/csrf`). Falta el CDN propio: requiere **dominio propio** (hoy los servicios solo existen en `onrender.com`).
  - **Objetivo 10.000 usuarios**: plan por fases en `docs/escalado-10000.md`.
  - **Pendientes con especificación**: `docs/pendientes.md` (comprobante asíncrono, worker `1c-2g`, Redis, dominio/CDN, métricas). Retomar ahí antes de reabrir el tema.
  - Al auditar caché, **`HEAD` miente**: Supabase responde `private, no-cache` a un `HEAD` aunque el `GET` del mismo objeto dé `max-age` largo y `cf-cache-status: HIT`. Usar `GET` o el endpoint `/object/info`.
- **Estáticos y Service Worker**: el SW (`static/sw.js`) sirve `/static/` con *stale-while-revalidate*, y enlazar CSS/JS con una versión **fija** (`?v=catalogo-180`) dejaba al teléfono con la copia vieja para siempre (el panel admin se veía sin estilos). Para archivos nuevos usa `?v={{ asset_version('css/admin.css') }}` (huella mtime+tamaño, global de Jinja); al cambiar `sw.js` sube `VERSION`.
- **Escalado horizontal (`WEB_CONCURRENCY > 1`)**: el estado de las **importaciones vive en la tabla `import_jobs`** (compartido entre workers; el archivo sigue en disco). Antes estaba solo en memoria y el *polling* fallaba con más de un worker. Lo que sigue siendo **por proceso** y hay que tener en cuenta al subir workers: cachés (`runtime_cache`), rate limit (límite efectivo ×N), métricas, el semáforo de OCR (por eso conviene limitarlo con advisory lock) y la sincronización diaria de la tasa. Con 1 worker + N hilos (`GUNICORN_THREADS`) el GIL limita el trabajo de Python a ~1 núcleo: **para aprovechar más vCPU hacen falta más procesos**.

## Convenciones

- Código, docstrings y comentarios en **español**.
- El código en el camino de request es **defensivo**: captura excepciones y devuelve un fallback en vez de propagar; logs con prefijos `[Localis ...]`.
- Rutas y lógica de negocio se registran en `main.py` (app global), no hay factory `create_app`.
- Capacitor/TWA: `appId` y `server.url` en `capacitor.config.json` apuntan a `https://localis.onrender.com`; los hashes SHA-256 viven en `.well-known/assetlinks.json`. Si cambias el package id, sincroniza con `npm run cap:sync` y regenera los hashes.
- **App Android (recompilar)**: la app carga la web remota, así que **los cambios de web/JS se aplican solos**; en cambio **la config nativa va dentro del APK** y sólo cambia recompilando. Afecta a: permisos nuevos (p. ej. `ACCESS_FINE_LOCATION` para "usar mi ubicación actual"), plugins, iconos, `appId`. Proceso: `npm run cap:sync` → compilar (`gradlew.bat assembleDebug` o `npm run android:bundle`) → instalar/publicar. **Cada subida a Play debe incrementar `versionCode`** en `android/app/build.gradle`. La firma de release se lee de `android/keystore.properties` (ignorado por git; plantilla en `keystore.properties.example`).

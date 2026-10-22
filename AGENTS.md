# AGENTS.md — Localis

Marketplace venezolano. Monolito **Flask (Python 3)** + Jinja2 server-rendered.
No hay bundler para la web: `templates/` y `static/` se sirven directamente.
`android/` es una **TWA de Capacitor** aparte (no forma parte del loop de Python).

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

## Tests

Cada `scripts/test_*.py` es un **script autónomo** con `main()` y un reportero `_ok()`; se ejecutan uno a uno y devuelven código de salida (no hay runner que los descubra). Inserta la raíz del repo en `sys.path`, así que corre desde cualquier lugar.

- Muchos requieren `DATABASE_URL` real y/o red: crean datos sandbox `__...__` y limpian al final (p. ej. `test_importacion_formatos.py` escribe en la BD).
- Verificación rápida y **sin BD ni red**: `test_clasificador_categorias.py`, `test_cobertura_universal.py`.
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
- **Importación CSV/Excel**: `backend/inventory_import.py` (parser ERP con cabecera dinámica) + `backend/import_queue.py` (202 + polling). UPSERT por EAN o nombre; el precio es opcional.
- **Ubicaciones**: cada comercio declara **sedes** con **nombre propio + enlace de Google Maps** (`backend/ubicaciones.py`); ya **no** se escriben `direccion`/`ciudad`/`zona` a mano. Esos datos se **derivan del enlace**: si es corto (`maps.app.goo.gl/…`) se resuelve la redirección y las coordenadas se geocodifican al revés con OpenStreetMap/Nominatim (`backend/geo_ubicacion.py`) para rellenar `ciudad`/`zona`. La primera sede es la **principal** y alimenta las columnas históricas de `comercios`. Se apaga con `LOCALIS_GEOCODIFICACION=0` (nunca lanza). La **búsqueda por ubicación** consulta las columnas del comercio **y** `sucursales` (nombre, dirección, ciudad, zona, enlace), así que no se pierden ni comercios antiguos ni nuevos. Backfill de datos existentes: `scripts/diagnostico_ubicaciones_geocodificacion.py [--apply]`.

## Convenciones

- Código, docstrings y comentarios en **español**.
- El código en el camino de request es **defensivo**: captura excepciones y devuelve un fallback en vez de propagar; logs con prefijos `[Localis ...]`.
- Rutas y lógica de negocio se registran en `main.py` (app global), no hay factory `create_app`.
- Capacitor/TWA: `appId` y `server.url` en `capacitor.config.json` apuntan a `https://localis.onrender.com`; los hashes SHA-256 viven en `.well-known/assetlinks.json`. Si cambias el package id, sincroniza con `npm run cap:sync` y regenera los hashes.

# Localis

Marketplace venezolano. Monolito **Flask (Python 3)** con plantillas **Jinja2**
renderizadas en el servidor. No hay bundler para la web: `templates/` y
`static/` se sirven directamente. La carpeta `android/` es una **TWA de
Capacitor** independiente.

## Requisitos

- Python 3.11+ (probado en 3.12)
- PostgreSQL (obligatorio; no hay SQLite de producción)
- Claves de Google OAuth y Supabase (ver `.env.example`)

## Puesta en marcha (desarrollo)

```bash
pip install -r requirements.txt
cp .env.example .env        # completa los valores reales
python init_db.py           # crea/migra el esquema (idempotente y aditivo)
python main.py              # arranca en http://0.0.0.0:10000
```

Producción (Render / Procfile):

```bash
gunicorn --config gunicorn.conf.py main:app
```

## Variables clave

Todo se configura por variables de entorno. La referencia completa y comentada
está en **`.env.example`**; léela antes de tocar features. Las más importantes:

| Variable | Para qué |
|---|---|
| `DATABASE_URL` | PostgreSQL (obligatoria) |
| `LOCALIS_SECRET_KEY` | Firma de sesión (obligatoria en producción) |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Login con Google |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | Storage de imágenes |
| `LOCALIS_ADMIN_EMAILS` | Correos de administrador (coma), sin tocar código |
| `LOCALIS_PAGOS_HABILITADOS` | Activa planes/pagos |
| `LOCALIS_IMG_PIPELINE` + `SERPER_API_KEY` | Pipeline profesional de imágenes |
| `LOCALIS_CSP` | Content-Security-Policy opcional (vacío = no se envía) |

## Pruebas

Cada `scripts/test_*.py` es un script autónomo (no hay runner ni CI que los
descubra). Se ejecutan uno a uno:

```bash
python scripts/test_pre_cuentas_roles.py
```

Pruebas rápidas **sin BD ni red** (las que corre el CI):

```bash
python scripts/test_clasificador_categorias.py
python scripts/test_cobertura_universal.py
```

El CI (`\.github/workflows/ci.yml`) compila todo y ejecuta esas dos pruebas en
cada push y pull request.

## Estructura

```
main.py                 App Flask global (rutas, arranque, hooks)
database.py             Conexión PostgreSQL, init_db, migraciones aditivas
config.py               Configuración por entorno y validación de arranque
backend/                Lógica de negocio (auth, admin, stores, pre_cuentas, ...)
services/               Pipeline profesional de imágenes
templates/  static/     UI server-rendered
scripts/                Tests y diagnósticos autónomos
docs/                   Documentación
android/                TWA de Capacitor
```

## Documentación

- `AGENTS.md` — guía para agentes/devs (comandos, gotchas, convenciones).
- `docs/arquitectura.md` — cómo está armado y qué flujos existen.
- `docs/auditoria-tecnica.md` — puntuación, riesgos y hoja de ruta.
- `docs/README-imagenes.md` — pipeline de imágenes en detalle.

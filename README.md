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
| `LOCALIS_PAGOS_HABILITADOS` | `0` = fase gratis (solo Destacados); `1` = reactiva los planes |
| `LOCALIS_IMG_PIPELINE` + `SERPER_API_KEY` | Pipeline profesional de imágenes |
| `LOCALIS_CSP` | Content-Security-Policy opcional (vacío = no se envía) |

## Fase de lanzamiento (temporal): gratis, sin límites y solo Destacados

> **Qué se hizo y por qué.** Por decisión de producto, Localis entró en una
> **fase de lanzamiento gratuita**: la app es **100% gratis y sin límites** y el
> **único pago opcional es el Destacado** de un producto. Los **planes de
> suscripción** (Gratis/Prueba, Básico, Pro, Business) quedaron **ocultos**, no
> borrados. El objetivo de esta fase es crecer (fama y adopción) sin fricción de
> precios, dejando el destaque como única palanca de ingreso.

- **Interruptor único:** `LOCALIS_PAGOS_HABILITADOS` (por defecto **`0`**).
  - `0` → **fase gratuita**: el panel no muestra Planes/Suscripción/Pago Móvil,
    no se aplican límites de productos y no se expiran tiendas por suscripción.
    Los **Destacados siguen visibles y funcionando** (pago móvil, Binance Pay o
    canje de puntos).
  - `1` → **se retoma** el modelo de planes tal como estaba.
- **Nada se eliminó.** Tablas, endpoints, plantillas y textos de planes siguen en
  el código, protegidos por los marcadores `RESTAURAR_PAGOS_INICIO/FIN` (web) y
  `PLANES_INICIO/FIN` (conocimiento del asistente).
- **Es temporal y se retomará en su momento.** Para volver al modelo de planes
  basta con poner la variable en `1` (en `.env` o en el panel del hosting); no hay
  que tocar código.
- **Asistente de IA:** en fase gratuita **no menciona planes ni precios de
  suscripción** (se excluyen de su base de conocimiento), pero **sus datos quedan
  guardados** y se reactivan solos al poner `LOCALIS_PAGOS_HABILITADOS=1`. Sí
  habla del **Destacado**, incluida la **revalidación del pago de un destacado**
  (el asistente puede volver a consultar Binance y reactivar el destaque).

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

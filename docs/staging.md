# Entorno de staging

Objetivo: un entorno **aislado** para probar cambios (especialmente el refactor
de Blueprints) **sin tocar producción**.

## 1) Proyecto Supabase de staging
Ya creado: `https://wlfpyygzoqnvxnhpuvvh.supabase.co`.

Lo que hay que copiar del dashboard (NO la "Project URL", sino la conexión):
- **Settings → Database → Connection string → URI** (usa el *Session pooler*),
  con formato:
  `postgresql://postgres.<ref>:<PASSWORD>@aws-0-<region>.pooler.supabase.com:5432/postgres`
  → esta es la `DATABASE_URL` de staging.
- **Settings → API → `service_role`** → `SUPABASE_SERVICE_ROLE_KEY` (para imágenes).

No hace falta crear tablas a mano: `init_db()` las crea al arrancar el servicio.

## 2) Servicio de staging en Render
Opción A (recomendada): **Preview Environments** (crea un servicio por PR).
Opción B: segundo **Web Service**:
- Repository: `yahyadiab666-eng/localis`
- Branch: `main_limpia`
- Build: `pip install -r requirements.txt`
- Start: `gunicorn --config gunicorn.conf.py main:app`
- Health check path: `/health/live`
- Python: `3.12.8` (o dejar que use `.python-version`)

### Variables de entorno de staging
```
DATABASE_URL=<connection string del proyecto Supabase de staging>
SUPABASE_URL=https://wlfpyygzoqnvxnhpuvvh.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service_role de staging>
LOCALIS_SECRET_KEY=<clave aleatoria distinta a producción>
GOOGLE_CLIENT_ID=<mismo que prod o uno de pruebas>
GOOGLE_CLIENT_SECRET=<mismo que prod o uno de pruebas>
LOCALIS_ADMIN_EMAILS=<tu correo>
LOCALIS_LOG_JSON=1
LOCALIS_PAGOS_HABILITADOS=0        # gratis para probar sin pagos
# SERPER_API_KEY= (opcional; sin ella las imágenes usan placeholder)
```

## 3) Verificación
```
curl https://<staging>.onrender.com/health/live
python scripts/e2e_smoke.py --base-url https://<staging>.onrender.com
```

## Uso en el flujo
`feature → CI verde → e2e_smoke contra staging → merge a main_limpia → deploy`.
El E2E debe pasar en staging **antes y después** de cada refactor.

## Nota de seguridad
No pegues la `DATABASE_URL` ni `service_role` en el chat: ponlas directamente
en las *Environment Variables* del servicio de staging en Render.

# Guía paso a paso: disponibilidad y staging

Dos cosas a configurar (ambas fuera del repo). Sigue el orden.

---

## A) Evitar el spin-down (plan gratuito de Render) — 5 min

Render duerme la instancia tras ~15 min sin tráfico; el primer usuario espera
30–60 s. Se evita con un monitor externo.

1. Entra a **https://uptimerobot.com** (o https://cron-job.org) y crea una
   cuenta gratis.
2. **Add Monitor** (UptimeRobot) con:
   - Monitor Type: **HTTP(s)**
   - Friendly Name: `Localis`
   - URL: `https://localis.onrender.com/health/live`
   - Monitoring Interval: **5 minutes**
   - Alert Contacts: tu correo.
3. Guarda. Debe aparecer como **Up**.

Alternativa: cambiar la rama por defecto del repo a `main_limpia` (GitHub →
Settings → Branches) para que el workflow `.github/workflows/keepalive.yml`
(ping cada 10 min) sí se ejecute. Es menos fiable que el monitor externo.

---

## B) Montar el entorno de STAGING (para el refactor de Blueprints)

Proyecto Supabase de staging ya creado: `wlfpyygzoqnvxnhpuvvh`.

### B1. Copiar la cadena de conexión (Postgres)
1. Supabase → proyecto de staging → **Settings → Database**.
2. En **Connection string**, elige la pestaña **URI** y el modo
   **Session pooler**. Copia la cadena, con formato:
   ```
   postgresql://postgres.wlfpyygzoqnvxnhpuvvh:[TU_PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres
   ```
3. Reemplaza `[TU_PASSWORD]` por la contraseña de la base (si no la sabes,
   **resetéala** en la misma pantalla). Usa el **pooler** (IPv4): Render no sale
   por IPv6.

### B2. Inicializar el esquema de staging (desde tu PC)
En la carpeta del proyecto:
```powershell
$env:STAGING_DATABASE_URL="<pega aquí la cadena del paso B1>"
python scripts/init_staging.py
```
Debe imprimir `tablas en staging: NN` y `OK staging inicializado.`
(Si te equivocas de proyecto, el script lo rechaza: protege producción.)

### B3. Copiar la llave de Storage (imágenes)
Supabase → staging → **Settings → API → Project API keys → `service_role`**.
Guárdala para el paso B4. También confirma la **Project URL**:
`https://wlfpyygzoqnvxnhpuvvh.supabase.co`

### B4. Crear el servicio en Render
Render → **New + → Web Service** → repo `yahyadiab666-eng/localis`.
- **Name:** `localis-staging`
- **Branch:** `main_limpia`
- **Build Command:** `pip install -r requirements.txt`
- **Start Command:** `gunicorn --config gunicorn.conf.py main:app`
- **Instance Type:** Free
- **Health Check Path:** `/health/live`

**Environment Variables** (Add):
| Clave | Valor |
|---|---|
| `DATABASE_URL` | la cadena de B1 |
| `SUPABASE_URL` | `https://wlfpyygzoqnvxnhpuvvh.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | la llave de B3 |
| `LOCALIS_SECRET_KEY` | una aleatoria (ver abajo) |
| `PYTHON_VERSION` | `3.12.8` (o bórrala y usa `.python-version`) |
| `GOOGLE_CLIENT_ID` | el mismo que producción |
| `GOOGLE_CLIENT_SECRET` | el mismo que producción |
| `LOCALIS_ADMIN_EMAILS` | tu correo |
| `LOCALIS_LOG_JSON` | `1` |
| `LOCALIS_PAGOS_HABILITADOS` | `0` |

Genera la clave secreta con:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### B5. Autorizar el login de Google en staging (¡importante!)
Si usas el mismo cliente de Google, en **Google Cloud Console →
APIs & Services → Credentials → tu OAuth 2.0 Client ID → Authorized redirect
URIs** añade:
```
https://localis-staging.onrender.com/login/google/callback
```
Sin esto, el login dará `redirect_uri_mismatch` en staging.

### B6. Verificar y avisar
Cuando el deploy termine:
- Abre `https://localis-staging.onrender.com/health/live` → debe decir
  `{"ok": true, ...}`.
- Pásame esa URL. Yo corro
  `python scripts/e2e_smoke.py --base-url https://localis-staging.onrender.com`
  y ejecuto el refactor de Blueprints.

---

## Seguridad
No pegues `DATABASE_URL` ni `service_role` en el chat: van directo en las
*Environment Variables* de Render (y en `$env:STAGING_DATABASE_URL` para el paso B2).

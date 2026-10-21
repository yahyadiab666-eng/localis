# Reporte diario de tráfico y PWA

Resumen diario del tráfico real y de la instalación/uso de la PWA, enviado por
correo a todos los administradores. Pensado para saber **si la app se usa** sin
depender de analítica externa ni de pagos.

## Qué mide

| Dato | Clave en BD | Cómo se obtiene |
|---|---|---|
| Peticiones totales | `peticiones` | Toda petición HTTP |
| Tráfico de la app | `peticiones_app` | Sin estáticos, `/health`, `/metrics` |
| Visitantes únicos (aprox.) | `visitas_unicas` | Huella IP+navegador por día, sin bots |
| Instalaciones de PWA | `pwa_instalaciones` | Evento `appinstalled` (beacon del cliente) |
| Aperturas desde la PWA | `pwa_aperturas` | Peticiones con `?source=pwa` |

### Qué NO se puede medir (a propósito)

- **Instalaciones en iPhone (iOS):** Safari no expone ningún evento al
  "Añadir a inicio", así que **no se cuentan**. Solo se ve su uso posterior
  (aperturas de la PWA).
- **Descargas de la app Android (Google Play):** solo están disponibles en
  **Google Play Console**. Automatizarlas requeriría la *Google Play Developer
  API* (cuenta de servicio + permisos). No se incluyen en el reporte.
- **"Visitantes únicos" es aproximado:** el conjunto de huellas vive en memoria
  del worker y se reinicia al reiniciar el proceso o al cambiar de día; puede
  subcontar ligeramente. Los días se calculan en **UTC** (el servidor corre en
  UTC), no en hora de Venezuela.

## Cómo funciona

1. Cada petición se cuenta en memoria (`backend/trafico.py`) desde el hook
   `after_request` (`backend/observabilidad.py`). No hay una escritura a la BD
   por petición.
2. Un hilo daemon vuelca los acumulados a la tabla **`metricas_diarias`**
   (`UPSERT` atómico) cada `LOCALIS_TRAFICO_FLUSH_SEC` segundos (por defecto 45).
   Si el volcado falla, los deltas se reintentan y no se pierden.
3. El reporte lee `metricas_diarias` y lo envía por correo.

Con `WEB_CONCURRENCY=1` el acumulador en memoria es consistente. **No subas los
workers** sin mover la agregación a un almacén compartido.

## Configuración (una sola vez)

En **Render** (variables de entorno):

```
LOCALIS_TRAFICO=1            # activado por defecto; 0 lo desactiva
LOCALIS_CRON_TOKEN=<cadena-larga-aleatoria>
SMTP_USER=<tu-correo>        # p. ej. Gmail con "contraseña de aplicación"
SMTP_PASSWORD=<app-password>
LOCALIS_ADMIN_EMAILS=<correo1@x.com>,<correo2@x.com>   # destinatarios
```

En **GitHub** (Settings → Secrets and variables → Actions):

- **Secret** `LOCALIS_CRON_TOKEN` = el mismo valor que en Render.
- **Variable** `LOCALIS_BASE_URL` = opcional (por defecto
  `https://localis.onrender.com`).

El workflow `.github/workflows/reporte-diario.yml` llama cada día a
`POST /tareas/reporte-trafico` con el token. Si el servicio está dormido, la
llamada lo despierta.

## Uso manual

```bash
# Ver el reporte sin enviar (no necesita SMTP):
python scripts/reporte_trafico_diario.py --sin-correo

# Enviar (necesita SMTP configurado), forzando aunque ya se envió hoy:
python scripts/reporte_trafico_diario.py --forzar

# Periodo de 7 días:
python scripts/reporte_trafico_diario.py --dias 7 --sin-correo
```

Disparar el endpoint (cron / pruebas):

```bash
curl -X POST -H "X-Cron-Token: $LOCALIS_CRON_TOKEN" \
  https://localis.onrender.com/tareas/reporte-trafico
```

## Idempotencia

El reporte no se reenvía dos veces el mismo día: la fecha del último envío se
guarda en `configuracion_sistema` (clave `reporte_trafico_ultimo_envio`). Usa
`?forzar=1` (o `--forzar`) para reenviar.

## Seguridad

- El endpoint `/tareas/reporte-trafico` exige `LOCALIS_CRON_TOKEN`. **Si no está
  configurado, la ruta responde 404** (no existe). Compare con tiempo constante.
- No expone datos personales: solo contadores agregados.

## Diagnóstico

- **No llega el correo:** revisa `SMTP_USER`/`SMTP_PASSWORD`. El script/log
  indican `SMTP no configurado` si faltan. Revisa también spam.
- **El reporte dice "Aún no hay datos":** la tabla está vacía; normal hasta que
  haya tráfico tras el despliegue (el volcado ocurre cada ~45 s).
- **Todo a cero:** verifica `LOCALIS_TRAFICO` (no debe ser `0`) y que la tabla
  `metricas_diarias` exista (`python init_db.py`).

## Pruebas

- `scripts/test_trafico.py` — lógica (sin BD ni red).
- `scripts/test_trafico_bd.py` — tabla, UPSERT, volcado, resumen, idempotencia.

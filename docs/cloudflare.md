# Poner Cloudflare delante (paso a paso)

> Motivo: el pico de tráfico debe morir en el borde, no en el origen. Hoy
> `localis.com` no existe: los servicios solo viven en `*.onrender.com`, y **no
> se puede gestionar el DNS de `onrender.com`**. Por eso esta parte necesita un
> **dominio propio** (acción del dueño).

## Ya está listo en el código (hecho)

- Las páginas públicas anónimas **no escriben la sesión** (sin `Set-Cookie`) y el
  **token CSRF del widget se pide aparte** (`GET /api/asistente/csrf`): el HTML es
  idéntico para todos los anónimos. Es la condición para cachear.
- Las imágenes de Storage se sirven con `cache-control` de un año y el catálogo
  usa **miniaturas propias** de 400 px (−83 % de bytes).
- `/static/*` ya manda `Cache-Control: public, max-age=604800`.

## 1. Dominio y zona (dueño)

1. Registrar/poseer el dominio (p. ej. `localis.com.ve`).
2. Crear cuenta en Cloudflare (plan **Free** basta) y añadir el dominio como zona;
   Cloudflare te dará **2 nameservers** para poner en el registrador.
3. Esperar la propagación (minutos-horas).

## 2. DNS (Cloudflare)

| Tipo | Nombre | Contenido | Proxy |
|---|---|---|---|
| `CNAME` | `@` (raíz) | `localis.onrender.com` | **Proxied** (nube naranja) |
| `CNAME` | `www` | `localis.onrender.com` | **Proxied** |
| `CNAME` | `api` (opcional) | `localis-worker.onrender.com` | Proxied |

> El proxy **debe estar activado** (naranja). En gris, Cloudflare no cachea ni
> protege nada.

## 3. Dominio en Render (yo lo hago por API, o tú desde el panel)

`POST /v1/services/srv-dbdcbvvb9u1s7396u1eg/custom-domains` con
`{"name": "localis.com.ve"}` (y otro para `www`). Render emite el **TLS** solo y
muestra los registros de validación si hacen falta. **No** actives el
"redirect" en Render: el que redirige `www`→raíz debe ser Cloudflare
(Redirect Rule), no dos capas.

## 4. Reglas de caché (Cloudflare → Rules → Cache Rules)

**Regla 1 — No cachear a quien tiene sesión** (prioridad 1)

- Expresión: `(http.cookie contains "localis_session")`
- Acción: **Bypass cache**

**Regla 2 — HTML público con TTL corto en el borde** (prioridad 2)

- Expresión:
  ```
  (http.request.method eq "GET"
   and not http.cookie contains "localis_session"
   and (http.request.uri.path eq "/"
        or http.request.uri.path starts_with "/tienda/"
        or http.request.uri.path starts_with "/categoria"
        or http.request.uri.path starts_with "/buscar"
        or http.request.uri.path eq "/como-funciona"
        or http.request.uri.path eq "/ayuda"))
  ```
- Acción: **Cache eligible** + **Edge TTL: 60 s** + "Ignore origin cache-control"
  (así no hace falta que la app mande `s-maxage`).
- Por qué 60 s y no más: los precios y el stock cambian; 60 s absorbe el pico sin
  servir datos rancios.

**Regla 3 — Estáticos** (prioridad 3)

- Expresión: `(http.request.uri.path starts_with "/static/")`
- Acción: Cache eligible + Edge TTL **1 año** + Browser TTL (respect origin).

> El orden importa: la Regla 1 (bypass por cookie) debe evaluarse **antes** que
> las de cachear.

## 5. Protección (Cloudflare → Security)

- **Bot Fight Mode**: ON (gratis).
- **Rate limiting rule** (el plan gratis incluye 1): p. ej. 300 peticiones/min
  por IP en `/api/asistente/*` y `/login`.
- **WAF managed rules**: ON con las reglas gratuitas.
- Mantén el límite propio de la app (`600 req/min` por IP) como segunda capa.

## 6. Verificación (obligatoria tras activar)

```bash
# 1) el HTML anónimo lo sirve el borde en la segunda visita
curl -sI https://localis.com.ve/ | findstr /i "cf-cache-status cache-control"
# esperado: cf-cache-status: HIT (la 2.ª vez) y que NO haya Set-Cookie

# 2) con cookie de sesión NO se cachea
curl -sI -H "Cookie: localis_session=x" https://localis.com.ve/ | findstr /i "cf-cache-status"
# esperado: BYPASS o DYNAMIC

# 3) un usuario logueado nunca recibe HTML anónimo cacheado
#    (comprobarlo a mano: entrar logueado y ver que sale el menú de usuario)

# 4) las imágenes siguen en HIT por Supabase
#    (auditar con GET o /object/info: HEAD miente)
```

## 7. Qué NO hacer

- No activar "Cache Everything" para **todo** el sitio: las páginas con sesión
  (panel del comercio, admin, carrito) nunca deben cachearse.
- No usar "Always Online"/"Rocket Loader" sin probar (Rocket Loader reordena el
  JS en línea y esta app depende de scripts en línea).
- No poner el proxy en gris: sin naranja no hay caché ni protección.
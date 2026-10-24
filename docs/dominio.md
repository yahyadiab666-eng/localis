# Dominio propio: contratar y migrar todo

> Objetivo: dejar de depender de `localis.onrender.com` para poder poner
> **Cloudflare** delante (ver `docs/cloudflare.md`) y tener una URL de marca.

## 1. Contratar el dominio

### ¿Sirve un `.ve` para el plan de CDN? **Sí (verificado)**

Lo que hacía falta comprobar es si NIC.ve deja **delegar los nameservers** a
Cloudflare (sin eso no hay caché ni WAF). Medido con dominios `.ve`/`.com.ve`
reales:

| Dominio | Nameservers | Veredicto |
|---|---|---|
| `traki.com.ve` | `quincy.ns.cloudflare.com`, `katelyn.ns.cloudflare.com` | **Cloudflare** ✅ |
| `venelogia.com` | `lloyd.ns.cloudflare.com`, `laura.ns.cloudflare.com` | **Cloudflare** ✅ |
| `farmatodo.com.ve` | GoDaddy DNS | delegación externa ✅ |
| `centralmadeirense.com.ve` | Cirion | delegación externa ✅ |
| `mercadolibre.com.ve` | AWS Route53 | delegación externa ✅ |

**Conclusión:** un `.ve` (o `.com.ve`) funciona con todo lo de `docs/cloudflare.md`.

**Lo que hay que confirmar antes de pagar** (el DNS no prueba disponibilidad):
disponibilidad en NIC.ve, el trámite (cédula/RIF, pago en **bolívares**) y que la
**renovación no es automática** — apuntar el vencimiento en el calendario, porque
perder el dominio tumbaría la app y el APK.

### Estado de los candidatos (medido por DNS: "libre" = no resuelve, hay que confirmarlo en el registrador)

| Dominio | Estado |
|---|---|
| `localis.com`, `localis.net`, `localis.app`, `localis.store`, `localis.shop`, `localis.xyz` | **ocupados** |
| `localisve.com`, `uselocalis.com`, `getlocalis.com`, `localisven.com`, `localis.market`, `localis.com.co`, `localis.com.ar` | probablemente libres |
| `localis.com.ve`, `localis.ve`, `localis.shop.ve` | probablemente libres (NIC.ve) |

Confirmar siempre en el registrador (el DNS no prueba que esté libre).

### Dónde comprarlo

| Opción | Precio aprox. (.com/año) | Pago | Notas |
|---|---|---|---|
| **Porkbun** | $10-11 | tarjeta internacional, PayPal | privacidad WHOIS gratis, panel claro. **Recomendado** |
| **Namecheap** | $11-13 | tarjeta, PayPal | privacidad gratis el 1er año |
| **Cloudflare Registrar** | ~$10 (a coste, sin margen) | tarjeta | exige los nameservers de Cloudflare; ideal como destino (`Transfer`) |
| GoDaddy / Hostinger | $15-25+ | varios | más caro y con extras que no hacen falta |
| **NIC.ve** (`.ve`, `.com.ve`) | variable, en bolívares | pago local (RIF/cédula) | trámite local más lento; DNS más básico |

**Recomendación (revisada con la evidencia de arriba):**
- **Primera opción: `localis.ve`** — marca limpia (mejor que `localisve.com`),
  señal geográfica para Venezuela y **el plan de CDN funciona** (verificado).
  Contra: trámite local en NIC.ve (cédula/RIF), pago en **bolívares** y
  renovación **no automática**.
- **Plan B para no bloquearse: `localisve.com`** en Porkbun/Namecheap (10 min con
  tarjeta). Si el trámite de NIC.ve se tarda, se arranca con el `.com` y luego se
  añade el `.ve` redirigiendo.
- Si el presupuesto lo permite, **registrar además `localis.com.ve`**: es el
  formato "corporativo" que muchos comercios esperan, y evita que otro lo registre
  y confunda a tus usuarios.

### Pasos

1. Comprar el dominio (10 min). Si el pago internacional es un problema: PayPal,
   o un `.ve` por NIC.ve con pago local.
2. En Cloudflare: *Add a site* → pegar el dominio → **plan Free** → copiar los 2
   nameservers.
3. En el registrador: cambiar los nameservers a los de Cloudflare.
4. Esperar la propagación. **Avisarme el dominio** y sigo con el punto 2 (yo hago
   el resto por API y commits).

## 2. Migrar todo al dominio (checklist)

### Lo que hago yo (API + código)

| # | Dónde | Qué |
|---|---|---|
| 1 | Render | `POST /v1/services/<id>/custom-domains` en **`localis`** y **`localis-worker`** (TLS automático). `RENDER_EXTERNAL_URL` se actualiza solo |
| 2 | `config.py` | `LOCALIS_ASISTENTE_SITIO` por defecto → el dominio (y la env var en Render) |
| 3 | `backend/correo.py` | `_base_url()` de los correos (enlaces de pago/renovación) |
| 4 | `capacitor.config.json` | `server.url` → `https://<dominio>` |
| 5 | `www/index.html` | la página de entrada del TWA (si lleva URL fija) |
| 6 | Docs | actualizar las menciones al dominio antiguo |
| 7 | Cloudflare | las reglas de caché de `docs/cloudflare.md` + DNS (CNAME a `onrender.com`, proxy naranja) |

> Los **hashes** de `.well-known/assetlinks.json` **no** dependen del dominio (la
> ruta ya se sirve desde `main.py`): al cambiar el dominio, siguen valiendo.
> El `manifest.json` de la PWA, el `sw.js` y el `scope: '/'` usan rutas relativas:
> **no cambian**.

### Lo que tienes que hacer tú (Android/Play)

| # | Qué | Por qué |
|---|---|---|
| 8 | `npm run cap:sync` + recompilar el APK/AAB | `server.url` es **config nativa**: no se actualiza sola |
| 9 | Subir `versionCode` en `android/app/build.gradle` | obligatorio en cada subida a Play |
| 10 | Publicar en Play | si ya está publicada, es una actualización normal |
| 11 | Google Search Console | añadir la propiedad del dominio nuevo y enviar el `sitemap.xml` |
| 12 | UptimeRobot (si lo usas) | cambiar la URL vigilada a `https://<dominio>/health/live` |

> **No** cambiar el `appId` (`com.onrender.localis.twa`): cambiar el id de paquete
> crea una app **nueva** en Play y pierdes las instalaciones. Aunque lleve
> "onrender" en el nombre, se queda.

### Verificación (tras migrar)

1. `https://<dominio>/health/live` responde y el `/health/ready` sale `ok`.
2. `https://<dominio>/.well-known/assetlinks.json` responde 200 (para el TWA).
3. `https://<dominio>/robots.txt` y `/sitemap.xml` con el dominio nuevo.
4. La portada anónima **sin `Set-Cookie`** (ya está; se vuelve a comprobar).
5. Los correos salen con enlaces al dominio nuevo (probar un recordatorio en
   seco o revisar la plantilla).
6. La app instalada en Android abre el dominio nuevo (tras recompilar).
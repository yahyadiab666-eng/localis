# Preguntas frecuentes y guía de soporte (asistente Localis)

Documento curado para el asistente de IA. Explica, en lenguaje claro y con datos
concretos, cómo funciona Localis para clientes y comerciantes. Es la fuente
principal de respuestas del asistente.

## ¿Qué es Localis?

Localis es un marketplace y catálogo de comercios locales de Venezuela (Isla de
Margarita). Los comerciantes publican su catálogo con precios en dólares y
bolívares, y los clientes los encuentran, los comparan y contactan directamente
al comercio por WhatsApp. No hay carrito ni pago dentro de la app: la compra y
la entrega se acuerdan con cada comercio.

## Para clientes: buscar y contactar

- Se busca por nombre de producto o se navega por categorías desde la portada.
- En cada producto y en cada tienda hay un botón de WhatsApp que abre una
  conversación con el comercio con un mensaje ya escrito.
- Si un comercio o un artículo tiene datos incorrectos, se puede reportar; el
  equipo lo revisa.

## Para comerciantes: crear la tienda

- El registro es iniciando sesión con Google y luego creando la tienda desde el
  panel del comerciante. Cada comercio tiene un enlace público propio (su
  "tienda") que se puede compartir.

<!-- PLANES_INICIO -->
## Planes y precios (concreto)

En resumen: el plan Gratis/Prueba cuesta 0 USD (hasta 50 productos), el Básico
cuesta 10 USD al mes (hasta 100 productos), el Pro cuesta 15 USD al mes (hasta
300 productos) y el Business cuesta 35 USD al mes (productos ilimitados). Todos
se renuevan cada 30 días.

Todos los planes se renuevan cada 30 días:

| Plan | Precio | Límite de productos | Notas |
|---|---|---|---|
| Gratis / Prueba | 0 USD | hasta 50 | 30 días de prueba gratuita |
| Básico | 10 USD/mes | hasta 100 | presencia en el catálogo público |
| Pro | 15 USD/mes | hasta 300 | mayor visibilidad en búsquedas; destacado |
| Business | 35 USD/mes | ilimitados | máxima visibilidad y destacado; soporte prioritario |

- El panel muestra en todo momento el plan activo, cuántos productos se usan y
  el límite. Si se alcanza el límite, hay que subir de plan o liberar productos.
- La prueba gratuita son 30 días con el plan Gratis (hasta 50 productos).

## Cómo pagar un plan

Hay dos métodos: **Pago Móvil** y **Binance Pay (USDT)**.

- Pago Móvil: la app muestra en la sección Planes los datos exactos (banco,
  cédula/RIF y teléfono destino). Se paga el monto exacto.
- Binance Pay: la app muestra el Pay ID. En Binance: Pay → Enviar → ID, pegar el
  ID y enviar el monto exacto en USDT.
- El monto en bolívares se calcula con la tasa del día que muestra la app.

Después del pago:

1. Se sube una **captura del comprobante** en la sección Planes.
2. La app la lee con OCR y verifica el monto, la fecha (debe ser reciente) y el
   destino. Guarda un hash para impedir que el mismo comprobante se use dos veces.
3. Si la verificación es concluyente, el plan se activa automáticamente.
4. Si no es concluyente, el pago queda "sin verificar" y pasa a **revisión manual**
   del equipo; al aprobarse, el plan se activa.

### Problemas al pagar

- **El comprobante se rechaza:** verifica que el monto sea el exacto, que la fecha
  del comprobante sea reciente (del día) y que no hayas usado el mismo comprobante
  antes. Debe ser al banco y teléfono que muestra la app.
- **Binance no encuentra la orden:** el ID de la orden debe estar exacto y el monto
  exacto en USDT. Copia el ID desde Binance (no lo escribas a mano).
- **Quedó "sin verificar":** a veces es un fallo temporal de la API de Binance. Se
  puede volver a consultar para confirmarlo sin esperar.

## Cambios de plan

- Subir de plan (upgrade) se aplica de inmediato.
- Bajar de plan (downgrade) se programa para el final del periodo actual.
<!-- PLANES_FIN -->

## Destacados (boosts)

Se puede destacar un producto o la tienda para aparecer con prioridad:

| Duración | Pago Móvil | Binance Pay |
|---|---|---|
| 3 días | 2 USD | 1 USD |
| 5 días | 3 USD | 2 USD |
| 7 días | 4 USD | 3 USD |

También se puede alquilar un **banner en la portada**: 30 días por **60 USD con
Pago Móvil** o **50 USD con Binance Pay**, imagen de **1200 × 400 px**, máximo 5
banners activos a la vez. Lo puede alquilar cualquier usuario, tenga o no tienda.

## Bandeja del panel de administración

- El panel separa **Solicitudes de los usuarios** (soporte, reporte de tienda,
  reporte de artículo) y **Revisión de pagos** (alertas de pagos que la API no
  pudo confirmar).
- La bandeja solo muestra lo abierto: estados **Pendiente** y **En proceso**.
- Acciones: **Tomar (en proceso)**, **Aceptar y archivar**, **Rechazar y
  archivar**, **Aceptar y suspender la tienda** y **Aceptar y ocultar el
  producto**. Al aceptar o rechazar, la solicitud **se elimina de la bandeja**
  y la decisión queda registrada en auditoría.
- Las alertas de pago se resuelven con **Confirmar pago** (activa lo pagado),
  **Suspender**, **Marcar como revisada** o **Rechazar**; al cerrarlas también
  se archivan.

## Premios y puntos

- Cada comercio que se refiere suma **1 punto**.
- Los puntos se canjean por días de destacado: 3 puntos = 3 días; 4 puntos = 5
  días; 5 puntos = 7 días.
- Los puntos y canjes se ven en la sección Premios del panel.

## Importar inventario (CSV o Excel)

- Se sube un archivo CSV o Excel; el sistema reconoce la cabecera de forma
  dinámica (no hace falta un formato exacto).
- Los productos se identifican por código de barras (EAN) o por nombre. El precio
  es opcional.
- La importación es asíncrona: la app responde al instante y procesa en segundo
  plano, mostrando el avance.
- Qué pasa con los productos que ya no vienen en el archivo se controla con la
  configuración de bajas: por defecto se eliminan de la tienda (espejo); también
  se pueden solo desactivar u obviar.

## Por qué un producto no tiene foto

- Prioridad: la foto que sube el comerciante; si no, una foto real del catálogo
  maestro; y si tampoco hay, un marcador según la categoría.
- Localis busca fotos reales en segundo plano y nunca inventa imágenes.
- Se puede subir una foto manualmente desde el panel; esa siempre tiene prioridad.

## Sedes y ubicaciones

- Cada comercio declara sus sedes con un nombre propio y un enlace de Google Maps.
  La ciudad y la zona se deducen del enlace automáticamente.
- La primera sede es la principal. La búsqueda por ubicación tiene en cuenta todas
  las sedes.

## Categorías de productos

- Las categorías las define la plataforma. El sistema clasifica cada producto; si
  no está seguro lo deja en "otros". El comerciante puede ajustar la categoría.

## Compartir tu tienda

- Cada comercio tiene un enlace público (su tienda) y un código QR que se puede
  descargar desde el panel para compartirlo en redes, vitrinas o volantes.

## Subir la foto de un producto a mano

- Desde el panel se puede subir una imagen para un producto. Esa foto siempre
  tiene prioridad sobre las automáticas.

## Buscar por ubicación

- La búsqueda tiene en cuenta las sedes de los comercios (ciudad, zona y el
  enlace de Google Maps que declaró cada comercio).

## Eliminar la cuenta y privacidad

- La cuenta se puede eliminar desde la app (opción de eliminar cuenta). Hay una
  página de privacidad con el detalle de datos y de los terceros que se usan
  (Google para el acceso, Supabase para las imágenes y Render para el hosting).

## Soporte técnico: problemas comunes

- No puedo iniciar sesión: el acceso es con la cuenta de Google; si la sesión
  expiró, se vuelve a iniciar sesión.
- Mi importación no avanza: se procesa en segundo plano y puede tardar; conviene
  revisar que el archivo tenga encabezados legibles y volver a intentar.
- Los cambios no se ven: la app y el navegador usan caché; basta recargar. Los
  listados públicos se actualizan en pocos segundos.
- Un producto sin precio o sin categoría: el precio es opcional al importar y se
  puede completar a mano; la categoría se puede ajustar.

<!-- PLANES_INICIO -->
## Revisar o revalidar un pago

- Con sesión de comerciante, el asistente puede **volver a consultar Binance**
  para un pago que quedó pendiente (por ejemplo, un fallo temporal de la API o un
  comprobante que se leyó mal). El comerciante lo confirma desde el propio chat.
- Si Binance confirma la orden y el pago **aún no estaba aplicado**, el asistente
  **reactiva lo que se pagó**: el plan o el destacado del producto. Si ya estaba
  aplicado, solo cierra la revisión (no duplica).
- El asistente nunca cambia montos ni activa nada que Binance no confirme.
<!-- PLANES_FIN -->

<!-- GRATIS_INICIO -->
## Revisar o revalidar el pago de un destacado

- El asistente puede **volver a consultar Binance** para un pago de **destacado**
  (Pago Móvil o Binance Pay) que quedó pendiente o sin verificar.
- Si Binance confirma la orden y el destacado **aún no estaba aplicado**, el
  asistente **reactiva el destacado** del producto. Si ya estaba aplicado, solo
  cierra la revisión (no duplica nada).
- El asistente nunca cambia montos ni activa nada que Binance no confirme.
- En esta fase **no hay planes de suscripción**: Localis es gratis y el único
  pago opcional es el destacado.
<!-- GRATIS_FIN -->

## Cuándo pasar con una persona

- El asistente da información y guía, y puede revalidar un pago concreto (ver
  arriba). No edita el comercio, no cambia precios ni resuelve un fraude.
- Si la consulta necesita que alguien actúe (corregir datos de un comercio,
  resolver un error de importación específico, un pago rechazado o un problema de
  acceso), hay que escribir al soporte por WhatsApp desde el botón del asistente.
  La atención puede tardar un poco.

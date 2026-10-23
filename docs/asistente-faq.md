# Preguntas frecuentes y guía de soporte (asistente Localis)

Documento curado para el asistente de IA. Explica, en lenguaje claro, cómo
funciona Localis para clientes y comerciantes, y cómo resolver los problemas
más comunes. Es la fuente principal de respuestas del asistente.

## ¿Qué es Localis?

Localis es un marketplace y catálogo de comercios locales de Venezuela (Isla de
Margarita). Los comerciantes publican su catálogo de productos con precios en
dólares y bolívares, y los clientes los encuentran, los comparan y contactan
directamente al comercio por WhatsApp. No hay carrito ni pago dentro de la app:
la compra y la entrega se acuerdan con cada comercio.

## Para clientes: buscar y contactar

- Se puede buscar por nombre de producto o navegar por categorías desde la
  portada. Los resultados muestran la tienda y el precio.
- En cada producto y en cada tienda hay un botón de WhatsApp que abre una
  conversación con el comercio con un mensaje ya escrito. El cliente cierra la
  compra directamente con el comercio.
- Si un comercio o un artículo tiene datos incorrectos, se puede reportar desde
  la app; el equipo lo revisa.

## Para comerciantes: crear la tienda

- El registro se hace iniciando sesión con Google y luego creando la tienda
  desde el panel del comerciante. Cada comercio tiene un enlace público propio
  (su "tienda") que se puede compartir.
- Desde el panel se edita el nombre, la descripción, el logo, las categorías y
  las sedes, y se gestionan los productos.

## Importar inventario (CSV o Excel)

- El panel permite subir un archivo CSV o Excel con el inventario. El sistema
  reconoce la cabecera de forma dinámica: no hace falta un formato exacto.
- Los productos se identifican por código de barras (EAN) o por nombre. El
  precio es opcional.
- La importación es asíncrona: la app responde al instante y procesa el archivo
  en segundo plano, mostrando el avance. Un archivo grande puede tardar.
- Qué pasa con los productos que ya no vienen en el archivo se controla con la
  configuración de bajas: por defecto se eliminan de la tienda (sincronización
  espejo); también se pueden solo desactivar u obviar.

## Por qué un producto no tiene foto

- El orden de prioridad de las imágenes es: la foto que sube el comerciante
  primero; si no hay, una foto real del catálogo maestro; y si tampoco hay, un
  marcador de posición según la categoría.
- Localis busca fotos reales de forma automática en segundo plano. Nunca
  inventa imágenes ni coloca logos falsos: si no encuentra una foto real, el
  producto queda con un marcador hasta conseguirla.
- Si un producto sigue sin foto, se puede subir una manualmente desde el panel.
  Esa foto siempre tiene prioridad.

## Planes y límites

- Localis trabaja con planes y un período de gracia de prueba (unos 30 días).
  Cada plan define un límite de productos publicables; algunos planes son
  ilimitados.
- El panel indica en todo momento el plan activo, los productos usados y el
  límite. Si se alcanza el límite, hay que cambiar de plan o liberar productos.
- La suscripción y los pagos se gestionan desde la sección Planes del panel.

## Pagos de planes (pago móvil y Binance)

- El pago se hace con pago móvil (los datos bancarios los muestra la app) o con
  Binance Pay en USDT.
- El comercio sube una captura del comprobante. La app la lee con OCR y verifica
  el monto y que la fecha sea reciente; además, guarda un hash para impedir que
  el mismo comprobante se use dos veces.
- Si la verificación automática no es concluyente, el pago queda "sin verificar"
  y pasa a revisión manual del equipo. Cuando se aprueba, el plan se activa.

## Sedes y ubicaciones

- Cada comercio declara sus sedes con un nombre propio y un enlace de Google
  Maps. No se escribe la dirección a mano: la ciudad y la zona se deducen del
  enlace automáticamente.
- La primera sede es la principal. La búsqueda por ubicación tiene en cuenta
  todas las sedes.

## Categorías de productos

- Las categorías son fijas y las define la plataforma. El sistema clasifica cada
  producto automáticamente; si no está seguro, lo deja en "otros". El
  comerciante puede revisar y ajustar la categoría de sus productos.

## Premios y puntos

- El programa de premios y referidos reparte puntos a los comercios. Los puntos
  y los canjes se consultan en la sección Premios del panel.

## Cómo reportar un comercio o un artículo

- Desde la ficha del comercio o del producto hay una opción de reporte. El
  reporte llega a la bandeja del equipo, que lo revisa y actúa.

## Soporte técnico: problemas comunes

### No puedo iniciar sesión

- El acceso es con la cuenta de Google. Si la sesión expiró, se vuelve a iniciar
  sesión. Si el problema continúa, hay que contactar al soporte con el correo
  usado.

### Mi importación quedó pendiente o no terminó

- Las importaciones se procesan en segundo plano. Si el archivo es grande, el
  avance puede tardar. Si tras un rato no avanza o queda en error, conviene
  revisar que el archivo tenga encabezados legibles y volver a intentar. Para
  casos concretos, el soporte puede revisar el trabajo de importación.

### Los cambios no se ven o la app muestra datos viejos

- La app instalada (Android) y el navegador usan caché. Se puede recargar la
  página. Los listados públicos tienen una caché muy corta, así que un cambio
  del comerciante se refleja en pocos segundos.

### Un producto aparece sin precio o sin categoría

- El precio es opcional al importar: si el archivo no traía precio, el producto
  queda sin precio y se puede completar a mano. La categoría se infiere
  automáticamente y se puede ajustar.

## Cuándo pasar con una persona

- El asistente es solo informativo: no puede revisar pagos, editar comercios ni
  consultar datos de una cuenta.
- Si la consulta necesita que alguien actúe (verificar un pago concreto, corregir
  datos de un comercio, resolver un error de importación específico o un problema
  de acceso), hay que escribir al soporte por WhatsApp desde el botón del
  asistente. La atención puede tardar un poco.

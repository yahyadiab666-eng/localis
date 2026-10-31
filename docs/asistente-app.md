# Guía real de la app (etiquetas exactas)

Documento curado con las **etiquetas y pantallas reales** de Localis, para que el
asistente guíe con los nombres exactos que el usuario ve en pantalla. Cuando el
asistente mencione un botón o sección, debe usar EXACTAMENTE estos nombres.

## Entrar (iniciar sesión o crear cuenta)

1. Arriba a la derecha hay un botón que dice **"Ingresar"**.
2. Al pulsarlo se abre la pantalla **"Bienvenido"**, con el texto *"Accede a tu
   cuenta o regístrate en un clic"*.
3. El botón para entrar es **"Continuar con Google"**.

No hay un formulario aparte ni un botón llamado "Registrarse" o "Insertar": el
mismo botón **"Continuar con Google"** sirve para entrar y para crear la cuenta
la primera vez.

## Menú del cliente (usuario normal)

Al pulsar el avatar (arriba a la derecha) aparece el menú con:

- **Inicio**
- **Registrar comercio** (si aún no tienes tienda) o **Ir a mi panel de comercio** (si ya la tienes)
- **Mi cuenta**
- **Cerrar sesión**

En la barra superior también aparece **"Cómo funciona"**.

## Panel del comerciante (menú superior)

Los enlaces del panel son: **Inicio · Panel · Premios · Banner · <!-- PLANES_INICIO -->Planes · <!-- PLANES_FIN -->Editar · Guía ·
Ver tienda · Mi perfil · Salir**. En el teléfono, el menú lateral añade
**"Alquilar banner"**, **"Editar tienda"**, **"Guía de uso"** y **"Soporte técnico"**.

## Banner en la portada (opcional, cualquier usuario)

- Se entra desde **"Banner"** en el panel o desde **"Alquilar banner"** en el
  menú del teléfono; también desde el perfil del usuario.
- Dura **30 días** y hay un máximo de **5 banners activos** a la vez en el
  carrusel de la portada.
- Precio: **60 USD con Pago Móvil** o **50 USD con Binance Pay**. La imagen debe
  medir **1200 × 400 px**.
- Se paga igual que el resto: se sube la **captura del comprobante** y el equipo
  lo revisa; el cobro es manual (OCR + revisión).

## Importar inventario

- En el **Panel**, la sección se llama **"Importador Inteligente (CSV / Excel)"**.
- Se sube un archivo CSV o Excel (la app reconoce la cabecera sola), y la
  importación se procesa en segundo plano con un indicador de avance.

<!-- PLANES_INICIO -->
## Planes y pago

- En **Planes** se ven los planes y se elige uno con el botón **"Elegir <plan>"**
  (o **"Cambiar plan"** / **"Programar cambio"** si ya tienes otro).
- Para pagar: **Pago Móvil** o **Binance Pay** (se eligen con las opciones del
  modal de pago).
- Tras pagar se sube la **captura del comprobante** y se pulsa
  **"Verificar comprobante y activar plan"**.
<!-- PLANES_FIN -->

## Ver la tienda pública y compartirla

- El botón **"Ver tienda"** abre la tienda pública (el catálogo que ven los
  clientes).
- Desde el panel se puede descargar el **código QR** de la tienda y compartir el
  enlace público.

## Buscar y contactar (para clientes)

- En la portada hay una **barra de búsqueda** y **chips de categorías**.
- En cada producto y en cada tienda hay un **botón de WhatsApp** para contactar
  directo al comercio.

## Beneficios para el CLIENTE (comprador)

- **Gratis** y sin intermediarios: ves el catálogo y contactas directo.
- **Precios en dólares y bolívares** (con la tasa del día) y comparación entre
  comercios.
- **Contacto directo por WhatsApp** con cada comercio para acordar la compra.
- **Apoyas el comercio local** de tu zona.

## Beneficios para el COMERCIANTE

- **Publicas tu catálogo** y apareces en el buscador y en el catálogo público.
- **Los clientes te contactan directo por WhatsApp** (sin comisiones de venta en
  la app).
<!-- PLANES_INICIO -->
- **Plan Gratis/Prueba**: 30 días gratis con hasta 50 productos; luego planes
  Básico (10 USD/100), Pro (15 USD/300) y Business (35 USD/ilimitados).
<!-- PLANES_FIN -->
- **Enlace público y código QR** para compartir tu tienda en redes, vitrina o
  volantes.
- **Premios y puntos** por referir otros comercios (canjeables por destacados).
- **Destacados** para aparecer primero (3 días 2 USD, 5 días 3 USD, 7 días 4 USD;
  con Binance Pay 1 USD menos: 1, 2 y 3 USD).

## Panel de administración (solo usuarios con rol admin)

Pantalla **"Localis | Panel Admin"**. Arriba hay **pestañas** (cada bloque del
panel vive en la suya, no en una sola página interminable):
**Resumen · Solicitudes · Pagos · Comercios · Banners · Ajustes**. En la
cabecera también están **Usuarios**, **Asistente IA**, **Ver Sitio Público** y
**Salir**.

- **Resumen**: cifras de un vistazo (solicitudes abiertas, pagos por revisar,
  comercios, banners ocupados), la lista "Qué requiere tu atención" con accesos
  directos y las métricas del **Asistente IA**.

- **Solicitudes de usuarios**: soporte, reporte de tienda y reporte de
  artículo. La pestaña solo muestra lo **abierto**: al aceptar o rechazar, la
  solicitud **se archiva** (se elimina de la bandeja) y la decisión queda en
  auditoría.
  - Filtros: **Todas · En proceso**, más el filtro por tipo
    (**Soporte · Tienda · Artículo · Pagos**).
  - Estados mientras está abierta: **Pendiente** y **En proceso**.
  - Acciones según el tipo: **Tomar (en proceso)**, **Aceptar y archivar**,
    **Rechazar y archivar**, **Aceptar y suspender la tienda** (solo reportes
    de tienda) y **Aceptar y ocultar el producto** (solo reportes de
    artículo). Se puede añadir una nota que queda en el registro.
- **Revisión de pagos**: alertas de pagos con Binance Pay que la API no pudo
  confirmar. Acciones: **Ver comercio**, **Confirmar pago**, **Suspender**,
  **Marcar como revisada** y **Rechazar**; muestran el ID de orden y el enlace
  al comprobante. Al cerrar la alerta también se archiva.
- **Salud de pagos (últimos 30 días)**: pagos registrados, confirmados por
  Binance, sin verificar, no encontrados y pendientes de revisión ahora.
- **Control de Comercios**: buscador (nombre, correo del dueño, RIF/Cédula o
  teléfono) y por comercio **Gestionar**, **Suspender**/**Reactivar**,
  **Eliminar**, **Transferir** o **Reasignar** dueño. En el teléfono la tabla se
  ve como tarjetas, una por comercio.
- **Usuarios registrados**: listado de perfiles con su rol (admin, comerciante,
  cliente) y sus comercios.
- **Asistente IA**: métricas del chat (consultas atendidas, respondidas, con
  error, latencia y 👍/👎). El chat se abre con la burbuja del asistente.
- **Configuración Económica** (tasa del dólar), **Banner principal de la
  portada** (la imagen que se ve cuando no hay banners alquilados) y **Nueva
  pre-cuenta** (tienda sin dueño, para armarla y transferirla después).
- **Banners de la portada**: el admin ve todos los banners (alquilados y
  propios), cuántos cupos quedan (máximo 5), y puede **pausar**, **activar**,
  **ampliar 30 días**, **corregir el enlace** o **eliminar** cada uno, además de
  **publicar un banner propio sin cobro**. La portada muestra **solo las
  imágenes**: no se añaden títulos ni textos encima.
- El asistente puede LEER las cifras del panel para responder, pero **no acepta
  ni rechaza solicitudes**: eso lo hace el administrador con los botones.
# GOAL

## Contexto

- **Goaffpro**: directorio de tiendas con programas de afiliados. Algunas tiendas requieren aprobación manual, otras dan "acceso instantáneo" (tilde en vez de cruz). Requiere cuenta/login (credenciales en `.env`).
- **Simplycodes**: directorio de códigos de descuento para tiendas online. Sin API pública — se opera vía navegador con sesión ya logueada.

## Decisiones tomadas

| Tema | Decisión |
|---|---|
| Motor de automatización | Windows UI Automation (`pywinauto`) sobre la ventana de Chrome real ya abierta — sin CDP, sin reiniciarla |
| Persistencia | SQLite local |
| Login Goaffpro | Credenciales propias en `.env` (`GOAFFPRO_EMAIL`, `GOAFFPRO_PASSWORD`), login automático |
| DeepSeek | Recurso opcional, usar solo si hace falta (ver `docs/models.md`) |
| Campos de formulario no previstos | Pausar y preguntar al usuario, no asumir |
| Rate-limit / captcha / bloqueo | Detener flujo, avisar, esperar intervención manual |

## Perfil fijo para formularios

- Nombre: Tomas
- Apellido: Rios
- Teléfono: 1134083120
- Email: tomyrios2006@gmail.com

## Flujo

1. **Goaffpro → filtrar candidatas**: entrar al listado de tiendas, quedarse con las que tienen "acceso instantáneo" real (✓, no ✗). Captura también currency/commission/cookie duration/registration status/affiliate portal de la misma card.
2. **Cruzar con Simplycodes**: por cada candidata se consulta `simplycodes.com/ajax/lookup.php?datatype=merchants&term=X` (el endpoint que usa el autocompletado del propio sitio), que devuelve `slug` + `url` (`/store/{dominio}`) de cada merchant. **La tienda se identifica por DOMINIO**, no por nombre: hay homónimos con slugs distintos (`FurEase` → `furease.co` y `furease.pet`; `pellepelle` → `pellepelle11` y `pellepelles`) y el slug no es derivable del nombre (`BJ's Wholesale Club` → `bjs`). Si el nombre completo no da resultados se reintenta con la marca (antes del primer `|`/`–`/`:`) y con la etiqueta del dominio. Después se abre `/editor/add/{slug}` y se **verifica que el encabezado diga la tienda esperada** — esa página responde 200 con cualquier slug, así que sin verificar un slug equivocado pasaba como válido. Estados: `ok`, `ineligible` ("not eligible for code sharing"), `not_found` (sale el selector de tiendas o abre otra tienda). El encabezado que no coincide **no corta la verificacion**: mientras la pagina no monto su documento, el unico texto legible es el de la ventana, que incluye el titulo de la pestana (`Add Promo Codes for Yazv - SimplyCodes - Google Chrome`) — decidir en esa primera lectura descartaba tiendas que si existian. Se sigue mirando hasta que el nombre coincida o se agote el tiempo, y el sufijo del titulo se recorta antes de comparar. Repetir hasta juntar `--batch-size` (default 10) persistidas por tanda.
3. **Enroll en Goaffpro**: por cada pendiente, click "Enroll". Si aparece el portal del merchant (`{tienda}.goaffpro.com/create-account`, template propio de Goaffpro), se completa el registro **emparejando cada input con su label** en el árbol de accesibilidad + el flag `IsPassword`. El orden de los campos NO es fijo: en inglés es Name/Email/Password y en español Email/Contraseña/Nombre (+ checkbox de términos y select de País), ambos confirmados en vivo. Si aparece un label que el perfil fijo no cubre se para y se pregunta (con fallback opcional a DeepSeek para interpretarlo, ver `field_map.py`).
4. **Leer el código de cupón y el descuento**: el código sale de `goaffpro.com/affiliate/stores` ("My Stores"), donde cada tienda tiene un panel con estructura fija: `Text '<tienda>'` → `Text 'Referral Link'` → `Edit '<url>'` → (opcional) `Text 'Coupon Code'` → `Edit '<CÓDIGO>'` → `Hyperlink 'Go to portal'`. Si el panel no tiene bloque "Coupon Code", el código todavía no existe → `pending_verification` (se reintenta en tandas siguientes). Este paso corre **siempre antes de cargar el cupón**, aunque la tienda ya estuviera `enrolled`: el código se persiste en la DB pero el descuento y la screenshot salen del dashboard, y las filas de corridas viejas los tienen vacíos — yendo directo a la carga, esas tiendas fallaban con "no sabemos el % de descuento" sin forma de recuperarse.

   El **descuento** no está en My Stores: está en el dashboard del merchant, como `Coupon Code 10% off`. Se llega ahí con el link "Go to portal" del panel, que es un `/login-as/{JWT}` — **entra ya logueado, sin password**, así que no hace falta guardar credenciales para releer el dashboard de una tienda ya afiliada. Ojo: el `Referral Link 20%` de al lado es la comisión del afiliado, no el descuento del cliente; por eso el número se ancla al label del cupón. El portal se sirve en el idioma del comercio, así que el label varía (`Coupon Code 10% off` / `Código promocional 10% de descuento`) — la regex contempla los dos. **Fallback**: si el merchant no publica el descuento, se asume que es igual a la comisión de afiliado. Es una suposición, no un dato leído, así que se registra con el nivel de log `fallback` (🔁, magenta) para poder filtrar después qué cupones se cargaron con un valor deducido. Si tampoco hay comisión, no se inventa nada y la tienda queda `coupon_failed`. La screenshot de prueba se saca de ese dashboard (muestra código + descuento, que es lo que SimplyCodes pide como evidencia).
5. **Cargar cupón**: en `simplycodes.com/editor/add/{slug}`, formulario de dos pasos.
   - **Paso 1**: campo del código + `Continue`. `Continue` se expone como **Text**, no como Button: no tiene patrón Invoke ni acepta foco, hay que clickearlo con el mouse y antes scrollearlo a pantalla (suele quedar abajo del viewport). Se verifica que la página haya cambiado; si no, se reintenta.
   - **Paso 2**: siete `<select>` nativos, dos obligatorios — `What's the discount?` → `% Off` (o `$ Off`) y `On what?` → `Store-wide deal` — más el valor del descuento y la screenshot. Chrome **no publica las opciones de un `<select>`** en el árbol de accesibilidad (`ComboBoxWrapper.select()` tira IndexError, `expand()` devuelve cero hijos), así que se recorren con flecha abajo leyendo el valor hasta que coincida. El input del porcentaje aparece recién al elegir la opción y no tiene label: es el primer `Edit` que sigue al select.
   - **Subida de la screenshot**: el rectángulo del `<input type=file>` incluye el texto "No file chosen" — hay que clickear el tramo izquierdo (el botón). El diálogo nativo se busca por handle con `EnumWindows` porque `Desktop().windows()` de pywinauto no devuelve ventanas *owned*. Dentro del diálogo, el campo de nombre **no es el primer `Edit`**: hay un `Edit` por cada archivo listado (la etiqueta editable de cada ítem) más el cuadro de búsqueda; se identifica por su nombre accesible terminado en dos puntos (`Nombre:` / `File name:`). Se confirma con Enter (el botón "Abrir" matchea 3 controles y es ambiguo) y se verifica que el control de la página pase a mostrar el nombre del archivo.
   - **Paso 3**: muestra el título generado (`10% Off (Storewide) at {tienda}`) y el botón `Finished`, que sí es un Button real. Se verifica que la página cambie después del click; si no cambia, no se marca `coupon_submitted`.
   - Mientras hay un diálogo nativo abierto, Chrome queda deshabilitado **y expone una ventana auxiliar sin título**; el driver la ignora (elige la ventana con título y más grande) y cierra diálogos colgados antes de cada `goto`.
   - Si falta el descuento, no hay screenshot, o algún paso no avanza, se aborta con error explícito: nunca se envía un cupón a medias.
6. **Manejo de bloqueos**: si en cualquier punto aparece rate-limiting, captcha o bloqueo externo → detener, avisar al usuario, esperar que continúe manualmente antes de retomar.
7. **Loop y corte**: al terminar una tanda de `--batch-size` tiendas, sigue buscando la siguiente tanda en loop. El usuario corta apretando **ESC** en la consola (o se pasa `--max-batches N` o `--stop-after N`). Al cortar, exporta `export.csv`.

## Exportación CSV

Al final de la sesión (ESC o `--max-batches` agotado) se genera `export.csv` con TODAS las tiendas persistidas hasta ese momento, columnas fijas (ver `db.CSV_COLUMNS`):

`STORE_NAME, STORE_DOMAIN, AFFILIATE_PORTAL, AFFILIATE_PORTAL_SIGNUP, REGISTRATIONS_OPENS, APPROVED_AUTOMATICALLY, COOKIE_DURATION, CURRENCY, COMISSION_TYPE, COMISSION_AMOUNT, COMISSION_ON, STORE_LINK_SIMPLY, STORE_NAME_SIMPLY, EDITOR_ADD_SIMPLY, POPULARITY, NEEDING_CODES, BADGE_AVAILABLE`

Mapeo de origen (confirmado en vivo contra los sitios reales, no inventado):
- `COMISSION_TYPE`/`COMISSION_AMOUNT` = tipo/valor del descuento del cupón (dashboard del merchant en Goaffpro, ej. "percent"/"10").
- `COMISSION_ON` = comisión de afiliado que muestra la card de Goaffpro (ej. "15%") — **distinto** de COMISSION_TYPE/AMOUNT (eso es el descuento al cliente, esto es lo que cobra el afiliado).
- `POPULARITY` — **queda vacío.** "Current coin rate" NO es un dato de la tienda: es una estadística de la cuenta del editor, aparece idéntica en la barra superior de todas las páginas de `/editor` (incluso en la que no tiene ninguna tienda cargada). Confirmado en vivo. Antes se guardaba igual, así que todas las filas salían con el mismo valor.
- `BADGE_AVAILABLE` = nombre del badge que se puede ganar en `/editor/add/{slug}` (ej. "Pioneer", "Hunter"). El **tier** (gold/silver/bronze) está solo en un tooltip CSS `:hover` que Chrome no publica en el árbol de accesibilidad ni con el mouse encima — no es alcanzable por UI Automation. Como el orden del CSV usaba el tier, esa parte del orden queda inactiva (antes también: la regex nunca matcheaba y el badge era siempre vacío).
- `NEEDING_CODES` — **sin fuente confirmada, queda vacío** (no se encontró en ningún lado del sitio real; si aparece después se agrega).
- `AFFILIATE_PORTAL`/`AFFILIATE_PORTAL_SIGNUP` = dominio / URL completa del link "View program" de la card de Goaffpro (leído del atributo de accesibilidad, sin necesidad de clickear).

Orden de las filas: mayor comisión de afiliado primero, después mejor badge (Gold > Silver > Bronze), después mejor popularity (High > Medium > Low).

## Chequeos

- `python test_parsers.py` — parsers de My Stores, matcheo de tienda en SimplyCodes, estados de `/editor/add`, escapado de teclas. Sin navegador.
- `python field_map.py` — tabla de sinónimos de labels de formulario.

## Estado

Flujo validado en vivo end-to-end con 1 tienda real (FurEase, badge Silver, código TOMASRIOS, cupón cargado y recibido por Simplycodes) usando el motor viejo (Playwright/CDP). Reescrito a `pywinauto`/UI Automation (ver decisión arriba) para no depender de reiniciar Chrome ni de herramientas de Claude. Estructura de las cards de Goaffpro y de las páginas de SimplyCodes usadas (`/editor/add/{slug}`, `/editor/merchant-hub/{slug}`) confirmada en vivo contra el sitio real. Pendiente: correr `enroll`/`add_coupon` de punta a punta con el driver nuevo (son acciones reales, no se probaron solas). Script: `main.py` + `goaffpro.py` + `simplycodes.py` + `winchrome.py` + `db.py`.

## Cómo correr el script

1. `pip install -r requirements.txt`
2. Chrome abierto normal, con `simplycodes.com` logueado a mano (Goaffpro lo loguea el script solo vía `.env`). No hace falta ningún flag ni reiniciar nada.
3. `python main.py` (o `python main.py --batch-size N`) — el script encuentra la ventana de Chrome ya abierta y opera ahí directo (Windows UI Automation). Corre en loop por tandas hasta que apretás **ESC** en la consola; al cortar exporta `export.csv`.

## Limitaciones conocidas

- El motor (`winchrome.py`, UI Automation) depende del árbol de accesibilidad que expone Chrome para cada página — más frágil que CDP. Si un sitio cambia de layout, lo que rompe es el selector puntual en `goaffpro.py`/`simplycodes.py`, no el driver.
- El registro en el portal del merchant se llena por label. Si un portal usa un label que ni la tabla de sinónimos de `field_map.py` ni DeepSeek reconocen, el flujo **para y pregunta** (no inventa datos). El select de País se deja en su valor por defecto.
- El descuento se detecta con regex sobre el dashboard del merchant (`Coupon Code N% off` / `Coupon Code $N off`). Si un portal lo escribe distinto, la tienda queda `coupon_failed` con el motivo explícito — no se envía un cupón con un descuento inventado.
- Todos los cupones se cargan como `Store-wide deal`. Las otras opciones de "On what?" (ítems, marcas, categorías) requieren datos que Goaffpro no expone.
- `NEEDING_CODES` y `POPULARITY` quedan siempre vacíos en el CSV — no se encontró fuente real para ninguno (ver mapeo arriba).
- El tier del badge (gold/silver/bronze) no es alcanzable por UI Automation; se guarda el nombre del badge.
- El buscador de SimplyCodes matchea por nombre de marca, no por dominio: una tienda cuyo nombre en Goaffpro no se parezca a su nombre en SimplyCodes puede no encontrarse aunque exista. Se prueban hasta 4 variantes del nombre antes de descartarla.
- Cloudflare / captchas pausan el script (`blockers.pause`) y esperan Enter en consola después de resolverlos a mano en la ventana de Chrome.
- El portal externo del merchant a veces abre en pestaña nueva de la misma ventana de Chrome; el driver sigue operando sobre la ventana top-level asumiendo que Chrome le da foco a la pestaña nueva solo (comportamiento normal). Si eso falla en un merchant puntual, revisar `goaffpro._handle_merchant_portal`.

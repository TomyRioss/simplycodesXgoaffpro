import re
from pathlib import Path
from urllib.parse import urlsplit

from blockers import page_is_blocked, pause


class NeedsVerification(Exception):
    """El portal del merchant pide verificación de mail, o Goaffpro todavía
    no generó el código de cupón — no es un error del script, hay que
    saltear esta tienda y seguir con la próxima."""


from log import log
from field_map import guess_field, match_field
from profile_data import (
    GOAFFPRO_EMAIL,
    GOAFFPRO_PASSWORD,
    MANUAL_SCREENSHOTS,
    PAYPAL_EMAIL,
    PROFILE,
)
from winchrome import escape_keys

# Wording alternativo de los mismos botones. Goaffpro y los portales de los
# merchants se sirven en varios idiomas y cambian el texto de los botones
# entre versiones — buscar un único string exacto era lo que hacía que el
# flujo diera "no existe" ante un cambio cosmético.
LOGIN_LABELS = ["Login", "Log in", "Sign in", "Iniciar sesión", "Ingresar"]
CREATE_LABELS = ["Create Account", "Crear una cuenta", "Crear cuenta", "Sign up", "Registrarse", "Register"]

# Portal de afiliado -> pestaña de pagos. El portal se sirve en el idioma
# del comercio (las capturas del cliente son en francés), así que cada
# label se busca en varios idiomas, igual que el resto del flujo.
PAYMENT_SETTINGS_LABELS = [
    "Paramètres", "Parametres", "Settings", "Configuración", "Configuracion",
    "Ajustes", "Einstellungen", "Impostazioni",
]
PAYMENT_MODE_LABELS = (
    "mode de paiement", "payment method", "payment mode", "método de pago",
    "metodo de pago", "forma de pago", "zahlungsmethode", "metodo di pagamento",
)
PAYPAL_EMAIL_LABELS = (
    "adresse e-mail paypal", "e-mail paypal", "email paypal", "paypal email",
    "paypal e-mail", "correo paypal", "correo electrónico de paypal",
)
PAYMENT_SUBMIT_LABELS = ["Soumettre", "Submit", "Enviar", "Guardar", "Save", "Absenden", "Invia"]


# --------------------------------------------------------------------------
# login
# --------------------------------------------------------------------------


def _is_logged_in(driver) -> bool:
    """Logueado = estamos bajo /affiliate y no hay ningún campo de password
    en pantalla. Mirar la URL sola no alcanza (la pantalla de login vive en
    goaffpro.com igual); mirar solo el form tampoco (tarda en montar)."""
    if "/affiliate" not in driver.current_url():
        return False
    return not any(driver.is_password(el) for _, el in driver.form_fields())


def login(driver):
    """Loguea en Goaffpro. Si la sesión ya está viva no toca nada."""
    log("goaffpro.login: chequeando si la sesión ya está abierta")
    driver.goto("https://goaffpro.com/affiliate")
    driver.wait_for_timeout(2500)
    if _is_logged_in(driver):
        log("goaffpro.login: ya había sesión abierta, no hace falta loguear")
        return

    log("goaffpro.login: navegando a https://goaffpro.com/login")
    driver.goto("https://goaffpro.com/login")
    driver.wait_for_timeout(1500)

    if reason := page_is_blocked(driver):
        log(f"goaffpro.login: BLOQUEADO antes de llenar el form — {reason}")
        pause(f"Goaffpro login: {reason}")

    _fill_credentials(driver, GOAFFPRO_EMAIL, GOAFFPRO_PASSWORD, "Goaffpro login")

    if reason := page_is_blocked(driver):
        log(f"goaffpro.login: BLOQUEADO después de llenar el form — {reason}")
        pause(f"Goaffpro login: {reason}")

    _submit_and_wait(driver, LOGIN_LABELS, "Goaffpro login")

    if driver.exists("Choose your dashboard"):
        log("goaffpro.login: pantalla 'Choose your dashboard' detectada, eligiendo 'I am an affiliate'")
        driver.click(text="I am an affiliate", exact=False)
        driver.wait_for_timeout(500)
        if driver.exists("Continue", exact=False):
            driver.click(text="Continue", exact=False)

    log("goaffpro.login: esperando URL **/affiliate**")
    driver.wait_for_url_contains("/affiliate", timeout=30)
    log("goaffpro.login: OK, logueado")


def _fill_credentials(driver, email: str, password: str, ctx: str):
    """Llena email + password ubicando los campos por su label y por el flag
    IsPassword, no por posición. El orden de los campos NO es estable: el
    portal en inglés es Name/Email/Password y el mismo portal en español es
    Email/Contraseña/Nombre (confirmado en vivo) — llenar por índice fijo
    cargaba el mail en el campo de nombre y la password en el de mail."""
    fields = driver.form_fields()
    pw_el = next((el for _, el in fields if driver.is_password(el)), None)
    email_el = next(
        (el for label, el in fields if not driver.is_password(el) and match_field(label) == "email"), None
    )
    if email_el is None:
        # sin label reconocible: el campo de mail es el Edit de texto que
        # está inmediatamente antes del de password (o el primero si el de
        # password va primero, como en los portales en español).
        text_els = [el for _, el in fields if not driver.is_password(el)]
        email_el = text_els[0] if text_els else None

    if email_el is None or pw_el is None:
        pause(
            f"{ctx}: no encontré el formulario de login en la página "
            f"({len(fields)} campos detectados). Logueate a mano en la ventana de Chrome "
            "y presioná Enter para que el script siga."
        )
        return

    log("goaffpro: llenando email/password (campos ubicados por label + IsPassword)")
    driver.set_value(email_el, email)
    driver.set_value(pw_el, password)


def _wait_for_turnstile(driver, labels, ctx: str) -> bool:
    """Espera a que el botón de submit se habilite (Cloudflare Turnstile
    verifica solo y recién ahí Goaffpro lo habilita). Devuelve True si se
    habilitó."""
    log(f"goaffpro: esperando a que el botón se habilite (Turnstile verificando) — {ctx}")
    for i in range(30):
        try:
            btn = driver.find_any(labels, control_type="Button", timeout=1)
        except Exception:
            driver.wait_for_timeout(500)
            continue
        if btn.is_enabled():
            log(f"goaffpro: botón habilitado después de {i * 0.5:.1f}s")
            return True
        driver.wait_for_timeout(500)
    return False


def _submit_and_wait(driver, labels, ctx: str):
    if not _wait_for_turnstile(driver, labels, ctx):
        log(f"goaffpro: el botón sigue deshabilitado después de 15s — {ctx}", level="warn")
        pause(f"{ctx}: el botón de submit no se habilitó (Turnstile). Resolvelo a mano y presioná Enter.")

    try:
        btn = driver.find_any(labels, control_type="Button", timeout=5)
    except Exception:
        log(f"goaffpro: el botón ya no está en pantalla — asumo que se envió a mano durante la pausa ({ctx})")
        return
    log(f"goaffpro: click en submit ({btn.window_text()!r})")
    driver.activate(btn)
    driver.wait_for_timeout(1500)


# --------------------------------------------------------------------------
# descubrimiento de candidatas (Available Stores)
# --------------------------------------------------------------------------

_LABELS = {"Currency", "Commission", "Cookie Duration"}


def _parse_cards(driver):
    """Parsea las cards de /affiliate/stores/search en orden de documento.
    Estructura confirmada en vivo (todas las cards la respetan):
    Nombre, Dominio(link), "Currency", valor, "Commission", valor,
    "Cookie Duration", valor, "Registration Open"(label fijo + ícono ✓/✗),
    "Instant Access"(label fijo + ícono ✓/✗), "Store ID:", valor,
    "View program"(link), "Enroll"(button).

    ponytail: "Instant Access"/"Registration Open" son labels de TEXTO FIJO
    — el valor real (✓/✗) es un ícono sin alt text ni Value por
    accesibilidad, así que se clasifica por color de pixel más adelante
    (ver winchrome.icon_is_green). Acá solo guardamos la referencia al
    elemento de texto para poder ubicar el ícono."""
    items = driver.ordered(control_types=("Text", "Hyperlink", "Button"))
    cards = []
    boundary = 0
    for i, (ct, name, el) in enumerate(items):
        if ct != "Text" or name != "Store ID:" or i + 1 >= len(items):
            continue
        window = items[boundary:i]
        # nombre/dominio: los dos elementos INMEDIATAMENTE antes de "Currency"
        # — no los primeros del window entero. La primera card de cada
        # página arrastra navbar/banner en boundary=0 (recién se resetea
        # después del primer 'Enroll'), así que "primer texto/link del
        # window" ahí agarra basura ("Introducing ClickSDK", "Learn more")
        # en vez del nombre real de la tienda.
        currency_idx = next((j for j, w in enumerate(window) if w[1] == "Currency"), len(window))
        pre = window[:currency_idx]
        texts = [w for w in pre if w[0] == "Text"]
        hyperlinks = [w for w in pre if w[0] == "Hyperlink"]
        card = {
            "store_id": items[i + 1][1],
            "name": texts[-1][1] if texts else "",
            "domain": hyperlinks[-1][1] if hyperlinks else "",
            "instant_access_el": next((e for _, n, e in window if n == "Instant Access"), None),
            "registration_el": next((e for _, n, e in window if n.lower().startswith("registration")), None),
        }
        for j, (ct2, name2, el2) in enumerate(window):
            if name2 in _LABELS and j + 1 < len(window):
                card[name2] = window[j + 1][1]
        card["currency"] = card.pop("Currency", "")
        card["commission"] = card.pop("Commission", "")
        card["cookie_duration"] = card.pop("Cookie Duration", "")
        if i + 2 < len(items) and items[i + 2][0] == "Hyperlink":
            card["view_program_href"] = driver.href(items[i + 2][2])
        if i + 3 < len(items) and items[i + 3][0] == "Button" and items[i + 3][1] == "Enroll":
            card["enroll_el"] = items[i + 3][2]
            boundary = i + 4
        cards.append(card)
    return cards


def _goto_page(driver, target_page: int):
    """Asume que está en la página 1 de /affiliate/stores/search y clickea
    'Next' hasta llegar a target_page (Goaffpro pagina client-side, sin
    query param en la URL — no hay forma de saltar directo)."""
    for _ in range(target_page - 1):
        next_btn = driver.find(text="Next", control_type="Button")
        if not next_btn.is_enabled():
            log("goaffpro.discover: 'Next' deshabilitado antes de llegar a la página guardada, quedo acá")
            break
        driver.activate(next_btn)
        driver.wait_for_timeout(700)


def _set_per_page_100(driver):
    """Pone el listado en 100 por pagina.

    El combo es estado de React y no queda guardado entre navegaciones:
    cada goto() lo resetea a 10, por eso se re-aplica en cada ronda.

    Antes se lo ubicaba como "el ultimo de al menos 3 ComboBox" y se lo
    manejaba a ciegas con tres flechas abajo. En la pagina hay 2, no 3, asi
    que la guarda cortaba siempre y el listado se quedaba en 10 por pagina:
    con 10 por pagina hacen falta 6 clicks en 'Next' para volver a la
    pagina 7 en CADA ronda. Ahora se lo ubica por su valor ('10 per page')
    y se elige la opcion verificando que haya quedado puesta."""
    combo = next(
        (c for c in driver.window.descendants(control_type="ComboBox")
         if re.match(r"^\d+\s+per page$", driver.value(c).strip(), re.IGNORECASE)),
        None,
    )
    if combo is None:
        log("goaffpro.discover: no encontre el combo de 'per page', sigo con el default", level="warn")
        return
    if driver.select_option(combo, "100 per page"):
        log("goaffpro.discover: listado en 100 por pagina")
        driver.wait_for_timeout(1500)
    else:
        log("goaffpro.discover: no pude poner 100 por pagina, sigo con lo que haya", level="warn")


def _open_page(driver, page: int):
    """Navega a /affiliate/stores/search y avanza hasta `page`. Se llama de
    nuevo en CADA ronda (no solo al arrancar): entre cada tienda que
    yieldeamos, el caller navega a SimplyCodes con el mismo `driver` para
    cruzarla — cuando el generador retoma, ya no está en Goaffpro. Volver
    a abrir la página es la forma simple de no asumir dónde quedó el
    navegador."""
    driver.goto("https://goaffpro.com/affiliate/stores/search")
    driver.click(text="Available Stores", control_type="Hyperlink")
    driver.wait_for_timeout(800)
    if reason := page_is_blocked(driver):
        log(f"goaffpro.discover: BLOQUEADO — {reason}")
        pause(f"Goaffpro Available Stores: {reason}")
    _set_per_page_100(driver)
    if page > 1:
        _goto_page(driver, page)
    # el scroll de la lista de cards NO se resetea solo entre páginas (es
    # SPA, no recarga completa) — sin esto, la página siguiente hereda el
    # scroll-al-fondo que dejó _classify_icons en la anterior y ya no
    # puede subir a ver las cards de arriba (solo scrollea hacia abajo).
    for _ in range(15):
        driver.wheel_scroll(30)
    driver.wait_for_timeout(300)


def _classify_icons(driver, cards, max_scrolls: int = 40, stop_event=None):
    """Clasifica instant_access/registration de cada card por color de
    ícono. Con 10/página todas las cards entran en pantalla y una sola
    captura alcanza; con 100/página la mayoría queda fuera del viewport
    — un elemento fuera de vista no aparece en la captura (confirmado en
    vivo: sus coordenadas ni siquiera se mueven con Page Down, solo con
    la rueda del mouse). Scrollea de a pasos, clasificando lo que va
    entrando en pantalla, hasta que todas las cards quedan resueltas."""
    pending = [c for c in cards if c["instant_access_el"] is not None]
    for c in cards:
        c["approved"] = False
        c["reg_open"] = None

    for _ in range(max_scrolls):
        if not pending:
            break
        if stop_event is not None and stop_event.is_set():
            log("goaffpro.discover: ESC detectado, corto la clasificación de íconos")
            break
        snap = driver.snapshot()
        icon_rects = driver.icon_rects()
        still_pending = []
        for card in pending:
            approved = driver.icon_is_green(card["instant_access_el"], snap, icon_rects)
            if approved is None:
                still_pending.append(card)
                continue
            card["approved"] = approved
            card["reg_open"] = (
                driver.icon_is_green(card["registration_el"], snap, icon_rects) if card["registration_el"] else None
            )
        pending = still_pending
        if pending:
            driver.wheel_scroll(-10)
            driver.wait_for_timeout(250)


def iter_instant_access_candidates(driver, conn, stop_event=None):
    """Yields dicts con store_id/name/domain/currency/commission/cookie_duration/
    registrations_opens/approved_automatically/affiliate_portal(_signup) para
    cada card de 'Available Stores' con Instant Access. Goaffpro pagina
    (no es scroll infinito) — recorre de a una página desde la 1.

    NO se guarda la última página vista para retomar: 'Available Stores'
    NO es append-only — las tiendas nuevas aparecen en la página 1, no al
    final, así que retomar en la página 8 se saltea para siempre las
    tiendas nuevas de las páginas 1-7. Rescanear desde la 1 es barato:
    already_seen() saltea toda tienda ya persistida con un lookup de
    SQLite y el caller corta apenas junta `count` candidatas nuevas —
    como las nuevas están adelante, en la práctica frena en 1-2 páginas.

    ponytail: reabre la página de Goaffpro desde cero en cada ronda
    (goto + N clicks en 'Next') en vez de asumir que el navegador se
    quedó ahí — O(n²) clicks para llegar a la página n, aceptable para
    decenas de páginas por tanda."""
    page = 1

    while True:
        if stop_event is not None and stop_event.is_set():
            log("goaffpro.discover: ESC detectado, corto (aunque esta página no tuviera candidatas)")
            break
        log(f"goaffpro.discover: abriendo página {page} de Goaffpro")
        _open_page(driver, page)
        driver.wait_for_timeout(1500)

        cards = _parse_cards(driver)
        log(f"goaffpro.discover: página {page} — {len(cards)} cards")
        _classify_icons(driver, cards, stop_event=stop_event)

        try:
            has_next = driver.find(text="Next", control_type="Button", timeout=3).is_enabled()
        except Exception:
            has_next = False

        for card in cards:
            if stop_event is not None and stop_event.is_set():
                log("goaffpro.discover: ESC detectado, corto sin yield más candidatas")
                return
            if not card["approved"]:
                log(f"goaffpro.discover: '{card['name'] or card['store_id']}' SIN instant access, descartada")
                continue

            registrations_opens = "Open" if card["reg_open"] else ("Closed" if card["reg_open"] is False else "")

            href = card.get("view_program_href", "")
            affiliate_portal = re.sub(r"^https?://", "", href).split("/")[0] if href else ""

            log(f"goaffpro.discover: '{card['name']}' ({card['domain']}) tiene Instant Access — candidata", level="candidate")
            yield {
                "store_id": card["store_id"],
                "goaffpro_page": page,
                "name": card["name"],
                "domain": card["domain"],
                "currency": card["currency"],
                "goaffpro_commission": card["commission"],
                "cookie_duration": card["cookie_duration"],
                "registrations_opens": registrations_opens,
                "approved_automatically": "yes",
                "affiliate_portal": affiliate_portal,
                "affiliate_portal_signup": href,
            }

        if not has_next:
            log("goaffpro.discover: llegué al final de la lista de Goaffpro")
            break
        page += 1


# --------------------------------------------------------------------------
# enroll
# --------------------------------------------------------------------------


def enroll(driver, store: dict):
    """Se afilia a la tienda. NO lee el código de cupón: el merchant lo
    genera varios minutos después del Enroll, así que leerlo acá daría
    NeedsVerification casi siempre. El código lo lee read_coupon_code() en
    una pasada posterior (ver main.enroll_and_submit)."""
    store_id = store["goaffpro_store_id"]
    page = int(store.get("goaffpro_page") or 1)
    log(f"goaffpro.enroll: navegando a página {page} de /affiliate/stores/search para '{store['name']}' (store_id={store_id})")
    _open_page(driver, page)

    cards = _parse_cards(driver)
    card = next((c for c in cards if c["store_id"] == str(store_id)), None)
    if card is None or "enroll_el" not in card:
        raise RuntimeError(f"no encontré la card de '{store['name']}' (store_id={store_id}) para hacer Enroll")

    log("goaffpro.enroll: click en Enroll")
    driver.activate(card["enroll_el"])
    driver.wait_for_timeout(2000)

    if driver.exists_any(["Submit information", "Enviar información"]):
        log("goaffpro.enroll: apareció modal 'Submit information', click")
        driver.activate(driver.find_any(["Submit information", "Enviar información"]))
        driver.wait_for_timeout(2000)
        _handle_merchant_portal(driver, store)
    else:
        log(f"goaffpro.enroll: '{store['name']}' sin portal externo, el código sale de 'My Stores'")


def _dismiss_save_password_popup(driver):
    """El popup nativo de Chrome '¿Guardar contraseña?' aparece apenas se
    envía un form de password (Create Account) y tapa la ventana — si el
    código sigue de largo mientras está arriba puede leer contenido a medio
    cargar. Se descarta antes de seguir."""
    if driver.exists_any(["Save password", "Guardar contraseña"]):
        log("goaffpro.enroll: descartando popup 'Guardar contraseña' de Chrome")
        for label in ("No thanks", "Never", "Ahora no", "Nunca"):
            if driver.exists(label, exact=False):
                driver.click(text=label, exact=False)
                break
        driver.wait_for_timeout(500)


def _portal_has_signup_form(driver) -> tuple[bool, list]:
    """El portal muestra un form de registro si hay un campo de password Y
    un botón cuyo texto sea alguno de CREATE_LABELS.

    Antes esto se decidía con driver.exists("Create Account") a secas: el
    mismo portal en español dice "Crear una cuenta", así que el script daba
    por hecho que no había form, no creaba la cuenta, y seguía de largo a
    leer un "dashboard" que en realidad era la pantalla de registro
    (los screenshots guardados como *_dashboard.png lo muestran)."""
    fields = driver.form_fields()
    has_password = any(driver.is_password(el) for _, el in fields)
    has_button = driver.exists_any(CREATE_LABELS, control_type="Button")
    return (has_password and has_button), fields


def _choose_option(driver, combo, value: str) -> bool:
    """Elige `value` en un <select> nativo, devuelve si quedó elegido.

    Primero por typeahead con la PRIMERA palabra ('Argentina', 'Buenos'):
    las listas de país tienen 200+ opciones y recorrerlas con flecha abajo
    leyendo el valor en cada paso (lo que hace driver.select_option) tarda
    minutos. El espacio no se tipea porque en un <select> despliega la lista
    en vez de seguir buscando.

    Si el typeahead no acierta, se cae al recorrido secuencial de winchrome,
    que sí verifica opción por opción — sirve para selects cortos tipo
    provincia."""
    want = value.strip()
    if not want:
        return False
    first = want.split()[0]
    try:
        combo.set_focus()
        combo.type_keys(escape_keys(first))
        driver.wait_for_timeout(400)
    except Exception:
        return False
    current = driver.value(combo).strip().lower()
    if current == want.lower() or (current and current.startswith(first.lower())):
        return True
    return driver.select_option(combo, want, max_options=60)


def _fill_signup_form(driver, fields, store: dict, password: str):
    """Llena el form de registro emparejando cada input con su label.

    Si aparece un campo requerido que el perfil fijo no cubre, se para y se
    pregunta (regla del proyecto: no inventar datos)."""
    values = {
        "name": PROFILE["full_name"],
        "first_name": PROFILE["first_name"],
        "last_name": PROFILE["last_name"],
        "email": PROFILE["email"],
        "phone": PROFILE["phone"],
        "password": password,
        "country": PROFILE["country"],
        "state": PROFILE["state"],
        "city": PROFILE["city"],
    }
    unknown = []

    for label, el in fields:
        ct = el.element_info.control_type

        if ct == "CheckBox":
            # "I agree to the terms and conditions" es requisito en varios
            # portales; sin tildarlo el submit no se habilita nunca y la
            # tienda se perdía sin explicación. Un checkbox NO se tilda con
            # invoke()/ENTER (lo que hace driver.activate) — necesita
            # TogglePattern, SPACE o click real; driver.toggle_on prueba los
            # tres y verifica.
            if driver.toggle_on(el):
                log(f"goaffpro.enroll: checkbox {label!r} tildado")
            else:
                log(f"goaffpro.enroll: no pude tildar el checkbox {label!r}", level="warn")
            continue

        if ct == "ComboBox":
            # el select de País/Provincia ahora sí se elige: el dato está en
            # el perfil del launcher. Si no se puede, queda el default (que
            # es válido) y se avisa — nunca frena la tienda por esto.
            field = match_field(label)
            if field and values.get(field):
                if _choose_option(driver, el, values[field]):
                    log(f"  {label!r} -> {field} = {values[field]}")
                else:
                    log(
                        f"goaffpro.enroll: no pude elegir {values[field]!r} en el select {label!r}, "
                        "queda el valor por defecto",
                        level="warn",
                    )
            else:
                log(f"goaffpro.enroll: dejo el select {label!r} con su valor por defecto")
            continue

        if driver.is_password(el):
            driver.set_value(el, password)
            log("  password: misma que la cuenta Goaffpro")
            continue

        field = match_field(label) or guess_field(label)
        if field is None:
            unknown.append((label, el))
            continue
        driver.set_value(el, values[field])
        shown = "***" if field == "password" else values[field]
        log(f"  {label!r} -> {field} = {shown}")

    if unknown:
        detalle = ", ".join(repr(lbl) for lbl, _ in unknown)
        pause(
            f"{store['name']}: el portal pide campo(s) que el perfil fijo no cubre: {detalle}. "
            "Completalos a mano en la ventana de Chrome (sin enviar el form) y presioná Enter."
        )


def _handle_merchant_portal(driver, store: dict):
    """El portal del merchant se abre en una pestaña nueva de la misma
    ventana de Chrome; como no manejamos pestañas por CDP, asumimos que
    Chrome le da foco solo (comportamiento normal) y seguimos operando
    sobre `driver`, que sigue apuntando a la ventana top-level."""
    driver.wait_for_timeout(1500)
    if reason := page_is_blocked(driver):
        log(f"goaffpro.enroll: BLOQUEADO en portal del merchant — {reason}")
        pause(f"{store['name']}: portal del merchant — {reason}")

    has_form, fields = _portal_has_signup_form(driver)
    if not has_form:
        log(f"goaffpro.enroll: '{store['name']}' no muestra form de registro (ya logueado o no lo requiere)")
        return

    log(f"goaffpro.enroll: '{store['name']}' pide crear cuenta, llenando por label")
    # La cuenta del portal del merchant usa la MISMA password que la cuenta
    # Goaffpro (pedido del cliente): así el cliente entra a cualquier portal
    # con la credencial que ya conoce. Antes se generaba una random por
    # tienda y quedaba solo en la DB.
    password = GOAFFPRO_PASSWORD
    create_url = driver.current_url()
    _fill_signup_form(driver, fields, store, password)

    if reason := page_is_blocked(driver):
        log(f"goaffpro.enroll: BLOQUEADO al crear cuenta — {reason}")
        pause(f"{store['name']}: creación de cuenta en portal del merchant — {reason}")

    _submit_and_wait(driver, CREATE_LABELS, f"{store['name']}: Create Account")
    _dismiss_save_password_popup(driver)

    if driver.exists_any(["You already have an account", "Ya tenés una cuenta", "Ya tiene una cuenta"]):
        log(
            f"goaffpro.enroll: '{store['name']}' ya tiene cuenta en este portal (intento previo) — "
            "no tenemos la password guardada",
            level="warn",
        )
        if driver.exists_any(["Click here to login", "Iniciar sesión"]):
            driver.activate(driver.find_any(["Click here to login", "Iniciar sesión"]))
        pause(
            f"{store['name']}: el portal dice que ya existe una cuenta con {PROFILE['email']} pero no "
            "tenemos la password guardada. Logueate a mano en Chrome y presioná Enter para seguir."
        )
        return

    for _ in range(24):
        driver.wait_for_timeout(500)
        if driver.current_url() != create_url:
            break
    else:
        log("goaffpro.enroll: la URL no cambió después de crear cuenta, sigo igual", level="warn")

    store["merchant_email"] = PROFILE["email"]
    store["merchant_password"] = password
    log("goaffpro.enroll: cuenta creada")


# --------------------------------------------------------------------------
# lectura del código de cupón
# --------------------------------------------------------------------------


def read_coupon_code(driver, store: dict):
    """Lee el código de cupón real desde 'My Stores' de Goaffpro y lo deja
    en store['affiliate_code'], además de la screenshot de prueba.

    /affiliate/stores expone, por cada tienda afiliada, un panel con esta
    estructura fija en el árbol de accesibilidad (confirmada en vivo):

        Text '<Nombre de la tienda>'
        Text 'Referral Link'   -> Edit 'https://.../?ref=xxxx'
        Text 'Coupon Code'     -> Edit 'TOMASRIOS'        (opcional)
        Hyperlink 'Go to portal'

    Leerlo de ahí reemplaza a la regex `\\b[A-Z0-9]{5,}\\b` sobre el texto
    del portal del merchant, que agarraba cualquier palabra en mayúsculas
    de la página: en las corridas anteriores devolvió 'CLOUDFLARE' (del
    widget de Turnstile) como si fuera un código válido, y además tapaba el
    chequeo de "falta verificar el mail", que solo corría cuando la regex
    no matcheaba nada — o sea, casi nunca.

    Si el panel de la tienda no tiene bloque 'Coupon Code', el código
    todavía no existe: eso es NeedsVerification de verdad, no una
    adivinanza sobre el texto de la página."""
    log(f"goaffpro.read_coupon_code: abriendo My Stores para '{store['name']}'")
    driver.goto("https://goaffpro.com/affiliate/stores")
    driver.wait_for_timeout(2500)

    panel = _find_panel(driver, store["name"], store.get("domain", ""))
    if panel is None or not panel["code"]:
        raise NeedsVerification(
            f"'{store['name']}' todavía no tiene 'Coupon Code' en My Stores "
            "(falta aprobación/verificación del merchant o no genera cupones)"
        )

    store["affiliate_code"] = panel["code"]
    log(f"goaffpro.read_coupon_code: código de '{store['name']}' = {panel['code']!r}", level="ok")

    if not panel["portal"]:
        raise NeedsVerification(f"'{store['name']}' no tiene link 'Go to portal' en My Stores")
    _read_dashboard(driver, store, panel["portal"])

    # El método de pago se configura al final del flujo (después de subir el
    # cupón), no acá: es independiente del cupón y no debe frenar la carga.
    # Se deja el portal para que el caller lo llame cuando corresponda.
    store["portal_url"] = panel["portal"]


_REFERRAL = "referral link"
_COUPON = "coupon code"
_PORTAL = ("go to portal", "ir al portal")


def _my_stores_panels(driver) -> list[dict]:
    """Corta My Stores en paneles, uno por tienda afiliada.

    Cada panel arranca en el label 'Referral Link' (el único marcador fijo
    de la estructura): el Text anterior es el nombre de la tienda, el Edit
    siguiente es el link de referido, si más adelante aparece el label
    'Coupon Code' el Edit que le sigue tiene el código, y el link
    'Go to portal' cierra el panel."""
    items = driver.ordered(control_types=("Text", "Edit", "Hyperlink"))
    starts = [i for i, (ct, name, _) in enumerate(items) if ct == "Text" and name.strip().lower() == _REFERRAL]

    panels = []
    for n, start in enumerate(starts):
        end = starts[n + 1] - 1 if n + 1 < len(starts) else len(items)
        name = next((nm for ct, nm, _ in reversed(items[:start]) if ct == "Text" and nm.strip()), "")
        block = items[start:end]

        referral = next((driver.value(el) for ct, _, el in block if ct == "Edit"), "")
        code = ""
        for j, (ct, nm, _) in enumerate(block):
            if ct == "Text" and nm.strip().lower() == _COUPON:
                code = next((driver.value(el) for ct2, _, el in block[j + 1:] if ct2 == "Edit"), "")
                break
        portal = next(
            (driver.href(el) for ct, nm, el in block if ct == "Hyperlink" and nm.strip().lower() in _PORTAL), ""
        )
        panels.append({"name": name, "referral": referral, "code": code, "portal": portal})
    return panels


# El portal del merchant se sirve en el idioma del comercio, asi que el label
# del cupon cambia ("Coupon Code" / "Codigo promocional"). El numero se ancla
# a ese label y no al texto suelto: la linea de al lado es la COMISION del
# afiliado con el mismo formato ("Tu enlace de referencia 10%" /
# "Referral Link 20%") y sin anclar se lee la equivocada.
_COUPON_LABEL = r"(?:coupon\s*code|c[oó]digo\s+promocional|promo\s*code|code\s+promo|gutscheincode|codice\s+promozionale)"


# La comision de afiliado vive en la misma pantalla y con el mismo formato
# ("Tu enlace de referencia 10%" / "Referral Link 20%"). Se parsea aparte
# para poder usarla como ULTIMO recurso cuando el merchant no publica el
# descuento del cupon, nunca para pisar uno publicado.
_REFERRAL_LABEL = (
    r"(?:referral\s*link|tu\s+enlace\s+de\s+referencia|enlace\s+de\s+referencia|"
    r"lien\s+de\s+parrainage|empfehlungslink)"
)


def _percent(value) -> str | None:
    """'10%' -> '10'. La card de Goaffpro guarda la comision con el simbolo."""
    m = re.search(r"(\d{1,3})\s*%", str(value or ""))
    return m.group(1) if m else None


def _parse_commission(text: str):
    """Porcentaje de comision de afiliado del dashboard, o None."""
    m = re.search(_REFERRAL_LABEL + r"\s*(\d{1,3})\s*%", text, re.IGNORECASE)
    return m.group(1) if m else None


def _parse_discount(text: str):
    """(porcentaje, monto) del cupon segun el dashboard; None si no figura.

    Solo se acepta un numero pegado al label (entre medio admite espacios y
    saltos de linea, nada mas), asi que si el merchant no publica el
    descuento no se inventa ninguno."""
    pct = re.search(_COUPON_LABEL + r"\s*(\d{1,3})\s*%", text, re.IGNORECASE)
    amt = re.search(_COUPON_LABEL + r"\s*[$€]\s*(\d+(?:[.,]\d+)?)", text, re.IGNORECASE)
    return (pct.group(1) if pct else None, amt.group(1).replace(",", ".") if amt else None)


def _read_dashboard(driver, store: dict, portal_url: str):
    """Abre el dashboard del merchant y saca de ahí el descuento del cupón
    y la screenshot de prueba.

    El link 'Go to portal' de My Stores es un `/login-as/{JWT}`: entra al
    portal del merchant ya logueado, sin pedir password. Por eso no hace
    falta guardar credenciales para volver a leer el dashboard de una
    tienda ya afiliada.

    El dashboard muestra el descuento pegado al label del código
    ('Coupon Code 10% off') — es el único lugar donde aparece. En My Stores
    de Goaffpro no está, y SimplyCodes lo pide como campo obligatorio al
    cargar el cupón, así que sin esto la carga no se puede completar.
    Ojo: 'Referral Link 20%' de al lado es la COMISIÓN del afiliado, no el
    descuento del cliente — por eso el número se ancla a 'Coupon Code'."""
    log(f"goaffpro.read_coupon_code: abriendo el portal del merchant de '{store['name']}'")
    driver.goto(portal_url)
    driver.wait_for_timeout(4000)

    text = driver.page_text()
    pct, amt = _parse_discount(text)
    if pct is not None:
        store["discount_type"] = "percent"
        store["discount_value"] = pct
    elif amt is not None:
        store["discount_type"] = "amount"
        store["discount_value"] = amt
    else:
        # Fallback pedido explicitamente: si el merchant no publica el
        # descuento del cupon, se asume que es igual a la comision de
        # afiliado. Es una SUPOSICION, no un dato leido, por eso se loguea
        # en su propio nivel ("fallback") y no como info normal: permite
        # filtrar despues que cupones se cargaron con un valor asumido.
        assumed = _parse_commission(text) or _percent(store.get("goaffpro_commission"))
        if assumed:
            store["discount_type"] = "percent"
            store["discount_value"] = assumed
            log(
                f"goaffpro.read_coupon_code: '{store['name']}' NO publica el descuento del cupon; "
                f"asumo {assumed}% (= comision de afiliado). Valor DEDUCIDO, no leido.",
                level="fallback",
            )
        else:
            log(
                f"goaffpro.read_coupon_code: no encontre el descuento ni la comision en el dashboard "
                f"de '{store['name']}'",
                level="warn",
            )
    log(
        f"goaffpro.read_coupon_code: descuento = {store.get('discount_value')!r} "
        f"({store.get('discount_type')})"
    )

    safe = re.sub(r"[^\w-]", "_", store["name"])
    screenshot_path = f"screenshots/{safe}_dashboard.png"
    _capture_proof(driver, store, screenshot_path)


def _capture_proof(driver, store: dict, screenshot_path: str):
    """Deja en `screenshot_path` la captura de prueba que SimplyCodes exige.

    Con capturas manuales activadas (checkbox del launcher) el flujo solo
    frena y avisa: la captura la saca el usuario y la sube el usuario en el
    formulario de SimplyCodes. El bot no saca ni sube nada — el cliente lo
    pidió así porque la captura automática a veces agarra una pantalla que no
    sirve y se subía igual."""
    path = Path(screenshot_path)
    if not MANUAL_SCREENSHOTS:
        driver.screenshot(screenshot_path)
        store["dashboard_screenshot_path"] = screenshot_path
        log(f"goaffpro.read_coupon_code: screenshot guardada en {screenshot_path}")
        return

    store["manual_screenshot"] = True
    pause(
        f"{store['name']}: CAPTURAS MANUALES activadas. "
        "Sacá vos la captura del dashboard del merchant ahora. "
        "Más adelante, en el formulario de SimplyCodes, la subís vos misma."
    )
    log("goaffpro.read_coupon_code: modo captura manual, el bot no toca la screenshot", level="ok")


def _find_panel(driver, store_name: str, domain: str) -> dict | None:
    """Panel de `store_name` en My Stores. Matchea por nombre o por el
    dominio del link de referido — los nombres a veces difieren en
    mayúsculas/puntuación entre 'Available Stores' y 'My Stores', el
    dominio no."""
    target_name = _norm(store_name)
    target_domain = _norm_domain(domain)

    panels = _my_stores_panels(driver)
    log(f"goaffpro.read_coupon_code: {len(panels)} panel(es) en My Stores")

    for p in panels:
        same_name = _norm(p["name"]) == target_name
        same_domain = bool(target_domain) and _norm_domain(p["referral"]) == target_domain
        if same_name or same_domain:
            return p

    log(f"goaffpro.read_coupon_code: '{store_name}' no aparece en My Stores", level="warn")
    return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _norm_domain(url_or_domain: str) -> str:
    d = re.sub(r"^https?://", "", (url_or_domain or "").strip().lower()).split("/")[0].split("?")[0]
    return d[4:] if d.startswith("www.") else d


# --------------------------------------------------------------------------
# método de pago (PayPal) en el portal de afiliado
# --------------------------------------------------------------------------


def payments_url(portal_url: str) -> str:
    """/payments del mismo portal al que apunta 'Go to portal'.

    'Go to portal' es un `/login-as/{JWT}` que deja la sesión abierta en el
    portal del merchant, así que a /payments se llega sin volver a
    loguearse. Solo se reemplaza el path — el host cambia por comercio."""
    parts = urlsplit(portal_url or "")
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}/payments"


def _labeled_field(fields, needles, control_type: str):
    for label, el in fields:
        low = (label or "").strip().lower().strip(" *:")
        if el.element_info.control_type == control_type and any(n in low for n in needles):
            return el
    return None


def set_payment_method(driver, store: dict, portal_url: str) -> bool:
    """Configura PayPal como método de pago de comisiones en el portal de
    afiliado de la tienda (pedido del cliente: pantalla Paiements ->
    Paramètres -> Mode de paiement = PayPal + e-mail).

    Devuelve True si quedó configurado (o ya lo estaba). No levanta
    excepción por no encontrar la pantalla: hay portales sin sección de
    pagos y eso no invalida la tienda."""
    if not PAYPAL_EMAIL:
        log("goaffpro.set_payment_method: no hay email de PayPal cargado en el launcher, salteo", level="warn")
        return False

    url = payments_url(portal_url)
    if not url:
        log(f"goaffpro.set_payment_method: portal inválido para '{store['name']}' ({portal_url!r})", level="warn")
        return False

    log(f"goaffpro.set_payment_method: abriendo {url}")
    driver.goto(url)
    driver.wait_for_timeout(3000)

    if reason := page_is_blocked(driver):
        log(f"goaffpro.set_payment_method: BLOQUEADO — {reason}")
        pause(f"{store['name']}: pantalla de pagos del portal — {reason}")

    if PAYPAL_EMAIL.lower() in driver.page_text().lower():
        log(f"goaffpro.set_payment_method: '{store['name']}' ya tiene {PAYPAL_EMAIL} configurado", level="ok")
        store["payment_method"] = "paypal"
        return True

    if not driver.exists_any(PAYMENT_SETTINGS_LABELS):
        log(f"goaffpro.set_payment_method: '{store['name']}' no muestra botón de configuración de pagos", level="warn")
        return False

    log("goaffpro.set_payment_method: click en Paramètres/Settings")
    driver.activate(driver.find_any(PAYMENT_SETTINGS_LABELS))
    driver.wait_for_timeout(2000)

    fields = driver.form_fields()
    combo = _labeled_field(fields, PAYMENT_MODE_LABELS, "ComboBox")
    if combo is not None and not _choose_option(driver, combo, "PayPal"):
        log("goaffpro.set_payment_method: no pude elegir 'PayPal' en el modo de pago", level="warn")

    # el campo de email recién aparece cuando el modo es PayPal
    driver.wait_for_timeout(800)
    fields = driver.form_fields()
    email_el = _labeled_field(fields, PAYPAL_EMAIL_LABELS, "Edit")
    if email_el is None:
        log(
            f"goaffpro.set_payment_method: '{store['name']}' no muestra el campo de e-mail de PayPal, "
            "no configuro nada",
            level="warn",
        )
        return False

    driver.set_value(email_el, PAYPAL_EMAIL)
    log(f"goaffpro.set_payment_method: e-mail PayPal = {PAYPAL_EMAIL}")

    if not driver.exists_any(PAYMENT_SUBMIT_LABELS, control_type="Button"):
        log("goaffpro.set_payment_method: no encontré el botón de envío del formulario", level="warn")
        return False
    driver.activate(driver.find_any(PAYMENT_SUBMIT_LABELS, control_type="Button"))
    driver.wait_for_timeout(2500)

    store["payment_method"] = "paypal"
    log(f"goaffpro.set_payment_method: '{store['name']}' -> PayPal configurado", level="ok")
    return True

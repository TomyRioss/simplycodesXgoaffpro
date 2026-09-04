import html
import json
import re
import urllib.parse

from blockers import page_is_blocked, pause
from log import log

LOOKUP_URL = "https://simplycodes.com/ajax/lookup.php?datatype=merchants&term={term}"

# Estados posibles de /editor/add/{slug} (texto confirmado en vivo).
_PICKER_TEXT = "which store are you adding"
_INELIGIBLE_TEXT = "not eligible for code sharing"


def _norm_domain(url_or_domain: str) -> str:
    """'https://www.FurEase.co/path' -> 'furease.co'. Sirve tanto para el
    dominio que muestra la card de Goaffpro como para el que viene en la
    URL de SimplyCodes."""
    d = re.sub(r"^https?://", "", (url_or_domain or "").strip().lower())
    d = d.split("/")[0].split("?")[0]
    return d[4:] if d.startswith("www.") else d


def _store_domain(url: str) -> str:
    """El dominio de la tienda que identifica un merchant de SimplyCodes va
    en el último segmento de su URL pública: 'https://simplycodes.com/store/
    furease.pet' -> 'furease.pet'. Ojo: no es el host de la URL (eso es
    siempre simplycodes.com)."""
    m = re.search(r"/store/([^/?#]+)", url or "")
    return _norm_domain(m.group(1)) if m else ""


# Sufijos de dominio, para quedarse con la etiqueta de la marca. Hay TLDs de
# dos niveles ('rivox.com.au'): quedarse con la anteúltima etiqueta daba
# 'com' como término de búsqueda, que devolvía 15 tiendas al azar.
_TLD_PARTS = {
    "com", "co", "net", "org", "edu", "gov", "io", "ai", "app", "shop", "store", "site",
    "online", "xyz", "pro", "biz", "info", "me", "tv", "cc", "uk", "us", "au", "ca", "nz",
    "de", "fr", "es", "it", "nl", "br", "ar", "mx", "za", "in", "jp", "cn", "pet",
}


def _domain_label(domain: str) -> str:
    """'www.rivox.com.au' -> 'rivox'; 'travel.bjs.com' -> 'bjs'."""
    parts = _norm_domain(domain).split(".")
    while len(parts) > 1 and parts[-1] in _TLD_PARTS:
        parts.pop()
    return parts[-1] if parts else ""


def _norm_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _lookup(driver, term: str) -> list[dict]:
    """Consulta el autocompletado real de SimplyCodes y devuelve la lista de
    merchants [{id, slug, url, name, label}].

    Es el mismo endpoint que usa el buscador del sitio (confirmado
    interceptando la request al tipear en '/editor/add'). Se navega a la URL
    con el driver en vez de pegarle con `requests` porque Cloudflare
    devuelve 403 a todo lo que no sea el navegador con la sesión del
    usuario — dentro de Chrome el JSON se renderiza como texto plano y
    get_text() lo lee.

    Reemplaza al scraping del buscador del homepage, que derivaba el slug
    del nombre con una regex: el slug REAL no es derivable (SimplyCodes usa
    'bjs' para "BJ's Wholesale Club") y /editor/add/{lo-que-sea} responde
    200 igual, así que un slug inventado nunca fallaba de forma visible."""
    # el endpoint matchea por nombre y se rompe con apóstrofes ("BJ's ..." ->
    # null); sin puntuación devuelve el match igual.
    clean = re.sub(r"[^\w\s.-]", "", term).strip()
    url = LOOKUP_URL.format(term=urllib.parse.quote(clean))
    driver.goto(url)
    driver.wait_for_timeout(700)

    raw = driver.page_text().strip()
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        log(f"simplycodes.lookup: '{clean}' sin resultados (respuesta: {raw[:80]!r})")
        return []
    try:
        results = json.loads(m.group(0))
    except json.JSONDecodeError:
        log(f"simplycodes.lookup: respuesta no parseable para '{clean}': {raw[:120]!r}", level="warn")
        return []
    log(f"simplycodes.lookup: '{clean}' -> {[r.get('slug') for r in results]}")
    return results


def _search_terms(name: str, domain: str) -> list[str]:
    """Términos a probar en el buscador, del más específico al más general.

    El nombre que muestra Goaffpro suele ser el <title> de la tienda, con
    cola de marketing y entidades HTML sin decodificar
    ('HMINLED | Commercial LED Tube Lights &amp; Lighting Fixtures'). El
    buscador de SimplyCodes matchea por nombre de marca, así que con el
    título entero no devuelve nada y la tienda se descartaba como "no está
    en SimplyCodes" cuando sí estaba."""
    clean = html.unescape(name or "").strip()
    terms = [clean]

    # 1) lo que va antes del primer separador: la marca
    head = re.split(r"\s*[|–—:]\s*|\s+[-]\s+", clean)[0].strip()
    terms.append(head)

    # 2) la marca sin el TLD pegado ('Diverstyle.shop' -> 'Diverstyle')
    terms.append(re.sub(r"\.[a-z]{2,}$", "", head, flags=re.IGNORECASE).strip())

    # 3) la etiqueta principal del dominio ('www.hminled.net' -> 'hminled')
    terms.append(_domain_label(domain))

    seen, out = set(), []
    for t in terms:
        k = t.lower()
        if t and len(t) >= 2 and k not in seen:
            seen.add(k)
            out.append(t)
    return out


def find_store(driver, name: str, domain: str) -> dict | None:
    """Busca la tienda en SimplyCodes y devuelve {"slug", "name", "domain"}
    solo si se pudo confirmar de cuál tienda se trata; si no, None.

    El desempate es por DOMINIO, no por nombre: 'FurEase' devuelve dos
    merchants distintos en SimplyCodes (furease.co y furease.pet), y
    'pellepelle' devuelve 'pellepelle11' y 'pellepelles' — elegir el
    primero es una moneda al aire. La card de Goaffpro trae el dominio
    exacto, que es lo que identifica la tienda sin ambigüedad."""
    goal_domain = _norm_domain(domain)

    results = []
    for term in _search_terms(name, domain):
        results = _lookup(driver, term)
        # con dominio conocido se sigue probando términos hasta encontrarlo:
        # una búsqueda puede devolver homónimos que no son esta tienda.
        if results and (not goal_domain or any(_store_domain(r.get("url", "")) == goal_domain for r in results)):
            break
    if not results:
        return None

    if goal_domain:
        for r in results:
            if _store_domain(r.get("url", "")) == goal_domain:
                log(f"simplycodes.find_store: match por dominio '{goal_domain}' -> slug '{r['slug']}'")
                return {"slug": r["slug"], "name": r.get("name") or r.get("label") or name, "domain": goal_domain}

    # Sin dominio en la card (o sin coincidencia), solo se acepta si hay UN
    # único candidato con el mismo nombre normalizado. Con dos candidatos
    # homónimos no hay forma de saber cuál es y adivinar carga el cupón en
    # la tienda equivocada — se descarta.
    by_name = [r for r in results if _norm_name(r.get("name") or r.get("label")) == _norm_name(name)]
    if len(by_name) == 1:
        r = by_name[0]
        log(f"simplycodes.find_store: sin match de dominio, pero hay un único homónimo -> slug '{r['slug']}'")
        return {"slug": r["slug"], "name": r.get("name") or r.get("label"), "domain": _store_domain(r.get("url", ""))}

    log(
        f"simplycodes.find_store: '{name}' ({goal_domain or 'sin dominio'}) no se pudo identificar "
        f"entre {len(results)} resultado(s) -> descartada",
        level="warn",
    )
    return None


def open_editor(driver, slug: str, store_name: str) -> str:
    """Abre /editor/add/{slug} y CONFIRMA que cargo la tienda esperada.
    Devuelve "ok", "ineligible" o "not_found".

    Esta verificacion es el punto que faltaba: la pagina responde 200 con
    cualquier slug. Con un slug inexistente muestra el selector de tiendas
    ('Which store are you adding discount codes for?') y con uno valido
    pero cerrado muestra 'not eligible for code sharing' -- sin mirar el
    texto, el flujo seguia como si todo estuviera bien y cargaba el cupon
    en la nada.

    El encabezado que NO coincide no corta el poll: mientras la pagina
    todavia no monto su documento, el texto disponible es el de la ventana
    e incluye el titulo de la pestana, que dice lo mismo pero con sufijo
    ('Add Promo Codes for Yazv - SimplyCodes - Google Chrome'). Cortando en
    la primera lectura, esa comparacion descartaba tiendas que si existian.
    Se sigue mirando hasta que el nombre coincida o se agote el tiempo."""
    log(f"simplycodes.open_editor: navegando a /editor/add/{slug}")
    driver.goto(f"https://simplycodes.com/editor/add/{slug}")

    last_seen = None
    for _ in range(20):
        driver.wait_for_timeout(500)
        text = driver.page_text()
        low = text.lower()
        if _INELIGIBLE_TEXT in low:
            log(f"simplycodes.open_editor: '{store_name}' no admite carga de codigos", level="warn")
            return "ineligible"
        if _PICKER_TEXT in low:
            log(f"simplycodes.open_editor: slug '{slug}' no resolvio ninguna tienda (salio el selector)", level="warn")
            return "not_found"
        for raw in re.findall(r"Add Promo Codes for (.+)", text):
            got = _clean_header(raw)
            if _norm_name(got) == _norm_name(store_name):
                log(f"simplycodes.open_editor: confirmado, la pagina es de '{got}'")
                return "ok"
            last_seen = got

    if last_seen is not None:
        log(
            f"simplycodes.open_editor: /editor/add/{slug} abrio '{last_seen}' pero esperabamos "
            f"'{store_name}' -> descartada",
            level="warn",
        )
    else:
        log(f"simplycodes.open_editor: /editor/add/{slug} no llego a ningun estado reconocible", level="warn")
    return "not_found"


def _clean_header(raw: str) -> str:
    """Saca el sufijo que agrega el titulo de la pestana de Chrome
    ('Yazv - SimplyCodes - Google Chrome' -> 'Yazv')."""
    got = re.split(r"\s+[|–—]\s+", raw.strip())[0]
    got = re.sub(r"\s*[-—]\s*(SimplyCodes|Google Chrome).*$", "", got, flags=re.IGNORECASE)
    return got.strip()


def read_badge(driver) -> str | None:
    """Badge que se puede ganar agregando un código para esta tienda, o
    None si la tienda no ofrece ninguno.

    Devuelve el tier ("Gold"/"Silver"/"Bronze") cuando la página lo expone,
    y si no el nombre del badge ("Pioneer"), que siempre está.

    Por qué no siempre el tier: el texto completo ("Earn a Pioneer badge in
    silver (medium difficulty)...") vive en un tooltip que el sitio arma
    con CSS :hover. Chrome no lo publica en el árbol de accesibilidad ni
    siquiera con el mouse encima (probado), así que por UI Automation el
    tier no es alcanzable. El chip con el nombre sí lo es.

    ponytail: esto igual es mejor que antes — la regex vieja
    ('Earn a badge in (bronze|silver|gold)') no contemplaba el nombre del
    badge en el medio, así que NUNCA matcheaba y el badge quedaba siempre
    None. El campo solo ordena el CSV, no filtra."""
    m = re.search(r"badge in\s+(bronze|silver|gold)", driver.page_text(), re.IGNORECASE)
    if m:
        tier = m.group(1).capitalize()
        log(f"simplycodes.read_badge: badge tier = {tier!r}")
        return tier

    # El chip del badge, cuando existe, va entre el encabezado
    # "Tips for {tienda}" y el párrafo que arranca con "Add promo codes".
    # Delimitarlo así y no por distancia en pixeles: hay tiendas SIN badge
    # (ej. Swiss Tides), y ahí el párrafo ocupa el lugar donde estaría el
    # chip — una búsqueda por banda de pixeles devolvía
    # 'Add promo codes Swiss Tides.' como si fuera el nombre del badge.
    try:
        driver.find(text="Tips for", control_type="Text", exact=False, timeout=6)
    except Exception:
        log("simplycodes.read_badge: la tienda no ofrece badge")
        return None

    items = driver.page_ordered(control_types=("Text",))
    start = next((i for i, (_, n, _) in enumerate(items) if n.startswith("Tips for")), None)
    if start is None:
        return None

    name = ""
    for _, n, _el in items[start + 1:]:
        if n.lower().startswith("add promo codes"):
            break
        if n and n not in ("*", "•"):
            name = n
            break

    log(f"simplycodes.read_badge: badge = {name or None!r} (tier no expuesto por accesibilidad)")
    return name or None


def _safe_top(el):
    try:
        return el.rectangle().top
    except Exception:
        return None


def add_coupon(driver, slug: str, store: dict):
    """Carga el cupón en /editor/add/{slug}. Asume que open_editor() ya
    confirmó que el slug corresponde a esta tienda."""
    state = open_editor(driver, slug, store.get("simplycodes_name") or store["name"])
    if state != "ok":
        raise RuntimeError(f"/editor/add/{slug} no está disponible para '{store['name']}' (estado: {state})")

    if reason := page_is_blocked(driver):
        log(f"simplycodes.add_coupon: BLOQUEADO — {reason}")
        pause(f"{store['name']}: /editor/add/{slug} — {reason}")

    store["badge"] = read_badge(driver)

    code = (store.get("affiliate_code") or "").strip()
    if not code:
        raise RuntimeError(f"'{store['name']}' no tiene código de afiliado para cargar")

    # El descuento es obligatorio en el paso 2 y no se puede inventar: sin
    # él no tiene sentido arrancar el formulario.
    discount = str(store.get("discount_value") or "").strip()
    if not discount:
        raise RuntimeError(
            f"'{store['name']}': no sabemos el % de descuento del cupón, y SimplyCodes lo pide "
            "como campo obligatorio. Revisar el portal del merchant a mano."
        )

    log(f"simplycodes.add_coupon: llenando código de cupón '{code}'")
    _fill_labeled(driver, ("enter coupon code", "coupon code"), code)
    _click_continue(driver, "paso 1")

    _fill_discount_step(driver, discount, store.get("discount_type") or "percent", store)

    if store.get("manual_screenshot"):
        pause(
            f"{store['name']}: CAPTURAS MANUALES activadas. "
            "Subí vos la captura del dashboard en este paso del formulario y después continuá."
        )
        log("simplycodes.add_coupon: modo captura manual, la subís vos")
    elif store.get("dashboard_screenshot_path"):
        log(f"simplycodes.add_coupon: subiendo screenshot de prueba ({store['dashboard_screenshot_path']})")
        _upload_file(driver, store["dashboard_screenshot_path"])
    else:
        raise RuntimeError(f"'{store['name']}': no hay screenshot de prueba, SimplyCodes la exige")

    driver.wait_for_timeout(800)
    _click_continue(driver, "paso 2")

    # el paso 3 muestra el título generado ("10% Off (Storewide) at X") y el
    # botón 'Finished', que sí es un Button de verdad
    try:
        finish = driver.find_any(["Finished", "Finalizar"], control_type="Button", timeout=8)
    except Exception:
        raise RuntimeError(f"'{store['name']}': no apareció el botón 'Finished', el cupón NO se envió")

    title = next(
        (driver.value(el) for _l, el in driver.form_fields()
         if el.element_info.control_type == "Edit" and " at " in driver.value(el)),
        "",
    )
    log(f"simplycodes.add_coupon: título generado = {title!r}")

    before = driver.page_text()
    log("simplycodes.add_coupon: click en 'Finished' (submit final)")
    driver.click_element(finish)
    driver.wait_for_timeout(2500)
    if driver.page_text() == before:
        raise RuntimeError(f"'{store['name']}': 'Finished' no hizo nada, el cupón NO se envió")

    store["status"] = "coupon_submitted"
    log(f"simplycodes.add_coupon: '{store['name']}' -> status = coupon_submitted")


# Placeholders de los dos <select> obligatorios del paso 2, y la opción que
# hay que elegir en cada uno. El valor actual de un <select> sin elegir es su
# primera opción, que es justamente el texto de la pregunta — por eso sirve
# para identificarlo.
_DISCOUNT_SELECT = ("what's the discount", "discount?")
_ON_WHAT_SELECT = ("on what",)


def _find_combo(driver, needles):
    for _label, el in driver.form_fields():
        if el.element_info.control_type != "ComboBox":
            continue
        v = driver.value(el).strip().lower()
        if any(n in v for n in needles):
            return el
    return None


def _fill_discount_step(driver, discount: str, discount_type: str, store: dict):
    """Paso 2: dos <select> obligatorios ("What's the discount?" -> '% Off'
    o '$ Off', "On what?" -> 'Store-wide deal'), el valor del descuento, y
    la screenshot.

    Antes esto se hacía con driver.click(text='% Off') y
    driver.click(text='Store-wide deal'): esos textos NO existen en la
    página, son opciones de un <select> nativo que Chrome no publica en el
    árbol de accesibilidad. Por eso reventaba con
    'no encontré elemento % Off' apenas llegaba acá."""
    option = "$ Off" if discount_type == "amount" else "% Off"

    combo = _find_combo(driver, _DISCOUNT_SELECT)
    if combo is None:
        raise RuntimeError(f"'{store['name']}': no encontré el select \"What's the discount?\" del paso 2")
    if not driver.select_option(combo, option):
        raise RuntimeError(f"'{store['name']}': no pude elegir '{option}'")
    driver.wait_for_timeout(700)

    # el input del valor aparece recién al elegir la opción, pegado al
    # select y sin label propio: es el primer Edit que le sigue.
    fields = driver.form_fields()
    pos = next(
        (i for i, (_l, el) in enumerate(fields)
         if el.element_info.control_type == "ComboBox" and driver.value(el).strip().lower() == option.lower()),
        None,
    )
    pct_el = next(
        (el for _l, el in fields[pos + 1:] if el.element_info.control_type == "Edit"), None
    ) if pos is not None else None
    if pct_el is None:
        raise RuntimeError(f"'{store['name']}': no encontré el campo del valor de descuento")
    log(f"simplycodes.add_coupon: valor de descuento = {discount} ({option})")
    driver.set_value(pct_el, discount)

    combo2 = _find_combo(driver, _ON_WHAT_SELECT)
    if combo2 is None:
        raise RuntimeError(f"'{store['name']}': no encontré el select \"On what?\" del paso 2")
    if not driver.select_option(combo2, "Store-wide deal"):
        raise RuntimeError(f"'{store['name']}': no pude elegir 'Store-wide deal'")
    driver.wait_for_timeout(700)


def _fill_labeled(driver, label_options, value: str):
    """Llena el input cuyo label matchee alguna de `label_options`. Cae al
    fill() por posición solo si no hay ningún label reconocible."""
    for label, el in driver.form_fields():
        low = label.lower()
        if any(opt in low for opt in label_options):
            driver.set_value(el, value)
            return
    log(f"simplycodes: no encontré input con label {label_options!r}, uso el último Edit", level="warn")
    driver.fill(value)


def _click_continue(driver, step: str):
    """Clickea 'Continue' y verifica que el formulario haya avanzado.

    'Continue' se expone como Text (no Button): no tiene patrón Invoke ni
    acepta foco, así que hay que clickearlo con el mouse, y antes traerlo a
    pantalla porque suele quedar abajo del viewport. Sin eso el click no
    pasaba nada, el formulario se quedaba en el paso 1, y el error recién
    aparecía después ('no encontré % Off') apuntando al lugar equivocado."""
    before = driver.page_text()
    for attempt in range(3):
        try:
            el = driver.find_any(["Continue", "Continuar"], timeout=5)
        except Exception:
            break
        driver.click_element(el)
        driver.wait_for_timeout(1200)
        if driver.page_text() != before:
            log(f"simplycodes.add_coupon: '{step}' avanzó")
            return
        log(f"simplycodes.add_coupon: '{step}' no avanzó, reintento {attempt + 1}/3", level="warn")
    raise RuntimeError(f"el botón 'Continue' del {step} no avanzó el formulario")


_FILENAME_LABELS = re.compile(r"^(file\s*name|nombre de archivo|nombre|nom du fichier|dateiname)\s*:$", re.IGNORECASE)


def _dialog_filename_field(dlg):
    """El campo 'Nombre:' del diálogo de archivo de Windows.

    No se puede tomar el primer Edit: el diálogo tiene uno por CADA archivo
    listado (la etiqueta editable de cada ítem, para renombrar) más el
    cuadro de búsqueda. `found_index=0` caía sobre un ítem de la lista, así
    que la ruta se pegaba encima del nombre de otro archivo, el Enter no
    hacía nada y el diálogo quedaba abierto bloqueando Chrome.

    Se identifica por su nombre accesible, que termina en dos puntos
    ('Nombre:' / 'File name:'), a diferencia de las etiquetas de los ítems.
    Si el idioma no está contemplado, se cae al Edit más ancho, que es ese
    campo (521px contra ~95px de cada ítem)."""
    edits = dlg.descendants(control_type="Edit")
    if not edits:
        return None
    for e in edits:
        try:
            if _FILENAME_LABELS.match((e.element_info.name or "").strip()):
                return e
        except Exception:
            continue

    def width(e):
        try:
            r = e.rectangle()
            return r.right - r.left
        except Exception:
            return 0

    widest = max(edits, key=width)
    log(f"simplycodes: campo de nombre de archivo no reconocido por label, uso el más ancho ({width(widest)}px)", level="warn")
    return widest


def _upload_file(driver, path: str):
    """Sube la screenshot por el diálogo nativo de Windows.

    Dos cosas que no eran obvias:
    - El rectángulo del control `<input type=file>` abarca el botón Y el
      texto 'No file chosen'. Clickear el centro cae sobre el texto y no
      abre nada; hay que pegarle al tramo izquierdo, que es el botón.
    - El diálogo se identifica por su clase de ventana (#32770), no por el
      título: se llama 'Open' o 'Abrir' según el idioma de Windows.
    """
    import os
    import time

    from pywinauto.mouse import click as mouse_click

    full_path = os.path.abspath(path)
    if not os.path.exists(full_path):
        raise RuntimeError(f"la screenshot de prueba no existe: {full_path}")

    btn = driver.find_any(["Choose File", "Elegir archivo", "Seleccionar archivo", "Examinar", "Browse"], timeout=8)
    driver.scroll_into_view(btn)

    dlg = None
    for _ in range(3):
        driver.focus()
        r = btn.rectangle()
        # el tramo izquierdo del rectángulo es el botón; el resto es el
        # texto 'No file chosen', que no abre nada
        mouse_click(coords=(r.left + min(45, (r.right - r.left) // 4), (r.top + r.bottom) // 2))
        dlg = driver.find_owned_dialog(timeout=6)
        if dlg is not None:
            break
    if dlg is None:
        raise RuntimeError("no se abrió el diálogo de subida de archivo de Windows")

    log(f"simplycodes.add_coupon: diálogo '{dlg.window_text()}' abierto, escribiendo la ruta")
    edit = _dialog_filename_field(dlg)
    if edit is None:
        driver.dismiss_owned_dialog()
        raise RuntimeError("no encontré el campo 'Nombre de archivo' en el diálogo de Windows")

    driver.paste_into(edit, full_path)
    time.sleep(0.4)
    # Enter y no click en 'Abrir': ese título matchea 3 controles distintos
    # del diálogo (botón, su flecha y un menú) y pywinauto aborta con
    # ElementAmbiguousError.
    edit.type_keys("{ENTER}")
    time.sleep(1.5)

    if driver.find_owned_dialog(timeout=2) is not None:
        driver.dismiss_owned_dialog()
        raise RuntimeError(f"el diálogo de archivo no se cerró; la screenshot {full_path} no se adjuntó")

    # el control pasa de 'Choose File: No file chosen' al nombre del archivo:
    # es la única confirmación de que quedó adjunto (sin ella el paso 2 se
    # completaba "bien" y recién fallaba al no habilitarse el Continue).
    filename = os.path.basename(full_path)
    if not driver.exists_any([filename]):
        raise RuntimeError(f"la screenshot {filename} no quedó adjunta al formulario")
    log(f"simplycodes.add_coupon: screenshot {filename} adjuntada")

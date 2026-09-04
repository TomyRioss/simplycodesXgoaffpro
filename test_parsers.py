"""Chequeo de los parsers que reemplazaron a las heurísticas rotas.

Corre sin navegador: usa un driver falso que devuelve un árbol de
accesibilidad fijo, copiado tal cual de un dump real (ver docs/GOAL.md).

    python test_parsers.py
"""

import goaffpro
import simplycodes


class FakeEl:
    def __init__(self, value=""):
        self.val = value


class FakeDriver:
    """Driver mínimo: solo lo que usan los parsers.

    Las tiendas de los fixtures que NO salen de un dump real se llaman
    TIENDA-FALSA-*: los logs de estos tests son indistinguibles de los de
    una corrida real, y con nombres de tiendas reales se confunde un caso
    de prueba con una falla en produccion."""

    def __init__(self, items=None, text=""):
        self._items = items or []
        self._text = text

    def ordered(self, control_types=None):
        return [(ct, name, el) for ct, name, el in self._items if not control_types or ct in control_types]

    def value(self, el):
        return el.val

    def get_text(self):
        return self._text

    page_text = get_text

    def href(self, el):
        return el.val

    def goto(self, url):
        self.last_url = url

    def wait_for_timeout(self, ms):
        pass

    def screenshot(self, path):
        self.last_screenshot = path


def _t(name):
    return ("Text", name, FakeEl())


def _e(value):
    return ("Edit", value, FakeEl(value))


# Dump real de goaffpro.com/affiliate/stores (recortado): Swiss Tides con
# código, Frontier Peptide Labs SIN bloque 'Coupon Code', FurEase con código.
MY_STORES = [
    _t("Swiss Tides"),
    _t("Referral Link"),
    _e("https://swiss-tides.com/?ref=cnbqzqfv"),
    _t("Coupon Code"),
    _e("tomasrios"),
    ("Hyperlink", "Go to portal", FakeEl("https://x.goaffpro.com/login-as/TOKEN")),
    _t("Frontier Peptide Labs"),
    _t("Referral Link"),
    _e("https://frontierpeptidelabs.com/?ref=nnnyxqwo"),
    ("Hyperlink", "Go to portal", FakeEl("https://x.goaffpro.com/login-as/TOKEN")),
    _t("FurEase"),
    _t("Referral Link"),
    _e("https://furease.pet/?ref=eniyvhhw"),
    _t("Coupon Code"),
    _e("TOMASRIOS"),
    ("Hyperlink", "Go to portal", FakeEl("https://x.goaffpro.com/login-as/TOKEN")),
]


def test_my_stores():
    d = FakeDriver(MY_STORES)
    panels = goaffpro._my_stores_panels(d)
    assert len(panels) == 3, panels
    assert panels[0]["name"] == "Swiss Tides"
    assert panels[0]["code"] == "tomasrios"
    # sin bloque 'Coupon Code' no debe robarse el de la tienda siguiente
    assert panels[1]["name"] == "Frontier Peptide Labs"
    assert panels[1]["code"] == "", panels[1]
    assert panels[2]["code"] == "TOMASRIOS"

    assert goaffpro._find_panel(d, "Swiss Tides", "swiss-tides.com")["code"] == "tomasrios"
    # match por dominio aunque el nombre venga distinto de Available Stores
    assert goaffpro._find_panel(d, "FUREASE (pet)", "www.furease.pet")["code"] == "TOMASRIOS"
    # sin código todavía -> vacío (NeedsVerification real, no adivinanza)
    assert goaffpro._find_panel(d, "Frontier Peptide Labs", "frontierpeptidelabs.com")["code"] == ""
    # tienda que no está en My Stores
    assert goaffpro._find_panel(d, "Otra Tienda", "otra.com") is None
    # el link login-as del panel es lo que da acceso al dashboard sin password
    assert "login-as" in panels[0]["portal"]

    # el bug viejo: la regex agarraba cualquier mayúscula de la página
    import re
    assert re.search(r"\b[A-Z0-9]{5,}\b", "CLOUDFLARE Privacidad Ayuda"), "el falso positivo era real"


def test_simplycodes_matching():
    # 'FurEase' devuelve DOS merchants distintos; desempata el dominio
    results = [
        {"id": "1", "slug": "furease", "url": "https://simplycodes.com/store/furease.co", "name": "FurEase"},
        {"id": "2", "slug": "fureasepet", "url": "https://simplycodes.com/store/furease.pet", "name": "FurEase"},
    ]
    d = FakeDriver()
    simplycodes._lookup = lambda drv, term: results

    assert simplycodes.find_store(d, "FurEase", "furease.pet")["slug"] == "fureasepet"
    assert simplycodes.find_store(d, "FurEase", "www.furease.co")["slug"] == "furease"
    # dominio que no matchea ninguno y dos homónimos -> no se adivina
    assert simplycodes.find_store(d, "FurEase", "furease.xyz") is None

    simplycodes._lookup = lambda drv, term: [
        {"id": "5347", "slug": "bjs", "url": "https://simplycodes.com/store/bjs.com", "name": "BJ's Wholesale Club"}
    ]
    # el slug real ('bjs') no es derivable del nombre -> sale de la API
    assert simplycodes.find_store(d, "BJ's Wholesale Club", "bjs.com")["slug"] == "bjs"

    simplycodes._lookup = lambda drv, term: []
    assert simplycodes.find_store(d, "No Existe", "noexiste.com") is None


def test_search_terms():
    # título con cola de marketing + entidad HTML: sin fallback no matcheaba
    t = simplycodes._search_terms("HMINLED | Commercial LED Tube Lights &amp; Lighting", "www.hminled.net")
    assert t[1] == "HMINLED", t
    assert "hminled" in [x.lower() for x in t], t
    # TLD pegado al nombre
    assert "Diverstyle" in simplycodes._search_terms("Diverstyle.shop", "diverstyle.shop")
    # nombre limpio: un solo término, sin repetir el dominio equivalente
    assert simplycodes._search_terms("FurEase", "furease.pet") == ["FurEase"]
    # TLD de dos niveles: la marca es 'rivox', no 'com'
    assert simplycodes._domain_label("rivox.com.au") == "rivox"
    assert simplycodes._domain_label("www.hminled.net") == "hminled"
    assert simplycodes._domain_label("travel.bjs.com") == "bjs"
    assert "com" not in simplycodes._search_terms("RIVOX Electric Scooter", "rivox.com.au")


def test_open_editor_states():
    def run(text):
        return simplycodes.open_editor(FakeDriver(text=text), "slug", "FurEase")

    assert run("Add Promo Codes for FurEase\nEnter coupon code:") == "ok"
    assert run("Unfortunately, this store is not eligible for code sharing.") == "ineligible"
    assert run("Which store are you adding discount codes for?") == "not_found"
    # slug que abre OTRA tienda: antes pasaba como válido
    assert run("Add Promo Codes for Hostinger") == "not_found"

    # El titulo de la pestana de Chrome dice lo mismo con sufijo. Si la
    # pagina todavia no monto su documento, ese es el unico texto visible y
    # la comparacion lo agarraba: 'Yazv' se descartaba por no ser igual a
    # 'Yazv - SimplyCodes - Google Chrome'.
    assert simplycodes._clean_header("Yazv — SimplyCodes - Google Chrome") == "Yazv"
    assert simplycodes._clean_header("FurEase - Google Chrome") == "FurEase"
    assert simplycodes._clean_header("Swiss Tides") == "Swiss Tides"
    assert simplycodes.open_editor(
        FakeDriver(text="Add Promo Codes for Yazv — SimplyCodes - Google Chrome"), "yazv", "Yazv"
    ) == "ok"


def test_dashboard_discount():
    """Texto real de dashboards de merchants, en los dos idiomas vistos.

    La linea de al lado es la COMISION del afiliado y tiene el mismo
    formato ('Referral Link 20%' / 'Tu enlace de referencia 10%'): por eso
    el numero se ancla al label del cupon o se lee el equivocado."""
    en = (
        "Referral Link 20%\nRefer your friends using the link below\n"
        "https://swiss-tides.com/?ref=cnbqzqfv\nCopy\nShare\n"
        "Coupon Code 10% off\nShare your coupon code with others\ntomasrios\n"
    )
    assert goaffpro._parse_discount(en) == ("10", None)
    assert goaffpro._parse_commission(en) == "20"

    # dashboard en espanol: la regex vieja era solo inglesa y devolvia None,
    # asi que la tienda moria con "no sabemos el % de descuento"
    es = (
        "Tu enlace de referencia 10%\nEnvia el siguiente enlace a tus contactos\n"
        "https://terunsoul.com/?ref=epevmtql\nCopiar\n"
        "C\u00f3digo promocional 10% de descuento\nComparte tu cupon\nTOMASRIOS\n"
    )
    assert goaffpro._parse_discount(es) == ("10", None)
    assert goaffpro._parse_commission(es) == "10"

    # monto fijo, y label separado del valor por un salto de linea
    assert goaffpro._parse_discount("Coupon Code $15 off") == (None, "15")
    assert goaffpro._parse_discount("Coupon Code\n12% off") == ("12", None)

    # merchant que no publica descuento: no se inventa uno, y NO se agarra
    # el 20% de la comision como si fuera el descuento
    assert goaffpro._parse_discount("Referral Link 20%\nCoupon Code\nTOMASRIOS") == (None, None)

    assert goaffpro._percent("25%") == "25"
    assert goaffpro._percent("30 day(s)") is None
    assert goaffpro._percent(None) is None


def test_discount_fallback_to_commission():
    """Sin descuento publicado se asume la comision de afiliado.

    Es una suposicion, no un dato: queda registrada con el nivel de log
    'fallback' para poder rastrear despues que cupones se cargaron con un
    valor deducido en vez de uno publicado por el merchant."""
    sin_descuento = "Tu enlace de referencia 10%\nC\u00f3digo promocional\nCODIGO-DE-PRUEBA"
    store = {"name": "TIENDA-FALSA-SIN-DESCUENTO", "goaffpro_commission": "10%"}
    goaffpro._read_dashboard(FakeDriver(text=sin_descuento), store, "https://x/login-as/T")
    assert store["discount_value"] == "10"
    assert store["discount_type"] == "percent"

    # el descuento publicado gana: el fallback no lo pisa aunque difiera
    con_descuento = "Referral Link 20%\nCoupon Code 5% off\ntomasrios"
    store2 = {"name": "TIENDA-FALSA-CON-DESCUENTO", "goaffpro_commission": "20%"}
    goaffpro._read_dashboard(FakeDriver(text=con_descuento), store2, "https://x/login-as/T")
    assert store2["discount_value"] == "5", store2

    # sin descuento NI comision en la pagina, sale de la card de Goaffpro
    store3 = {"name": "TIENDA-FALSA-SOLO-CARD", "goaffpro_commission": "15%"}
    goaffpro._read_dashboard(FakeDriver(text="Coupon Code\nCODIGO"), store3, "https://x/login-as/T")
    assert store3["discount_value"] == "15"

    # sin nada de donde sacarlo: no se inventa
    store4 = {"name": "TIENDA-FALSA-SIN-NADA", "goaffpro_commission": None}
    goaffpro._read_dashboard(FakeDriver(text="Coupon Code\nCODIGO"), store4, "https://x/login-as/T")
    assert store4.get("discount_value") is None


def test_badge_regex():
    # texto real: el nombre del badge va en el medio, por eso la regex
    # vieja ('Earn a badge in ...') no matcheaba nunca
    text = "Earn a Pioneer badge in silver (medium difficulty) for finding and adding a working code."
    assert simplycodes.read_badge(FakeDriver(text=text)) == "Silver"


def test_escape_keys():
    from winchrome import _url_key, escape_keys

    # '%' es ALT en type_keys: sin escapar, '...term=Dual%20Aminos' se
    # tipeaba como 'Dual0Aminos' y la búsqueda salía vacía
    assert escape_keys("term=Dual%20Aminos") == "term=Dual{%}20Aminos"
    assert escape_keys("a b") == "a{SPACE}b"
    assert escape_keys("p+w^d~(x)") == "p{+}w{^}d{~}{(}x{)}"

    # el chequeo de navegación tiene que distinguir dos páginas del MISMO
    # sitio, no solo el dominio
    assert _url_key("https://simplycodes.com/editor/add/furease/") == "simplycodes.com/editor/add/furease"
    assert _url_key("https://www.Simplycodes.com/A") == _url_key("http://simplycodes.com/A")
    assert _url_key("https://simplycodes.com/a") != _url_key("https://simplycodes.com/b")


def test_payments_url():
    # 'Go to portal' es un /login-as/{JWT} del portal del merchant; la
    # pantalla de pagos es /payments del MISMO host
    assert goaffpro.payments_url("https://tienda.goaffpro.com/login-as/eyJhbGci") == "https://tienda.goaffpro.com/payments"
    assert goaffpro.payments_url("https://otra.com/login-as/x?y=1") == "https://otra.com/payments"
    assert goaffpro.payments_url("") == ""
    assert goaffpro.payments_url("/login-as/x") == ""


class FakeInfo:
    def __init__(self, ct):
        self.control_type = ct


class FakeCtrl:
    def __init__(self, ct):
        self.element_info = FakeInfo(ct)


def test_payment_labels():
    combo, edit, otro = FakeCtrl("ComboBox"), FakeCtrl("Edit"), FakeCtrl("Edit")
    fields = [
        ("Mode de paiement *", combo),
        ("Adresse e-mail Paypal *", edit),
        ("Autre champ", otro),
    ]
    assert goaffpro._labeled_field(fields, goaffpro.PAYMENT_MODE_LABELS, "ComboBox") is combo
    assert goaffpro._labeled_field(fields, goaffpro.PAYPAL_EMAIL_LABELS, "Edit") is edit
    # el combo no se devuelve como Edit ni al reves
    assert goaffpro._labeled_field(fields, goaffpro.PAYMENT_MODE_LABELS, "Edit") is None
    # el mismo portal en ingles/espanol
    assert goaffpro._labeled_field([("Payment method", combo)], goaffpro.PAYMENT_MODE_LABELS, "ComboBox") is combo
    assert goaffpro._labeled_field([("PayPal Email", edit)], goaffpro.PAYPAL_EMAIL_LABELS, "Edit") is edit
    assert goaffpro._labeled_field([("Nombre", edit)], goaffpro.PAYPAL_EMAIL_LABELS, "Edit") is None


def test_config_roundtrip():
    import json
    import config

    original = config.CONFIG_PATH.read_text(encoding="utf-8") if config.CONFIG_PATH.exists() else None
    try:
        saved = config.save({"first_name": "Ana", "batch_size": "7", "max_batches": "", "manual_screenshots": True})
        assert saved["batch_size"] == 7 and saved["max_batches"] is None
        cfg = config.load()
        assert cfg["first_name"] == "Ana"
        assert cfg["manual_screenshots"] is True
        # una clave desconocida no se guarda, y las que faltan quedan en su default
        assert "cualquiera" not in json.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
        assert cfg["last_name"] == config.DEFAULTS["last_name"]
        # credenciales vacias en config.json no pisan las del .env
        assert cfg["goaffpro_email"] == config.DEFAULTS["goaffpro_email"]
    finally:
        if original is None:
            config.CONFIG_PATH.unlink(missing_ok=True)
        else:
            config.CONFIG_PATH.write_text(original, encoding="utf-8")


def test_webui_endpoints():
    """Levanta el launcher en un puerto libre y chequea los endpoints que no
    lanzan el programa (POST /start valida ANTES de spawnear main.py)."""
    import json
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    import webui

    server = ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert b"<form id=\"f\"" in urllib.request.urlopen(base + "/").read()

        cfg = json.loads(urllib.request.urlopen(base + "/config").read())
        assert set(cfg) == set(__import__("config").DEFAULTS)

        status = json.loads(urllib.request.urlopen(base + "/status").read())
        assert status["running"] is False

        req = urllib.request.Request(
            base + "/start", data=json.dumps({"goaffpro_email": ""}).encode(), method="POST"
        )
        try:
            urllib.request.urlopen(req)
            raise AssertionError("sin credenciales, /start tiene que fallar y NO lanzar main.py")
        except urllib.error.HTTPError as e:
            assert e.code == 400
            assert "goaffpro_password" in json.loads(e.read())["error"]
    finally:
        server.shutdown()


if __name__ == "__main__":
    test_escape_keys()
    test_my_stores()
    test_simplycodes_matching()
    test_search_terms()
    test_open_editor_states()
    test_dashboard_discount()
    test_discount_fallback_to_commission()
    test_badge_regex()
    test_payments_url()
    test_payment_labels()
    test_config_roundtrip()
    test_webui_endpoints()
    print("test_parsers: ok")

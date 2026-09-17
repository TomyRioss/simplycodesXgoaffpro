"""Driver que controla la ventana de Chrome YA abierta del usuario vía Windows
UI Automation (pywinauto) — sin CDP, sin reiniciar Chrome, sin Claude. Expone
una API chica parecida a la de Playwright (goto/click/fill/text) para que
goaffpro.py y simplycodes.py casi no tengan que cambiar.

ponytail: UI Automation en vez de CDP porque el cliente final no puede
reiniciar/perder su sesión de Chrome ya abierta. Es más lento y más frágil
que CDP (depende de que Chrome exponga bien el árbol de accesibilidad) —
si algún selector falla en un sitio puntual, el upgrade path es agregar
un `wait_for`/reintento puntual ahí, no reescribir el driver.
"""

import ctypes
import ctypes.wintypes
import re
import time

import psutil
from pywinauto import Desktop
from pywinauto.clipboard import win32clipboard
from pywinauto.mouse import click as _mouse_click, move as _mouse_move, scroll as _mouse_scroll

from log import log

# Sin esto, si Windows escala la pantalla (>100%), pywinauto reporta
# coordenadas "virtualizadas" que no coinciden con las reales — los
# clicks caen en otro lugar de la pantalla (ej: la barra de tareas).
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


class ElementNotFound(Exception):
    pass


class StopRequested(Exception):
    """El usuario pidió cortar (Ctrl+Alt+F12/ESC/archivo STOP) durante una
    espera larga del driver: se aborta la operación en curso para cortar ya,
    sin esperar los timeouts de goto/find/scroll."""


# type_keys() interpreta estos caracteres como modificadores/secuencias, no
# como texto: '%' es ALT, '^' es CTRL, '+' es SHIFT, '~' es ENTER. Una URL
# con espacios escapados ('...&term=Dual%20Aminos') terminaba tipeada como
# 'Dual0Aminos' porque '%2' se comió como ALT+2 — la búsqueda salía vacía y
# la tienda se descartaba por "no está en SimplyCodes".
_KEY_SPECIALS = "^%+~(){}[]"


def escape_keys(text: str) -> str:
    out = []
    for ch in text:
        if ch in _KEY_SPECIALS:
            out.append("{" + ch + "}")
        elif ch == " ":
            out.append("{SPACE}")
        else:
            out.append(ch)
    return "".join(out)


def _set_clipboard(text: str, attempts: int = 5) -> bool:
    """Pone `text` en el portapapeles de Windows. Devuelve False si no pudo.

    Reintenta porque el portapapeles es un recurso global: si otra app lo
    tiene abierto en ese instante, OpenClipboard falla. Se usa
    SetClipboardText y no SetClipboardData porque este último espera un
    handle de memoria y con un str tira 'Controlador no válido' de forma
    intermitente."""
    for i in range(attempts):
        try:
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
            finally:
                win32clipboard.CloseClipboard()
            return True
        except Exception as e:
            if i + 1 == attempts:
                log(f"winchrome: no pude escribir el portapapeles ({e}), tipeo en vez de pegar", level="warn")
                return False
            time.sleep(0.25)
    return False


def _url_key(url: str) -> str:
    """Normaliza una URL para comparar 'estoy donde quería estar': sin
    esquema, sin 'www.', sin barra final, en minúsculas."""
    u = re.sub(r"^https?://", "", (url or "").strip().lower())
    if u.startswith("www."):
        u = u[4:]
    return u.rstrip("/")


class ChromeDriver:
    def __init__(self):
        self.stop_event = None
        self._url_cache, self._url_cache_at = "", 0.0
        self.window = self._find_chrome_window()
        self.window.set_focus()
        self._snap_left_half()

    def check_stop(self):
        """Aborta con StopRequested si el usuario pidió cortar. Se llama en
        cada loop de espera largo: sin esto, un Ctrl+Alt+F12 durante un
        goto() de 3 intentos x 12s no cortaba hasta que terminara."""
        if self.stop_event is not None and self.stop_event.is_set():
            raise StopRequested()

    def _visible_rect(self, wr):
        """Intersección del rect de la ventana con el área de trabajo del
        monitor (sin la barra de tareas). La taskbar tapa los últimos ~40px
        de la ventana: un elemento 'dentro de la ventana' pero debajo de
        ella NO está visible y el click en su centro cae en la taskbar —
        era por lo que el 'Continue' del editor de SimplyCodes nunca
        avanzaba (centro del botón en y=1045, taskbar desde y=1040)."""
        try:
            user32 = ctypes.windll.user32
            left, top = wr.left, wr.top
            right, bottom = wr.right, wr.bottom
            work = (ctypes.c_long * 4)()
            if not user32.SystemParametersInfoW(0x0030, 0, work, 0):  # SPI_GETWORKAREA
                return (left, top, right, bottom)
            left = max(left, work[0])
            top = max(top, work[1])
            right = min(right, work[2])
            bottom = min(bottom, work[3])
            if right <= left or bottom <= top:
                return (wr.left, wr.top, wr.right, wr.bottom)
            return (left, top, right, bottom)
        except Exception:
            return (wr.left, wr.top, wr.right, wr.bottom)

    def _snap_left_half(self):
        """Ubica la ventana de Chrome en la mitad izquierda de la pantalla
        (la consola va en la mitad derecha, ver main._snap_console_right_half),
        para que el usuario vea ambas a la vez mientras corre el script.

        Se mueve por handle con user32 directo, no con
        HwndWrapper.move_window(): el wrapper que devuelve Desktop(backend=
        "uia") es un UIAWrapper y ese método no existe ahí (sí en el backend
        win32) — probado en vivo, tiraba AttributeError."""
        try:
            user32 = ctypes.windll.user32
            screen_w = user32.GetSystemMetrics(0)
            screen_h = user32.GetSystemMetrics(1)
            hwnd = self.window.handle
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE, por si estaba maximizada
            user32.MoveWindow(hwnd, 0, 0, screen_w // 2, screen_h, True)
            log("winchrome: ventana de Chrome ubicada en la mitad izquierda de la pantalla")
        except Exception as e:
            log(f"winchrome: no pude reubicar la ventana de Chrome ({e})", level="warn")

    def _find_chrome_window(self, attempts: int = 3):
        """Busca la ventana de Chrome con EnumWindows nativo (GetClassNameW /
        GetWindowTextW), no con Desktop().windows(): el backend uia recorre
        el árbol COMPLETO del escritorio con COM y una sola ventana colgada
        (app elevada, renderer ocupado) bloquea esa llamada 20-60s o para
        siempre — medido en vivo: 18.9s con 9 ventanas. EnumWindows es un
        snapshot Win32 que no bloquea (~0ms con 249 ventanas). El wrapper
        UIA se crea recién para la ventana elegida, que sí es rápido.

        Reintenta porque la enumeración es un snapshot: si justo se está
        abriendo o cerrando una pestaña, la ventana puede no aparecer en esa
        pasada y el script moría con 'no encontré Chrome' teniéndolo abierto
        adelante."""
        u = ctypes.windll.user32
        for attempt in range(attempts):
            found = []

            def _cb(hwnd, _lparam):
                if not u.IsWindowVisible(hwnd):
                    return True
                buf = ctypes.create_unicode_buffer(256)
                u.GetClassNameW(hwnd, buf, 256)
                if buf.value != "Chrome_WidgetWin_1":
                    return True
                # Mientras hay un diálogo nativo abierto, Chrome expone
                # además una ventana auxiliar sin título y deshabilitada.
                # Quedarse con la primera que aparezca agarraba esa, y
                # todo type_keys() posterior moría con ElementNotVisible.
                title = ctypes.create_unicode_buffer(512)
                u.GetWindowTextW(hwnd, title, 512)
                if not (title.value or "").strip():
                    return True
                pid = ctypes.wintypes.DWORD()
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    if psutil.Process(pid.value).name().lower() != "chrome.exe":
                        return True
                except Exception:
                    return True
                rect = ctypes.wintypes.RECT()
                u.GetWindowRect(hwnd, ctypes.byref(rect))
                found.append((hwnd, (rect.right - rect.left) * (rect.bottom - rect.top)))
                return True

            u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(_cb), 0)
            if found:
                # la ventana más grande es la del navegador real
                hwnd = max(found, key=lambda c: c[1])[0]
                if len(found) > 1:
                    log(f"winchrome: {len(found)} ventanas de Chrome, uso la más grande")
                w = Desktop(backend="uia").window(handle=hwnd)
                log(f"winchrome: encontré ventana de Chrome — '{w.window_text()}'")
                return w
            if attempt + 1 < attempts:
                time.sleep(1.5)
        raise RuntimeError(
            "No encontré ninguna ventana de Google Chrome abierta (Brave/Edge no cuentan). "
            "Abrí Chrome y volvé a correr main.py."
        )

    # --- navegación ---

    def goto(self, url: str):
        """Navega pegando la URL en la barra de direcciones desde el
        portapapeles, no tipeándola.

        type_keys() manda la URL tecla por tecla y pierde sincronía con el
        estado de SHIFT cuando la máquina está cargada: se vio a
        'https://goaffpro.com/affiliate' llegar a la omnibox como
        '+h+ttps+://go+aff+pr+o.c+om/+af+fili+ate+++' y terminar en una
        búsqueda de Google. Un solo Ctrl+V no tiene ese problema, y de paso
        no le da tiempo al autocompletado de meter una sugerencia."""
        log(f"winchrome.goto: {url}")
        target = _url_key(url)
        # Si ya estamos en esa URL, recargarla da el mismo contenido: exigir
        # que el texto CAMBIE haría esperar al pedo los 8s del timeout.
        # un diálogo nativo abierto deja la ventana deshabilitada y todo
        # type_keys() posterior tira ElementNotEnabled
        self.dismiss_owned_dialog()
        before = "" if _url_key(self.current_url()).startswith(target) else self.page_text()
        for attempt in range(3):
            self.window.set_focus()
            time.sleep(0.1)
            pasted = _set_clipboard(url)
            self.window.type_keys("^l", pause=0.05)
            time.sleep(0.2)
            self.window.type_keys("^a", pause=0.05)
            if pasted:
                self.window.type_keys("^v", pause=0.05)
            else:
                self.window.type_keys(escape_keys(url), pause=0.02, with_spaces=True)
                self.window.type_keys("{DELETE}")
            time.sleep(0.35)  # que el desplegable de sugerencias se asiente antes del Enter
            self.window.type_keys("{ENTER}")
            # Se compara la URL COMPLETA, no solo el dominio: navegando entre
            # dos páginas del mismo sitio (ej. dos consultas seguidas al
            # buscador de SimplyCodes) el chequeo por dominio daba OK al
            # instante con la página anterior todavía cargada, y el paso
            # siguiente leía el resultado de la consulta anterior.
            deadline = time.time() + 12
            t0, last_report = time.time(), 0.0
            while time.time() < deadline:
                self.check_stop()
                # startswith y no ==: muchas de estas URLs redirigen sin que
                # sea un error (/affiliate -> /affiliate/dashboard, /login ->
                # /affiliate si la sesión ya estaba abierta).
                if _url_key(self.current_url(use_cache=False)).startswith(target):
                    self._wait_for_new_document(before)
                    return
                # Sin este aviso, con COM lento hay hasta 36s de silencio y
                # parece colgado con la página ya cargada.
                if time.time() - last_report >= 5:
                    last_report = time.time()
                    log(f"winchrome.goto: todavía esperando {url} "
                        f"(está en '{self.current_url(use_cache=False)}', {int(time.time() - t0)}s)")
                time.sleep(0.8)
            log(f"winchrome.goto: intento {attempt + 1} no llegó a la URL exacta (está en '{self.current_url()}')")

        # Si el dominio es el correcto, la página redirigió a otra ruta y
        # seguir es válido; el caller verifica el contenido igual. Solo se
        # aborta si ni siquiera se salió del sitio anterior.
        current, domain = self.current_url(), target.split("/")[0]
        if domain in current:
            log(f"winchrome.goto: quedó en '{current}' (redirección), sigo", level="warn")
            return
        raise RuntimeError(f"goto('{url}') no navegó después de 3 intentos, quedó en '{current}'")

    def _wait_for_new_document(self, before: str, timeout: float = 8.0):
        """Espera a que el contenido de la página deje de ser el anterior y
        se estabilice.

        La omnibox cambia apenas se manda el Enter, antes de que Chrome
        pinte la página nueva: quien leyera el texto justo ahí se llevaba el
        de la página ANTERIOR. Se vio devolver 'this store is not eligible'
        para un slug inexistente porque ese texto era el de la página que
        todavía estaba en pantalla."""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            self.check_stop()
            time.sleep(0.8)
            now = self.page_text()
            if now and now != before and now == last:
                return
            last = now
        log("winchrome.goto: la página no se estabilizó a tiempo, sigo igual")

    def current_url(self, use_cache: bool = True) -> str:
        """URL de la omnibox.

        Cada lectura es UN scan COM completo del árbol: con COM lento, los
        callers que la invocan en loop (goto cada 0.8s) o por campo
        (form_fields vía _is_omnibox) quemaban minutos en silencio. Por eso
        hay caché de 2s — el loop de goto la saltea (use_cache=False) porque
        ahí sí necesita dato fresco."""
        if use_cache and time.monotonic() - self._url_cache_at < 2.0:
            return self._url_cache
        url = self._current_url_fresh()
        self._url_cache, self._url_cache_at = url, time.monotonic()
        return url

    def _current_url_fresh(self) -> str:
        """Lectura real (sin caché). Fast path primero: si el primer Edit ya
        parece URL, es la omnibox y sale con 1 sola llamada COM. Solo si no,
        se busca por nombre accesible (el dashboard de Goaffpro tiene inputs
        propios que antes se devolvían como si fueran la URL y el goto
        esperaba 3×12s con la página ya cargada). Sin nada confiable se
        devuelve "" (nunca basura: decidir sobre basura es peor que esperar).
        """
        try:
            edits = self.window.descendants(control_type="Edit")
        except Exception:
            return ""
        if not edits:
            return ""
        try:
            first = edits[0].window_text() or ""
        except Exception:
            first = ""
        if "://" in first or first.startswith("www."):
            return first
        for el in edits:  # la omnibox por su nombre accesible
            try:
                if el.element_info.name == "Address and search bar":
                    return el.window_text() or ""
            except Exception:
                continue
        for el in edits:  # el primer Edit con pinta de URL
            try:
                t = el.window_text() or ""
            except Exception:
                continue
            if "://" in t or t.startswith("www."):
                return t
        return ""

    def wait_for_url_contains(self, substr: str, timeout: int = 20):
        log(f"winchrome.wait_for_url_contains: esperando '{substr}' (timeout={timeout}s)")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if substr in self.current_url():
                return
            self.check_stop()
            time.sleep(1.0)
        raise TimeoutError(f"URL nunca contuvo '{substr}', quedó en '{self.current_url()}'")

    def new_tab(self):
        """Abre una pestaña nueva (Ctrl+T) para trabajo secundario
        (SimplyCodes/dashboard) sin perder la página de Goaffpro/search
        de la pestaña original. Al terminar, close_tab() vuelve sola a
        la original — así no hay que reabrir /search + '100 per page' +
        N clicks en 'Next' por cada tienda (~25-30s ahorrados)."""
        self.window.set_focus()
        self.window.type_keys("^t", pause=0.05)
        time.sleep(0.6)

    def close_tab(self):
        """Cierra la pestaña activa (Ctrl+W). Chrome devuelve el foco solo a
        la pestaña que la abrió (portales de merchant que se abren con
        target=_blank) — así no se acumula una pestaña nueva por tienda."""
        self.window.set_focus()
        self.window.type_keys("^w")
        time.sleep(0.4)

    # --- lectura de la página ---

    def get_text(self, skip_chrome_ui: bool = False) -> str:
        """Junta el texto visible de todos los controles de la ventana
        (equivalente aproximado a page.content() para regex/keyword scraping).

        Con skip_chrome_ui=True descarta el titulo de las pestanas y la
        barra de direcciones. El titulo de la pestana repite el <title> de
        la pagina con un sufijo (" - SimplyCodes - Google Chrome"), asi que
        una regex que busca el encabezado lo matchea a el y se lleva el
        sufijo pegado: fue lo que hizo descartar tiendas que si existian."""
        chunks = []
        current = self.current_url() if skip_chrome_ui else None
        for el in self._descendants():
            t = (el.window_text() or "").strip()
            if not t:
                continue
            if skip_chrome_ui and self._is_chrome_ui(el, current):
                continue
            chunks.append(t)
        return "\n".join(chunks)

    def _is_chrome_ui(self, el, current=None) -> bool:
        """True si el elemento es de la interfaz de Chrome (pestanas,
        omnibox) y no del contenido de la pagina."""
        try:
            ct = el.element_info.control_type
        except Exception:
            return False
        if ct in ("TabItem", "Tab"):
            return True
        if ct == "Edit" and self._is_omnibox(el, current):
            return True
        return "Google Chrome" in (el.window_text() or "")

    def page_text(self) -> str:
        """Como get_text() pero SOLO el contenido de la página, sin la
        interfaz de Chrome.

        get_text() recorre la ventana entera, así que también trae el título
        de las pestañas y la barra de direcciones. Eso ensuciaba las
        comparaciones de texto: al verificar que /editor/add/{slug} abrió la
        tienda esperada, el match agarraba el título de la pestaña
        ('Add Promo Codes for FurEase — SimplyCodes - Google Chrome') y la
        comparación contra 'FurEase' fallaba aunque la página fuera la
        correcta."""
        deadline = time.time() + 2.0
        while True:
            self.check_stop()
            doc = self._main_document()
            if doc is not None:
                try:
                    chunks = []
                    for el in doc.descendants():
                        t = (el.window_text() or "").strip()
                        if t:
                            chunks.append(t)
                    if chunks:
                        return "\n".join(chunks)
                except Exception:
                    pass
            if time.time() >= deadline:
                break
            time.sleep(0.2)
        # Sin documento tras reintentar: se devuelve la ventana entera para
        # no dejar ciego al caller, pero sin la UI de Chrome.
        log("winchrome.page_text: no encontre el documento, uso la ventana entera", level="warn")
        return self.get_text(skip_chrome_ui=True)

    def page_ordered(self, control_types=("Text", "Hyperlink", "Button")):
        """Como ordered() pero solo el contenido de la página, en orden de
        documento — sin la interfaz de Chrome."""
        doc = self._main_document()
        if doc is None:
            return self.ordered(control_types)
        out = []
        try:
            children = doc.descendants()
        except Exception:
            return self.ordered(control_types)
        for el in children:
            try:
                ct = el.element_info.control_type
                name = (el.window_text() or "").strip()
            except Exception:
                continue
            if control_types and ct not in control_types:
                continue
            if name:
                out.append((ct, name, el))
        return out

    def _main_document(self):
        """El Document más grande de la ventana es el de la página; los
        otros son los iframes del widget de Cloudflare."""
        try:
            docs = self.window.descendants(control_type="Document")
        except Exception:
            return None
        best, best_n = None, -1
        for doc in docs:
            try:
                n = len(doc.descendants())
            except Exception:
                continue
            if n > best_n:
                best, best_n = doc, n
        return best

    def _descendants(self, control_type: str = None):
        try:
            return self.window.descendants(control_type=control_type) if control_type else self.window.descendants()
        except Exception:
            return []

    # --- interacción ---

    def find(self, text: str = None, control_type: str = None, exact: bool = True, timeout: int = 10):
        """Busca un descendiente por texto visible (name) y/o control_type.
        Si exact=False, matchea por substring (case-insensitive)."""
        deadline = time.time() + timeout
        last_err = None
        while time.time() < deadline:
            self.check_stop()
            for el in self._descendants(control_type):
                name = (el.window_text() or "").strip()
                if text is None:
                    return el
                if exact and name == text:
                    return el
                if not exact and text.lower() in name.lower():
                    return el
            time.sleep(0.3)
        raise ElementNotFound(f"no encontré elemento text={text!r} control_type={control_type!r}")

    def find_any(self, texts, control_type: str = None, exact: bool = False, timeout: int = 10):
        """Primer elemento que matchee CUALQUIERA de `texts`. Los sitios que
        automatizamos se sirven en varios idiomas y cambian el wording de
        los botones ('Login'/'Log in'/'Iniciar sesión') — buscar un único
        string exacto es lo que hacía que el flujo diera 'no existe' por un
        cambio cosmético."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            self.check_stop()
            for el in self._descendants(control_type):
                name = (el.window_text() or "").strip().lower()
                if not name:
                    continue
                for t in texts:
                    t = t.lower()
                    if (exact and name == t) or (not exact and t in name):
                        return el
            time.sleep(0.3)
        raise ElementNotFound(f"no encontré ninguno de {texts!r} control_type={control_type!r}")

    def exists_any(self, texts, control_type: str = None, exact: bool = False) -> bool:
        try:
            self.find_any(texts, control_type=control_type, exact=exact, timeout=1)
            return True
        except ElementNotFound:
            return False

    def activate(self, el):
        """Dispara la acción del control sin mover el mouse (patrón Invoke
        de UI Automation). click_input() mueve el mouse a coordenadas de
        pantalla calculadas por pywinauto que en setups multi-monitor o
        con escalado no coinciden con las reales — vimos clicks aterrizar
        en la barra de tareas de Windows. invoke() no tiene ese problema
        porque no usa coordenadas en absoluto."""
        try:
            el.invoke()
        except Exception:
            try:
                el.set_focus()
                el.type_keys("{ENTER}")
            except Exception:
                el.click_input()

    def click(self, text: str = None, control_type: str = None, exact: bool = True, timeout: int = 10):
        log(f"winchrome.click: text={text!r} control_type={control_type!r}")
        el = self.find(text=text, control_type=control_type, exact=exact, timeout=timeout)
        self._scroll_into_view(el)
        self.activate(el)
        time.sleep(0.3)

    def toggle_on(self, el) -> bool:
        """Tilda un checkbox y devuelve si quedó tildado.

        Un checkbox NO responde a invoke()/ENTER (lo que hace activate()):
        UI Automation usa TogglePattern, y los checkbox ARIA (div
        role=checkbox) a veces solo reaccionan al click real. Se prueban en
        orden — toggle(), SPACE, click de mouse — verificando el estado
        después de cada intento. Si el control no expone estado, se asume
        que el intento que no tiró error surtió efecto."""
        def checked():
            try:
                return el.get_toggle_state() == 1
            except Exception:
                return None

        if checked() is True:
            return True
        self.scroll_into_view(el)
        for act in (
            lambda: el.toggle(),
            lambda: (el.set_focus(), el.type_keys("{SPACE}")),
            lambda: self.click_element(el),
        ):
            try:
                act()
            except Exception:
                continue
            time.sleep(0.3)
            c = checked()
            if c is True:
                return True
            if c is None:
                return True
        return False

    def in_view(self, el) -> bool:
        """True si el rectángulo del elemento cae dentro del área visible de
        la ventana. `is_visible()` no alcanza: para un elemento web devuelve
        True aunque esté scrolleado fuera de pantalla.

        El área visible es la intersección con el área de trabajo del
        monitor: la barra de tareas tapa los últimos ~40px de la ventana y
        ahí el 'Continue' del editor de SimplyCodes quedaba 'visible' pero
        su centro caía sobre la taskbar — el click no hacía nada."""
        try:
            r, wr = el.rectangle(), self.window.rectangle()
        except Exception:
            return False
        vr = self._visible_rect(wr)
        return vr[1] <= r.top and r.bottom <= vr[3] and vr[0] <= r.left and r.right <= vr[2]

    def scroll_into_view(self, el, max_scrolls: int = 30) -> bool:
        """Scrollea hasta que el elemento entre en pantalla, en la dirección
        que corresponda."""
        for _ in range(max_scrolls):
            self.check_stop()
            if self.in_view(el):
                return True
            try:
                r, vr = el.rectangle(), self._visible_rect(self.window.rectangle())
            except Exception:
                return False
            self.wheel_scroll(-4 if r.top > vr[1] else 4)
            time.sleep(0.12)
        return self.in_view(el)

    def click_element(self, el) -> bool:
        """Click de mouse real sobre el centro del elemento, scrolleando
        primero para que esté en pantalla.

        Hace falta para los controles que la página dibuja como texto y no
        como <button>: el 'Continue' del editor de SimplyCodes es un Text en
        el árbol de accesibilidad, así que no tiene patrón Invoke ni acepta
        foco, y activate() se quedaba sin recursos. Encima estaba abajo del
        viewport (y=1097 con el documento terminando en y=992), así que el
        click de fallback caía fuera de la ventana. El formulario se quedaba
        en el paso 1 y el paso siguiente fallaba con 'no encontré % Off'."""
        if not self.scroll_into_view(el):
            log("winchrome.click_element: no pude traer el elemento a pantalla", level="warn")
            return False
        # si Chrome no está en primer plano, el click solo activa la ventana
        # y no llega a la página
        self.focus()
        r = el.rectangle()
        _mouse_click(coords=((r.left + r.right) // 2, (r.top + r.bottom) // 2))
        time.sleep(0.4)
        return True

    def focus(self):
        try:
            self.window.set_focus()
            time.sleep(0.2)
        except Exception:
            pass

    def select_option(self, combo, label: str, max_options: int = 15) -> bool:
        """Elige la opción `label` en un <select> nativo y VERIFICA que haya
        quedado elegida.

        Chrome no publica las opciones de un <select> en el árbol de
        accesibilidad ni siquiera desplegándolo: `ComboBoxWrapper.select()`
        tira IndexError y `expand()` devuelve cero hijos (probado en vivo).
        Lo único que funciona es el teclado.

        Se recorren las opciones de a una con flecha abajo leyendo el valor
        en cada paso, en vez de usar un índice fijo o el typeahead de
        Chrome: el índice depende de que SimplyCodes no reordene la lista, y
        el typeahead se rompe con etiquetas que llevan espacios — al
        escribir '% Off' el espacio despliega el combo y las letras
        siguientes ('Off') terminan escritas en el campo de al lado."""
        self.scroll_into_view(combo)
        try:
            combo.set_focus()
        except Exception:
            return False
        time.sleep(0.2)

        want = label.strip().lower()
        combo.type_keys("{HOME}")
        time.sleep(0.25)
        for _ in range(max_options):
            self.check_stop()
            if self.value(combo).strip().lower() == want:
                log(f"winchrome.select_option: {label!r} elegida")
                return True
            combo.type_keys("{DOWN}")
            time.sleep(0.25)
        if self.value(combo).strip().lower() == want:
            return True
        log(f"winchrome.select_option: no pude elegir {label!r}, quedó {self.value(combo)!r}", level="warn")
        return False

    def _scroll_into_view(self, el, max_scrolls: int = 15):
        """type_keys() explota con ElementNotVisible si el control existe en
        el árbol pero quedó scrolleado fuera de la ventana (páginas largas
        tipo el form de cupón de Simplycodes). Scrollea hasta que se pueda
        verificar visible, o se agotan los intentos."""
        for _ in range(max_scrolls):
            self.check_stop()
            try:
                if el.is_visible():
                    return
            except Exception:
                return
            self.wheel_scroll(-10)
            self.wait_for_timeout(150)

    def fill(self, value: str, label: str = None, placeholder: str = None, control_type: str = "Edit", timeout: int = 10):
        """Encuentra un input y escribe value ahí, reemplazando lo que tenga.
        Con label/placeholder busca por texto accesible; sin eso, toma el
        último Edit que no sea la barra de direcciones (muchos inputs de
        React no exponen name/placeholder por UI Automation, pero suelen
        ser el control más "nuevo" del árbol)."""
        log(f"winchrome.fill: label={label!r} placeholder={placeholder!r}")
        target_name = label or placeholder
        if target_name:
            el = self.find(text=target_name, control_type=control_type, exact=False, timeout=timeout)
        else:
            candidates = [
                e for e in self.find_all(control_type=control_type)
                if "address" not in (e.element_info.name or "").lower()
            ]
            el = candidates[-1] if candidates else self.find(control_type=control_type, timeout=timeout)
        self.set_value(el, value.strip())

    def fill_nth(self, index: int, value: str, control_type: str = "Edit"):
        """Como fill(), pero elige por posición entre los inputs visibles
        (sin la barra de direcciones) — para forms donde el input no expone
        placeholder/label vía accesibilidad (común en apps React)."""
        log(f"winchrome.fill_nth: index={index}")
        candidates = [
            e for e in self.find_all(control_type=control_type)
            if "address" not in (e.element_info.name or "").lower()
        ]
        self.set_value(candidates[index], value)

    def exists(self, text: str, control_type: str = None, exact: bool = False) -> bool:
        try:
            self.find(text=text, control_type=control_type, exact=exact, timeout=1)
            return True
        except ElementNotFound:
            return False

    def screenshot(self, path: str):
        log(f"winchrome.screenshot: guardando en {path}")
        img = self.window.capture_as_image()
        img.save(path)

    def snapshot(self):
        """Captura la ventana como imagen PIL, para leer íconos ✓/✗ que no
        exponen nada útil por accesibilidad (sin alt text ni Value)."""
        return self.window.capture_as_image()

    def wheel_scroll(self, clicks: int = -10):
        """Scroll real de la página (rueda del mouse sobre el centro de la
        ventana). Page Down / flechas NO mueven el scroll de esta página
        (probado en vivo, coordenadas no cambian) — la rueda sí.

        rectangle() es una llamada COM a UI Automation y de tanto en tanto
        tira un COMError transitorio ('Un evento no pudo invocar a ninguno
        de los subscriptores') sin relación con el estado real de la
        ventana — visto en vivo justo al cortar con ESC. Sin este catch
        tumbaba la corrida entera por un solo scroll fallido."""
        try:
            wr = self.window.rectangle()
        except Exception as e:
            log(f"winchrome.wheel_scroll: COM falló leyendo el rectángulo de la ventana ({e}), salteo este scroll", level="warn")
            return
        cx, cy = (wr.left + wr.right) // 2, (wr.top + wr.bottom) // 2
        _mouse_scroll(coords=(cx, cy), wheel_dist=clicks)

    def icon_rects(self):
        """Rectángulos de todos los Image de la ventana, calculados UNA
        VEZ. rectangle() es una llamada COM cara; con 100 cards x ~300
        íconos, pedirla de nuevo por cada card a clasificar multiplica el
        costo por cientos — reusar esta lista durante un mismo paso de
        scroll (antes de volver a scrollear) es lo que hace viable
        clasificar una página de 100 en tiempo razonable."""
        rects = []
        for im in self._descendants("Image"):
            try:
                rects.append((im, im.rectangle()))
            except Exception:
                continue
        return rects

    def icon_is_green(self, text_el, snap, icon_rects) -> bool | None:
        """Dado un elemento de texto (label fijo tipo "Instant Access"),
        busca el ícono ✓/✗ más cercano en la misma fila (entre
        `icon_rects`, de icon_rects()) y clasifica por color de pixel
        sobre `snap` (de snapshot()). True=verde/check, False=rojo/cruz,
        None si no se pudo determinar (fuera de la ventana visible, o
        color ambiguo)."""
        wr = self.window.rectangle()
        tr = text_el.rectangle()
        best_ir, best_dy = None, None
        for _im, ir in icon_rects:
            dy = abs(((ir.top + ir.bottom) / 2) - ((tr.top + tr.bottom) / 2))
            if best_dy is None or dy < best_dy:
                best_dy, best_ir = dy, ir
        if best_ir is None:
            return None
        cx = (best_ir.left + best_ir.right) // 2 - wr.left
        cy = (best_ir.top + best_ir.bottom) // 2 - wr.top
        if not (4 <= cx < snap.size[0] - 4 and 4 <= cy < snap.size[1] - 4):
            return None
        pixels = list(snap.crop((cx - 4, cy - 4, cx + 4, cy + 4)).getdata())
        r, g, b = (sum(p[i] for p in pixels) // len(pixels) for i in range(3))
        if g > r + 15:
            return True
        if r > g + 15:
            return False
        return None

    def wait_for_timeout(self, ms: int):
        time.sleep(ms / 1000)

    def find_all(self, text: str = None, control_type: str = None, exact: bool = True):
        out = []
        for el in self._descendants(control_type):
            name = (el.window_text() or "").strip()
            if text is None:
                out.append(el)
            elif exact and name == text:
                out.append(el)
            elif not exact and text.lower() in name.lower():
                out.append(el)
        return out

    def is_password(self, el) -> bool:
        """True si el Edit es un campo de password. Chrome expone
        IsPassword por UI Automation aunque el input no tenga label ni
        placeholder accesible — es el único dato 100% confiable para
        identificar el campo de contraseña en un form desconocido."""
        try:
            return bool(el.element_info.element.CurrentIsPassword)
        except Exception:
            return False

    def value(self, el) -> str:
        """Contenido actual de un Edit. window_text() de un input web
        devuelve el placeholder o un object-replacement char (\\ufffc), no
        el valor — el valor real está en LegacyIAccessible.Value."""
        try:
            v = el.legacy_properties().get("Value")
            if v:
                return v.strip()
        except Exception:
            pass
        t = (el.window_text() or "").strip()
        return "" if t == "￼" else t

    def set_value(self, el, value: str):
        """Escribe `value` en un Edit ya localizado, reemplazando lo que
        tenga. Único camino de escritura del driver: fill() y fill_nth()
        solo eligen el elemento y delegan acá.

        Pega desde el portapapeles por el mismo motivo que goto(): tipear
        tecla por tecla pierde sincronía con SHIFT bajo carga y mete
        caracteres de más. En un campo de password eso no se ve en pantalla
        y la cuenta queda creada con una contraseña distinta a la que
        guardamos en la DB — irrecuperable."""
        self.scroll_into_view(el)
        self.paste_into(el, value)
        # los campos de password no devuelven su contenido (o devuelven
        # puntitos), así que ahí no hay nada que verificar.
        written = "" if self.is_password(el) else self.value(el)
        if written and written != value:
            log(f"winchrome.set_value: quedó {written!r} en vez de {value!r}, reintento tipeando", level="warn")
            el.type_keys("^a", pause=0.05)
            el.type_keys("{DELETE}")
            el.type_keys(escape_keys(value), with_spaces=True, pause=0.03)

    def form_fields(self):
        """Devuelve [(label, el)] para cada Edit/CheckBox/ComboBox del
        contenido de la página, donde `label` es el texto del último Text
        que apareció antes del control en orden de documento.

        Es la forma de llenar un form cuyo layout/idioma no conocemos: los
        <label> de HTML aparecen en el árbol de accesibilidad como Text
        inmediatamente antes de su input (verificado en vivo contra los
        portales de Goaffpro en inglés Name/Email/Password y en español
        Email/Contraseña/Nombre, que tienen ORDEN DISTINTO). Llenar por
        posición fija rompe apenas cambia el idioma; llenar por label no.

        ponytail: se ignoran los Text vacíos y los '*' de campo requerido,
        que son nodos sueltos entre el label y su input."""
        fields = []
        label = ""
        current = self.current_url()  # UNA vez: _is_omnibox por campo re-escanearía el árbol entero
        for el in self._descendants():
            try:
                ct = el.element_info.control_type
            except Exception:
                continue
            if ct == "Text":
                t = (el.window_text() or "").strip()
                if t and t not in ("*", "•", "￼"):
                    label = t
            elif ct in ("Edit", "CheckBox", "ComboBox"):
                if ct == "Edit" and self._is_omnibox(el, current):
                    continue
                fields.append((label, el))
                label = ""
        return fields

    def paste_into(self, el, value: str):
        """Reemplaza el contenido de un campo de texto, sin scrollear.

        Sirve tanto para inputs de la página como para los campos de un
        diálogo nativo de Windows (donde scrollear no aplica y
        `set_edit_text()` tira COMError 'El usuario ha cancelado la
        operación' de forma intermitente).

        Pega desde el portapapeles y solo tipea si el portapapeles falla:
        tipear tecla por tecla pierde sincronía con SHIFT bajo carga y mete
        caracteres de más. En un campo de password eso no se ve en pantalla
        y la cuenta queda creada con una contraseña distinta a la que
        guardamos en la DB — irrecuperable."""
        el.set_focus()
        time.sleep(0.15)
        el.type_keys("^a", pause=0.05)
        if _set_clipboard(value):
            el.type_keys("^v", pause=0.05)
        else:
            el.type_keys("{DELETE}")
            el.type_keys(escape_keys(value), with_spaces=True, pause=0.03)
        time.sleep(0.25)

    def _is_omnibox(self, el, current=None) -> bool:
        """La barra de direcciones de Chrome también es un Edit del árbol.
        `current` evita re-escanear el árbol por cada campo (ver
        current_url/use_cache). Ojo con el "" : con URL desconocida un Edit
        vacío matcheaba `t == current_url()` y form_fields saltaba inputs
        vacíos reales como si fueran la omnibox."""
        try:
            if el.element_info.name == "Address and search bar":
                return True
            t = el.window_text() or ""
        except Exception:
            return False
        if "://" in t:
            return True
        if current is None:
            current = self.current_url()
        return bool(current) and t.strip() == current

    def find_owned_dialog(self, timeout: float = 10.0, class_name: str = "#32770"):
        """Devuelve el diálogo nativo de Windows que abrió Chrome (el
        'Abrir' de subir archivo), o None.

        Hay que buscarlo por handle con EnumWindows: `Desktop().windows()`
        de pywinauto NO devuelve ventanas *owned*, y el diálogo de archivo
        lo es. Buscándolo ahí daba siempre 'no encontré el diálogo' aunque
        estuviera abierto y visible tapando Chrome."""
        u = ctypes.windll.user32
        parent = self.window.handle
        deadline = time.time() + timeout
        while time.time() < deadline:
            found = []

            def _cb(hwnd, _lparam):
                if u.GetWindow(hwnd, 4) != parent or not u.IsWindowVisible(hwnd):  # 4 = GW_OWNER
                    return True
                buf = ctypes.create_unicode_buffer(256)
                u.GetClassNameW(hwnd, buf, 256)
                if buf.value == class_name:
                    found.append(hwnd)
                return True

            u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(_cb), 0)
            if found:
                return Desktop(backend="uia").window(handle=found[0])
            time.sleep(0.4)
        return None

    def dismiss_owned_dialog(self) -> bool:
        """Cierra un diálogo nativo que haya quedado abierto. Mientras está
        arriba, la ventana de Chrome queda deshabilitada y cualquier
        type_keys() contra ella tira ElementNotEnabled."""
        dlg = self.find_owned_dialog(timeout=1)
        if dlg is None:
            return False
        log("winchrome: cerrando diálogo nativo que quedó abierto", level="warn")
        try:
            dlg.type_keys("{ESC}")
            time.sleep(0.6)
        except Exception:
            pass
        return True

    def hover(self, el):
        """Mueve el mouse sobre un elemento (para tooltips que solo existen
        en el DOM con :hover y por eso no están en el árbol accesible)."""
        try:
            r = el.rectangle()
            _mouse_move(coords=((r.left + r.right) // 2, (r.top + r.bottom) // 2))
        except Exception:
            pass

    def descendants_of(self, el, control_type: str = None):
        try:
            return el.descendants(control_type=control_type) if control_type else el.descendants()
        except Exception:
            return []

    def href(self, el) -> str:
        """Devuelve el href real de un Hyperlink sin clickearlo (Chrome lo
        expone en LegacyIAccessible.Value)."""
        try:
            return el.legacy_properties().get("Value", "") or ""
        except Exception:
            return ""

    def ordered(self, control_types=("Text", "Hyperlink", "Button")):
        """Descendientes visibles con texto, en orden del documento —
        para parsear cards/tablas por posición relativa entre labels."""
        out = []
        for el in self._descendants():
            ct = el.element_info.control_type
            if control_types and ct not in control_types:
                continue
            name = (el.window_text() or "").strip()
            if name:
                out.append((ct, name, el))
        return out

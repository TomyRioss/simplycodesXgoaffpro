"""Pause-and-wait helper for captchas, rate-limiting, logins, or any external
blocker the script cannot solve itself. Prints a clear message and waits for
the user to resolve it in the visible Chrome window, then continues."""

from log import log
from winchrome import StopRequested

# main.py lo setea al arrancar (es el mismo Event del driver). Sin esto,
# cortar durante una pausa manual no hacía nada hasta presionar Enter.
stop_event = None


def _stop_hit() -> bool:
    try:
        return bool(stop_event is not None and stop_event.is_set())
    except Exception:
        return False


def _read_line() -> str:
    """input() que aborta con StopRequested si cortan (ESC/Ctrl+Alt+F12/
    STOP) mientras esperaba. Lee tecla por tecla con msvcrt para poder
    chequear el corte cada 50ms — input() a secas bloquea para siempre."""
    import msvcrt
    import time

    buf: list[str] = []
    while True:
        if _stop_hit():
            print()
            raise StopRequested()
        if not msvcrt.kbhit():
            time.sleep(0.05)
            continue
        ch = msvcrt.getwch()
        if ch in ("\r", "\n"):
            print()
            return "".join(buf)
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x1b":
            print()
            raise StopRequested()
        if ch in ("\x08", "\x7f"):
            if buf:
                buf.pop()
                print("\b \b", end="", flush=True)
            continue
        buf.append(ch)
        print(ch, end="", flush=True)


class UserSkipped(Exception):
    """El usuario respondió N en un pause_yn: la tienda no generó el cupón.
    El flujo la marca como fallada y sigue con la próxima, sin reintentar."""


def _alert():
    """Alarma sonora (triple beep) para avisar que hace falta intervención
    humana. Si el beep de Windows no está disponible, cae a un bell simple."""
    try:
        import winsound
        for freq, dur in ((880, 180), (1175, 180), (880, 180)):
            winsound.Beep(freq, dur)
    except Exception:
        try:
            print("\a", end="", flush=True)
        except Exception:
            pass


def pause(reason: str):
    print("\n" + "\033[91m" + "=" * 60)
    print("🛑 FLUJO DETENIDO — se necesita tu intervención")
    print(reason)
    print("Resolvé lo que haga falta en la ventana de Chrome y presioná Enter para continuar.")
    print("=" * 60 + "\033[0m")
    _alert()
    _read_line()


def pause_yn(reason: str, store_name: str) -> bool:
    """Como pause(), pero el usuario decide sobre la tienda: True = el cupón
    se generó bien y sigo con el flujo (Enter/Y), False = no se generó, se la
    marca fallada y el flujo pasa a la próxima (N)."""
    print("\n" + "\033[91m" + "=" * 60)
    print("🛑 FLUJO DETENIDO — se necesita tu intervención")
    print(reason)
    print("Resolvé lo que haga falta en la ventana de Chrome.")
    print(f"¿Se pudo generar el cupón de '{store_name}'? [Y] sí, sigo con el flujo / [N] no, la marco fallada y sigo con otra. (Enter = Y)")
    print("=" * 60 + "\033[0m")
    _alert()
    while True:
        ans = _read_line().strip().lower()
        if ans in ("", "y", "s", "si", "sí", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("Respondé Y (sí, sigo) o N (no, falló):")


def intervention_loop(reason: str, store_name: str, retry):
    """Falla CRÍTICA (ej. el cupón ya está lleno y es válido pero el botón
    no avanza): intenta `retry()`; si no puede, pide intervención humana con
    alarma y vuelve a intentar, hasta que el retry dé True o el usuario diga
    N (levanta UserSkipped y el flujo pasa a la próxima tienda).

    Antes esto tiraba la tienda a la basura con 'coupon_failed' sin avisar:
    se perdía un código perfectamente funcional."""
    while True:
        if retry():
            return True
        if not pause_yn(
            reason + " Revisá la ventana de Chrome, corregí lo que haga falta y respondé.",
            store_name,
        ):
            raise UserSkipped(f"{store_name}: {reason} — marcada fallada a pedido del usuario")


def page_is_blocked(driver) -> str | None:
    """Returns a reason string if the current page looks like a Cloudflare
    challenge / captcha / login wall, else None."""
    log(f"blockers.page_is_blocked: chequeando '{driver.current_url()}'")
    text = driver.page_text().lower()
    if "verificación de seguridad" in text or "just a moment" in text or "verifique que es un ser humano" in text:
        log("blockers.page_is_blocked: SÍ, Cloudflare/captcha detectado", level="warn")
        return "Cloudflare / verificación humana detectada."
    if "application error" in text or "client-side exception" in text:
        log("blockers.page_is_blocked: SÍ, crash client-side de la app", level="warn")
        return "La app tiró un error client-side (Application error) — recargá a mano en Chrome."
    log("blockers.page_is_blocked: no, página libre")
    return None

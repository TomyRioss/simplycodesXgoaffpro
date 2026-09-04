"""Pause-and-wait helper for captchas, rate-limiting, logins, or any external
blocker the script cannot solve itself. Prints a clear message and waits for
the user to resolve it in the visible Chrome window, then continues."""

from log import log


def pause(reason: str):
    print("\n" + "\033[91m" + "=" * 60)
    print("🛑 FLUJO DETENIDO — se necesita tu intervención")
    print(reason)
    print("Resolvé lo que haga falta en la ventana de Chrome y presioná Enter para continuar.")
    print("=" * 60 + "\033[0m")
    input()


def page_is_blocked(driver) -> str | None:
    """Returns a reason string if the current page looks like a Cloudflare
    challenge / captcha / login wall, else None."""
    log(f"blockers.page_is_blocked: chequeando '{driver.current_url()}'")
    text = driver.page_text().lower()
    if "verificación de seguridad" in text or "just a moment" in text or "verifique que es un ser humano" in text:
        log("blockers.page_is_blocked: SÍ, Cloudflare/captcha detectado", level="warn")
        return "Cloudflare / verificación humana detectada."
    log("blockers.page_is_blocked: no, página libre")
    return None

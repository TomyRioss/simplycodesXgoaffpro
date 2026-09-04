import datetime
import io
import sys
from pathlib import Path

# Los errores se copian acá para que el launcher web pueda mostrarlos: main.py
# corre en otra consola y sin esto sus errores no los junta nadie.
ERRORS_PATH = Path(__file__).parent / "errors.log"

_LEVELS = {
    "info": ("", ""),
    "ok": ("\033[92m", "✅"),
    "error": ("\033[91m", "❌"),
    "warn": ("\033[93m", "⚠️"),
    "candidate": ("\033[96m", "⭐"),
    # descuento DEDUCIDO de la comision, no leido del dashboard: nivel
    # aparte para poder rastrear despues que cupones se cargaron con un
    # valor asumido en vez de uno publicado por el merchant
    "fallback": ("[95m", "🔁"),
}

# La consola de Windows arranca en cp1252: imprimir los emojis de los niveles
# (o el nombre de una tienda con acentos) tiraba UnicodeEncodeError y mataba
# la corrida entera desde adentro del log — justo en los mensajes de warn y
# error, que son los que más importa ver.
if hasattr(sys.stdout, "buffer") and (sys.stdout.encoding or "").lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)


def reset_errors():
    """Vacía errors.log — se llama al arrancar cada corrida para que el archivo
    tenga solo los errores de la última."""
    try:
        ERRORS_PATH.write_text("", encoding="utf-8")
    except OSError:
        pass


def log(msg: str, level: str = "info"):
    color, icon = _LEVELS.get(level, ("", ""))
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    prefix = f"{icon} " if icon else ""
    line = f"[{ts}] {prefix}{msg}"
    out = f"{color}{line}\033[0m" if color else line
    try:
        print(out, flush=True)
    except UnicodeEncodeError:
        print(out.encode("ascii", "replace").decode("ascii"), flush=True)
    if level == "error":
        try:
            with ERRORS_PATH.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

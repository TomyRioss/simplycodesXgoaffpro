"""Tablero siempre visible: tiempo corriendo + intentos + completadas.

Lo abre solo main.py (sin consola propia). Lee status.json 1 vez por
segundo — el tiempo lo calcula en vivo, no necesita que el programa
escriba a cada segundo. Si el programa muere, muestra DETENIDO.

Solo stdlib (tkinter). Se cierra con la X. Es toolwindow topmost sin
foco permanente: no interfiere con la automatización de Chrome.
"""

import datetime
import json
import time
import tkinter as tk
from pathlib import Path

HERE = Path(__file__).parent
STATUS = HERE / "status.json"
STALE_S = 30


def read_status() -> dict:
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def format_elapsed(started_iso, now=None) -> str:
    try:
        t0 = datetime.datetime.fromisoformat(started_iso)
    except (TypeError, ValueError):
        return "--:--:--"
    now = now or datetime.datetime.now()
    s = max(int((now - t0).total_seconds()), 0)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}"


def is_alive(status: dict, age_s: float) -> bool:
    if status.get("state") == "stopped":
        return False
    return bool(status.get("started_at")) and age_s < STALE_S


def _clock_text(st: dict, mtime) -> str:
    """Reloj congelado cuando el flujo terminó (ended_at o última escritura);
    en vivo solo mientras corre."""
    for key in ("ended_at",):
        if st.get(key):
            try:
                return format_elapsed(st.get("started_at"),
                                      datetime.datetime.fromisoformat(st[key]))
            except (TypeError, ValueError):
                pass
    if st.get("state") == "stopped" and mtime:
        return format_elapsed(st.get("started_at"),
                              datetime.datetime.fromtimestamp(mtime))
    if mtime and (time.time() - mtime) >= STALE_S:
        return format_elapsed(st.get("started_at"),
                              datetime.datetime.fromtimestamp(mtime))
    return format_elapsed(st.get("started_at"))


def build(root: tk.Tk) -> dict:
    """Crea los widgets y devuelve sus refs para actualizarlas en tick()."""
    root.title("scrapper")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    W, H = 252, 122
    x = root.winfo_screenwidth() - W - 12
    y = root.winfo_screenheight() - H - 60
    root.geometry(f"{W}x{H}+{x}+{y}")

    mono = ("Consolas", 11, "bold")
    big = ("Consolas", 20, "bold")

    state = tk.Label(root, text="● DETENIDO", fg="red", bg="black", font=mono, anchor="w")
    state.pack(fill="x", padx=8, pady=(6, 0))
    clock = tk.Label(root, text="--:--:--", fg="#00ff88", bg="black", font=big, anchor="w")
    clock.pack(fill="x", padx=8)
    counts = tk.Label(root, text="intentos 0   ok 0", fg="white", bg="black", font=mono, anchor="w")
    counts.pack(fill="x", padx=8)
    hint = tk.Label(root, text="arrastrame / X para cerrar", fg="gray", bg="black",
                    font=("Consolas", 8), anchor="w")
    hint.pack(fill="x", padx=8)

    top = tk.Frame(root, bg="black")
    top.place(relx=1.0, rely=0.0, anchor="ne")
    tk.Button(top, text="X", command=root.destroy, fg="white", bg="#550000",
              font=("Consolas", 8, "bold"), bd=0, padx=6, pady=0).pack()

    pos = {}

    def start_move(e):
        pos["dx"], pos["dy"] = e.x, e.y

    def do_move(e):
        root.geometry(f"+{root.winfo_x() + e.x - pos['dx']}+{root.winfo_y() + e.y - pos['dy']}")

    root.bind("<Button-1>", start_move)
    root.bind("<B1-Motion>", do_move)
    return {"state": state, "clock": clock, "counts": counts}


def refresh(w: dict):
    """Una actualización desde status.json. Devuelve True si sigue vivo."""
    st = read_status()
    try:
        mtime = STATUS.stat().st_mtime
    except OSError:
        mtime = None
    age = (time.time() - mtime) if mtime else float("inf")
    alive = is_alive(st, age)
    w["clock"].config(text=_clock_text(st, mtime))
    w["counts"].config(text=f"intentos {st.get('attempted', 0)}   ok {st.get('completed', 0)}")
    w["state"].config(text="● CORRIENDO" if alive else "● DETENIDO",
                      fg="#00ff88" if alive else "red")
    return alive


def main():
    root = tk.Tk()
    w = build(root)

    def tick():
        refresh(w)
        root.after(1000, tick)

    tick()
    root.mainloop()


if __name__ == "__main__":
    main()

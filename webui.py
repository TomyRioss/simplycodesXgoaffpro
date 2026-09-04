"""Launcher web del scrapper.

El programa maneja la ventana de Chrome por UI Automation: mientras corre no
se puede usar el navegador ni otra app. Por eso esta pantalla actúa SOLO
antes de arrancar (cargar datos y opciones) y después de terminar (bajar el
CSV) — durante la corrida no hay que tocarla.

    python webui.py     ->  abre http://localhost:8765

`main.py` se lanza en una CONSOLA NUEVA a propósito: las pausas por
captcha/verificación (`input()`) necesitan una consola propia. El corte NO
depende de esa consola: hotkey global Ctrl+Alt+F12, botón "Detener" (que
escribe el archivo STOP) o ESC en la consola si tiene foco.

ponytail: http.server de la stdlib, sin Flask ni build de frontend — son
cuatro endpoints y un HTML.
"""

import csv
import hashlib
import json
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import config

HERE = Path(__file__).parent
INDEX = HERE / "web" / "index.html"
README_PATH = HERE / "README.md"
CSV_PATH = HERE / "export.csv"
STOP_FLAG = HERE / "STOP"
ERRORS_PATH = HERE / "errors.log"
PORT = 8765

_proc: subprocess.Popen | None = None


def _running() -> bool:
    return _proc is not None and _proc.poll() is None


def _start() -> str | None:
    """Lanza main.py en consola nueva. Devuelve un error para mostrar, o None."""
    global _proc
    if _running():
        return "El programa ya está corriendo. Cortalo con Ctrl+Alt+F12 o el botón Detener."
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    _proc = subprocess.Popen([sys.executable, str(HERE / "main.py")], cwd=str(HERE), creationflags=flags)
    return None


PREVIEW_ROWS = 1000


def _csv_files() -> list[Path]:
    """Todos los CSV generados: el `export.csv` del proyecto más las copias
    fechadas de la carpeta elegida en el launcher. Más nuevo primero."""
    found = {}
    if CSV_PATH.exists():
        found[CSV_PATH.resolve()] = None
    csv_dir = (config.load().get("csv_dir") or "").strip()
    if csv_dir:
        try:
            for f in Path(csv_dir).glob("export_*.csv"):
                found[f.resolve()] = None
        except OSError:
            pass
    return sorted(found, key=lambda f: f.stat().st_mtime, reverse=True)


def _file_id(path: Path) -> str:
    """Id estable derivado de la ruta. Los endpoints reciben este id y nunca
    una ruta: así una URL armada a mano no puede leer un archivo cualquiera
    del disco."""
    return hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]


def _resolve(file_id: str) -> Path | None:
    return next((f for f in _csv_files() if _file_id(f) == file_id), None)


def _row_count(path: Path) -> int:
    """Filas de datos (sin el encabezado). Se cuenta leyendo, no parseando:
    un CSV de 500 tiendas no justifica cargar todo en memoria dos veces."""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            return max(sum(1 for _ in f) - 1, 0)
    except OSError:
        return 0


def _errors() -> dict:
    """errors.log de la última corrida: texto completo, cantidad de errores y
    el último como resumen. Cada error empieza con el icono ❌."""
    try:
        text = ERRORS_PATH.read_text(encoding="utf-8")
    except OSError:
        text = ""
    marks = [ln for ln in text.splitlines() if "❌" in ln]
    return {"text": text, "count": len(marks), "summary": marks[-1] if marks else ""}


def _history() -> list[dict]:
    items = []
    for f in _csv_files():
        stat = f.stat()
        items.append({
            "id": _file_id(f),
            "name": f.name,
            "folder": str(f.parent),
            "size": stat.st_size,
            "modified": stat.st_mtime,
            "rows": _row_count(f),
            "current": f == CSV_PATH.resolve(),
        })
    return items


def _preview(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        columns = next(reader, [])
        rows = []
        for n, row in enumerate(reader):
            if n >= PREVIEW_ROWS:
                break
            rows.append(row)
        truncated = next(reader, None) is not None
    return {"columns": columns, "rows": rows, "truncated": truncated, "shown": len(rows)}


def _pick_folder(initial: str = "") -> str | None:
    """Abre el selector de carpetas de Windows y devuelve la ruta elegida.

    Corre en el proceso del launcher, que está en la misma máquina que el
    navegador: la web no puede pedir una ruta real del disco (el input de
    archivos del navegador nunca la expone), así que el diálogo lo abre el
    server. Devuelve None si el usuario cancela."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        chosen = filedialog.askdirectory(
            title="Carpeta para los CSV",
            initialdir=initial or str(HERE),
            parent=root,
        )
    finally:
        root.destroy()
    return str(Path(chosen)) if chosen else None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, content_type: str, extra: dict = None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, code: int = 200):
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)
        file_id = (query.get("id") or [""])[0]

        if path == "/":
            self._send(200, INDEX.read_bytes(), "text/html; charset=utf-8")
        elif path == "/config":
            self._json(config.load())
        elif path == "/status":
            self._json({"running": _running(), "csv": CSV_PATH.exists()})
        elif path == "/history":
            self._json({"files": _history()})
        elif path == "/errors":
            self._json(_errors())
        elif path == "/readme":
            text = README_PATH.read_text(encoding="utf-8") if README_PATH.exists() else "README.md no encontrado."
            self._send(200, text.encode("utf-8"), "text/plain; charset=utf-8")
        elif path == "/preview":
            target = _resolve(file_id)
            if target is None:
                self._json({"error": "ese CSV ya no está"}, 404)
                return
            self._json(_preview(target))
        elif path == "/csv":
            target = _resolve(file_id) if file_id else (CSV_PATH if CSV_PATH.exists() else None)
            if target is None:
                self._json({"error": "todavía no hay CSV para descargar"}, 404)
                return
            self._send(
                200, target.read_bytes(), "text/csv; charset=utf-8",
                {"Content-Disposition": f'attachment; filename="{target.name}"'},
            )
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/stop":
            if not _running():
                self._json({"error": "no hay nada corriendo"}, 409)
                return
            STOP_FLAG.write_text("", encoding="utf-8")
            self._json({"ok": True})
            return
        if path not in ("/start", "/pick-folder"):
            self._json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._json({"error": "JSON inválido"}, 400)
            return

        if path == "/pick-folder":
            try:
                chosen = _pick_folder(str(data.get("current") or ""))
            except Exception as e:
                self._json({"error": f"no pude abrir el selector de carpetas ({e}). Escribí la ruta a mano."}, 500)
                return
            self._json({"path": chosen} if chosen else {"cancelled": True})
            return

        faltan = [k for k in ("goaffpro_email", "goaffpro_password") if not str(data.get(k) or "").strip()]
        if faltan:
            self._json({"error": "Faltan datos obligatorios: " + ", ".join(faltan)}, 400)
            return

        config.save(data)
        if error := _start():
            self._json({"error": error}, 409)
            return
        self._json({"ok": True})

    def log_message(self, *args):
        pass  # la consola del launcher es para el usuario, no para los GET


class Server(ThreadingHTTPServer):
    # En Windows, allow_reuse_address deja que un SEGUNDO launcher se ate al
    # mismo puerto sin error: quedan dos servidores atendiendo alternado y la
    # página termina hablando con el proceso viejo (endpoints que "no
    # existen", config que no se guarda). Mejor fallar claro.
    allow_reuse_address = False


def main():
    url = f"http://localhost:{PORT}"
    try:
        server = Server(("127.0.0.1", PORT), Handler)
    except OSError:
        print(f"Ya hay un launcher abierto en {url}. Cerrá esa consola (Ctrl+C) y volvé a intentar.")
        return
    print(f"Launcher abierto en {url}  (Ctrl+C para cerrarlo)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("cerrando launcher")


if __name__ == "__main__":
    main()

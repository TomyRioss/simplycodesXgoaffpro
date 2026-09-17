"""
Cruza Goaffpro (tiendas con acceso instantáneo) y Simplycodes (directorio de
cupones), se afilia a cada match y carga el cupón generado. Persiste toda
tienda que matchee en ambos sitios; badge/popularity de SimplyCodes se
guardan solo para ordenar el CSV (mejores arriba), no filtran.

El script controla la ventana de Chrome que YA tenés abierta (con tu sesión
de simplycodes.com logueada) vía Windows UI Automation — no usa CDP, no la
cierra, no abre una nueva. Ver docs/GOAL.md.

Descubre candidatas nuevas y corre el flujo COMPLETO por cada una antes de
pasar a la siguiente: goaffpro enroll -> leer código/descuento -> subir
cupón en Simplycodes -> método de pago en goaffpro. Corre hasta juntar
--stop-after cupones subidos, o sin tope (recorre todo el catálogo de
Goaffpro en loop) hasta ESC/Ctrl+Alt+F12/botón Detener. Al cortar exporta
export.csv. Ver README.md.

Las tiendas que quedan pending_verification/coupon_failed se reintentan
solas al arrancar cada tanda del loop (además, `python main.py
--retry-pending` corre SOLO esos reintentos y sale).

Correr:
    python main.py [--stop-after N]
    python main.py --retry-pending
"""

import argparse
import ctypes
import datetime
import json
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import config
import goaffpro
import simplycodes
import blockers
from db import already_seen, count_completed, export_csv, get_conn, insert_store, pending_stores, update_store
from log import log, reset_errors
from winchrome import ChromeDriver, StopRequested
Path("screenshots").mkdir(exist_ok=True)


STOP_FLAG = Path(__file__).parent / "STOP"
STATUS_PATH = Path(__file__).parent / "status.json"


def _save_status(conn, stats):
    """Deja tiempo/intentos/completadas SIEMPRE visibles: los escribe a
    status.json (lo lee el HUD desktop) y al título de la consola (visible
    aunque el log scrollee). Best-effort: nunca debe tumbar la corrida."""
    if stats is None:
        return
    try:
        run_start = stats.get("run_start")
        completed = count_completed(conn, since_run=run_start) if run_start else count_completed(conn)
        payload = {
            "started_at": stats.get("started_at"),
            "attempted": stats.get("attempted", 0),
            "completed": completed,
            "state": "running",
        }
        STATUS_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:
        log(f"main: no pude guardar status.json ({e})", level="warn")
        return
    try:
        ctypes.windll.kernel32.SetConsoleTitleW(
            f"{_elapsed(stats)} | intentos {payload['attempted']} | ok {payload['completed']} — scrapper"
        )
    except Exception:
        pass


def _save_stopped(stats=None):
    """Avisa al HUD que el flujo terminó: DETENIDO + reloj congelado YA,
    sin esperar el timeout de inactividad. Se llama en TODAS las salidas
    (fin normal, corte, --retry-pending, crash)."""
    try:
        payload = {}
        if STATUS_PATH.exists():
            try:
                payload = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            except ValueError:
                payload = {}
        payload["state"] = "stopped"
        payload["ended_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        STATUS_PATH.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


def _snap_console_right_half():
    """Ubica esta consola en la mitad derecha de la pantalla, en espejo con
    Chrome (mitad izquierda, ver winchrome.ChromeDriver._snap_left_half)."""
    try:
        kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return
        screen_w = user32.GetSystemMetrics(0)
        screen_h = user32.GetSystemMetrics(1)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE, por si arrancó maximizada
        user32.MoveWindow(hwnd, screen_w // 2, 0, screen_w // 2, screen_h, True)
    except Exception as e:
        log(f"main: no pude reubicar la consola ({e})", level="warn")


def _stop_listener(stop_event: threading.Event):
    """Corta el loop. Tres vías, ninguna necesita que la consola tenga foco
    (mientras corre, pywinauto acapara mouse y foco de Chrome):
    1. hotkey global Ctrl+Alt+F12 (RegisterHotKey de Win32) — dispara con
       cualquier ventana activa; pywinauto tipea en Chrome pero no consume
       hotkeys del sistema.
    2. archivo STOP en la carpeta del proyecto — lo crea el launcher web
       (botón "Detener") o el usuario a mano.
    3. ESC en la consola — si llega a tener foco.
    Cualquiera de las tres hace corte limpio y exporta el CSV."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    HOTKEY_ID = 1
    MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
    VK_F12 = 0x7B
    WM_HOTKEY = 0x0312
    PM_REMOVE = 0x0001

    registered = bool(user32.RegisterHotKey(None, HOTKEY_ID, MOD_ALT | MOD_CONTROL | MOD_NOREPEAT, VK_F12))
    if registered:
        log("main: hotkey global Ctrl+Alt+F12 para cortar (no hace falta foco en la consola)")
    else:
        log("main: no pude registrar Ctrl+Alt+F12; cortá con el botón 'Detener' del launcher o ESC", level="warn")

    if STOP_FLAG.exists():
        STOP_FLAG.unlink()

    msg = wintypes.MSG()
    try:
        while not stop_event.is_set():
            if registered and user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_HOTKEY:
                    log("main: Ctrl+Alt+F12 detectado, corto YA (sin terminar la tienda en curso)", level="warn")
                    stop_event.set()
                    break
            if STOP_FLAG.exists():
                log("main: archivo STOP detectado, corto YA (sin terminar la tienda en curso)", level="warn")
                stop_event.set()
                break
            if msvcrt.kbhit() and msvcrt.getch() == b"\x1b":
                log("main: ESC detectado, corto YA (sin terminar la tienda en curso)", level="warn")
                stop_event.set()
                break
            stop_event.wait(0.2)
    finally:
        if registered:
            user32.UnregisterHotKey(None, HOTKEY_ID)
        if STOP_FLAG.exists():
            STOP_FLAG.unlink()


def _elapsed(stats) -> str:
    """Tiempo corriendo en H:MM:SS desde que arrancó main()."""
    s = int(time.monotonic() - stats["t0"])
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _pipeline(driver, conn, store: dict, stop_event: threading.Event, stats: dict | None = None):
    """Flujo entero para UNA tienda ya persistida, en orden:
    goaffpro enroll -> leer código + descuento del dashboard -> subir cupón
    en Simplycodes -> configurar método de pago en goaffpro (último).

    Cada paso se saltea según el `status` de la fila, así los reintentos
    (pending_verification/coupon_failed) retoman donde quedaron."""
    name = store["name"]
    try:
        if store["status"] == "discovered":
            log(f"main: [{name}] enroll en goaffpro")
            goaffpro.enroll(driver, store)
            update_store(
                conn, store["id"], status="enrolled",
                merchant_email=store.get("merchant_email"),
                merchant_password=store.get("merchant_password"),
            )
            store["status"] = "enrolled"

        if stop_event.is_set():
            return

        if store["status"] != "coupon_submitted":
            # Siempre se pasa por el dashboard del merchant: el DESCUENTO y la
            # screenshot salen de ahí, no de la DB. El merchant genera el
            # código minutos después del enroll — si todavía no está,
            # read_coupon_code tira NeedsVerification y se reintenta la tanda
            # siguiente.
            log(f"main: [{name}] leyendo código + descuento del dashboard")
            goaffpro.read_coupon_code(driver, store)
            if not store.get("affiliate_code"):
                update_store(conn, store["id"], status="enroll_failed")
                log(f"main: [{name}] sin código -> enroll_failed", level="error")
                return
            update_store(
                conn, store["id"], status="enrolled",
                affiliate_code=store["affiliate_code"],
                discount_type=store.get("discount_type"),
                discount_value=store.get("discount_value"),
                dashboard_screenshot_path=store.get("dashboard_screenshot_path"),
            )
            log(f"main: [{name}] código {store['affiliate_code']}, descuento {store.get('discount_value')}")

            if stop_event.is_set():
                log(f"main: [{name}] ESC antes de subir el cupón, queda en enrolled", level="warn")
                return

            if stats is not None:
                stats["attempted"] += 1
                log(f"main: [{name}] intento #{stats['attempted']} de carga en Simplycodes ({_elapsed(stats)})")
                _save_status(conn, stats)
            log(f"main: [{name}] subiendo cupón en Simplycodes")
            simplycodes.add_coupon(driver, store["simplycodes_slug"], store)
            update_store(
                conn, store["id"], status=store["status"], badge=store.get("badge"),
                popularity=store.get("popularity"), needing_codes=store.get("needing_codes"),
                coin_rate=store.get("coin_rate"),
                completed_at=datetime.datetime.now().isoformat(timespec="seconds"),
            )
            store["status"] = "coupon_submitted"
            log(f"main: [{name}] LISTO -> código {store['affiliate_code']} ({store.get('badge', 'sin badge')})", level="ok")
            _save_status(conn, stats)

        # Sin 3er open_editor: add_coupon() ya leyó badge/popularity/coin
        # en ESTA misma página segundos antes (y update_store arriba los
        # guarda). Reabrir /editor/add por 3ra vez era 1 goto + 2 lecturas
        # (~10s) por tienda para releer lo mismo.

        # Método de pago: último y best-effort — no debe tumbar el flujo.
        if store.get("portal_url"):
            try:
                log(f"main: [{name}] configurando método de pago en goaffpro")
                if goaffpro.set_payment_method(driver, store, store["portal_url"]):
                    update_store(conn, store["id"], payment_method=store.get("payment_method"))
            except Exception as e:
                log(f"main: [{name}] método de pago falló ({type(e).__name__}: {e}), sigo", level="warn")

    except goaffpro.NeedsVerification as e:
        log(f"main: [{name}] pendiente de verificación — {e}. Se reintenta la tanda siguiente", level="warn")
        update_store(conn, store["id"], status="pending_verification")
    except goaffpro.NoCouponCode as e:
        log(f"main: [{name}] SIN CÓDIGO — {e}. Marcada failed (NO CODE), no se reintenta", level="error")
        update_store(conn, store["id"], status="no_code", affiliate_code=None)
    except blockers.UserSkipped as e:
        log(f"main: [{name}] marcada fallada a pedido del usuario — {e}", level="warn")
        update_store(conn, store["id"], status="user_failed")
    except StopRequested:
        log(f"main: [{name}] corte pedido, queda como está (se reintenta en la próxima run)", level="warn")
        return
    except Exception:
        log(f"main: [{name}] ERROR:\n{traceback.format_exc()}", level="error")
        # Si ya hay código, lo que falló es la carga del cupón: 'coupon_failed'
        # (no 'enroll_failed', que la sacaría de los reintentos para siempre).
        fail_status = "coupon_failed" if store.get("affiliate_code") else "enroll_failed"
        update_store(conn, store["id"], status=fail_status)
        log(f"main: [{name}] marcada '{fail_status}'", level="warn")


def retry_pending(driver, conn, stop_event: threading.Event, stats: dict | None = None):
    """Reintenta las tiendas que quedaron a medias en corridas anteriores —
    sobre todo pending_verification (para que el merchant haya tenido tiempo
    de generar el código) y coupon_failed (ej. falló la carga del cupón).
    Se llama sola al arrancar cada tanda del loop normal; con `python main.py
    --retry-pending` corre SOLO esto y sale."""
    rows = pending_stores(conn, "discovered", "enrolled", "coupon_failed", "pending_verification")
    if not rows:
        return
    log(f"=== reintentando {len(rows)} tiendas pendientes ===")
    for row in rows:
        if stop_event.is_set():
            log("main: ESC detectado, corto los reintentos")
            break
        try:
            _pipeline(driver, conn, dict(row), stop_event, stats)
        except StopRequested:
            log("main: corte pedido durante un reintento, corto")
            break


def process_new(driver, conn, target: int | None, stop_event: threading.Event, run_start: str | None = None, stats: dict | None = None) -> bool:
    """Descubre candidatas nuevas y corre el flujo entero por cada una.
    `target` = tope de tiendas COMPLETADAS (cupón subido) DENTRO DE ESTA
    RUN; None = sin tope, recorre todo el catálogo de Goaffpro una vez.
    Devuelve True si cortó porque llegó al target (para que el caller no
    reinicie el escaneo)."""
    log(f"=== descubrir + procesar candidatas ({'sin tope' if not target else f'meta {target} completadas'}) ===")
    for candidate in goaffpro.iter_instant_access_candidates(driver, conn, stop_event):
        if stop_event.is_set():
            return False
        if target and count_completed(conn, since_run=run_start) >= target:
            return True
        if already_seen(conn, candidate["store_id"]):
            log(f"main: '{candidate['name']}' ya procesada antes (store_id={candidate['store_id']}), salteo")
            continue

        common = dict(
            goaffpro_store_id=candidate["store_id"],
            goaffpro_page=candidate["goaffpro_page"],
            name=candidate["name"],
            domain=candidate["domain"],
            currency=candidate["currency"],
            goaffpro_commission=candidate["goaffpro_commission"],
            cookie_duration=candidate["cookie_duration"],
            registrations_opens=candidate["registrations_opens"],
            approved_automatically=candidate["approved_automatically"],
            affiliate_portal=candidate["affiliate_portal"],
            affiliate_portal_signup=candidate["affiliate_portal_signup"],
        )

        # Gate: match en Simplycodes ANTES de afiliarse. Es un GET sin efectos;
        # afiliarse primero significaría enroll a decenas de tiendas que después
        # se descartan. /editor/add/{cualquier-cosa} responde 200, por eso se
        # confirma que el slug abre la página de ESTA tienda.
        # Corre en TAB SECUNDARIA: la pestaña de Goaffpro/search queda intacta
        # y al cerrarla no hay que reabrir + 100/página + N Next (~25-30s).
        log(f"main: revisando '{candidate['name']}' en Simplycodes...")
        _use_tabs = hasattr(driver, "new_tab") and hasattr(driver, "close_tab")
        _tab_open = False
        try:
            if _use_tabs:
                driver.new_tab()
                _tab_open = True
            found = simplycodes.find_store(driver, candidate["name"], candidate["domain"])
            if found:
                state = simplycodes.open_editor(driver, found["slug"], found["name"])
            else:
                state = None
            if not found or state != "ok":
                if not found:
                    log(f"main: '{candidate['name']}' descartada, no está en Simplycodes")
                    insert_store(conn, simplycodes_slug=None, status="rejected_no_simplycodes", **common)
                else:
                    log(f"main: '{candidate['name']}' descartada, /editor/add/{found['slug']} -> {state}")
                    insert_store(conn, simplycodes_slug=found["slug"], status=f"rejected_{state}", **common)
                continue

            badge = simplycodes.read_badge(driver)
            popularity, needing_codes, coin_rate = simplycodes.read_popularity(driver)
        finally:
            if _tab_open:
                try:
                    driver.close_tab()
                except Exception:
                    pass
        sid = insert_store(
            conn,
            simplycodes_slug=found["slug"],
            simplycodes_name=found["name"],
            badge=badge,
            popularity=popularity,
            needing_codes=needing_codes,
            coin_rate=coin_rate,
            **common,
        )
        store = dict(conn.execute("SELECT * FROM stores WHERE id = ?", (sid,)).fetchone())
        log(f"main: '{candidate['name']}' persistida, corriendo flujo entero", level="ok")
        _pipeline(driver, conn, store, stop_event, stats)
        if target and count_completed(conn, since_run=run_start) >= target:
            return True

    return False


def archive_csv(csv_path: str, dest_dir: str) -> str:
    """Copia el CSV exportado a la carpeta elegida en el launcher, con la
    fecha en el nombre — así cada corrida deja su propio archivo en vez de
    pisar el anterior. Sin carpeta configurada, se queda donde estaba."""
    if not dest_dir:
        return csv_path
    dest = Path(dest_dir)
    try:
        dest.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        final = dest / f"export_{stamp}.csv"
        shutil.copyfile(csv_path, final)
        return str(final)
    except OSError as e:
        log(f"main: no pude copiar el CSV a {dest_dir!r} ({e}), queda en {csv_path}", level="warn")
        return csv_path


def main():
    # los defaults salen del launcher web (config.json); los flags de linea
    # de comandos siguen andando y pisan la config.
    cfg = config.load()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stop-after", type=int, default=cfg["stop_after"],
        help="cortar solo, sin ESC, al juntar esta cantidad de tiendas completadas (cupón subido)",
    )
    parser.add_argument("--csv-dir", default=cfg["csv_dir"], help="carpeta donde dejar una copia fechada del CSV")
    parser.add_argument(
        "--retry-pending", action="store_true",
        help="no descubre tiendas nuevas: solo reintenta las pending_verification/coupon_failed/etc. y sale",
    )
    parser.add_argument(
        "--no-hud", action="store_true",
        help="no abrir el tablero desktop (tiempo + intentos siempre visibles)",
    )
    args = parser.parse_args()

    reset_errors()
    _snap_console_right_half()
    conn = get_conn()

    log("main: conectando a la ventana de Chrome ya abierta...")
    driver = ChromeDriver()

    stop_event = threading.Event()
    driver.stop_event = stop_event
    blockers.stop_event = stop_event
    threading.Thread(target=_stop_listener, args=(stop_event,), daemon=True).start()

    goaffpro.login(driver)
    stats = {
        "t0": time.monotonic(),
        "attempted": 0,
        "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }

    if not args.no_hud:
        try:
            subprocess.Popen(
                [sys.executable, str(Path(__file__).parent / "hud.py")],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                cwd=str(Path(__file__).parent),
            )
            log("main: tablero en pantalla (tiempo + intentos siempre visibles, cerralo con la X)")
        except Exception as e:
            log(f"main: no pude abrir el tablero ({e}), sigo solo con el título de la consola", level="warn")

    if args.retry_pending:
        log("main: --retry-pending, reintento pendientes y salgo (sin descubrir tiendas nuevas)")
        retry_pending(driver, conn, stop_event, stats)
        log("main: exportando CSV...")
        csv_path = archive_csv(export_csv(conn), args.csv_dir)
        log(f"main: CSV listo en {csv_path}")
        _save_stopped(stats)
        return

    log(f"main: arrancando, {'sin tope' if not args.stop_after else f'meta {args.stop_after} tiendas completadas'} (ESC/Ctrl+Alt+F12 para cortar)")
    run_start = datetime.datetime.now().isoformat(timespec="seconds")
    stats["run_start"] = run_start
    _save_status(conn, stats)
    try:
        while not stop_event.is_set():
            log(f"main: [{_elapsed(stats)}] === escaneando Goaffpro ({count_completed(conn, since_run=run_start)} completadas, {stats['attempted']} intentos) ===")
            _save_status(conn, stats)
            retry_pending(driver, conn, stop_event, stats)
            reached_target = process_new(driver, conn, args.stop_after, stop_event, run_start, stats)
            if reached_target:
                log(f"main: llegué a --stop-after={args.stop_after} tiendas completadas (cupón subido), corto")
                break
            if not stop_event.is_set():
                log("main: agoté el catálogo de Goaffpro sin llegar a la meta, reescaneo desde la página 1")
    except StopRequested:
        log("main: corte pedido durante una espera del driver, exporto y salgo", level="warn")

    log("main: exportando CSV...")
    csv_path = archive_csv(export_csv(conn), args.csv_dir)
    log(f"main: CSV listo en {csv_path}")

    log(f"=== RESUMEN FINAL === ({_elapsed(stats)} corriendo, {stats['attempted']} intentos de carga, {count_completed(conn, since_run=run_start)} completadas en esta run) ===")
    for row in conn.execute("SELECT name, status, affiliate_code, badge FROM stores"):
        log(f"  {row['name']}: {row['status']} | código {row['affiliate_code']} | badge {row['badge']}")
    _save_stopped(stats)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Un crash acá (ej. no conecta con Chrome) cerraba la consola sin dejar
        # rastro y el launcher lo mostraba como "terminó ok". Ahora queda en
        # errors.log y la consola espera para poder leerlo.
        _save_stopped()
        log(f"main: la corrida se cortó por un error:\n{traceback.format_exc()}", level="error")
        input("\nERROR. Revisá 'Error logs' en el launcher. ENTER para cerrar...")
        raise

"""
Cruza Goaffpro (tiendas con acceso instantáneo) y Simplycodes (directorio de
cupones), se afilia a cada match y carga el cupón generado. Persiste toda
tienda que matchee en ambos sitios; badge/coin_rate de SimplyCodes se guardan
solo para ordenar el CSV (mejores arriba), no filtran.

El script controla la ventana de Chrome que YA tenés abierta (con tu sesión
de simplycodes.com logueada) vía Windows UI Automation — no usa CDP, no la
cierra, no abre una nueva. Ver docs/GOAL.md.

Corre en tandas en loop hasta que apretás ESC, hasta --max-batches tandas,
o hasta juntar --stop-after cupones subidos — lo que pase primero. En cada
tanda descubre hasta --batch-size (default 10) tiendas nuevas y corre el
flujo COMPLETO por cada una antes de pasar a la siguiente:
goaffpro enroll -> leer código/descuento -> subir cupón en Simplycodes ->
método de pago en goaffpro. Al cortar exporta export.csv. Ver README.md.

Las tiendas que quedan pending_verification/coupon_failed NO se reintentan
solas: correr aparte `python main.py --retry-pending`.

Correr:
    python main.py [--batch-size 10] [--max-batches N] [--stop-after N]
    python main.py --retry-pending
"""

import argparse
import datetime
import shutil
import threading
import traceback
from pathlib import Path

import config
import goaffpro
import simplycodes
from db import already_seen, count_completed, export_csv, get_conn, insert_store, pending_stores, update_store
from log import log, reset_errors
from winchrome import ChromeDriver

BATCH = 10
Path("screenshots").mkdir(exist_ok=True)


STOP_FLAG = Path(__file__).parent / "STOP"


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


def _pipeline(driver, conn, store: dict, stop_event: threading.Event):
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

            log(f"main: [{name}] subiendo cupón en Simplycodes")
            simplycodes.add_coupon(driver, store["simplycodes_slug"], store)
            update_store(conn, store["id"], status=store["status"], badge=store.get("badge"))
            store["status"] = "coupon_submitted"
            log(f"main: [{name}] LISTO -> código {store['affiliate_code']} ({store.get('badge', 'sin badge')})", level="ok")

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
    except Exception:
        log(f"main: [{name}] ERROR:\n{traceback.format_exc()}", level="error")
        # Si ya hay código, lo que falló es la carga del cupón: 'coupon_failed'
        # (no 'enroll_failed', que la sacaría de los reintentos para siempre).
        fail_status = "coupon_failed" if store.get("affiliate_code") else "enroll_failed"
        update_store(conn, store["id"], status=fail_status)
        log(f"main: [{name}] marcada '{fail_status}'", level="warn")


def retry_pending(driver, conn, stop_event: threading.Event):
    """Reintenta las tiendas que quedaron a medias en corridas anteriores —
    sobre todo pending_verification (para que el merchant haya tenido tiempo
    de generar el código). NO se llama sola: correr `python main.py
    --retry-pending`."""
    rows = pending_stores(conn, "discovered", "enrolled", "coupon_failed", "pending_verification")
    if not rows:
        return
    log(f"=== reintentando {len(rows)} tiendas pendientes ===")
    for row in rows:
        if stop_event.is_set():
            log("main: ESC detectado, corto los reintentos")
            break
        _pipeline(driver, conn, dict(row), stop_event)


def process_new(driver, conn, count: int, stop_event: threading.Event):
    """Por cada candidata NUEVA de Goaffpro corre el flujo entero antes de
    pasar a la siguiente. `count` = tope de tiendas nuevas persistidas por
    tanda."""
    log(f"=== descubrir + procesar hasta {count} tiendas nuevas ===")
    done = 0
    for candidate in goaffpro.iter_instant_access_candidates(driver, conn, stop_event):
        if stop_event.is_set() or done >= count:
            break
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
        log(f"main: revisando '{candidate['name']}' en Simplycodes...")
        found = simplycodes.find_store(driver, candidate["name"], candidate["domain"])
        if not found:
            log(f"main: '{candidate['name']}' descartada, no está en Simplycodes")
            insert_store(conn, simplycodes_slug=None, status="rejected_no_simplycodes", **common)
            continue

        state = simplycodes.open_editor(driver, found["slug"], found["name"])
        if state != "ok":
            log(f"main: '{candidate['name']}' descartada, /editor/add/{found['slug']} -> {state}")
            insert_store(conn, simplycodes_slug=found["slug"], status=f"rejected_{state}", **common)
            continue

        badge = simplycodes.read_badge(driver)
        sid = insert_store(
            conn,
            simplycodes_slug=found["slug"],
            simplycodes_name=found["name"],
            badge=badge,
            **common,
        )
        store = dict(conn.execute("SELECT * FROM stores WHERE id = ?", (sid,)).fetchone())
        done += 1
        log(f"main: '{candidate['name']}' persistida ({done}/{count}), corriendo flujo entero", level="ok")
        _pipeline(driver, conn, store, stop_event)


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
    parser.add_argument("--batch-size", type=int, default=cfg["batch_size"] or BATCH, help="tiendas a buscar/cruzar por tanda")
    parser.add_argument("--max-batches", type=int, default=cfg["max_batches"], help="tope de tandas (si no se pasa, no hay tope)")
    parser.add_argument(
        "--stop-after", type=int, default=cfg["stop_after"],
        help="cortar solo, sin ESC, al juntar esta cantidad de tiendas persistidas",
    )
    parser.add_argument("--csv-dir", default=cfg["csv_dir"], help="carpeta donde dejar una copia fechada del CSV")
    parser.add_argument(
        "--retry-pending", action="store_true",
        help="no descubre tiendas nuevas: solo reintenta las pending_verification/coupon_failed/etc. y sale",
    )
    args = parser.parse_args()

    reset_errors()
    conn = get_conn()

    log("main: conectando a la ventana de Chrome ya abierta...")
    driver = ChromeDriver()

    stop_event = threading.Event()
    threading.Thread(target=_stop_listener, args=(stop_event,), daemon=True).start()

    goaffpro.login(driver)

    if args.retry_pending:
        log("main: --retry-pending, reintento pendientes y salgo (sin descubrir tiendas nuevas)")
        retry_pending(driver, conn, stop_event)
        log("main: exportando CSV...")
        csv_path = archive_csv(export_csv(conn), args.csv_dir)
        log(f"main: CSV listo en {csv_path}")
        return

    log(f"main: arrancando, tandas de {args.batch_size}, ESC para cortar")
    batch_num = 0
    while not stop_event.is_set():
        batch_num += 1
        log(f"main: === TANDA {batch_num} ({count_completed(conn)} completadas hasta ahora) ===")
        process_new(driver, conn, args.batch_size, stop_event)
        if args.stop_after and count_completed(conn) >= args.stop_after:
            log(f"main: llegué a --stop-after={args.stop_after} tiendas completadas (cupón subido), corto")
            break
        if args.max_batches and batch_num >= args.max_batches:
            log(f"main: llegué a --max-batches={args.max_batches}, corto")
            break
        if not stop_event.is_set():
            log(f"main: tanda completa, sigo buscando {args.batch_size} más (ESC para cortar)")

    log("main: exportando CSV...")
    csv_path = archive_csv(export_csv(conn), args.csv_dir)
    log(f"main: CSV listo en {csv_path}")

    log("=== RESUMEN FINAL ===")
    for row in conn.execute("SELECT name, status, affiliate_code, badge FROM stores"):
        log(f"  {row['name']}: {row['status']} | código {row['affiliate_code']} | badge {row['badge']}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Un crash acá (ej. no conecta con Chrome) cerraba la consola sin dejar
        # rastro y el launcher lo mostraba como "terminó ok". Ahora queda en
        # errors.log y la consola espera para poder leerlo.
        log(f"main: la corrida se cortó por un error:\n{traceback.format_exc()}", level="error")
        input("\nERROR. Revisá 'Error logs' en el launcher. ENTER para cerrar...")
        raise

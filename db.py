import csv
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goaffpro_store_id TEXT,
    name TEXT NOT NULL,
    domain TEXT,
    simplycodes_slug TEXT,
    status TEXT NOT NULL DEFAULT 'discovered',
    merchant_email TEXT,
    merchant_password TEXT,
    affiliate_code TEXT,
    discount_type TEXT,
    discount_value TEXT,
    discount_scope TEXT,
    badge TEXT,
    dashboard_screenshot_path TEXT,
    affiliate_portal TEXT,
    affiliate_portal_signup TEXT,
    registrations_opens TEXT,
    approved_automatically TEXT,
    cookie_duration TEXT,
    currency TEXT,
    goaffpro_commission TEXT,
    simplycodes_name TEXT,
    coin_rate TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

CSV_COLUMNS = [
    "STORE_NAME", "STORE_DOMAIN", "AFFILIATE_PORTAL", "AFFILIATE_PORTAL_SIGNUP",
    "REGISTRATIONS_OPENS", "APPROVED_AUTOMATICALLY", "COOKIE_DURATION", "CURRENCY",
    "COMISSION_TYPE", "COMISSION_AMOUNT", "COMISSION_ON", "STORE_LINK_SIMPLY",
    "STORE_NAME_SIMPLY", "EDITOR_ADD_SIMPLY", "POPULARITY", "NEEDING_CODES", "BADGE_AVAILABLE",
]

_BADGE_RANK = {"gold": 3, "silver": 2, "bronze": 1}
_TIER_RANK = {"high": 3, "medium": 2, "low": 1}


_NEW_COLUMNS = [
    "affiliate_portal", "affiliate_portal_signup", "registrations_opens",
    "approved_automatically", "cookie_duration", "currency",
    "goaffpro_commission", "simplycodes_name", "coin_rate", "goaffpro_page",
    "payment_method",
]


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(stores)")}
    for col in _NEW_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE stores ADD COLUMN {col} TEXT")
    conn.commit()
    return conn


# ponytail: sin uso desde que goaffpro.py dejó de retomar la paginación por
# tanda (Available Stores no es append-only). Se dejan por si vuelve a hacer
# falta persistir estado simple key/value; la tabla `state` queda vacía.
def get_state(conn, key: str, default):
    row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return int(row["value"]) if row else default


def set_state(conn, key: str, value):
    conn.execute(
        "INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


def count_completed(conn) -> int:
    """Tiendas con el flujo terminado de verdad (cupón subido a Simplycodes).
    No cuenta pending_verification/enroll_failed/coupon_failed — esos son
    intentos que no llegaron a destino, --stop-after no debe darlos por
    buenos."""
    return conn.execute("SELECT COUNT(*) FROM stores WHERE status = 'coupon_submitted'").fetchone()[0]


def already_seen(conn, goaffpro_store_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM stores WHERE goaffpro_store_id = ?", (str(goaffpro_store_id),)
    ).fetchone() is not None


def insert_store(conn, goaffpro_store_id: str, name: str, domain: str, simplycodes_slug: str, **extra) -> int:
    fields = {
        "goaffpro_store_id": goaffpro_store_id,
        "name": name,
        "domain": domain,
        "simplycodes_slug": simplycodes_slug,
        **extra,
    }
    cols = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    cur = conn.execute(f"INSERT INTO stores ({cols}) VALUES ({placeholders})", tuple(fields.values()))
    conn.commit()
    return cur.lastrowid


def update_store(conn, store_id: int, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE stores SET {cols} WHERE id = ?", (*fields.values(), store_id))
    conn.commit()


def pending_stores(conn, *statuses: str):
    placeholders = ", ".join("?" for _ in statuses)
    return conn.execute(f"SELECT * FROM stores WHERE status IN ({placeholders})", statuses).fetchall()


def _commission_pct(value: str) -> float:
    try:
        return float((value or "0").replace("%", "").strip())
    except ValueError:
        return 0.0


def export_csv(conn, path: str = "export.csv"):
    """Vuelca todas las tiendas persistidas al CSV pedido en
    docs/Servicios_SimplyCodes_Scrapping — ordenado con las mejores
    (mayor comisión, mejor badge, mejor popularidad) arriba."""
    rows = conn.execute(
        "SELECT * FROM stores WHERE status NOT LIKE 'rejected_%'"
    ).fetchall()
    rows = sorted(
        rows,
        key=lambda r: (
            _commission_pct(r["goaffpro_commission"]),
            _BADGE_RANK.get((r["badge"] or "").lower(), 0),
            _TIER_RANK.get((r["coin_rate"] or "").lower(), 0),
        ),
        reverse=True,
    )
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for r in rows:
            slug = r["simplycodes_slug"] or ""
            writer.writerow([
                r["name"],
                r["domain"],
                r["affiliate_portal"],
                r["affiliate_portal_signup"],
                r["registrations_opens"],
                r["approved_automatically"],
                r["cookie_duration"],
                r["currency"],
                r["discount_type"],
                r["discount_value"],
                r["goaffpro_commission"],
                f"https://simplycodes.com/{slug}" if slug else "",
                r["simplycodes_name"],
                f"https://simplycodes.com/editor/add/{slug}" if slug else "",
                r["coin_rate"],
                "",  # NEEDING_CODES: sin fuente confirmada, queda vacío
                r["badge"],
            ])
    return path

"""Config editable desde el launcher web (`webui.py`), persistida en
`config.json`.

Antes los datos del perfil estaban hardcodeados en `profile_data.py` y las
credenciales solo en `.env`: el cliente final no edita archivos .py ni .env,
así que todo lo que puede cambiar entre corridas vive acá y se escribe desde
la web.

`.env` sigue siendo el default de las credenciales (compatibilidad con las
corridas actuales); si `config.json` trae valor, ese gana.

ponytail: JSON plano, sin esquema ni validación de tipos más allá de los
ints de las tandas — un archivo de config de 15 claves no necesita más.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

CONFIG_PATH = Path(__file__).parent / "config.json"

DEFAULTS = {
    # credenciales Goaffpro (afiliado)
    "goaffpro_email": os.environ.get("GOAFFPRO_EMAIL", ""),
    "goaffpro_password": os.environ.get("GOAFFPRO_PASSWORD", ""),
    # perfil fijo para los forms de los portales de merchant
    "first_name": "Tomas",
    "last_name": "Rios",
    "phone": "1134083120",
    "country": "Argentina",
    "state": "Buenos Aires",
    "city": "Florencio Varela",
    # método de pago por comisión en el portal de afiliado
    "paypal_email": "",
    # corrida
    "batch_size": 10,
    "max_batches": "",
    "stop_after": "",
    "manual_screenshots": False,
    "csv_dir": "",
}

_INTS = ("batch_size", "max_batches", "stop_after")


def _as_int(value):
    """'' / None / basura -> None. El form web manda strings vacíos cuando
    el campo queda en blanco, y esos significan 'sin tope'."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except ValueError:
            saved = {}
        cfg.update({k: v for k, v in saved.items() if k in DEFAULTS})
    # un campo vacío en config.json no debe pisar lo que hay en .env
    for key in ("goaffpro_email", "goaffpro_password"):
        if not str(cfg.get(key) or "").strip():
            cfg[key] = DEFAULTS[key]
    for key in _INTS:
        cfg[key] = _as_int(cfg[key])
    cfg["manual_screenshots"] = bool(cfg["manual_screenshots"])
    return cfg


def save(data: dict) -> dict:
    """Guarda solo las claves conocidas; lo que no venga queda en su default."""
    cfg = {k: data.get(k, v) for k, v in DEFAULTS.items()}
    for key in _INTS:
        cfg[key] = _as_int(cfg[key])
    cfg["manual_screenshots"] = bool(cfg["manual_screenshots"])
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    return cfg


def demo():
    assert _as_int("") is None and _as_int(None) is None and _as_int("x") is None
    assert _as_int(" 7 ") == 7 and _as_int(7) == 7
    cfg = load()
    assert set(cfg) == set(DEFAULTS)
    assert cfg["batch_size"] is None or isinstance(cfg["batch_size"], int)
    print("config: ok")


if __name__ == "__main__":
    demo()

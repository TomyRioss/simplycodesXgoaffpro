"""Perfil fijo y credenciales, leídos de `config.json` (ver config.py).

Los valores se toman al importar: el launcher web escribe la config ANTES de
lanzar main.py, así que una corrida siempre usa lo que se cargó en la
pantalla de inicio.
"""

import config

_CFG = config.load()

PROFILE = {
    "first_name": _CFG["first_name"],
    "last_name": _CFG["last_name"],
    "full_name": f"{_CFG['first_name']} {_CFG['last_name']}".strip(),
    "phone": _CFG["phone"],
    "email": _CFG["goaffpro_email"],
    "country": _CFG["country"],
    "state": _CFG["state"],
    "city": _CFG["city"],
}

GOAFFPRO_EMAIL = _CFG["goaffpro_email"]
GOAFFPRO_PASSWORD = _CFG["goaffpro_password"]
PAYPAL_EMAIL = _CFG["paypal_email"]
MANUAL_SCREENSHOTS = _CFG["manual_screenshots"]

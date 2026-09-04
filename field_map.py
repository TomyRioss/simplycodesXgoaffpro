"""Traduce el label de un input de un form desconocido a un campo del perfil
fijo.

Los portales de los merchants son el mismo template de Goaffpro pero
servidos en el idioma del comercio, con los campos en distinto orden
(inglés: Name/Email/Password; español: Email/Contraseña/Nombre — ambos
confirmados en vivo). Por eso el form se llena por label y no por posición,
y por eso el label hay que poder interpretarlo en cualquier idioma.

Primero se prueba una tabla de sinónimos (determinista, sin red). Solo si el
label no matchea nada se consulta DeepSeek como último recurso antes de
frenar y preguntarle al usuario — cada label desconocido es, hoy, una tienda
perdida.
"""

import json
import os
import re
import urllib.error
import urllib.request

from log import log

PROFILE_FIELDS = (
    "name", "first_name", "last_name", "email", "phone", "password",
    "country", "state", "city",
)

# Sinónimos por campo. Se matchea por substring sobre el label normalizado,
# probando primero los campos más específicos (first/last name antes que
# name, que si no se come "First name").
_SYNONYMS = [
    ("first_name", ("first name", "firstname", "given name", "nombre de pila", "prenom", "prénom", "vorname")),
    ("last_name", ("last name", "lastname", "surname", "family name", "apellido", "nom de famille", "nachname")),
    ("email", ("email", "e-mail", "correo", "mail", "courriel")),
    ("password", ("password", "contrasena", "contraseña", "clave", "mot de passe", "passwort")),
    ("phone", ("phone", "mobile", "telefono", "teléfono", "celular", "whatsapp", "telefone", "handy")),
    # país/provincia/ciudad van ANTES de "name": "Nom de la ville" y
    # "Nombre de la ciudad" contienen "nom"/"nombre" y caían en el campo
    # equivocado. Antes estos labels no se reconocían y el flujo frenaba a
    # preguntar en cada portal que los pedía.
    ("country", ("country", "pais", "país", "pays", "land", "nazione", "paese")),
    ("state", ("state", "province", "provincia", "estado", "región", "region", "departamento", "bundesland")),
    ("city", ("city", "ciudad", "town", "ville", "localidad", "stadt", "città", "citta")),
    ("name", ("full name", "your name", "name", "nombre", "nom", "razon social", "razón social")),
]


def _norm(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip().lower()).strip(" *:·-")


def match_field(label: str) -> str | None:
    """Campo del perfil que corresponde a `label`, o None si no se reconoce."""
    low = _norm(label)
    if not low:
        return None
    for field, needles in _SYNONYMS:
        if any(n in low for n in needles):
            return field
    return None


# --------------------------------------------------------------------------
# fallback opcional con DeepSeek
# --------------------------------------------------------------------------

_API_URL = "https://api.deepseek.com/v1/chat/completions"
_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

_PROMPT = (
    "Sos un clasificador. Te doy la etiqueta de un campo de un formulario de registro "
    "de afiliados (puede estar en cualquier idioma). Respondé SOLO con una de estas "
    "palabras exactas, sin explicar nada:\n"
    + "\n".join(PROFILE_FIELDS)
    + "\nunknown\n\n"
    "Respondé 'unknown' si la etiqueta no corresponde claramente a ninguno "
    "(por ejemplo: dirección, país, empresa, sitio web, CUIT, código postal).\n\n"
    "Etiqueta: "
)


def guess_field(label: str) -> str | None:
    """Último recurso: le pregunta a DeepSeek a qué campo del perfil
    corresponde un label que la tabla de sinónimos no reconoció.

    Devuelve None si no hay API key, si falla la llamada, o si el modelo no
    está seguro — en ese caso el flujo frena y le pregunta al usuario, que
    es la regla del proyecto. Nunca inventa el valor del campo: solo decide
    a cuál de los datos del perfil fijo corresponde la etiqueta.

    ponytail: sin reintentos ni librería de cliente; una llamada, timeout
    corto, y si falla se sigue como si no existiera."""
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None

    payload = json.dumps(
        {
            "model": _MODEL,
            "messages": [{"role": "user", "content": _PROMPT + _norm(label)}],
            "temperature": 0,
            # deepseek-v4-flash razona antes de contestar y ese razonamiento
            # descuenta de max_tokens: con un presupuesto chico la respuesta
            # llega vacía (finish_reason "length", content "") y todo label
            # quedaba sin reconocer.
            "max_tokens": 512,
        }
    ).encode()
    req = urllib.request.Request(
        _API_URL,
        data=payload,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
        content = (body["choices"][0]["message"].get("content") or "").strip().lower()
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError) as e:
        log(f"field_map: DeepSeek no respondió para {label!r} ({type(e).__name__}), sigo sin él", level="warn")
        return None

    # se toma la última palabra: el modelo a veces envuelve la respuesta
    # ("El campo es: phone"), y la etiqueta pedida siempre queda al final.
    words = re.findall(r"[a-z_]+", content)
    answer = words[-1] if words else ""

    if answer in PROFILE_FIELDS:
        log(f"field_map: DeepSeek mapeó {label!r} -> {answer}")
        return answer
    log(f"field_map: DeepSeek no reconoció {label!r} (respondió {answer!r})", level="warn")
    return None


def demo():
    assert match_field("Nombre") == "name"
    assert match_field("Contraseña *") == "password"
    assert match_field("E-Mail") == "email"
    assert match_field("First Name") == "first_name"
    assert match_field("Last name") == "last_name"
    assert match_field("Teléfono") == "phone"
    assert match_field("País") == "country"
    assert match_field("Estado/Provincia *") == "state"
    assert match_field("Ciudad") == "city"
    assert match_field("Country") == "country"
    # labels que contienen "nom"/"nombre" pero NO son el nombre de la persona
    assert match_field("Nom de la ville") == "city"
    assert match_field("Nombre de la ciudad") == "city"
    assert match_field("") is None
    # "First name" no debe caer en "name"
    assert match_field("First name") != "name"
    print("field_map: ok")


if __name__ == "__main__":
    demo()

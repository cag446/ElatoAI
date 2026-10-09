#!/usr/bin/env python3
"""Administra las claves por dispositivo del bridge de voz.

Cada aparato que habla con el bridge (el ESP32 del parlante, el Cardputer, los
que se sumen) tiene su PROPIA clave. El bridge guarda solo el SHA-256 de cada
una, en un archivo FUERA de los repos:

    ~/.config/hermes-bridge/devices.json   (o $BRIDGE_DEVICES_FILE)

Uso (en la Mac mini):

    python3 devices_admin.py add esp32-parlante   # crea la clave y la muestra UNA vez
    python3 devices_admin.py add cardputer --tecleable  # clave facil de tipear a mano
    python3 devices_admin.py list                 # aparatos registrados
    python3 devices_admin.py revoke esp32-parlante  # la desactiva (el bridge la rechaza)
    python3 devices_admin.py enable esp32-parlante  # la reactiva
    python3 devices_admin.py delete esp32-parlante  # la borra del registro

La clave se muestra UNA sola vez, al crearla: hay que cargarla en la
configuracion del aparato (como la clave del WiFi). Si se pierde, se crea otra
con `delete` + `add`. El bridge relee el archivo solo cuando cambia: no hace
falta reiniciarlo.

No muestres la clave en chats, logs ni avisos.
"""

import datetime
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile

DEVICES_FILE = os.environ.get(
    "BRIDGE_DEVICES_FILE", os.path.expanduser("~/.config/hermes-bridge/devices.json"))
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
# Formato para aparatos donde la clave se TIPEA (Cardputer): minusculas y
# numeros sin los que se confunden (0/o, 1/l), en grupos de 4. 6 grupos de un
# alfabeto de 32 = 120 bits, tan fuerte como la normal y mucho mas comoda.
TECLEABLE_ALFABETO = "abcdefghijkmnpqrstuvwxyz23456789"


def clave_tecleable() -> str:
    return "-".join("".join(secrets.choice(TECLEABLE_ALFABETO) for _ in range(4)) for _ in range(6))


def load() -> dict:
    try:
        with open(DEVICES_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    data.setdefault("devices", {})
    return data


def save(data: dict) -> None:
    """Escritura atomica, con permisos 700 en el directorio y 600 en el archivo."""
    d = os.path.dirname(DEVICES_FILE)
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".devices-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, DEVICES_FILE)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def cmd_add(name: str, tecleable: bool = False) -> int:
    if not NAME_RE.match(name):
        print("Nombre invalido: minusculas, numeros y guiones (ej: esp32-parlante).")
        return 2
    data = load()
    if name in data["devices"]:
        print("'%s' ya existe. Para darle una clave nueva: delete + add." % name)
        return 1
    token = clave_tecleable() if tecleable else secrets.token_urlsafe(24)
    data["devices"][name] = {
        "sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "created": datetime.date.today().isoformat(),
        "enabled": True,
    }
    save(data)
    print("Dispositivo '%s' registrado." % name)
    print()
    print("  CLAVE:  %s" % token)
    print()
    print("Cargala en la configuracion del aparato. Se muestra UNA sola vez:")
    print("no queda guardada en ningun lado (solo su hash). No la pegues en chats.")
    return 0


def cmd_list() -> int:
    devs = load()["devices"]
    if not devs:
        print("No hay dispositivos registrados en %s" % DEVICES_FILE)
        return 0
    for name, info in sorted(devs.items()):
        print("%-24s %-10s creado %s" % (
            name, "ACTIVO" if info.get("enabled", True) else "REVOCADO", info.get("created", "?")))
    return 0


def cmd_set_enabled(name: str, enabled: bool) -> int:
    data = load()
    if name not in data["devices"]:
        print("No existe '%s'." % name)
        return 1
    data["devices"][name]["enabled"] = enabled
    save(data)
    print("'%s' %s." % (name, "reactivado" if enabled else "revocado: el bridge lo rechaza"))
    return 0


def cmd_delete(name: str) -> int:
    data = load()
    if data["devices"].pop(name, None) is None:
        print("No existe '%s'." % name)
        return 1
    save(data)
    print("'%s' borrado del registro." % name)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "list":
        return cmd_list()
    if len(argv) == 4 and argv[1] == "add" and argv[3] == "--tecleable":
        return cmd_add(argv[2], tecleable=True)
    if len(argv) == 3:
        cmd, name = argv[1], argv[2]
        if cmd == "add":
            return cmd_add(name)
        if cmd == "revoke":
            return cmd_set_enabled(name, False)
        if cmd == "enable":
            return cmd_set_enabled(name, True)
        if cmd == "delete":
            return cmd_delete(name)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

"""Inyecta la IP del puente Hermes desde la variable de entorno HERMES_SERVER_IP.

Evita tener la IP de una red domestica escrita en el repo. Si la variable no
esta definida se usa el valor por defecto de Config.cpp (un placeholder), y el
build avisa por consola.

    HERMES_SERVER_IP=192.168.1.100 pio run -t upload
"""

import os

Import("env")  # noqa: F821  (lo inyecta PlatformIO)

ip = os.environ.get("HERMES_SERVER_IP", "").strip()

if ip:
    stringify = getattr(env, "StringifyMacro", None)  # noqa: F821
    quoted = stringify(ip) if stringify else '\\"%s\\"' % ip
    env.Append(CPPDEFINES=[("HERMES_SERVER_IP", quoted)])  # noqa: F821
    print("[hermes] IP del puente: %s (desde HERMES_SERVER_IP)" % ip)
else:
    print(
        "[hermes] AVISO: HERMES_SERVER_IP no esta definida; se usa el "
        "placeholder de Config.cpp. El firmware NO va a encontrar el puente."
    )

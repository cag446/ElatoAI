#!/usr/bin/env python3
"""Prueba enfocada de los fixes §4.1 y §4.3 del informe de mejora 2026-09-22.

No usa audio ni red: instancia una Session con un WebSocket de mentira y maneja
la maquina de estados a mano. Corre en segundos y falla fuerte si la logica se
rompe.

Uso:  cd ~/Proyectos/experimentos/hermes-bridge && .venv/bin/python3 tests/test_barge_state.py
"""
import asyncio, os, pathlib, sys

os.environ.setdefault("HERMES_API_KEY", "dummy-para-import")
REPO = pathlib.Path(__file__).resolve().parent.parent
if (REPO / "bridge.py").exists():
    sys.path.insert(0, str(REPO))
else:
    sys.path.insert(0, str(pathlib.Path.home() / "Proyectos/experimentos/hermes-bridge"))

import bridge

FALLOS = []

def check(nombre, cond, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + nombre + (("  " + extra) if extra else ""))
    if not cond:
        FALLOS.append(nombre)

class FakeWS:
    def __init__(self): self.sent = []
    async def send_str(self, s): self.sent.append(s)
    async def send_bytes(self, b): self.sent.append(b)

def nueva_session():
    ws = FakeWS()
    s = bridge.Session.__new__(bridge.Session)      # sin __init__: evita VoiceHistory/sqlite
    s.ws = ws
    s.speaking = False
    s.speak_gen = 0
    s.barge_event = asyncio.Event()
    s.barge_src = ""
    s.barge_buf = bytearray()
    s.pre_buffer = b""
    s.cancel_pending = False
    s.cancel_src = ""
    s.turn_task = None
    s.turn_tasks = set()
    s.speak_task = None
    s.probe_speech = []
    return s, ws

async def main():
    print("=== §4.1 — el barge por BOTON conserva el audio del usuario ===")
    s, ws = nueva_session()
    s.speaking = True
    s.barge_buf = bytearray(b"\x11\x22" * 8000)      # 32000 B = 1 s a 16 kHz mono
    n = len(s.barge_buf)
    s.barge_src = "device"                            # <- boton
    s.barge_event.set()
    await s._handle_barge()
    check("boton: el audio se reencola en pre_buffer (antes se descartaba)",
          len(s.pre_buffer) == n, f"pre_buffer={len(s.pre_buffer)} B de {n} B")
    check("boton: no se reenvia BARGE al device (no ping-pong)",
          not any("BARGE" in str(m) for m in ws.sent))
    check("boton: speaking apagado", s.speaking is False)
    check("boton: barge_buf vaciado", len(s.barge_buf) == 0)

    print("=== §4.1 — la voz sigue conservando (no hubo regresion) ===")
    s, ws = nueva_session()
    s.speaking = True
    s.barge_buf = bytearray(b"\x33\x44" * 5000)
    n = len(s.barge_buf)
    s.barge_src = "device-voice"
    s.barge_event.set()
    await s._handle_barge()
    check("voz: el audio se reencola", len(s.pre_buffer) == n)
    check("voz: no se reenvia BARGE", not any("BARGE" in str(m) for m in ws.sent))

    print("=== §4.1 — barge del SERVER si manda BARGE al device ===")
    s, ws = nueva_session()
    s.speaking = True
    s.barge_src = "server"
    s.barge_event.set()
    await s._handle_barge()
    check("server: manda BARGE al device", any("BARGE" in str(m) for m in ws.sent))

    print("=== §4.3 — BARGE durante el STT (speaking aun False) ===")
    s, ws = nueva_session()
    async def turno_largo(): await asyncio.sleep(5)
    t = asyncio.create_task(turno_largo())
    s.turn_task = t
    s.speaking = False                                # estamos en STT
    s.request_barge("device")
    check("hay turno en curso -> se marca cancel_pending", s.cancel_pending is True)
    check("se guarda el origen", s.cancel_src == "device")
    check("NO se setea barge_event (eso es para SPEAKING)", not s.barge_event.is_set())
    await s._abort_pending_turn("texto que se descarta")
    check("abortar limpia la bandera", s.cancel_pending is False and s.cancel_src == "")
    check("origen device -> NO se manda BARGE (ya volvio a LISTENING)",
          not any("BARGE" in str(m) for m in ws.sent))
    t.cancel()

    print("=== §4.3 — sin turno en curso se sigue ignorando ===")
    s, ws = nueva_session()
    s.speaking = False
    s.turn_task = None
    s.request_barge("device")
    check("sin turno -> no se marca nada", s.cancel_pending is False)

    print("=== §4.3 — turno YA terminado tampoco marca ===")
    s, ws = nueva_session()
    async def nada(): return
    t = asyncio.create_task(nada()); await t
    s.turn_task = t
    s.speaking = False
    s.request_barge("device")
    check("turno done -> no se marca", s.cancel_pending is False)

    print("=== §4.3 — un barge real limpia el cancel pendiente ===")
    s, ws = nueva_session()
    s.cancel_pending = True
    s.cancel_src = "device"
    s.speaking = True
    s.barge_src = "device"
    s.barge_event.set()
    await s._handle_barge()
    check("_handle_barge limpia cancel_pending", s.cancel_pending is False)

    print()
    if FALLOS:
        print(f"!! {len(FALLOS)} FALLO(S): " + ", ".join(FALLOS))
        return 1
    print("TODO OK")
    return 0

sys.exit(asyncio.run(main()))

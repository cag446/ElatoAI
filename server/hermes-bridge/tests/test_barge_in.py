#!/usr/bin/env python3
"""Cliente de prueba del barge-in (Fase A: A.1 / A.2 / A.3 / A.4).

Hace de ESP32 contra una instancia de PRUEBA del bridge (ver
`tests/run_bridge_isolated.py`, puertos 8100/3100). No toca el bridge de
producción ni el firmware.

Escenario:
  1. Turno normal: manda la frase A (Piper 16 kHz, en tiempo real) + silencio
     hasta que el VAD cierra la frase. Espera AUDIO.COMMITTED / RESPONSE.CREATED.
  2. Con la respuesta sonando, manda audio de la frase B y luego el mensaje de
     texto que mandará el firmware al detectar la interrupción:
         {"type":"server_action","msg":"BARGE"}
  3. Verifica que el bridge corta (deja de mandar Opus) y que la frase B se
     reprocesa como turno nuevo (segundo RESPONSE.CREATED + transcripción).

Uso:
    cd ~/Proyectos/experimentos/hermes-bridge
    .venv/bin/python3 tests/test_barge_in.py                 # ws://127.0.0.1:8100/
    .venv/bin/python3 tests/test_barge_in.py --ws ws://...   # otra instancia

Salida: timeline por stdout + PASS/FAIL por chequeo. Exit code 0 = todo OK.
"""

import argparse
import asyncio
import json
import pathlib
import subprocess
import sys
import tempfile
import time

import aiohttp

REPO = pathlib.Path(__file__).resolve().parent.parent
VOICE = pathlib.Path.home() / "piper-voices" / "es_ES-davefx-medium.onnx"
MIC_RATE = 16000
FRAME_BYTES = MIC_RATE * 30 // 1000 * 2      # 960 B = 30 ms, igual que el VAD del bridge
FRAME_S = 0.030

# Frase A: pide respuesta larga (hace falta que Deb hable varios segundos).
PHRASE_A = ("Contame un cuento largo, de unas diez frases, sobre un robot "
            "que aprende a cocinar asado en Cordoba.")
# Frase B: lo que dice el usuario encima de la respuesta (la interrupción).
PHRASE_B = "Para, para, mejor decime que hora es."


def _synth_pcm(text: str) -> bytes:
    """Texto -> PCM 16 kHz mono s16le (Piper + ffmpeg)."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav = f.name
    subprocess.run(["piper", "--model", str(VOICE), "--output_file", wav],
                   input=text.encode(), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raw = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", wav, "-f", "s16le",
         "-acodec", "pcm_s16le", "-ar", str(MIC_RATE), "-ac", "1", "-"],
        capture_output=True, check=True).stdout
    pathlib.Path(wav).unlink(missing_ok=True)
    return raw


class Timeline:
    def __init__(self):
        self.t0 = time.monotonic()
        self.events = []          # (t_rel, tipo, detalle)
        self.packets = 0
        self.last_packet_t: float | None = None
        self.packet_times: list[float] = []

    def add(self, kind: str, detail: str = ""):
        t = time.monotonic() - self.t0
        self.events.append((round(t, 3), kind, detail))
        print(f"  [{t:6.2f}s] {kind} {detail}", flush=True)

    def mark(self) -> float:
        return time.monotonic() - self.t0


async def reader(ws, tl: Timeline, stop: asyncio.Event):
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    tl.add("TEXT?", msg.data[:80])
                    continue
                if data.get("type") == "server":
                    tl.add(f"SERVER {data.get('msg')}")
                else:
                    tl.add("DEV/OTHER", json.dumps(data)[:80])
            elif msg.type == aiohttp.WSMsgType.BINARY:
                tl.packets += 1
                tl.last_packet_t = time.monotonic() - tl.t0
                tl.packet_times.append(tl.last_packet_t)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        tl.add("READER-ERROR", repr(e))
    finally:
        stop.set()


async def send_pcm(ws, pcm: bytes):
    """Manda PCM en frames de 30 ms, en tiempo real."""
    i = 0
    while i < len(pcm):
        await ws.send_bytes(pcm[i:i + FRAME_BYTES])
        i += FRAME_BYTES
        await asyncio.sleep(FRAME_S)
    return i


async def send_silence(ws, seconds: float):
    await send_pcm(ws, b"\x00" * int(MIC_RATE * 2 * seconds))


async def wait_event(tl: Timeline, kind_pred, timeout: float, start: int = 0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for ev in tl.events[start:]:
            if kind_pred(ev):
                return ev
        await asyncio.sleep(0.05)
    return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ws", default="ws://127.0.0.1:8100/")
    ap.add_argument("--token", default="elato-local-token")
    ap.add_argument("--mac", default="B8:F8:62:D7:59:C4")
    ap.add_argument("--no-barge", action="store_true",
                    help="control: turno normal sin interrupción (regresión)")
    args = ap.parse_args()

    tl = Timeline()
    checks = []

    print("Sintetizando frases de prueba con Piper...", flush=True)
    pcm_a = _synth_pcm(PHRASE_A)
    pcm_b = _synth_pcm(PHRASE_B)
    print(f"  frase A: {len(pcm_a) / 2 / MIC_RATE:.1f}s | frase B: "
          f"{len(pcm_b) / 2 / MIC_RATE:.1f}s", flush=True)

    stop = asyncio.Event()
    async with aiohttp.ClientSession() as http:
        ws = await http.ws_connect(
            args.ws, headers={"Authorization": f"Bearer {args.token}",
                              "X-Device-Mac": args.mac})
        tl.add("CONNECTED", args.ws)
        rt = asyncio.create_task(reader(ws, tl, stop))

        # --- 1. turno normal: frase A ---
        await asyncio.sleep(0.4)
        await send_pcm(ws, pcm_a)
        await send_silence(ws, 1.0)          # cierra la frase (VAD 800 ms)
        n0 = len(tl.events)
        ev_created = await wait_event(
            tl, lambda e: e[1] == "SERVER RESPONSE.CREATED", 90, start=n0)
        checks.append(("A: turno normal arranca la respuesta",
                       ev_created is not None))
        if ev_created is None:
            print("!! sin RESPONSE.CREATED: abortando", flush=True)
            await ws.close()
            rt.cancel()
            return 1
        tl.add("PLAYBACK-START", f"paquetes={tl.packets}")

        if args.no_barge:
            # Control: dejar terminar la respuesta completa sin interrumpir.
            await send_silence(ws, 1.0)
            ev_done = await wait_event(
                tl, lambda e: e[1] == "SERVER RESPONSE.COMPLETE", 180,
                start=len(tl.events))
            checks.append(("control: el turno normal cierra con RESPONSE.COMPLETE",
                           ev_done is not None))
            checks.append(("control: se recibió audio (paquetes Opus)",
                           tl.packets > 10))
            checks.append(("control: ningún BARGE",
                           not any(e[1] == "DEV/OTHER BARGE" or
                                   "BARGE" in e[1] for e in tl.events)))
            await ws.close()
            rt.cancel()
            return _summary(tl, checks, None)

        # --- 2. interrupción: frase B + BARGE, con la respuesta sonando ---
        await send_pcm(ws, pcm_b)             # el "mic" mientras Deb habla
        t_barge = tl.mark()
        await ws.send_str(json.dumps({"type": "server_action", "msg": "BARGE"}))
        tl.add("SENT BARGE", f"t={t_barge:.2f}s")
        # El device sigue mandando audio (ya volvió a LISTENING): silencio para
        # que el VAD cierre la frase interrumpida.
        await send_silence(ws, 2.0)

        await asyncio.sleep(1.0)
        # cuánto siguió mandando audio después del BARGE (antes del turno nuevo)
        post = [e for e in tl.events if e[1] == "SERVER RESPONSE.COMPLETE" and e[0] > t_barge]
        checks.append(("A.2: NO llega RESPONSE.COMPLETE tras el corte", not post))
        # Frontera del turno nuevo: el bridge vuelve a cerrar frase con
        # AUDIO.COMMITTED. Hasta ahí corre la ventana de "audio que no debía salir".
        ev_committed2 = await wait_event(
            tl, lambda e: e[1] == "SERVER AUDIO.COMMITTED" and e[0] > t_barge,
            10, start=len(tl.events))
        window_end = ev_committed2[0] if ev_committed2 else t_barge + 5.0
        after = [t for t in tl.packet_times if t_barge <= t < window_end]
        if after:
            cut = max(after) - t_barge
            checks.append((f"A.2: el audio corta en <0.6s tras el BARGE "
                           f"(medido: {cut:+.2f}s)", cut < 0.6))
        else:
            cut = None
            checks.append(("A.2: ningún paquete después del BARGE (corte inmediato)",
                           True))

        n1 = len(tl.events)
        ev_created2 = await wait_event(
            tl, lambda e: e[1] == "SERVER RESPONSE.CREATED" and e[0] > t_barge,
            120, start=n1)
        checks.append(("A.3/A.4: la frase interrumpida se reprocesa "
                       "(nuevo RESPONSE.CREATED)", ev_created2 is not None))

        if ev_created2 is not None:
            # esperar a que termine el turno nuevo (RESPONSE.COMPLETE)
            ev_done = await wait_event(
                tl, lambda e: e[1] == "SERVER RESPONSE.COMPLETE" and e[0] > ev_created2[0],
                180, start=len(tl.events))
            checks.append(("turno nuevo cierra con RESPONSE.COMPLETE",
                           ev_done is not None))

        await ws.close()
        rt.cancel()

    return _summary(tl, checks, (t_barge, cut))


def _summary(tl: Timeline, checks, barge_info) -> int:
    print("\n=== RESUMEN ===", flush=True)
    print(f"paquetes Opus recibidos: {tl.packets}")
    if barge_info:
        t_barge, cut = barge_info
        print(f"BARGE enviado a t={t_barge:.2f}s | corte medido: {cut}")
        pre = [t for t in tl.packet_times if t < t_barge]
        post = [t for t in tl.packet_times if t > t_barge]
        pre_s = f"{pre[-1] - t_barge:+.2f}s" if pre else "n/a"
        post_s = f"{post[0] - t_barge:+.2f}s" if post else "n/a"
        print(f"último paquete Opus antes del BARGE: {pre_s} | "
              f"próximo paquete (turno nuevo): {post_s}")
    ok = True
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

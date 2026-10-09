#!/usr/bin/env python3
"""Pruebas de las tareas largas por voz y de las claves por dispositivo (2026-10-08).

Corren SIN tocar produccion: levantan un Hermes de mentira que tarda a
proposito, reemplazan `hermes send` por un script que escribe en un archivo, y
usan una state.db temporal (HOME apunta a un directorio temporal). No hace falta
audio real: STT y TTS estan simulados.

Uso (con el venv del bridge, que tiene opuslib, numpy y aiohttp):
    .venv/bin/python3 tests/test_long_task_and_auth.py
Exit code 0 = todo OK.
"""
import asyncio
import hashlib
import io
import json
import os
import pathlib
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import wave
from urllib.parse import unquote

# ---------------------------------------------------------------------------
# Entorno aislado ANTES de importar bridge / voice_history
# ---------------------------------------------------------------------------
TMP = pathlib.Path(tempfile.mkdtemp(prefix="bridge-test-"))
os.environ["HOME"] = str(TMP)                       # voice_history -> TMP/.hermes/state.db
(TMP / ".hermes").mkdir()
DB = TMP / ".hermes" / "state.db"
con = sqlite3.connect(DB)
con.executescript("""
CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL, ended_at REAL,
  title TEXT, message_count INTEGER DEFAULT 0, user_id TEXT);
CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
  content TEXT, timestamp REAL);
""")
con.commit(); con.close()

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

HERMES_PORT = free_port()
SENT = TMP / "telegram_enviado.txt"
STUB = TMP / "stub_hermes_send.py"
STUB.write_text(
    "import sys\n"
    "open(%r, 'a', encoding='utf-8').write('ARGS=' + ' '.join(sys.argv[1:]) + '\\n' + sys.stdin.read() + '\\n----\\n')\n"
    % str(SENT))
DEVICES = TMP / "cfg" / "devices.json"

os.environ.update({
    "HERMES_URL": "http://127.0.0.1:%d/v1/chat/completions" % HERMES_PORT,
    "HERMES_API_KEY": "clave-de-prueba",
    "HERMES_TIMEOUT_S": "10",
    "BRIDGE_LONG_TASK_SILENCE_S": "2",
    "HERMES_CLI": "%s %s" % (sys.executable, STUB),
    "BRIDGE_DEVICES_FILE": str(DEVICES),
    "BRIDGE_AUTH_MODE": "transition",
})
HERE = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

import bridge                      # noqa: E402
from aiohttp import ClientSession, web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

FALLOS = []
def check(nombre, cond, extra=""):
    print(("  [PASS] " if cond else "  [FAIL] ") + nombre + (("  " + str(extra)) if extra else ""))
    if not cond:
        FALLOS.append(nombre)

# ---------------------------------------------------------------------------
# Hermes de mentira: SSE estilo OpenAI, con keepalives como el real
# ---------------------------------------------------------------------------
ESCENARIO = {"modo": "rapido", "demora": 0}

async def fake_hermes(request):
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await resp.prepare(request)
    def chunk(delta):
        return ("data: " + json.dumps({"choices": [{"index": 0, "delta": delta}]}) + "\n\n").encode()
    await resp.write(chunk({"role": "assistant"}))
    modo = ESCENARIO["modo"]
    if modo == "rapido":
        for t in ["Hola. ", "Todo ", "bien."]:
            await resp.write(chunk({"content": t})); await asyncio.sleep(0.05)
    elif modo == "corto_error":
        # Pregunta corta que Hermes cierra con error y sin texto.
        pass
    elif modo in ("lento", "lento_vacio", "lento_error"):
        # Igual que Hermes usando herramientas: keepalives y progreso, SIN texto.
        fin = time.monotonic() + ESCENARIO["demora"]
        while time.monotonic() < fin:
            await resp.write(b": keepalive\n\n")
            await resp.write(b"event: hermes.tool.progress\ndata: {\"tool\": \"terminal\"}\n\n")
            await asyncio.sleep(0.5)
        if modo == "lento":
            for t in ["Resultado final ", "de la tarea larga."]:
                await resp.write(chunk({"content": t})); await asyncio.sleep(0.05)
    if modo in ("lento_error", "corto_error"):
        # Como Hermes cuando el proveedor rechaza (api_server_openai_routes.py):
        # chunk final con finish_reason "error" y el mensaje crudo del proveedor.
        fin = {"choices": [{"index": 0, "delta": {}, "finish_reason": "error"}],
               "error": {"message": "Error code: 400 - {'error': {'message': 'Content Exists Risk (request_id: x)'}}",
                         "type": "BadRequestError"}}
        await resp.write(("data: " + json.dumps(fin) + "\n\n").encode())
    await resp.write(b"data: [DONE]\n\n")
    return resp

async def start_fake_hermes():
    app = web.Application(); app.router.add_post("/v1/chat/completions", fake_hermes)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", HERMES_PORT).start()
    return runner

# ---------------------------------------------------------------------------
# Simulaciones de STT / TTS y un WebSocket de mentira
# ---------------------------------------------------------------------------
TEXTO_USUARIO = {"v": "contame algo largo"}
bridge.transcribe = lambda pcm: (TEXTO_USUARIO["v"], 0.01)
bridge.synthesize = lambda text: (b"\x00\x00" * 4410, 0.01)   # 0,2 s de silencio a 22050 Hz
bridge.piper_sample_rate = lambda: 22050
bridge.PACKET_PACE_S = 0.01

class FakeWS:
    def __init__(self): self.sent = []
    async def send_str(self, s): self.sent.append(("txt", s))
    async def send_bytes(self, b): self.sent.append(("bin", len(b)))
    def states(self):
        return [json.loads(s).get("msg") for k, s in self.sent if k == "txt" and '"server"' in s]

def mensajes_db(sid):
    c = sqlite3.connect(DB)
    rows = c.execute("SELECT role, content FROM messages WHERE session_id=? ORDER BY id", (sid,)).fetchall()
    c.close(); return rows

async def esperar_tareas_largas(timeout=20):
    t0 = time.monotonic()
    while bridge._LONG_TASKS and time.monotonic() - t0 < timeout:
        await asyncio.sleep(0.1)

# ---------------------------------------------------------------------------
async def main():
    runner = await start_fake_hermes()
    http = ClientSession()
    try:
        print("=== HermesPump: mide tiempo SIN TEXTO e ignora keepalives ===")
        ESCENARIO.update(modo="rapido")
        pump = bridge.HermesPump(http, [{"role": "user", "content": "hola"}])
        toks = []
        while True:
            t = await pump.next_token(2)
            if t is None: break
            toks.append(t)
        check("respuesta rapida: llega entera", "".join(toks) == "Hola. Todo bien.", "".join(toks))
        ESCENARIO.update(modo="lento", demora=4)
        pump = bridge.HermesPump(http, [{"role": "user", "content": "algo largo"}])
        t0 = time.monotonic(); disparo = False
        try:
            await pump.next_token(2)
        except asyncio.TimeoutError:
            disparo = True
        check("tarea larga: dispara a los ~2 s aunque haya keepalives", disparo and 1.8 < time.monotonic() - t0 < 3,
              "%.1f s" % (time.monotonic() - t0))
        full, err = await pump.wait_all()
        check("tarea larga: la lectura sigue hasta el final", full == "Resultado final de la tarea larga." and err is None, full)

        print("=== WS (ESP32): turno con tarea larga ===")
        SENT.write_text("")
        ESCENARIO.update(modo="lento", demora=5)
        TEXTO_USUARIO["v"] = "contame algo largo"
        ws = FakeWS(); s = bridge.Session(ws, http, "AA:BB:CC:DD:EE:FF")
        await s.greet()
        sid = s.vh.session_id
        t0 = time.monotonic()
        await s.process_utterance(b"\x00\x00" * 16000)
        dur = time.monotonic() - t0
        st = ws.states()
        check("el turno de voz termina rapido (no espera a Hermes)", dur < 4.5, "%.1f s" % dur)
        check("dice el aviso: RESPONSE.CREATED y luego RESPONSE.COMPLETE",
              "RESPONSE.CREATED" in st and st[-1] == "RESPONSE.COMPLETE", st)
        check("manda audio del aviso", any(k == "bin" for k, _ in ws.sent))
        check("el parlante queda libre (turn_lock suelto)", not s.turn_lock.locked())
        check("Telegram todavia no (Hermes sigue trabajando)", SENT.read_text() == "")
        # el device se desconecta mientras tanto (como un No PONG en reposo)
        await s.shutdown(); await bridge._vh(s.vh.close_session)
        await esperar_tareas_largas()
        txt = SENT.read_text(encoding="utf-8")
        check("Telegram: llega el resultado con la pregunta", "Pedido por voz: «contame algo largo»" in txt
              and "Resultado final de la tarea larga." in txt, txt[:160].replace("\n", " | "))
        check("Telegram: usa `hermes send --to telegram --file -`", "ARGS=send --to telegram --file -" in txt)
        rows = mensajes_db(sid)
        check("historial: pregunta + respuesta COMPLETA, aunque la sesion se cerro",
              rows == [("user", "contame algo largo"), ("assistant", "Resultado final de la tarea larga.")], rows)

        print("=== WS (ESP32): respuesta normal, sin cambios ===")
        SENT.write_text("")
        ESCENARIO.update(modo="rapido")
        TEXTO_USUARIO["v"] = "como estas"
        ws = FakeWS(); s = bridge.Session(ws, http, "AA:BB:CC:DD:EE:01")
        await s.greet(); sid = s.vh.session_id
        await s.process_utterance(b"\x00\x00" * 16000)
        await asyncio.sleep(0.3)
        check("respuesta normal: no hay tarea larga", not bridge._LONG_TASKS and SENT.read_text() == "")
        check("respuesta normal: termina con RESPONSE.COMPLETE", ws.states()[-1] == "RESPONSE.COMPLETE", ws.states())
        check("respuesta normal: guarda el turno", mensajes_db(sid) == [("user", "como estas"), ("assistant", "Hola. Todo bien.")],
              mensajes_db(sid))

        print("=== WS (ESP32): tarea larga que falla / vuelve vacia ===")
        SENT.write_text("")
        ESCENARIO.update(modo="lento_vacio", demora=3)
        TEXTO_USUARIO["v"] = "hace algo raro"
        ws = FakeWS(); s = bridge.Session(ws, http, "AA:BB:CC:DD:EE:02")
        await s.greet()
        await s.process_utterance(b"\x00\x00" * 16000)
        await esperar_tareas_largas()
        txt = SENT.read_text(encoding="utf-8")
        check("tarea sin resultado: igual avisa por Telegram", "No se pudo completar" in txt, txt[:120].replace("\n", " | "))

        print("=== WS (ESP32): tarea larga que Hermes cierra con error del proveedor ===")
        SENT.write_text("")
        ESCENARIO.update(modo="lento_error", demora=3)
        TEXTO_USUARIO["v"] = "posts de alguien"
        ws = FakeWS(); s = bridge.Session(ws, http, "AA:BB:CC:DD:EE:03")
        await s.greet()
        await s.process_utterance(b"\x00\x00" * 16000)
        await esperar_tareas_largas()
        txt = SENT.read_text(encoding="utf-8")
        check("error del proveedor: Telegram dice la CAUSA", "rechazo el contenido por su filtro" in txt,
              txt[:160].replace("\n", " | "))
        check("error del proveedor: ya no dice 'no devolvio texto'", "no devolvio texto" not in txt)

        print("=== WS (ESP32): pregunta corta que Hermes cierra con error ===")
        SENT.write_text("")
        ESCENARIO.update(modo="corto_error", demora=0)
        TEXTO_USUARIO["v"] = "pregunta corta"
        ws = FakeWS(); s = bridge.Session(ws, http, "AA:BB:CC:DD:EE:04")
        await s.greet()
        await s.process_utterance(b"\x00\x00" * 16000)
        check("corta con error: no dispara tarea larga ni Telegram", not bridge._LONG_TASKS and SENT.read_text() == "")
        check("corta con error: termina igual que antes (RESPONSE.COMPLETE)",
              ws.states() and ws.states()[-1] == "RESPONSE.COMPLETE", ws.states())
        check("corta con error: el parlante queda libre", not s.turn_lock.locked())

        print("=== Claves por dispositivo (con devices_admin.py) ===")
        env = dict(os.environ)
        out = subprocess.run([sys.executable, str(HERE / "devices_admin.py"), "add", "esp32-test"],
                             env=env, capture_output=True, text=True).stdout
        clave = [l.split("CLAVE:")[1].strip() for l in out.splitlines() if "CLAVE:" in l][0]
        check("alta: archivo con permisos 600", oct(DEVICES.stat().st_mode & 0o777) == "0o600")
        check("alta: guarda el hash, NO la clave", clave not in DEVICES.read_text()
              and hashlib.sha256(clave.encode()).hexdigest() in DEVICES.read_text())
        check("identify: clave buena -> nombre", bridge.identify_device(clave) == "esp32-test")
        check("identify: clave mala -> nada", bridge.identify_device("cualquier-cosa") is None)
        check("identify: sin clave -> nada", bridge.identify_device("") is None)
        time.sleep(1.1)  # mtime distinto para la recarga
        subprocess.run([sys.executable, str(HERE / "devices_admin.py"), "revoke", "esp32-test"], env=env, capture_output=True)
        check("revocado: el bridge lo deja de reconocer sin reiniciar", bridge.identify_device(clave) is None)
        time.sleep(1.1)
        subprocess.run([sys.executable, str(HERE / "devices_admin.py"), "enable", "esp32-test"], env=env, capture_output=True)

        print("=== Endpoints: transicion vs enforce ===")
        app = web.Application(); app["http"] = http
        app.router.add_get("/", bridge.handle_ws)
        app.router.add_post("/voice", bridge.handle_voice)
        app.router.add_get("/api/generate_auth_token", bridge.handle_token)
        async with TestClient(TestServer(app)) as cli:
            def wav_bytes():
                b = io.BytesIO(); w = wave.open(b, "wb")
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
                w.writeframes(b"\x00\x00" * 16000); w.close(); return b.getvalue()
            bridge.AUTH_MODE = "transition"
            r = await cli.get("/api/generate_auth_token?macAddress=X")
            check("transicion: el endpoint de token sigue andando", r.status == 200)
            ESCENARIO.update(modo="rapido"); TEXTO_USUARIO["v"] = "hola cardputer"
            r = await cli.post("/voice?device=cardputer", data=wav_bytes())
            check("transicion: /voice sin clave se acepta", r.status == 200, r.status)

            bridge.AUTH_MODE = "enforce"
            r = await cli.post("/voice?device=cardputer", data=wav_bytes())
            check("enforce: /voice sin clave -> 401", r.status == 401, r.status)
            r = await cli.post("/voice?device=cardputer", data=wav_bytes(),
                               headers={"Authorization": "Bearer " + clave})
            check("enforce: /voice con clave valida -> 200", r.status == 200, r.status)
            r = await cli.get("/", headers={"X-Device-Mac": "AA"})
            check("enforce: WebSocket sin clave -> 401", r.status == 401, r.status)
            r = await cli.get("/api/generate_auth_token?macAddress=X")
            check("enforce: el endpoint de token se apaga (404)", r.status == 404, r.status)

            print("=== /voice (Cardputer): tarea larga ===")
            SENT.write_text("")
            ESCENARIO.update(modo="lento", demora=4); TEXTO_USUARIO["v"] = "resumime el mail"
            t0 = time.monotonic()
            r = await cli.post("/voice?device=cardputer", data=wav_bytes(),
                               headers={"Authorization": "Bearer " + clave})
            dur = time.monotonic() - t0
            check("Cardputer: contesta el aviso sin esperar a Hermes",
                  r.status == 200 and unquote(r.headers.get("X-Reply", "")) == bridge.LONG_TASK_PHRASE and dur < 3.5,
                  "%s %.1f s" % (r.status, dur))
            await esperar_tareas_largas()
            txt = SENT.read_text(encoding="utf-8")
            check("Cardputer: el resultado llega por Telegram",
                  "«resumime el mail»" in txt and "Resultado final de la tarea larga." in txt)
    finally:
        await http.close()
        await runner.cleanup()

    print()
    if FALLOS:
        print("!! %d FALLO(S): %s" % (len(FALLOS), "; ".join(FALLOS)))
        return 1
    print("TODO OK")
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

#!/usr/bin/env python3
"""
ElatoAI <-> Hermes bridge.

Replaces the Elato cloud server with a local pipeline on the Mac mini:

  ESP32 (DEV_MODE firmware)
    -> GET  http://<this-host>:3000/api/generate_auth_token   (token, plain HTTP)
    -> WS   ws://<this-host>:8000/                            (audio, plain WS)

  Uplink   : raw PCM 16 kHz mono 16-bit (the firmware sends no Opus upstream)
  Downlink : raw Opus packets, 24 kHz mono, 120 ms frames, ~24 kbps
  Control  : JSON text messages replicating the Elato Deno server protocol:
             {"type":"auth", ...} on connect, then
             {"type":"server","msg":"AUDIO.COMMITTED"|"RESPONSE.CREATED"|
              "RESPONSE.COMPLETE"|"RESPONSE.ERROR"|"SESSION.END"}

  Pipeline per utterance:
    VAD (webrtcvad) -> STT (faster-whisper, local) ->
    LLM (Hermes, OpenAI-compatible API on localhost:8642) ->
    TTS (piper, local) -> resample 24 kHz -> Opus -> paced send

Pacing matters: the ESP32 audio buffer is 10 KB (~200 ms of 24 kHz PCM), so
Opus packets are sent one per ~110 ms, slightly faster than real time.

Configuration is via environment variables; see DEFAULTS below and the runbook
(docs/runbook-hermes-bridge.md).
"""

import asyncio
import json
import logging
import os
import struct
import subprocess
import time

import numpy as np
import opuslib
import webrtcvad
from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

log = logging.getLogger("hermes-bridge")

# ----------------------------------------------------------------------------
# Configuration (env vars, all optional)
# ----------------------------------------------------------------------------
HTTP_PORT = int(os.environ.get("BRIDGE_HTTP_PORT", "3000"))
WS_PORT = int(os.environ.get("BRIDGE_WS_PORT", "8000"))
AUTH_TOKEN = os.environ.get("BRIDGE_AUTH_TOKEN", "elato-local-token")

HERMES_URL = os.environ.get(
    "HERMES_URL", "http://127.0.0.1:8642/v1/chat/completions")
HERMES_API_KEY = os.environ.get("HERMES_API_KEY", "")
HERMES_MODEL = os.environ.get("HERMES_MODEL", "hermes-agent")
HERMES_TIMEOUT_S = float(os.environ.get("HERMES_TIMEOUT_S", "60"))

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
LANGUAGE = os.environ.get("BRIDGE_LANGUAGE", "es")

PIPER_BIN = os.environ.get("PIPER_BIN", "piper")
PIPER_VOICE = os.environ.get(
    "PIPER_VOICE", os.path.expanduser("~/piper-voices/es_ES-davefx-medium.onnx"))

SYSTEM_PROMPT = os.environ.get(
    "BRIDGE_SYSTEM_PROMPT",
    "Eres un asistente de voz. Responde siempre en espanol, de forma breve y "
    "conversacional (1-3 frases), sin markdown ni listas: tu respuesta sera "
    "leida en voz alta por un altavoz.")

MAX_HISTORY = int(os.environ.get("BRIDGE_MAX_HISTORY", "20"))

# VAD tuning
VAD_AGGRESSIVENESS = int(os.environ.get("VAD_AGGRESSIVENESS", "2"))  # 0..3
VAD_SILENCE_MS = int(os.environ.get("VAD_SILENCE_MS", "800"))
VAD_MIN_SPEECH_MS = int(os.environ.get("VAD_MIN_SPEECH_MS", "300"))
MAX_UTTERANCE_S = float(os.environ.get("MAX_UTTERANCE_S", "30"))

# Audio constants (fixed by the firmware -- do not change)
MIC_RATE = 16000          # uplink PCM sample rate (Config.cpp MIC_SAMPLE_RATE)
SPK_RATE = 24000          # downlink rate (Config.cpp SAMPLE_RATE)
VAD_FRAME_MS = 30
VAD_FRAME_BYTES = MIC_RATE * VAD_FRAME_MS // 1000 * 2       # 960 bytes
OPUS_FRAME_MS = 120       # matches the Deno server encoder
OPUS_FRAME_SAMPLES = SPK_RATE * OPUS_FRAME_MS // 1000       # 2880
OPUS_FRAME_BYTES = OPUS_FRAME_SAMPLES * 2                   # 5760
PACKET_PACE_S = 0.110     # send one 120 ms packet every 110 ms

# ----------------------------------------------------------------------------
# Lazy-loaded heavy components
# ----------------------------------------------------------------------------
_whisper = None


def get_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        log.info("Loading faster-whisper model '%s'...", WHISPER_MODEL)
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        log.info("Whisper model loaded.")
    return _whisper


def transcribe(pcm16k: bytes) -> str:
    """PCM 16 kHz mono 16-bit -> text. Blocking; run in executor."""
    audio = np.frombuffer(pcm16k, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _info = get_whisper().transcribe(
        audio, language=LANGUAGE, beam_size=1, vad_filter=True)
    return " ".join(s.text.strip() for s in segments).strip()


def piper_sample_rate() -> int:
    cfg = PIPER_VOICE + ".json"
    with open(cfg, "r", encoding="utf-8") as f:
        return int(json.load(f)["audio"]["sample_rate"])


def synthesize(text: str) -> bytes:
    """Text -> raw PCM 16-bit mono at the piper voice's native rate.
    Blocking; run in executor."""
    proc = subprocess.run(
        [PIPER_BIN, "--model", PIPER_VOICE, "--output-raw"],
        input=text.encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError("piper failed: " + proc.stderr.decode(errors="replace")[-400:])
    return proc.stdout


def resample_to_24k(pcm: bytes, src_rate: int) -> bytes:
    """Linear-interpolation resample of 16-bit mono PCM to 24 kHz."""
    if src_rate == SPK_RATE:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16)
    n_out = int(len(samples) * SPK_RATE / src_rate)
    x_out = np.linspace(0, len(samples) - 1, n_out)
    out = np.interp(x_out, np.arange(len(samples)), samples.astype(np.float64))
    return out.astype(np.int16).tobytes()


def encode_opus_packets(pcm24k: bytes) -> list[bytes]:
    """PCM 24 kHz mono 16-bit -> list of raw Opus packets (120 ms frames)."""
    enc = opuslib.Encoder(SPK_RATE, 1, opuslib.APPLICATION_VOIP)
    enc.bitrate = 24000
    # pad the tail to a whole frame so no audio is dropped
    remainder = len(pcm24k) % OPUS_FRAME_BYTES
    if remainder:
        pcm24k += b"\x00" * (OPUS_FRAME_BYTES - remainder)
    packets = []
    for off in range(0, len(pcm24k), OPUS_FRAME_BYTES):
        frame = pcm24k[off:off + OPUS_FRAME_BYTES]
        packets.append(enc.encode(frame, OPUS_FRAME_SAMPLES))
    return packets


# ----------------------------------------------------------------------------
# Hermes client
# ----------------------------------------------------------------------------
async def ask_hermes(http: ClientSession, history: list[dict]) -> str:
    payload = {
        "model": HERMES_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if HERMES_API_KEY:
        headers["Authorization"] = "Bearer " + HERMES_API_KEY
    async with http.post(HERMES_URL, json=payload, headers=headers,
                         timeout=ClientTimeout(total=HERMES_TIMEOUT_S)) as resp:
        if resp.status != 200:
            body = (await resp.text())[:400]
            raise RuntimeError(f"Hermes HTTP {resp.status}: {body}")
        data = await resp.json()
        return data["choices"][0]["message"]["content"].strip()


# ----------------------------------------------------------------------------
# Per-connection session
# ----------------------------------------------------------------------------
class Session:
    def __init__(self, ws: web.WebSocketResponse, http: ClientSession):
        self.ws = ws
        self.http = http
        self.vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        self.history: list[dict] = []
        self.pending = b""        # partial VAD frame accumulator
        self.utterance = b""      # collected speech
        self.in_speech = False
        self.silence_ms = 0
        self.speech_ms = 0
        self.utter_start = 0.0

    async def send_json(self, obj: dict):
        await self.ws.send_str(json.dumps(obj))

    async def send_state(self, msg: str):
        await self.send_json({"type": "server", "msg": msg})

    async def greet(self):
        await self.send_json({
            "type": "auth",
            "volume_control": 70,
            "pitch_factor": 1.0,
            "is_ota": False,
            "is_reset": False,
        })
        # Device connects in PROCESSING state; RESPONSE.COMPLETE moves it to
        # LISTENING (after its internal 1 s delay).
        await self.send_state("RESPONSE.COMPLETE")

    async def feed_audio(self, chunk: bytes):
        """Feed uplink PCM; when an utterance completes, run the pipeline."""
        self.pending += chunk
        while len(self.pending) >= VAD_FRAME_BYTES:
            frame = self.pending[:VAD_FRAME_BYTES]
            self.pending = self.pending[VAD_FRAME_BYTES:]
            await self._feed_frame(frame)

    async def _feed_frame(self, frame: bytes):
        try:
            is_speech = self.vad.is_speech(frame, MIC_RATE)
        except Exception:
            is_speech = False

        if not self.in_speech:
            if is_speech:
                self.in_speech = True
                self.utterance = frame
                self.speech_ms = VAD_FRAME_MS
                self.silence_ms = 0
                self.utter_start = time.monotonic()
            return

        self.utterance += frame
        if is_speech:
            self.speech_ms += VAD_FRAME_MS
            self.silence_ms = 0
        else:
            self.silence_ms += VAD_FRAME_MS

        too_long = (time.monotonic() - self.utter_start) > MAX_UTTERANCE_S
        if self.silence_ms >= VAD_SILENCE_MS or too_long:
            utterance, speech_ms = self.utterance, self.speech_ms
            self.in_speech = False
            self.utterance = b""
            if speech_ms >= VAD_MIN_SPEECH_MS:
                await self.process_utterance(utterance)
            # else: noise blip; keep listening silently

    async def process_utterance(self, pcm: bytes):
        loop = asyncio.get_running_loop()

        # Device -> PROCESSING (red LED, mic muted on-device)
        await self.send_state("AUDIO.COMMITTED")

        try:
            t0 = time.monotonic()
            text = await loop.run_in_executor(None, transcribe, pcm)
            t_stt = time.monotonic() - t0
            if not text:
                log.info("Empty transcription, back to listening.")
                await self.send_state("RESPONSE.COMPLETE")
                return
            log.info("User: %s", text)

            self.history.append({"role": "user", "content": text})
            self.history = self.history[-MAX_HISTORY:]

            t1 = time.monotonic()
            reply = await ask_hermes(self.http, self.history)
            t_llm = time.monotonic() - t1
            self.history.append({"role": "assistant", "content": reply})
            log.info("Hermes: %s", reply)

            t2 = time.monotonic()
            src_rate = piper_sample_rate()
            raw = await loop.run_in_executor(None, synthesize, reply)
            pcm24 = resample_to_24k(raw, src_rate)
            packets = encode_opus_packets(pcm24)
            t_tts = time.monotonic() - t2

            t_total = time.monotonic() - t0
            log.info("Tiempos — STT: %.2fs  LLM: %.2fs  TTS: %.2fs  TOTAL: %.2fs",
                     t_stt, t_llm, t_tts, t_total)

            # Device -> SPEAKING (blue LED), then paced Opus stream
            await self.send_state("RESPONSE.CREATED")
            for pkt in packets:
                await self.ws.send_bytes(pkt)
                await asyncio.sleep(PACKET_PACE_S)
            await self.send_state("RESPONSE.COMPLETE")

        except Exception:
            log.exception("Pipeline error")
            # RESPONSE.ERROR also schedules a listening restart on the device
            await self.send_state("RESPONSE.ERROR")


# ----------------------------------------------------------------------------
# Servers
# ----------------------------------------------------------------------------
async def handle_token(request: web.Request) -> web.Response:
    mac = request.query.get("macAddress", "?")
    log.info("Token request from MAC %s", mac)
    return web.json_response({"token": AUTH_TOKEN})


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=None)
    await ws.prepare(request)
    mac = request.headers.get("X-Device-Mac", "?")
    log.info("Device connected (MAC %s)", mac)

    session = Session(ws, request.app["http"])
    await session.greet()

    try:
        async for msg in ws:
            if msg.type == WSMsgType.BINARY:
                await session.feed_audio(msg.data)
            elif msg.type == WSMsgType.TEXT:
                log.info("Device text: %s", msg.data)
            elif msg.type == WSMsgType.ERROR:
                log.warning("WS error: %s", ws.exception())
    finally:
        log.info("Device disconnected (MAC %s)", mac)
    return ws


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Fail fast on missing pieces (robustness: no surprises mid-conversation)
    if not os.path.exists(PIPER_VOICE):
        raise SystemExit(
            f"Piper voice not found: {PIPER_VOICE}\n"
            "Download one, e.g.:\n"
            "  mkdir -p ~/piper-voices && cd ~/piper-voices\n"
            "  curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx\n"
            "  curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx.json")
    piper_sample_rate()          # validates the .json sidecar
    get_whisper()                # load the model up front, not on first phrase

    http = ClientSession()

    ws_app = web.Application()
    ws_app["http"] = http
    ws_app.router.add_get("/", handle_ws)

    token_app = web.Application()
    token_app.router.add_get("/api/generate_auth_token", handle_token)

    ws_runner = web.AppRunner(ws_app)
    token_runner = web.AppRunner(token_app)
    await ws_runner.setup()
    await token_runner.setup()
    await web.TCPSite(ws_runner, "0.0.0.0", WS_PORT).start()
    await web.TCPSite(token_runner, "0.0.0.0", HTTP_PORT).start()

    log.info("Bridge ready: ws://0.0.0.0:%d/  token http://0.0.0.0:%d"
             "/api/generate_auth_token", WS_PORT, HTTP_PORT)
    log.info("Hermes endpoint: %s (model %s)", HERMES_URL, HERMES_MODEL)

    try:
        await asyncio.Event().wait()
    finally:
        await http.close()


if __name__ == "__main__":
    asyncio.run(main())

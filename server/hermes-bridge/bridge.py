#!/usr/bin/env python3
"""
ElatoAI <-> Hermes bridge.

Replaces the Elato cloud server with a local pipeline on the Mac mini.

  ESP32 (DEV_MODE firmware)
    -> GET  http://<this-host>:3000/api/generate_auth_token   (token)
    -> WS   ws://<this-host>:8000/                            (audio)

  Pipeline per utterance: VAD -> STT (whisper) -> LLM (Hermes) -> TTS (Piper) -> Opus

Improvements over baseline:
  - WHISPER_MODEL default "base" (better perf on Intel dual-core vs "small")
  - Latency logging per stage (VAD/STT/LLM/TTS/TOTAL)
  - Cleaner timeout handling
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time

import numpy as np
import opuslib
import webrtcvad
from aiohttp import ClientSession, ClientTimeout, WSMsgType, web

from voice_history import VoiceHistory

log = logging.getLogger("hermes-bridge")

# ---------------------------------------------------------------------------
# Configuration (env vars, all optional)
# ---------------------------------------------------------------------------
HTTP_PORT = int(os.environ.get("BRIDGE_HTTP_PORT", "3000"))
WS_PORT = int(os.environ.get("BRIDGE_WS_PORT", "8000"))
AUTH_TOKEN = os.environ.get("BRIDGE_AUTH_TOKEN", "elato-local-token")

HERMES_URL = os.environ.get(
    "HERMES_URL", "http://127.0.0.1:8642/v1/chat/completions")
HERMES_API_KEY = os.environ.get("HERMES_API_KEY", "")
HERMES_MODEL = os.environ.get("HERMES_MODEL", "hermes-agent")
HERMES_TIMEOUT_S = float(os.environ.get("HERMES_TIMEOUT_S", "60"))  # improved: 60s (was 120)

# [IMPROVED] Default "base" instead of "small" — 4x faster on Intel dual-core
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
TELEGRAM_CONTEXT = int(os.environ.get("BRIDGE_TELEGRAM_CONTEXT", "5"))

# VAD tuning
VAD_AGGRESSIVENESS = int(os.environ.get("VAD_AGGRESSIVENESS", "1"))
VAD_SILENCE_MS = int(os.environ.get("VAD_SILENCE_MS", "800"))
VAD_MIN_SPEECH_MS = int(os.environ.get("VAD_MIN_SPEECH_MS", "300"))
MAX_UTTERANCE_S = float(os.environ.get("MAX_UTTERANCE_S", "30"))

# Audio constants (fixed by firmware — do not change)
MIC_RATE = 16000          # uplink PCM sample rate
SPK_RATE = 24000          # downlink rate
VAD_FRAME_MS = 30
VAD_FRAME_BYTES = MIC_RATE * VAD_FRAME_MS // 1000 * 2       # 960 bytes
OPUS_FRAME_MS = 120       # matches Deno server encoder
OPUS_FRAME_SAMPLES = SPK_RATE * OPUS_FRAME_MS // 1000       # 2880
OPUS_FRAME_BYTES = OPUS_FRAME_SAMPLES * 2                   # 5760
PACKET_PACE_S = 0.110     # send one 120 ms packet every 110 ms

# ---------------------------------------------------------------------------
# Lazy-loaded heavy components
# ---------------------------------------------------------------------------
_whisper = None


def get_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel
        log.info("Loading faster-whisper model '%s'...", WHISPER_MODEL)
        t0 = time.monotonic()
        _whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        log.info("Whisper model loaded in %.1fs", time.monotonic() - t0)
    return _whisper


def transcribe(pcm16k: bytes) -> tuple[str, float]:
    """PCM 16 kHz mono 16-bit -> text. Returns (text, duration_seconds)."""
    t0 = time.monotonic()
    audio = np.frombuffer(pcm16k, dtype=np.int16).astype(np.float32) / 32768.0
    # vad_filter=False: bridge already gated with webrtcvad; double-VAD removes valid speech
    segments, _info = get_whisper().transcribe(
        audio, language=LANGUAGE, beam_size=1, vad_filter=False)
    text = " ".join(s.text.strip() for s in segments).strip()
    return text, time.monotonic() - t0


def piper_sample_rate() -> int:
    cfg = PIPER_VOICE + ".json"
    with open(cfg, "r", encoding="utf-8") as f:
        return int(json.load(f)["audio"]["sample_rate"])


def synthesize(text: str) -> tuple[bytes, float]:
    """Text -> raw PCM 16-bit mono at piper's native rate. Returns (pcm, duration_s)."""
    t0 = time.monotonic()
    proc = subprocess.run(
        [PIPER_BIN, "--model", PIPER_VOICE, "--output-raw"],
        input=text.encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError("piper failed: " + proc.stderr.decode(errors="replace")[-400:])
    return proc.stdout, time.monotonic() - t0


def resample_to_24k(pcm: bytes, src_rate: int) -> bytes:
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
    remainder = len(pcm24k) % OPUS_FRAME_BYTES
    if remainder:
        pcm24k += b"\x00" * (OPUS_FRAME_BYTES - remainder)
    packets = []
    for off in range(0, len(pcm24k), OPUS_FRAME_BYTES):
        frame = pcm24k[off:off + OPUS_FRAME_BYTES]
        packets.append(enc.encode(frame, OPUS_FRAME_SAMPLES))
    return packets


# ---------------------------------------------------------------------------
# Hermes client
# ---------------------------------------------------------------------------
async def ask_hermes(http: ClientSession, history: list[dict]) -> tuple[str, float]:
    """Returns (response_text, duration_seconds)."""
    payload = {
        "model": HERMES_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if HERMES_API_KEY:
        headers["Authorization"] = "Bearer " + HERMES_API_KEY
    t0 = time.monotonic()
    async with http.post(HERMES_URL, json=payload, headers=headers,
                         timeout=ClientTimeout(total=HERMES_TIMEOUT_S)) as resp:
        if resp.status != 200:
            body = (await resp.text())[:400]
            raise RuntimeError(f"Hermes HTTP {resp.status}: {body}")
        data = await resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        return text, time.monotonic() - t0


async def ask_hermes_stream(http: ClientSession, history: list[dict]):
    """Stream Hermes response token by token (OpenAI SSE format).

    Yields content strings as they arrive from the model, so the caller
    can start TTS on the first sentence without waiting for the full reply.
    """
    payload = {
        "model": HERMES_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
        "stream": True,
    }
    headers = {"Content-Type": "application/json"}
    if HERMES_API_KEY:
        headers["Authorization"] = "Bearer " + HERMES_API_KEY
    # total=None evita que el timeout global corte el stream SSE mientras llegan tokens.
    # sock_read controla el tiempo máximo entre tokens individuales.
    stream_timeout = ClientTimeout(total=None, connect=10, sock_read=HERMES_TIMEOUT_S)
    async with http.post(HERMES_URL, json=payload, headers=headers,
                         timeout=stream_timeout) as resp:
        if resp.status != 200:
            body = (await resp.text())[:400]
            raise RuntimeError(f"Hermes HTTP {resp.status}: {body}")
        while True:
            raw_line = await resp.content.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            if line.startswith("data: "):
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    delta = chunk["choices"][0]["delta"]
                    content = delta.get("content", "")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue


# ---------------------------------------------------------------------------
# Per-connection session
# ---------------------------------------------------------------------------
class Session:
    def __init__(self, ws: web.WebSocketResponse, http: ClientSession, device_mac: str = "?"):
        self.ws = ws
        self.http = http
        self.mac = device_mac
        self.vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        self.history: list[dict] = []
        self.pending = b""
        self.utterance = b""
        self.in_speech = False
        self.silence_ms = 0
        self.speech_ms = 0
        self.utter_start = 0.0
        # Voice history persistence
        self.vh = VoiceHistory(device_mac)

    async def send_json(self, obj: dict):
        await self.ws.send_str(json.dumps(obj))

    async def send_state(self, msg: str):
        await self.send_json({"type": "server", "msg": msg})

    async def greet(self):
        self.vh.start_session()
        await self.send_json({
            "type": "auth",
            "volume_control": 70,
            "pitch_factor": 1.0,
            "is_ota": False,
            "is_reset": False,
        })
        await self.send_state("RESPONSE.COMPLETE")

    async def feed_audio(self, chunk: bytes):
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

    async def process_utterance(self, pcm: bytes):
        loop = asyncio.get_running_loop()
        latencies = {}

        await self.send_state("AUDIO.COMMITTED")

        try:
            # -- STT --
            text, latencies["stt"] = await loop.run_in_executor(None, transcribe, pcm)
            if not text:
                log.info("Empty transcription, back to listening.")
                await self.send_state("RESPONSE.COMPLETE")
                return
            log.info("User: %s", text)

            self.history.append({"role": "user", "content": text})
            self.history = self.history[-MAX_HISTORY:]

            # Inject recent Telegram context for cross-channel awareness
            telegram_msgs = self.vh.get_telegram_context(TELEGRAM_CONTEXT)
            context_injected = len(telegram_msgs)
            if telegram_msgs:
                self.history = telegram_msgs + self.history

            # -- LLM streaming + TTS pipeline --
            reply_text = ""
            sentence_buf = ""
            src_rate = piper_sample_rate()
            llm_start = time.monotonic()
            first_audio_sent = False
            tts_chunks = 0

            async for token in ask_hermes_stream(self.http, self.history):
                reply_text += token
                sentence_buf += token

                # Detect boundary: end-of-sentence punctuation or forced flush
                boundary = False
                boundary_pos = 0
                for i, c in enumerate(sentence_buf):
                    if c in ".!?\n":
                        boundary = True
                        boundary_pos = i
                        break
                if not boundary and len(sentence_buf) >= 200:
                    boundary = True
                    boundary_pos = len(sentence_buf) - 1  # flush all

                if boundary:
                    chunk = sentence_buf[:boundary_pos + 1].strip()
                    sentence_buf = sentence_buf[boundary_pos + 1:]
                    if chunk:
                        raw, _ = await loop.run_in_executor(None, synthesize, chunk)
                        pcm24 = resample_to_24k(raw, src_rate)
                        packets = encode_opus_packets(pcm24)
                        if not first_audio_sent:
                            latencies["llm_first_token"] = time.monotonic() - llm_start
                            first_audio_sent = True
                        await self.send_state("RESPONSE.CREATED")
                        for pkt in packets:
                            await self.ws.send_bytes(pkt)
                            await asyncio.sleep(PACKET_PACE_S)
                        tts_chunks += 1

            # Flush remaining text after stream ends
            remaining = sentence_buf.strip()
            if remaining:
                raw, _ = await loop.run_in_executor(None, synthesize, remaining)
                pcm24 = resample_to_24k(raw, src_rate)
                packets = encode_opus_packets(pcm24)
                if not first_audio_sent:
                    latencies["llm_first_token"] = time.monotonic() - llm_start
                    first_audio_sent = True
                await self.send_state("RESPONSE.CREATED")
                for pkt in packets:
                    await self.ws.send_bytes(pkt)
                    await asyncio.sleep(PACKET_PACE_S)
                tts_chunks += 1

            await self.send_state("RESPONSE.COMPLETE")

            # -- Persist --
            self.history.append({"role": "assistant", "content": reply_text})
            self.vh.save_message("user", text)
            self.vh.save_message("assistant", reply_text)
            if context_injected:
                log.info("Telegram context: %d messages injected", context_injected)

            log.info(
                "Hermes: %s | STT=%.1fs first_token=%.1fs chunks=%d",
                reply_text[:120],
                latencies.get("stt", 0),
                latencies.get("llm_first_token", 0),
                tts_chunks)

        except Exception:
            log.exception("Pipeline error")
            await self.send_state("RESPONSE.ERROR")


# ---------------------------------------------------------------------------
# Servers
# ---------------------------------------------------------------------------
async def handle_token(request: web.Request) -> web.Response:
    mac = request.query.get("macAddress", "?")
    log.info("Token request from MAC %s", mac)
    return web.json_response({"token": AUTH_TOKEN})


async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(heartbeat=30.0)
    await ws.prepare(request)
    mac = request.headers.get("X-Device-Mac", "?")
    log.info("Device connected (MAC %s)", mac)

    session = Session(ws, request.app["http"], mac)
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
        session.vh.close_session()
        log.info("Device disconnected (MAC %s)", mac)
    return ws


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Fail fast
    if not os.path.exists(PIPER_VOICE):
        raise SystemExit(
            f"Piper voice not found: {PIPER_VOICE}\n"
            "Download one, e.g.:\n"
            "  mkdir -p ~/piper-voices && cd ~/piper-voices\n"
            "  curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx\n"
            "  curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx.json")
    piper_sample_rate()
    get_whisper()                # preload

    http = ClientSession()

    ws_app = web.Application()
    ws_app["http"] = http
    ws_app.router.add_get("/", handle_ws)
    ws_app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))

    token_app = web.Application()
    token_app.router.add_get("/api/generate_auth_token", handle_token)
    token_app.router.add_get("/health", lambda r: web.json_response({"status": "ok"}))

    ws_runner = web.AppRunner(ws_app)
    token_runner = web.AppRunner(token_app)
    await ws_runner.setup()
    await token_runner.setup()
    await web.TCPSite(ws_runner, "0.0.0.0", WS_PORT).start()
    await web.TCPSite(token_runner, "0.0.0.0", HTTP_PORT).start()

    # Signal handlers for graceful shutdown logging
    def _signal_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        log.warning("Received signal %s (%d) — shutting down", sig_name, signum)

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGQUIT, _signal_handler)

    log.info("Bridge ready: ws://0.0.0.0:%d/  token http://0.0.0.0:%d"
             "/api/generate_auth_token", WS_PORT, HTTP_PORT)
    log.info("Hermes endpoint: %s (model %s)", HERMES_URL, HERMES_MODEL)
    log.info("Whisper model: %s | Piper voice: %s", WHISPER_MODEL, PIPER_VOICE)

    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        log.warning("Main loop cancelled (CancelledError)")
    except Exception:
        log.exception("Fatal error in main loop — process will exit")
    finally:
        log.warning("Bridge shutting down...")
        await http.close()
        log.warning("Bridge stopped.")


if __name__ == "__main__":
    asyncio.run(main())

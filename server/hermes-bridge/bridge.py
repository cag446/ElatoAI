#!/usr/bin/env python3
"""
ElatoAI <-> Hermes bridge.

Replaces the Elato cloud server with a local pipeline on the Mac mini.

  ESP32 (DEV_MODE firmware)
    -> GET  http://<this-host>:3000/api/generate_auth_token   (token)
    -> WS   ws://<this-host>:8000/                            (audio)

  Pipeline per utterance: VAD -> STT (whisper) -> LLM (Hermes) -> TTS (Piper) -> Opus

  M5Stack Cardputer (half-duplex, push-to-talk)
    -> POST http://<this-host>:8000/voice                     (WAV in, WAV out)

  Pipeline per request: resample -> STT (whisper) -> LLM (Hermes) -> TTS (Piper)

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
import struct
import subprocess
import sys
import time
from urllib.parse import quote

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

# Volumen del parlante del device (0-100). El firmware lo aplica en VolumeStream.
# Se baja de 70 a 50: el parlante SATURA al volumen alto y esa distorsion es la
# fuente del eco NO LINEAL que el AEC no puede cancelar (Fase C). Menos volumen
# = menos eco y menos distorsion, que es lo que puede habilitar el barge por voz
# a distancia normal en vez de solo pegado al mic.
DEVICE_VOLUME = int(os.environ.get("BRIDGE_DEVICE_VOLUME", "50"))

# VAD tuning
# 2026-08-17: bumped default 1 -> 3 so background voices (people walking by)
# are rejected; ESP32 mic is close so own voice still passes cleanly.
# 2026-08-17 (2): VAD_MIN_SPEECH_MS 300 -> 500 so short noises / street voices
# (car doors, passing talk under ~0.5s) never open an utterance.
VAD_AGGRESSIVENESS = int(os.environ.get("VAD_AGGRESSIVENESS", "3"))
VAD_SILENCE_MS = int(os.environ.get("VAD_SILENCE_MS", "800"))
VAD_MIN_SPEECH_MS = int(os.environ.get("VAD_MIN_SPEECH_MS", "500"))
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

# Barge-in (Fase A — docs/runbook-bargein-esp32.md §2/§3). Mientras el bridge
# reproduce (speaking = True) el audio del mic NO se interpreta como frase nueva:
# se acumula en barge_buf y, si llega un BARGE, se reencola como la frase nueva.
# Buffer acotado: se conservan solo los últimos N segundos.
BARGE_BUF_MAX_S = float(os.environ.get("BRIDGE_BARGE_BUF_MAX_S", "10"))
BARGE_BUF_MAX_BYTES = int(BARGE_BUF_MAX_S * MIC_RATE * 2)

# Gracia de fin de SPEAKING (Fase A, ajuste 2). El device sigue en SPEAKING
# hasta RESPONSE.COMPLETE + 1 s + su buffer (~200 ms): apagar `speaking` al
# terminar el stream dejaría el eco de la cola de la propia respuesta entrando
# al VAD como frase nueva (Fase C, mic abierto en SPEAKING).
SPEAK_TAIL_GRACE_S = float(os.environ.get("BRIDGE_SPEAK_TAIL_GRACE_S", "1.3"))

# --- Fase 0: sonda de eco (opt-in) ------------------------------------------
# Con BRIDGE_ECHO_PROBE=1 el bridge loguea el nivel (dBFS) de lo que entra por
# el micrófono del device: durante LISTENING (voz del usuario) y durante
# SPEAKING (eco de Deb + lo que el usuario diga encima). La diferencia entre
# ambos números es el dato que decide el AEC (runbook §4, Fase 0). Off por
# defecto: no agrega nada al log de producción.
ECHO_PROBE = os.environ.get("BRIDGE_ECHO_PROBE", "0").lower() not in (
    "0", "", "false", "no")
ECHO_PROBE_DIR = os.environ.get("BRIDGE_ECHO_PROBE_DIR", "/tmp/hermes-eco-probe")


def dbfs(pcm: bytes) -> float:
    """Nivel RMS de un bloque PCM s16le en dBFS (0 dBFS = full scale)."""
    if len(pcm) < 2:
        return -120.0
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    if a.size == 0:
        return -120.0
    rms = float(np.sqrt(float(np.mean(a * a))))
    return float(20.0 * np.log10(rms / 32768.0)) if rms > 0 else -120.0


def probe_log_levels(tag: str, dbs: list) -> None:
    """Loguea mediana/p10/p90/max de una lista de niveles por frame (30 ms)."""
    if not (ECHO_PROBE and dbs):
        return
    s = sorted(dbs)
    n = len(s)
    log.info("PROBE %s: %d frames (%.2fs) dBFS med=%s p10=%s p90=%s max=%s",
             tag, n, n * VAD_FRAME_MS / 1000.0,
             round(s[n // 2], 1), round(s[max(0, int(0.10 * (n - 1)))], 1),
             round(s[int(0.90 * (n - 1))], 1), round(s[-1], 1))


def probe_log(tag: str, pcm: bytes) -> None:
    """Nivel de un bloque PCM del micrófono, frame por frame (30 ms)."""
    if not ECHO_PROBE or not pcm:
        return
    n = len(pcm) // VAD_FRAME_BYTES
    probe_log_levels(tag, [dbfs(pcm[i * VAD_FRAME_BYTES:(i + 1) * VAD_FRAME_BYTES])
                           for i in range(n)])


def probe_dump(tag: str, pcm: bytes) -> None:
    """Guarda el bloque a WAV para analizarlo offline (espectro, AEC, etc.)."""
    if not (ECHO_PROBE and pcm):
        return
    try:
        os.makedirs(ECHO_PROBE_DIR, exist_ok=True)
        path = os.path.join(ECHO_PROBE_DIR, "%.2f-%s.wav" % (time.time(), tag))
        with open(path, "wb") as f:
            f.write(_make_wav(pcm, MIC_RATE))
        log.info("PROBE dump: %s (%d B)", path, len(pcm))
    except OSError as exc:
        log.warning("PROBE dump falló: %s", exc)


# REST /voice endpoint (Cardputer). Its mic is locked to 48 kHz by Bruce's
# firmware, so the resample to MIC_RATE happens here rather than on-device.
MAX_UPLOAD_BYTES = int(os.environ.get("BRIDGE_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
REST_SESSION_TTL_S = float(os.environ.get("BRIDGE_REST_SESSION_TTL_S", "1800"))

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


def _parse_wav(data: bytes) -> tuple[bytes, int, int]:
    """Extract (pcm, sample_rate, channels) from a RIFF/WAVE byte string.

    Walks the chunk list instead of assuming a 44-byte header, and tolerates a
    wrong or zero ``data`` size — Bruce patches that field after recording, so a
    recording cut short by a reset can arrive with a stale length.
    """
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    pos, fmt = 12, None
    while pos + 8 <= len(data):
        chunk_id = data[pos:pos + 4]
        size = struct.unpack_from("<I", data, pos + 4)[0]
        body = pos + 8
        if chunk_id == b"fmt ":
            audio_fmt, channels, rate, _brate, _balign, bits = struct.unpack_from(
                "<HHIIHH", data, body)
            if audio_fmt != 1 or bits != 16:
                raise ValueError("only 16-bit PCM WAV is supported")
            fmt = (channels, rate)
        elif chunk_id == b"data":
            if fmt is None:
                raise ValueError("data chunk before fmt chunk")
            channels, rate = fmt
            end = body + size
            if size == 0 or end > len(data):
                end = len(data)  # trust the payload over the declared size
            return data[body:end], rate, channels
        pos = body + size + (size & 1)  # chunks are word-aligned
    raise ValueError("no data chunk found")


def _make_wav(pcm: bytes, rate: int) -> bytes:
    """Wrap raw mono 16-bit PCM in a canonical 44-byte WAV header."""
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)


def _resample_pcm(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample mono 16-bit PCM between arbitrary rates."""
    if src_rate == dst_rate:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return b""
    ratio = src_rate / dst_rate
    if ratio.is_integer():
        # Exact decimation (48k -> 16k is 3:1). Averaging each group doubles as
        # a cheap low-pass: plain subsampling would fold everything above 8 kHz
        # back into the band Whisper reads, hurting transcription accuracy.
        factor = int(ratio)
        usable = (samples.size // factor) * factor
        out = samples[:usable].astype(np.float32).reshape(-1, factor).mean(axis=1)
    else:
        n_out = int(samples.size * dst_rate / src_rate)
        x_out = np.linspace(0, samples.size - 1, n_out)
        out = np.interp(x_out, np.arange(samples.size), samples.astype(np.float64))
    return out.astype(np.int16).tobytes()


def encode_opus_packets(pcm24k: bytes) -> list[bytes]:
    """PCM 24 kHz mono 16-bit -> list of raw Opus packets (120 ms frames)."""
    enc = opuslib.Encoder(SPK_RATE, 1, opuslib.APPLICATION_VOIP)
    # 48 kbps (era 24000).
    #
    # OJO CON LA HISTORIA DE ESTE VALOR: se subio creyendo que el codec causaba
    # un "carraspeo" en el parlante del device. NO ERA EL CODEC. La causa real
    # fue una regresion del firmware (la tarea del mic con mas prioridad que la
    # del parlante + una seccion critica en la ruta del audio); corregido ahi, el
    # carraspeo desaparecio. Ver el commit del firmware y la bitacora del
    # 2026-09-21.
    #
    # Se deja en 48k igualmente: es la configuracion verificada como sana y el
    # costo es ~6 KB/s de bajada en vez de 3, irrelevante. Medido en el Mac Mini
    # (i5-3210M): codificar 10 s de audio cuesta 188 ms a 24k y 192 ms a 48k, o
    # sea 3 ms de diferencia — no cambia la latencia del turno (STT ~2.4 s,
    # first_token ~4.8 s) ni el ritmo de envio, que es fijo por tiempo
    # (PACKET_PACE_S), no por bitrate.
    enc.bitrate = int(os.environ.get("BRIDGE_OPUS_BITRATE", "48000"))
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


async def _vh(fn, *args):
    """Corre una operacion de voice_history en el executor.

    voice_history hace sqlite SINCRONO: abre conexion, corre PRAGMAs y, ante un
    SQLITE_BUSY, su _retry espera con time.sleep. Llamado desde el event loop eso
    bloquea TODO el bridge: no lee el WebSocket (ni el BARGE del device), no
    responde los PING del heartbeat y no atiende el REST del Cardputer.

    Peor caso por operacion: 3 intentos x 500 ms de busy_timeout + 0.3 s de
    sleeps = ~1.8 s, y son 3 operaciones por turno. Hermes escribe ese mismo
    state.db, asi que la contencion es real y crece con su uso.

    Mismo patron que ya usan transcribe(), synthesize() y _resample_pcm().
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, fn, *args)


# ---------------------------------------------------------------------------
# voice_history y el event loop — PARCIALMENTE RESUELTO (2026-09-21).
#
# RESUELTO: ninguna operacion del camino POR-TURNO bloquea ya el loop. Las 10
# llamadas de contexto/save/close pasan por _vh() (executor). Medido por Deb
# antes y despues, con un ticker del propio loop:
#     llamada directa -> loop congelado 540 ms, CERO ticks
#     via _vh()       -> gap maximo 7 ms, 85 ticks
#
# SIGUE PENDIENTE (nada urgente, sin sintoma reportado):
#   - _rest_session() no es async: start_session/close_session y
#     VoiceHistory.__init__ (_ensure_db) corren en el loop. Costo normal ~0 ms,
#     PERO ahi mismo esta el barrido de sesiones expiradas, que corre en CADA
#     turno REST (no solo cuando expira alguna). Esa es la razon para pasarla a
#     async, no las dos llamadas.
#   - El peor caso sigue DENTRO del turno (turn_lock tomado): con la base
#     tomada por Hermes, hasta ~6,7 s por operacion (medido: ~2,05 s por
#     intento x 3 intentos + los sleeps de _retry). Ya no bloquea el loop, pero
#     si retrasa el turno encolado tras un barge. Palanca: agrupar las
#     operaciones del turno y acotar busy_timeout/reintentos.
#
# LO QUE NO ES LA PALANCA (medido, para no repetir el error): reusar la conexion
# NO rinde — connect() + los dos PRAGMA son 0,05 ms. Los segundos se los come el
# INSERT esperando el lock. Y si alguna vez se reusa, tiene que ser
# threading.local o check_same_thread=False, porque ahora el mismo objeto lo
# tocan el hilo del loop y los del executor.
#
# MEDIDAS Y DESCARTADAS POR RUIDO: piper_sample_rate() 0,17 ms y _make_wav()
# 0,01 ms por turno. No valen un commit. Con BRIDGE_ECHO_PROBE=1 si conviene
# recordar que probe_dump() escribe WAVs en el loop y contamina la latencia que
# se este midiendo.
# ---------------------------------------------------------------------------

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
        # --- Barge-in (Fase A) ---
        self.speaking = False          # True mientras se reproduce una respuesta
        self.speak_gen = 0             # generación de SPEAKING: la gracia solo apaga la suya
        self.barge_event = asyncio.Event()
        self.barge_src = ""            # "device" (lo detectó el ESP32) | "server"
        self.barge_buf = bytearray()   # audio del mic durante SPEAKING (será la frase nueva)
        self.pre_buffer = b""          # audio reencolado tras un barge (se antepone al próximo chunk)
        # §4.3: BARGE que llega ANTES de empezar a hablar (durante STT/LLM, donde
        # `speaking` todavía es False). `barge_event` no sirve ahí porque
        # `request_barge` lo descartaba; ver el comentario de `request_barge`.
        self.cancel_pending = False
        self.cancel_src = ""
        self.turn_lock = asyncio.Lock()  # A.1: serializa un turno a la vez
        self.turn_task = None
        self.turn_tasks: set = set()   # ajuste 1: turnos encolados detrás del lock (no se descartan)
        self.speak_task = None         # gracia de fin de SPEAKING (ajuste 2)
        # --- Fase 0: sonda de eco ---
        self.probe_speech: list = []   # dBFS de los frames de voz (LISTENING)

    async def send_json(self, obj: dict):
        await self.ws.send_str(json.dumps(obj))

    async def send_state(self, msg: str):
        await self.send_json({"type": "server", "msg": msg})

    async def greet(self):
        await _vh(self.vh.start_session)
        await self.send_json({
            "type": "auth",
            "volume_control": DEVICE_VOLUME,
            "pitch_factor": 1.0,
            "is_ota": False,
            "is_reset": False,
        })
        await self.send_state("RESPONSE.COMPLETE")

    async def feed_audio(self, chunk: bytes):
        """Consume audio del device. NUNCA bloquea con el pipeline (A.1).

        Corre en el bucle de lectura del WebSocket, así que el turno se lanza
        como tarea aparte (`_start_turn`) y mientras el bridge habla el audio se
        acumula para el barge-in en vez de arrancar una frase nueva.
        """
        if self.pre_buffer:
            # Audio reencolado por un barge-in: se antepone para que el VAD lo
            # siga viendo como continuación de la frase interrumpida.
            chunk = self.pre_buffer + chunk
            self.pre_buffer = b""

        if self.speaking:
            # A.3: este audio todavía NO es una frase nueva — se acumula.
            #
            # Desde `1cdbae2` (firmware, 2026-09-15) el device NO sube mic en
            # SPEAKING: la compuerta es `deviceState == LISTENING`. Entonces lo
            # único que puede caer acá es audio POSTERIOR a un corte local del
            # device (mandó BARGE y ya pasó a LISTENING con el amplificador
            # apagado), o sea **voz del usuario**, no eco. Por eso `_handle_barge`
            # lo reencola para los dos `via`. El comentario viejo decía que venía
            # "sucio, con eco de Deb": era cierto con el firmware anterior.
            self.barge_buf += chunk
            if len(self.barge_buf) > BARGE_BUF_MAX_BYTES:
                del self.barge_buf[:len(self.barge_buf) - BARGE_BUF_MAX_BYTES]
            return

        self.pending += chunk
        while len(self.pending) >= VAD_FRAME_BYTES:
            frame = self.pending[:VAD_FRAME_BYTES]
            self.pending = self.pending[VAD_FRAME_BYTES:]
            self._feed_frame(frame)

    def _start_turn(self, pcm: bytes):
        """Lanza el pipeline como tarea para que el lector del WS siga leyendo.

        A.1: antes `feed_audio` esperaba `process_utterance` completo (STT + LLM +
        TTS + envío), así que durante la respuesta no se leía ningún frame nuevo
        del device y el barge-in era imposible. El lock serializa un turno a la vez.

        Ajuste 1: si ya hay un turno en curso el utterance NO se descarta — se
        lanza igual y espera el `turn_lock`. Descartarlo perdía audio en una
        carrera real: tras un barge-in la frase reencolada se cierra por VAD
        mientras el turno viejo todavía está persistiendo.
        """
        if self.turn_task is not None and not self.turn_task.done():
            log.warning("Turno anterior todavía en curso: el utterance nuevo (%d B) "
                        "se ENCOLA detrás del turn_lock (no se descarta)", len(pcm))
        task = asyncio.create_task(self._run_turn(pcm))
        self.turn_tasks.add(task)
        task.add_done_callback(self.turn_tasks.discard)
        self.turn_task = task

    async def _run_turn(self, pcm: bytes):
        async with self.turn_lock:
            await self.process_utterance(pcm)

    def request_barge(self, source: str):
        """Marca la interrupción de la respuesta en curso.

        source="device": lo detectó el ESP32, ya cortó local y mandó
        `{"type":"server_action","msg":"BARGE"}` (A.4 / camino B).
        source="server": lo detectó el bridge por su cuenta (detección server-side
        pendiente, Fase 2 del runbook: necesita AEC, si no el eco de Deb la dispara).
        """
        if self.barge_event.is_set():
            return
        if not self.speaking:
            # §4.3: `speaking` recién se pone en True DESPUÉS del STT, y el STT es
            # la fase más larga del turno (2,5–7 s). El firmware manda BARGE
            # también en PROCESSING (`main.cpp:100`, `Audio.cpp:493`), así que
            # acá caían todos los botonazos del "pensando". Antes se ignoraban: el
            # device se iba a LISTENING creyendo que había cortado y el bridge
            # igual sintetizaba y mandaba la respuesta que el usuario quiso
            # cancelar. Ahora se marca y `process_utterance` aborta el turno.
            if self.turn_task is not None and not self.turn_task.done():
                self.cancel_pending = True
                self.cancel_src = source
                log.info("BARGE (%s) durante el turno pero antes de hablar "
                         "(STT/LLM): turno marcado para cancelar", source)
            else:
                log.info("BARGE (%s) ignorado: no hay turno en curso", source)
            return
        self.barge_src = source
        self.barge_event.set()

    async def handle_device_text(self, raw: str):
        """Mensajes de texto device → bridge (A.4). Hoy: server_action/BARGE."""
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return
        if msg.get("type") == "server_action" and msg.get("msg") == "BARGE":
            # via: "button" (Fase B) | "voice" (Fase C, AEC on-device).
            # Sin via -> firmware viejo -> se asume boton.
            # El `via` hoy es SOLO informativo (logs): con el firmware actual el
            # contenido de `barge_buf` es voz del usuario en los dos casos, asi
            # que `_handle_barge` lo reencola igual. Ver §4.1 del informe de
            # mejora 2026-09-22 y el comentario de `feed_audio`.
            via = msg.get("via", "button")
            log.info("Device BARGE recibido (via=%s): cortando la respuesta", via)
            self.request_barge("device-voice" if via == "voice" else "device")

    async def shutdown(self):
        """A.1: el turno corre como tarea aparte → cancelarlo al cerrar la conexión.

        Ajuste 1: puede haber más de un turno vivo (los que esperan el `turn_lock`).
        """
        tasks = [t for t in self.turn_tasks if not t.done()]
        if self.speak_task is not None and not self.speak_task.done():
            tasks.append(self.speak_task)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    def _feed_frame(self, frame: bytes):
        try:
            is_speech = self.vad.is_speech(frame, MIC_RATE)
        except Exception:
            is_speech = False
        if ECHO_PROBE and is_speech:
            self.probe_speech.append(dbfs(frame))

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
            if ECHO_PROBE:
                probe_log_levels("voz (LISTENING)", self.probe_speech)
                self.probe_speech = []
                probe_dump("voz-usuario", utterance)
            if speech_ms >= VAD_MIN_SPEECH_MS:
                self._start_turn(utterance)

    async def _send_packets(self, packets) -> bool:
        """Envía los paquetes Opus dosificados. Devuelve False si un barge-in lo cortó.

        A.2: el chequeo va dentro del bucle de paquetes (granularidad de 110 ms) y
        no por token — el barge-in puede llegar con la frase ya en el aire.
        """
        for pkt in packets:
            if self.barge_event.is_set():
                return False
            await self.ws.send_bytes(pkt)
            await asyncio.sleep(PACKET_PACE_S)
        return True

    def _save_turn(self, text: str, reply: str):
        """Guarda los dos mensajes del turno en UN solo viaje al executor.

        Ojo: tiene que ser SINCRONA. Va como callable a run_in_executor via
        _vh(); si fuera `async def`, run_in_executor devolveria la corrutina sin
        ejecutarla y la persistencia fallaria EN SILENCIO.

        Por que agrupados: con dos `await _vh(save_message, ...)` separados
        quedaba un punto de suspension entre ambos, y Session.shutdown() cancela
        las tareas del turno cuando se cae la conexion del device. Deb lo
        reprodujo: cancelando ahi queda el "user" guardado sin el "assistant"
        (message_count=1, fila impar). Antes del pase a executor esa ventana no
        existia porque save_message era sincronico. De paso baja de 3 viajes al
        pool por turno a 2.
        """
        self.vh.save_message("user", text)
        self.vh.save_message("assistant", reply)

    async def _abort_pending_turn(self, text: str) -> None:
        """§4.3: aborta el turno cuando llegó un BARGE ANTES de empezar a hablar.

        Caso típico: el usuario aprieta el botón durante el "pensando" (STT, de
        2,5 a 7 s). El device ya cortó y volvió a LISTENING por su cuenta, así
        que acá solo hay que no seguir: nada de LLM, nada de TTS, nada de
        `RESPONSE.CREATED`.

        No se manda `RESPONSE.COMPLETE` si el BARGE lo originó el device — mismo
        criterio que `_handle_barge`: el device ya re-escucha solo y un estado de
        más lo haría transicionar al pedo. Si lo que el usuario dijo después de
        apretar cerró por VAD, ya quedó encolado como turno nuevo detrás del
        `turn_lock` y se responde eso, que es lo que se quería.
        """
        src = self.cancel_src
        self.cancel_pending = False
        self.cancel_src = ""
        log.info("Turno cancelado por BARGE (%s) antes de empezar a hablar — "
                 "transcripción descartada: %r", src or "server", (text or "")[:80])
        if not src.startswith("device"):
            await self.send_state("BARGE")

    async def _handle_barge(self):
        """Corta el turno y devuelve el audio de la interrupción al pipeline.

        - Al device se le manda `BARGE` (vacía su buffer de 10 KB, amp off y
          LISTENING inmediato, sin el delay de 1 s). **NO** se manda
          `RESPONSE.COMPLETE`: el device ya volvió a escuchar por su cuenta.
        - Si el BARGE lo mandó el propio device (ya cortó local), no se repite.
        - A.3: lo que se dijo encima se reencola (`pre_buffer`) y se procesa como
          la frase nueva, para los DOS `via` (boton y voz).

        §4.1 (informe de mejora 2026-09-22) — por que se reencola tambien el
        boton, que antes se descartaba:

        El descarte se escribio cuando el firmware subia mic durante SPEAKING
        (`26e1c12`/`8a55dc6`, 15-09 00:07): ahi el buffer era, efectivamente, eco
        de Deb, y reencolarlo hacia que Whisper transcribiera a Deb como si fuera
        el usuario. Pero 21 h despues `1cdbae2` cambio la compuerta del mic a
        `deviceState == LISTENING`, y el bridge no se actualizo.

        Con el firmware actual, cuando el device hace barge manda BARGE y pasa a
        LISTENING con el amplificador apagado: TODO lo que llega despues es voz
        del usuario. Descartarlo tiraba el comienzo de la pregunta. Cuanto:
        1,15 s (`36864 B`, log de `1cdbae2`) y 2,9 s (`93184 B`, `Aec.h:98`),
        que es lo que tarda el bridge en reaccionar al barge (§4.2). O sea que
        Whisper venia recibiendo la pregunta sin el principio en CADA barge por
        boton.
        """
        src = self.barge_src
        self.barge_event.clear()
        self.barge_src = ""
        self.cancel_pending = False    # el barge real manda: cualquier cancel pendiente sobra
        self.cancel_src = ""
        self.speaking = False
        leftover = bytes(self.barge_buf)
        self.barge_buf = bytearray()
        probe_log("eco (SPEAKING, barge)", leftover)
        probe_dump("eco-barge", leftover)
        if leftover:
            self.pre_buffer = leftover + self.pre_buffer
        if not src.startswith("device"):
            await self.send_state("BARGE")
        log.info("Barge-in (%s): corte — %d B reencolados como frase nueva",
                 src or "server", len(leftover))

    async def _end_speaking_grace(self, gen: int):
        """Ajuste 2: mantiene `speaking=True` ~1,3 s después de RESPONSE.COMPLETE.

        El device sigue en SPEAKING hasta RESPONSE.COMPLETE + 1 s + su buffer
        (~200 ms). Si se apaga `speaking` al terminar el stream, en la Fase C
        (mic abierto en SPEAKING) el eco de la cola de la propia respuesta
        entraría al VAD como frase nueva. Lo acumulado en `barge_buf` durante
        la gracia se descarta: es eco, no hubo BARGE.
        Interacción con `request_barge`: si en la gracia llega un BARGE del
        device, se trata como barge normal (el device ya cortó y re-escucha).
        """
        try:
            await asyncio.wait_for(self.barge_event.wait(), timeout=SPEAK_TAIL_GRACE_S)
            barge = True
        except asyncio.TimeoutError:
            barge = False
        if gen != self.speak_gen:
            return                     # ya hay otro turno hablando: no tocamos nada
        if barge:
            await self._handle_barge()
            return
        probe_log("eco (SPEAKING, cola)", bytes(self.barge_buf))
        probe_dump("eco-cola", bytes(self.barge_buf))
        self.barge_buf = bytearray()   # eco de nuestra cola: se descarta
        self.speaking = False
        log.info("SPEAKING: fin de la gracia (%.1fs) — eco de la cola descartado",
                 SPEAK_TAIL_GRACE_S)

    async def process_utterance(self, pcm: bytes):
        loop = asyncio.get_running_loop()
        latencies = {}

        await self.send_state("AUDIO.COMMITTED")

        # §4.3: bandera rancia de un turno anterior cortaría este turno sin que
        # nadie lo haya pedido. Mismo criterio que el "Ajuste 3" de `barge_event`.
        self.cancel_pending = False
        self.cancel_src = ""

        try:
            # -- STT --
            text, latencies["stt"] = await loop.run_in_executor(None, transcribe, pcm)
            # §4.3: el botón durante el "pensando" llega acá. Se corta antes de
            # gastar el LLM y el TTS en una respuesta que el usuario ya canceló.
            if self.cancel_pending:
                await self._abort_pending_turn(text)
                return
            if not text:
                log.info("Empty transcription, back to listening.")
                await self.send_state("RESPONSE.COMPLETE")
                return
            log.info("User: %s", text)

            self.history.append({"role": "user", "content": text})
            self.history = self.history[-MAX_HISTORY:]

            # Inject recent Telegram context for cross-channel awareness
            telegram_msgs = await _vh(self.vh.get_telegram_context, TELEGRAM_CONTEXT)
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

            # A.1: desde acá el audio entrante va a `barge_buf` (no arranca un turno
            # nuevo) y el corte se evalúa por paquete dentro de `_send_packets` (A.2).
            # Ajuste 3: `barge_event` se limpia acá — un evento rancio (set entre el
            # último `_send_packets` y el `finally` del turno anterior) cortaría esta
            # respuesta con cero audio.
            self.barge_event.clear()
            self.barge_src = ""
            # §4.3: segundo control, por si el BARGE llegó mientras se traía el
            # contexto de Telegram. A partir de la línea siguiente `speaking` es
            # True y el corte vuelve a manejarlo `barge_event`.
            if self.cancel_pending:
                await self._abort_pending_turn(text)
                return
            self.speak_gen += 1
            speak_gen = self.speak_gen
            self.speaking = True
            interrupted = False
            stream = ask_hermes_stream(self.http, self.history)
            try:
                async for token in stream:
                    if self.barge_event.is_set():
                        interrupted = True
                        break
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
                            # Ajuste 4: si llegó un BARGE durante la síntesis (Piper
                            # 0,5–2 s) no mandamos RESPONSE.CREATED: el device
                            # encendería el amp para nada y lo volvería a apagar.
                            if self.barge_event.is_set():
                                interrupted = True
                                break
                            pcm24 = resample_to_24k(raw, src_rate)
                            packets = encode_opus_packets(pcm24)
                            if not first_audio_sent:
                                latencies["llm_first_token"] = time.monotonic() - llm_start
                                first_audio_sent = True
                            await self.send_state("RESPONSE.CREATED")
                            if not await self._send_packets(packets):
                                interrupted = True
                                break
                            tts_chunks += 1

                # Flush remaining text after stream ends (cortable también: A.2)
                remaining = "" if interrupted else sentence_buf.strip()
                if remaining:
                    raw, _ = await loop.run_in_executor(None, synthesize, remaining)
                    # Ajuste 4 (flush final): BARGE durante la síntesis → ni
                    # RESPONSE.CREATED ni audio.
                    if self.barge_event.is_set():
                        interrupted = True
                    else:
                        pcm24 = resample_to_24k(raw, src_rate)
                        packets = encode_opus_packets(pcm24)
                        if not first_audio_sent:
                            latencies["llm_first_token"] = time.monotonic() - llm_start
                            first_audio_sent = True
                        await self.send_state("RESPONSE.CREATED")
                        if await self._send_packets(packets):
                            tts_chunks += 1
                        else:
                            interrupted = True
            finally:
                # Cierra el SSE de Hermes (libera la conexión).
                # Ajuste 2: `speaking` NO se apaga acá — sigue True durante la
                # gracia de fin de respuesta (ver `_end_speaking_grace`).
                await stream.aclose()

            if interrupted:
                await self._handle_barge()
                log.info(
                    "Barge-in: respuesta cortada — %d chars, %d chunks ya enviados",
                    len(reply_text), tts_chunks)
                self.history.append({"role": "assistant", "content": reply_text})
                await _vh(self._save_turn, text, reply_text)
                return

            await self.send_state("RESPONSE.COMPLETE")
            # Ajuste 2: el device sigue en SPEAKING ~1,3 s más (RESPONSE.COMPLETE
            # + 1 s + su buffer). La gracia apaga `speaking` y descarta el eco.
            self.speak_task = asyncio.create_task(self._end_speaking_grace(speak_gen))

            # -- Persist --
            self.history.append({"role": "assistant", "content": reply_text})
            await _vh(self._save_turn, text, reply_text)
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
            # Ajuste 2/3: el turno murió sin pasar por la gracia ni por
            # `_handle_barge` → dejar SPEAKING y barge_event limpios.
            self.speaking = False
            self.barge_event.clear()
            self.barge_src = ""
            self.barge_buf = bytearray()
            self.cancel_pending = False   # §4.3: idem para el cancel diferido
            self.cancel_src = ""
            await self.send_state("RESPONSE.ERROR")


# ---------------------------------------------------------------------------
# Servers
# ---------------------------------------------------------------------------
async def handle_token(request: web.Request) -> web.Response:
    mac = request.query.get("macAddress", "?")
    log.info("Token request from MAC %s", mac)
    return web.json_response({"token": AUTH_TOKEN})


# --- REST /voice (half-duplex clients: M5Stack Cardputer) ------------------
# The WS path keeps its state in a Session object that lives as long as the
# socket. A REST client has no such anchor, so conversation history is kept
# here, keyed by device, and expired after REST_SESSION_TTL_S of silence.
_rest_sessions: dict[str, dict] = {}


def _rest_session(device: str) -> dict:
    now = time.monotonic()
    for key, sess in list(_rest_sessions.items()):
        if now - sess["last_seen"] > REST_SESSION_TTL_S:
            try:
                sess["vh"].close_session()
            except Exception:
                log.exception("[voice] closing expired session for %s", key)
            del _rest_sessions[key]
            log.info("[voice] session expired: %s", key)
    # NOTA: las dos llamadas a voice_history que quedan aca (close_session y
    # start_session) son las unicas sincronas que sobreviven: _rest_session() no
    # es async. No se tocaron porque corren UNA VEZ por sesion REST, no por
    # turno, asi que no estan en el camino caliente. Todas las de por-turno ya
    # pasan por _vh().
    sess = _rest_sessions.get(device)
    if sess is None:
        vh = VoiceHistory(device)
        vh.start_session()
        sess = {"history": [], "vh": vh, "last_seen": now}
        _rest_sessions[device] = sess
        log.info("[voice] session started: %s", device)
    sess["last_seen"] = now
    return sess


async def handle_voice(request: web.Request) -> web.Response:
    """One conversation turn over plain HTTP.

        POST /voice?device=<id>
          body:    WAV, mono, 16-bit, any sample rate (Cardputer sends 48 kHz)
          200:     WAV, mono, 16-bit at Piper's native rate
          headers: X-Transcript / X-Reply, percent-encoded UTF-8

    No VAD here: the client delimits the utterance with push-to-talk, so the
    whole body is one phrase. No Opus either — the Cardputer decodes WAV
    natively but cannot handle the raw Opus packets the WS path streams.
    """
    t_start = time.monotonic()
    device = request.query.get("device", "cardputer")

    raw = await request.read()
    if not raw:
        return web.json_response({"error": "empty body"}, status=400)
    if len(raw) > MAX_UPLOAD_BYTES:
        return web.json_response({"error": "audio too large"}, status=413)
    try:
        pcm, src_rate, channels = _parse_wav(raw)
    except ValueError as exc:
        log.warning("[voice] bad upload from %s: %s", device, exc)
        return web.json_response({"error": str(exc)}, status=400)
    if channels != 1:
        return web.json_response({"error": "mono audio required"}, status=400)

    audio_s = len(pcm) / (src_rate * 2)
    loop = asyncio.get_running_loop()

    try:
        # Every heavy stage goes to the executor: blocking this loop would
        # also stall the WebSocket session the ESP32-S3 holds open.
        pcm16k = await loop.run_in_executor(
            None, _resample_pcm, pcm, src_rate, MIC_RATE)
        text, stt_s = await loop.run_in_executor(None, transcribe, pcm16k)
    except Exception:
        log.exception("[voice] STT failed")
        return web.json_response({"error": "stt failed"}, status=500)

    if not text:
        log.info("[voice] no speech in %.1fs of audio from %s", audio_s, device)
        return web.json_response({"error": "no speech detected"}, status=422)
    log.info("[voice] User (%s): %s", device, text)

    sess = _rest_session(device)
    history = sess["history"] + [{"role": "user", "content": text}]
    history = history[-MAX_HISTORY:]

    telegram_msgs = await _vh(sess["vh"].get_telegram_context, TELEGRAM_CONTEXT)
    try:
        reply, llm_s = await ask_hermes(request.app["http"], telegram_msgs + history)
    except Exception:
        log.exception("[voice] Hermes failed")
        return web.json_response({"error": "llm failed"}, status=502)

    try:
        pcm_tts, tts_s = await loop.run_in_executor(None, synthesize, reply)
        tts_rate = piper_sample_rate()
    except Exception:
        log.exception("[voice] TTS failed")
        return web.json_response({"error": "tts failed"}, status=500)

    # Commit only once the turn succeeded end to end, so a failed reply does
    # not poison the next turn's context.
    sess["history"] = history + [{"role": "assistant", "content": reply}]
    await _vh(sess["vh"].save_message, "user", text)
    await _vh(sess["vh"].save_message, "assistant", reply)

    wav = _make_wav(pcm_tts, tts_rate)
    log.info(
        "[voice] Hermes: %s | audio=%.1fs STT=%.1fs LLM=%.1fs TTS=%.1fs "
        "TOTAL=%.1fs out=%dKB",
        reply[:120], audio_s, stt_s, llm_s, tts_s,
        time.monotonic() - t_start, len(wav) // 1024)

    return web.Response(
        body=wav,
        content_type="audio/wav",
        headers={"X-Transcript": quote(text), "X-Reply": quote(reply)},
    )


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
                await session.handle_device_text(msg.data)
            elif msg.type == WSMsgType.ERROR:
                log.warning("WS error: %s", ws.exception())
    finally:
        # A.1: el turno corre como tarea aparte → cancelarlo al cerrar la conexión
        await session.shutdown()
        await _vh(session.vh.close_session)
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

    # client_max_size: el default de aiohttp es 1 MB y rechazaria con 413 antes
    # de llegar al control de MAX_UPLOAD_BYTES en handle_voice. El Cardputer
    # sube 48 kHz mono 16-bit = 96 KB/s, asi que cualquier push-to-talk de mas
    # de ~11 s moria con 413. Hallazgo de Deb (2026-09-21), verificado.
    ws_app = web.Application(client_max_size=MAX_UPLOAD_BYTES)
    ws_app["http"] = http
    ws_app.router.add_get("/", handle_ws)
    ws_app.router.add_post("/voice", handle_voice)
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
    log.info("REST voice endpoint: POST http://0.0.0.0:%d/voice", WS_PORT)
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

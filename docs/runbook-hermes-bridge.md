# Runbook: puente Hermes — ESP32-S3 Zero

---

## Descripcion general

Procedimiento completo para poner en marcha el **puente ElatoAI ↔ Hermes**:
el ESP32-S3 Zero deja de hablar con la nube de Elato y pasa a usar un servidor
local (Mac mini) que transcribe con faster-whisper, consulta al agente
**Hermes** y sintetiza voz con Piper. Este runbook esta escrito para que
**cualquier agente o persona** pueda ejecutarlo de principio a fin.

> **NOTA:** El codigo del puente ya existe en `server/hermes-bridge/bridge.py`
> y el firmware ya esta cambiado a `DEV_MODE` (commit correspondiente). Lo que
> queda es la instalacion en la Mac mini y ajustar la IP. El diseño y sus
> razones estan en [[propuesta-hermes-bridge]].

---

## Flujo de operacion final

```flowchart
   ESP32 (DEV_MODE)                      Mac mini
        │                                   │
        │ ① GET http://IP:3000/api/         │
        │    generate_auth_token            │──► responde {"token":"..."}
        │                                   │
        │ ② WS ws://IP:8000/                │
        │    (PCM 16 kHz crudo ──────────►) │
        │                                   ├─ VAD (webrtcvad)
        │                                   ├─ STT (faster-whisper)
        │                                   ├─ LLM streaming (Hermes :8642)
        │                                   ├─ TTS por frases (Piper)
        │ (◄────────── Opus 24 kHz + JSON)  ├─ Opus + pacing 110 ms
        ▼                                   ▼
   altavoz MAX98357A                   log en /tmp/hermes-bridge.log
```

El pipeline usa **LLM en modo streaming**: el puente empieza a sintetizar voz
al recibir la primera frase completa del modelo (boundary en `.!?\n`), sin
esperar la respuesta entera. La latencia percibida es mucho menor que en la
version inicial.

---

## Requisitos previos

- Mac mini con **Hermes instalado y funcionando** (`hermes gateway` arranca).
- Homebrew y Python 3.10+ en la Mac mini.
- El repo ElatoAI clonado o copiado en la Mac mini (basta `server/hermes-bridge/`).
- El ESP32-S3 Zero con su montaje validado (ver [[conexionado-por-componente]]).
- Un equipo con PlatformIO para reflashear el firmware (paso 6).

---

## Paso 1 — Habilitar la API de Hermes

En la Mac mini, editar `~/.hermes/.env` y añadir:

```bash
API_SERVER_ENABLED=true
API_SERVER_KEY=elige-una-clave-secreta
```

Reiniciar el gateway y comprobar que aparece la linea del API server:

```bash
hermes gateway
# esperado en el log:  [API Server] API server listening on http://127.0.0.1:8642
```

Verificar con curl:

```bash
curl http://localhost:8642/v1/chat/completions \
  -H "Authorization: Bearer elige-una-clave-secreta" \
  -H "Content-Type: application/json" \
  -d '{"model":"hermes-agent","messages":[{"role":"user","content":"hola"}]}'
```

> **ADVERTENCIA:** La API de Hermes da acceso a todo su toolset (incluida la
> terminal). **No cambiar** `API_SERVER_HOST` de su valor por defecto
> (`127.0.0.1`): el puente corre en la misma maquina y no necesita que Hermes
> escuche en la red.

---

## Paso 2 — Fijar la IP de la Mac mini

El firmware apunta a la IP que le pases al compilar en la variable de entorno
`HERMES_SERVER_IP` (ver Paso 6). Reservar esa IP para la Mac mini en el router
(reserva DHCP) para que nunca cambie.

```bash
# En la Mac mini, para conocer la IP actual:
ipconfig getifaddr en0 || ipconfig getifaddr en1
```

---

## Paso 3 — Instalar el puente

```bash
cd <repo>/server/hermes-bridge
brew install opus                      # libopus, requerido por opuslib
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

El directorio incluye `voice_history.py` (stub funcional). El puente arranca
con el, aunque sin persistencia ni contexto Telegram. Ver seccion
"VoiceHistory y contexto Telegram" mas abajo si se quiere la version completa.

---

## Paso 4 — Descargar la voz de Piper (español)

```bash
mkdir -p ~/piper-voices && cd ~/piper-voices
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx
curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_ES/davefx/medium/es_ES-davefx-medium.onnx.json
```

> **TIP:** Cualquier voz de `rhasspy/piper-voices` sirve (hay varias en
> español). Si se usa otra, ajustar `PIPER_VOICE` en el paso 5. El `.onnx.json`
> es obligatorio: el puente lee de ahi el sample rate.

---

## Paso 5 — Probar el puente manualmente

```bash
cd <repo>/server/hermes-bridge
HERMES_API_KEY=elige-una-clave-secreta \
PIPER_VOICE=~/piper-voices/es_ES-davefx-medium.onnx \
.venv/bin/python3 bridge.py
```

Salida esperada (la primera vez tarda ~2 s: carga el modelo Whisper):

```
... Loading faster-whisper model 'base'...
... Whisper model loaded in 1.8s
... Bridge ready: ws://0.0.0.0:8000/  token http://0.0.0.0:3000/api/generate_auth_token
... Hermes endpoint: http://127.0.0.1:8642/v1/chat/completions (model hermes-agent)
... Whisper model: base | Piper voice: /Users/.../es_ES-davefx-medium.onnx
```

Verificar los endpoints desde otra maquina de la LAN:

```bash
# Token
curl "http://$HERMES_SERVER_IP:3000/api/generate_auth_token?macAddress=TEST"
# esperado: {"token": "elato-local-token"}

# Health (nuevo en version de produccion)
curl http://$HERMES_SERVER_IP:3000/health
curl http://$HERMES_SERVER_IP:8000/health
# esperado: {"status": "ok"}
```

Log por turno de conversacion (nuevo en version de produccion):

```
User: hola como estas
Hermes: Bien, gracias. ¿En qué te puedo ayudar? | STT=0.4s first_token=1.2s chunks=2
```

### Variables de configuracion (todas opcionales)

| Variable | Default | Uso |
|---|---|---|
| `HERMES_API_KEY` | (vacia) | La `API_SERVER_KEY` de Hermes |
| `HERMES_URL` | `http://127.0.0.1:8642/v1/chat/completions` | Endpoint LLM |
| `HERMES_TIMEOUT_S` | `60` | Timeout maximo entre tokens del stream SSE |
| `WHISPER_MODEL` | `base` | Modelo STT (`base` = rapido, `small`/`medium` = mas preciso) |
| `PIPER_VOICE` | `~/piper-voices/es_ES-davefx-medium.onnx` | Voz TTS |
| `BRIDGE_LANGUAGE` | `es` | Idioma para Whisper |
| `BRIDGE_SYSTEM_PROMPT` | (prompt en español) | Personalidad del asistente |
| `VAD_SILENCE_MS` | `800` | Silencio que cierra la frase |
| `VAD_AGGRESSIVENESS` | `1` | Sensibilidad del VAD (0-3; subir si corta frases a mitad) |
| `BRIDGE_AUTH_TOKEN` | `elato-local-token` | Token devuelto al ESP32 (no se valida) |
| `BRIDGE_MAX_HISTORY` | `20` | Mensajes de historial que se mantienen en memoria |
| `BRIDGE_TELEGRAM_CONTEXT` | `5` | Mensajes de Telegram inyectados como contexto (requiere VoiceHistory completo) |

---

## Paso 6 — Firmware: IP y flasheo

El firmware ya esta en `DEV_MODE` + `VOICE_SERVER_DENO`. La IP de la Mac mini
**no esta escrita en el codigo**: se inyecta al compilar desde la variable de
entorno `HERMES_SERVER_IP` (lo hace `firmware-arduino/scripts/hermes_server_ip.py`,
registrado como `extra_scripts` en `platformio.ini`).

```bash
export HERMES_SERVER_IP=192.168.1.100   # <- la IP reservada de tu Mac mini
```

Conviene dejarlo en el `~/.bashrc` (o `~/.zshrc`) del equipo que compila, asi
no hay que acordarse en cada build. Si la IP cambia, se recompila con el nuevo
valor: no hay que tocar `Config.cpp`.

> **AVISO:** si `HERMES_SERVER_IP` no esta definida, el build **no falla** pero
> usa el placeholder `192.168.1.100` de `Config.cpp` y el firmware no va a
> encontrar el puente. El script lo avisa en la salida de `pio run`:
> `[hermes] AVISO: HERMES_SERVER_IP no esta definida...`

Compilar y flashear (el Zero necesita `--no-stub`, ya configurado; detalles en
[[firmware-binarios-y-flasheo]]):

```bash
cd firmware-arduino
pio run -t upload --upload-port /dev/ttyACM0
```

> **NOTA:** El token de Elato guardado en NVS no estorba: en DEV_MODE el
> firmware pide un token nuevo al puente solo si NVS esta vacio, y el puente
> acepta cualquier token (no valida el header). No hace falta borrar NVS.

---

## Paso 7 — Verificacion end-to-end

1. Con el puente corriendo, encender el ESP32.
2. **Log del puente**: debe aparecer `Token request from MAC B8:F8:62:D7:59:C4`
   y luego `Device connected (MAC ...)`.
3. **LED**: verde/amarillo (escuchando) tras el arranque.
4. **Hablar** una frase corta y callar: el puente muestra `User: <texto>` y el
   altavoz responde antes de que Hermes termine (streaming). El log muestra
   `STT=Xs first_token=Xs chunks=N` al finalizar el turno.
5. El **boton KY-004** sigue funcionando igual (dormir/despertar).

Monitor serie del ESP32 (opcional): `[WSc] Connected to url: /`, luego
`AUDIO.COMMITTED` / `RESPONSE.CREATED` / `RESPONSE.COMPLETE` en cada turno.
`RESPONSE.CREATED` aparece varias veces por turno (una por frase sintetizada).

---

## Paso 8 — Dejarlo como servicio (launchd)

```bash
cd <repo>/server/hermes-bridge
# 1. Editar com.elato.hermes-bridge.plist: rutas reales del venv/repo,
#    HERMES_API_KEY y PIPER_VOICE (buscar "AJUSTAR").
cp com.elato.hermes-bridge.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.elato.hermes-bridge.plist
# logs:
tail -f /tmp/hermes-bridge.log
```

Con `KeepAlive` el puente se reinicia solo si se cae, y arranca al encender la
Mac mini. El ESP32 reintenta la conexion WebSocket cada 1 s automaticamente,
asi que un reinicio del puente se recupera sin tocar la placa.

Para detenerlo limpiamente (el bridge registra la señal en el log):

```bash
launchctl stop com.elato.hermes-bridge   # lo detiene; KeepAlive lo relanza
launchctl unload ~/Library/LaunchAgents/com.elato.hermes-bridge.plist  # lo desactiva
```

---

## VoiceHistory y contexto Telegram

El repo incluye `voice_history.py` como stub funcional: el puente arranca y
conversa normalmente, pero sin persistencia ni integracion con Telegram.

La implementacion completa (disponible en la Mac mini de DebBot) añade:
- Persistencia del historial de conversacion por MAC del dispositivo.
- `get_telegram_context()`: inyecta los ultimos N mensajes del canal de
  Telegram al historial antes de consultar a Hermes, de modo que el asistente
  de voz sabe lo que se hablo en ese canal.

Para activarla, sustituir `voice_history.py` por la version completa de DebBot.
La variable `BRIDGE_TELEGRAM_CONTEXT` controla cuantos mensajes se inyectan
(default 5; poner 0 para desactivar aunque el modulo completo este instalado).

---

## Rollback a la nube de Elato

En `firmware-arduino/src/Config.h`, invertir los defines y reflashear:

```cpp
// #define DEV_MODE
#define ELATO_MODE
...
// #define VOICE_SERVER_DENO
#define VOICE_SERVER_CLOUDFLARE
```

Nada mas: los certificados y URLs de Elato siguen intactos en `Config.cpp`.
Tambien existe el respaldo `~/Firmware_ELATO_ok_limpio_merged.bin` (ver
[[firmware-binarios-y-flasheo]]) que restaura la placa completa a la
configuracion Elato con un solo comando.

---

## Solucion de problemas

| Sintoma | Causa probable | Solucion |
|---|---|---|
| `Piper voice not found` al arrancar | Falta la voz o la ruta es otra | Paso 4; ajustar `PIPER_VOICE` |
| `Hermes HTTP 401` en el log | `HERMES_API_KEY` no coincide | Usar la misma `API_SERVER_KEY` de `~/.hermes/.env` |
| `Hermes HTTP` con connection refused | Hermes no corre o API no habilitada | Paso 1; `hermes gateway` activo |
| El ESP32 no pide token | IP mal puesta en `Config.cpp` o firewall | Verificar paso 6 y el firewall de macOS (permitir Python) |
| Conecta pero nunca transcribe | VAD no detecta voz o ruido de fondo alto | Bajar `VAD_AGGRESSIVENESS` a 0 o subir a 2; revisar el micro |
| Transcribe frases vacias | Doble-VAD (no deberia ocurrir con `vad_filter=False`) | Verificar que se usa la version de produccion del bridge |
| El altavoz corta a mitad de la primera frase | VAD_SILENCE_MS demasiado bajo | Subir `VAD_SILENCE_MS` a 1000 |
| Audio entrecortado en el altavoz | CPU saturada; pacing roto | Cerrar cargas pesadas; usar `WHISPER_MODEL=base` |
| `first_token` muy alto (>5 s) en el log | Hermes usa herramientas (agentico) | Configurar `model_routes` en Hermes para respuestas directas |
| `ImportError: opuslib` | Falta libopus | `brew install opus` |
| `ModuleNotFoundError: voice_history` | Falta el archivo stub | Verificar que `voice_history.py` esta en el mismo directorio que `bridge.py` |
| WS se desconecta cada ~60 s | heartbeat no configurado (version antigua) | Usar la version de produccion (`heartbeat=30.0`) |
| El puente no registra la señal de parada | Version antigua sin signal handlers | Usar la version de produccion |

---

## Referencia rapida

```
MAC MINI:
  ~/.hermes/.env             -> API_SERVER_ENABLED=true, API_SERVER_KEY=...
  server/hermes-bridge/      -> bridge.py + voice_history.py + requirements.txt + plist
  ~/piper-voices/            -> voz TTS (.onnx + .onnx.json)
  puertos: 3000 (token+health), 8000 (WS+health), 8642 (Hermes, solo local)

HEALTH CHECK:
  curl http://$HERMES_SERVER_IP:3000/health  ->  {"status": "ok"}
  curl http://$HERMES_SERVER_IP:8000/health  ->  {"status": "ok"}

FIRMWARE (firmware-arduino/):
  src/Config.h    -> #define DEV_MODE + #define VOICE_SERVER_DENO
  IP del puente   -> env HERMES_SERVER_IP (inyectada al compilar)
  flasheo         -> pio run -t upload --upload-port /dev/ttyACM0

SERVICIO:
  launchctl load   ~/Library/LaunchAgents/com.elato.hermes-bridge.plist
  launchctl stop   com.elato.hermes-bridge   # parada temporal (KeepAlive lo relanza)
  launchctl unload ~/Library/LaunchAgents/com.elato.hermes-bridge.plist  # desactivar
  tail -f /tmp/hermes-bridge.log

LOG POR TURNO:
  User: <texto>
  Hermes: <respuesta> | STT=0.4s first_token=1.2s chunks=2

ROLLBACK: Config.h -> ELATO_MODE + VOICE_SERVER_CLOUDFLARE y reflashear.
```

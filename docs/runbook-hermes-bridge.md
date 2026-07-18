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
        │                                   ├─ LLM (Hermes :8642)
        │                                   ├─ TTS (Piper)
        │ (◄────────── Opus 24 kHz + JSON)  ├─ Opus + pacing 110 ms
        ▼                                   ▼
   altavoz MAX98357A                   log en /tmp/hermes-bridge.log
```

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

El firmware apunta a una IP fija. Reservar la IP de la Mac mini en el router
(reserva DHCP) para que nunca cambie. Anotarla; se usa en los pasos 6 y 7.

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

Salida esperada (la primera vez tarda: descarga el modelo Whisper):

```
... Loading faster-whisper model 'small'...
... Whisper model loaded.
... Bridge ready: ws://0.0.0.0:8000/  token http://0.0.0.0:3000/api/generate_auth_token
... Hermes endpoint: http://127.0.0.1:8642/v1/chat/completions (model hermes-agent)
```

Verificar el endpoint del token desde otra maquina de la LAN:

```bash
curl "http://<IP-mac-mini>:3000/api/generate_auth_token?macAddress=TEST"
# esperado: {"token": "elato-local-token"}
```

### Variables de configuracion (todas opcionales)

| Variable | Default | Uso |
|---|---|---|
| `HERMES_API_KEY` | (vacia) | La `API_SERVER_KEY` de Hermes |
| `HERMES_URL` | `http://127.0.0.1:8642/v1/chat/completions` | Endpoint LLM |
| `WHISPER_MODEL` | `small` | Modelo STT (`base` = mas rapido, `medium` = mas preciso) |
| `PIPER_VOICE` | `~/piper-voices/es_ES-davefx-medium.onnx` | Voz TTS |
| `BRIDGE_LANGUAGE` | `es` | Idioma para Whisper |
| `BRIDGE_SYSTEM_PROMPT` | (prompt en español) | Personalidad del asistente |
| `VAD_SILENCE_MS` | `800` | Silencio que cierra la frase |
| `BRIDGE_AUTH_TOKEN` | `elato-local-token` | Token devuelto al ESP32 (no se valida) |

---

## Paso 6 — Firmware: IP y flasheo

El firmware ya esta en `DEV_MODE` + `VOICE_SERVER_DENO`. Solo falta poner la
IP real de la Mac mini (paso 2) en **dos lineas** de
`firmware-arduino/src/Config.cpp` (bloque `#ifdef DEV_MODE`, ~lineas 43 y 54):

```cpp
const char *ws_server = "192.168.1.50";      // <- IP de la Mac mini
...
const char *backend_server = "192.168.1.50"; // <- la misma IP
```

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
4. **Hablar** una frase y callar: el log muestra `User: <transcripcion>`,
   luego `Hermes: <respuesta>`, el LED pasa a rojo (procesa) y azul (habla),
   y el altavoz reproduce la respuesta.
5. El **boton KY-004** sigue funcionando igual (dormir/despertar).

Monitor serie del ESP32 (opcional): `[WSc] Connected to url: /`, luego
`AUDIO.COMMITTED` / `RESPONSE.CREATED` / `RESPONSE.COMPLETE` en cada turno.

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
| Conecta pero nunca transcribe | El VAD no detecta voz (ruido de fondo alto) | Subir `VAD_AGGRESSIVENESS` a 3 o revisar el micro |
| Transcribe frases vacias/cortadas | Silencio de cierre muy corto | Subir `VAD_SILENCE_MS` (p. ej. 1000) |
| Audio entrecortado en el altavoz | Se perdio el pacing (CPU saturada) | Cerrar cargas pesadas; whisper `base` en vez de `small` |
| Respuestas muy lentas | Hermes usa herramientas (agentico) | Configurar `model_routes` en Hermes para respuestas directas |
| `ImportError: opuslib` | Falta libopus | `brew install opus` |
| El puente muere al desconectar el ESP32 | — (no debe pasar; reconexion soportada) | Revisar `/tmp/hermes-bridge.err` y reportar |

---

## Referencia rapida

```
MAC MINI:
  ~/.hermes/.env             -> API_SERVER_ENABLED=true, API_SERVER_KEY=...
  server/hermes-bridge/      -> bridge.py + requirements.txt + plist
  ~/piper-voices/            -> voz TTS (.onnx + .onnx.json)
  puertos: 3000 (token HTTP), 8000 (WS audio), 8642 (Hermes, solo local)

FIRMWARE (firmware-arduino/):
  src/Config.h    -> #define DEV_MODE + #define VOICE_SERVER_DENO
  src/Config.cpp  -> ws_server y backend_server = IP de la Mac mini
  flasheo         -> pio run -t upload --upload-port /dev/ttyACM0

SERVICIO:
  launchctl load ~/Library/LaunchAgents/com.elato.hermes-bridge.plist
  tail -f /tmp/hermes-bridge.log

ROLLBACK: Config.h -> ELATO_MODE + VOICE_SERVER_CLOUDFLARE y reflashear.
```

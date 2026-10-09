# Puente ElatoAI ↔ Hermes

Servidor local (Mac mini) que reemplaza a la nube de Elato: recibe el audio del
ESP32, transcribe con **faster-whisper**, consulta al agente **Hermes**
(`localhost:8642/v1/chat/completions`) y responde con voz **Piper** codificada
en Opus.

- **Guía completa de instalación**: [docs/runbook-hermes-bridge.md](../../docs/runbook-hermes-bridge.md)
- **Diseño y decisiones**: [docs/propuesta-hermes-bridge.md](../../docs/propuesta-hermes-bridge.md)
- **Claves por aparato y tareas largas**: [docs/claves-y-tareas-largas.md](../../docs/claves-y-tareas-largas.md)

## Arranque rápido

```bash
brew install opus
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# descargar una voz de piper (ver runbook, paso 4)
HERMES_API_KEY=<tu-key> .venv/bin/python3 bridge.py
```

El ESP32 debe tener el firmware en `DEV_MODE` + `VOICE_SERVER_DENO`, compilado
con `HERMES_SERVER_IP=<IP de esta máquina>` (ver runbook, paso 6).

Cada aparato necesita **su propia clave** (desde 2026-10-08 el bridge corre en
`BRIDGE_AUTH_MODE=enforce` y rechaza al que no la tenga):

```bash
python3 devices_admin.py add esp32-parlante          # se muestra UNA vez
python3 devices_admin.py add cardputer --tecleable   # fácil de tipear
python3 devices_admin.py list | revoke | enable | delete <nombre>
```

Si Hermes pasa 25 s sin texto, el aparato avisa y el resultado llega por
Telegram (`hermes send`). Detalle de todo en
[docs/claves-y-tareas-largas.md](../../docs/claves-y-tareas-largas.md).

## Pruebas

```bash
.venv/bin/python3 tests/test_long_task_and_auth.py   # tareas largas + claves
.venv/bin/python3 tests/test_barge_state.py          # máquina de estados del barge-in
```

`check_barge_contract.py` (contrato firmware↔bridge; 18/20 es lo normal) vive
solo en el repo de Deb en la Mac mini: `~/Proyectos/experimentos/hermes-bridge/tests/`.

## Puertos

| Puerto | Protocolo | Uso |
|---|---|---|
| 3000 | HTTP | `/health`. `GET /api/generate_auth_token` (clave compartida vieja) solo en modo `transition`; en `enforce` da 404 |
| 8000 | WebSocket + HTTP | Audio bidireccional con el ESP32; `POST /voice` del Cardputer; `/health` |
| 8642 | HTTP (solo localhost) | API de Hermes (la consume el puente) |

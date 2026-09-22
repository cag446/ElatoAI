# Puente ElatoAI ↔ Hermes

Servidor local (Mac mini) que reemplaza a la nube de Elato: recibe el audio del
ESP32, transcribe con **faster-whisper**, consulta al agente **Hermes**
(`localhost:8642/v1/chat/completions`) y responde con voz **Piper** codificada
en Opus.

- **Guía completa de instalación**: [docs/runbook-hermes-bridge.md](../../docs/runbook-hermes-bridge.md)
- **Diseño y decisiones**: [docs/propuesta-hermes-bridge.md](../../docs/propuesta-hermes-bridge.md)

## Arranque rápido

```bash
brew install opus
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# descargar una voz de piper (ver runbook, paso 4)
HERMES_API_KEY=<tu-key> .venv/bin/python3 bridge.py
```

El ESP32 debe tener el firmware en `DEV_MODE` + `VOICE_SERVER_DENO` con la IP
de esta máquina en `Config.cpp` (ver runbook, paso 6).

## Puertos

| Puerto | Protocolo | Uso |
|---|---|---|
| 3000 | HTTP | `GET /api/generate_auth_token` (token para el ESP32) |
| 8000 | WebSocket | Audio bidireccional con el ESP32 |
| 8642 | HTTP (solo localhost) | API de Hermes (la consume el puente) |

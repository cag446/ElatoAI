# Documentacion — ElatoAI sobre ESP32-S3 Zero + agente Hermes

Indice de la documentacion para operar el hardware ELATO (placa **Waveshare
ESP32-S3 Zero**, LED RGB WS2812 integrado y boton KY-004) contra el **agente
Hermes** local (Mac mini de la LAN), sin nube de Elato ni servicios de pago.

El hardware y el conexionado son **identicos** a la configuracion Elato
([README_ELATO.md](README_ELATO.md)); lo que cambia es a donde habla el
firmware y quien responde. Cada documento esta disponible en **Markdown**
(editable) y **PDF** (estilizado para imprimir/compartir).

## Documentos

| Documento | Descripcion | Markdown | PDF |
|---|---|---|---|
| **Runbook: puente Hermes** | Instalacion paso a paso ejecutable por cualquier agente: API de Hermes, puente en la Mac mini (launchd), voz Piper, firmware en `DEV_MODE`, verificacion y rollback. **Empieza aqui.** | [runbook-hermes-bridge.md](runbook-hermes-bridge.md) | [runbook-hermes-bridge.pdf](runbook-hermes-bridge.pdf) |
| **Propuesta: integracion Hermes** | Diseño y contexto del puente ESP32 ↔ Hermes: hechos verificados del protocolo con referencias archivo:linea, arquitectura, decisiones y riesgos. Contexto para LLMs que analicen el proyecto. | [propuesta-hermes-bridge.md](propuesta-hermes-bridge.md) | [propuesta-hermes-bridge.pdf](propuesta-hermes-bridge.pdf) |
| **Port al ESP32-S3 Zero** | Adaptacion de hardware (comun a ambas configuraciones): LED WS2812 (GPIO21), boton (GPIO2), particiones de 4 MB, `platformio.ini`, mapa de pines y diagrama de conexionado. | [port-esp32-s3-zero.md](port-esp32-s3-zero.md) | [port-esp32-s3-zero.pdf](port-esp32-s3-zero.pdf) |
| **Conexionado por componente** | Diagrama de conexion uno por uno: INMP441 (micro), MAX98357A + altavoz 8 Ω, boton y WS2812 integrado. Igual para Elato y Hermes. | [conexionado-por-componente.md](conexionado-por-componente.md) | [conexionado-por-componente.pdf](conexionado-por-componente.pdf) |
| **Firmware, binarios y flasheo** | Binarios, offsets, `firmware.bin` vs merged, como flashear (`--no-stub`) y respaldos — incluye el respaldo para volver a Elato. | [firmware-binarios-y-flasheo.md](firmware-binarios-y-flasheo.md) | [firmware-binarios-y-flasheo.pdf](firmware-binarios-y-flasheo.pdf) |

El codigo del puente vive en [`server/hermes-bridge/`](../server/hermes-bridge/)
(`bridge.py` + `requirements.txt` + plantilla launchd + README propio).

## Diferencias respecto a la configuracion Elato

| Aspecto | Elato (README_ELATO) | Hermes (este README) |
|---|---|---|
| Servidor | Nube de Elato (`talkedge.deno.dev`, Cloudflare) | Puente local `bridge.py` en la Mac mini (`$HERMES_SERVER_IP`) |
| Modo del firmware (`Config.h`) | `ELATO_MODE` + `VOICE_SERVER_CLOUDFLARE` | `DEV_MODE` + `VOICE_SERVER_DENO` |
| Transporte | WSS con TLS + certificados CA | WS y HTTP **sin TLS** (LAN domestica) |
| Token | JWT real de elatoai.com (registro de la MAC en la web) | Fijo (`elato-local-token`), sin registro ni cuenta |
| Puertos | 443 (WS), 3000 (backend Vercel) | 8000 (WS), 3000 (token), 8642 (Hermes, solo localhost) |
| STT / LLM / TTS | OpenAI Realtime (nube, limite 30 min/mes gratis) | faster-whisper + Hermes + Piper (todo local, sin limites) |
| Personalidad del agente | Se elige en elatoai.com | `BRIDGE_SYSTEM_PROMPT` (env del puente) + config de Hermes |
| Requisito de red | Internet | Solo LAN (la Mac mini encendida con el puente corriendo) |
| Hardware y conexionado | — | **Sin cambios** (mismo montaje validado) |

Cambios concretos en el repo para el modo Hermes (ya aplicados y compilados):

1. `firmware-arduino/src/Config.h` → `#define DEV_MODE` + `#define VOICE_SERVER_DENO`
   (antes `ELATO_MODE` + `VOICE_SERVER_CLOUDFLARE`).
2. `firmware-arduino/src/Config.cpp` → `ws_server` y `backend_server` =
   `$HERMES_SERVER_IP` (IP reservada de la Mac mini); ademas, certificados vacios
   en `DEV_MODE` para satisfacer al linker (`FactoryReset.h`).
3. Nuevo `server/hermes-bridge/` con el puente completo.

> **NOTA:** El rollback a Elato es trivial: invertir los dos defines de
> `Config.h` y reflashear (ver runbook, seccion "Rollback"). Nada de la
> configuracion Elato se ha borrado.

## Por donde empezar

1. **Hardware ya montado y validado** → si no, empieza por
   [README_ELATO.md](README_ELATO.md) (port + conexionado + prueba con la nube).
2. **Desplegar el puente en la Mac mini** → *Runbook: puente Hermes*,
   pasos 1-5 y 8 (API de Hermes, venv, voz Piper, prueba manual, launchd).
3. **Flashear el firmware** → runbook paso 6 (ya esta configurado; solo
   `pio run -t upload --upload-port /dev/ttyACM0`).
4. **Verificar la conversacion** → runbook paso 7.
5. **Entender el diseño** (opcional) → *Propuesta: integracion Hermes*.

## Regenerar los PDF

Los PDF se generan a partir de los `.md` con el skill `gen-pdf-doc`
(usa `python3` con `markdown` y `weasyprint`). Si editas un `.md`, vuelve a
generar su PDF para mantenerlos sincronizados.

## Resumen de la configuracion Hermes (referencia rapida)

| Aspecto | Valor |
|---|---|
| Placa | Waveshare ESP32-S3 Zero (ESP32-S3FH4R2) — montaje sin cambios |
| Servidor | `bridge.py` (aiohttp) en la Mac mini `$HERMES_SERVER_IP` |
| Puertos del puente | 3000 (HTTP token), 8000 (WebSocket audio) |
| Agente | Hermes gateway, API OpenAI-compatible en `127.0.0.1:8642` (solo localhost) |
| STT | faster-whisper `small` (int8, CPU, idioma `es`) |
| TTS | Piper `es_ES-davefx-medium` (22 kHz → resample a 24 kHz) |
| Audio subida | PCM crudo 16 kHz mono 16-bit (frames binarios WS) |
| Audio bajada | Opus 24 kHz mono, frames de 120 ms, 1 paquete cada 110 ms |
| Fin de frase | VAD webrtcvad en el puente (800 ms de silencio) |
| Servicio | launchd `com.elato.hermes-bridge` (`KeepAlive`), logs en `/tmp/hermes-bridge.log` |
| Firmware | `DEV_MODE` + `VOICE_SERVER_DENO`; flasheo con `--no-stub` (ya en `platformio.ini`) |

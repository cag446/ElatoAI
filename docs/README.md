# Documentacion — ElatoAI sobre ESP32-S3 Zero

Indice de la documentacion tecnica del port de ElatoAI a la placa
**Waveshare ESP32-S3 Zero**, con LED RGB WS2812 integrado y boton tactil.

Cada documento esta disponible en **Markdown** (editable) y **PDF** (estilizado
para imprimir/compartir).

## Documentos

| Documento | Descripcion | Markdown | PDF |
|---|---|---|---|
| **Port al ESP32-S3 Zero** | Adaptacion de hardware: LED WS2812 (GPIO21), boton tactil (GPIO2), tabla de particiones de 4 MB, `platformio.ini`, mapa de pines y **diagrama de conexionado**. | [port-esp32-s3-zero.md](port-esp32-s3-zero.md) | [port-esp32-s3-zero.pdf](port-esp32-s3-zero.pdf) |
| **Conexionado por componente** | Diagrama de conexion uno por uno: INMP441 (micro), MAX98357A + altavoz 8 Ω, touch y WS2812 integrado. | [conexionado-por-componente.md](conexionado-por-componente.md) | [conexionado-por-componente.pdf](conexionado-por-componente.pdf) |
| **Etapa 1: probar con la nube de Elato** | Receta paso a paso para registrar la MAC y dejar la placa hablando con el servidor gratuito de Elato (validar el hardware). | [etapa-1-elato-cloud.md](etapa-1-elato-cloud.md) | [etapa-1-elato-cloud.pdf](etapa-1-elato-cloud.pdf) |
| **Registro del dispositivo y backend** | Logica de servidor: los 3 componentes (frontend/Supabase, servidor edge, ESP32), alta de la MAC, flujo del token JWT y modos `DEV`/`PROD`/`ELATO`. | [registro-dispositivo-backend.md](registro-dispositivo-backend.md) | [registro-dispositivo-backend.pdf](registro-dispositivo-backend.pdf) |
| **Firmware, binarios y flasheo** | Que es cada binario (bootloader/particiones/app), offsets, `firmware.bin` vs binario **merged**, como flashear y respaldos. | [firmware-binarios-y-flasheo.md](firmware-binarios-y-flasheo.md) | [firmware-binarios-y-flasheo.pdf](firmware-binarios-y-flasheo.pdf) |

## Por donde empezar

1. **Montar y flashear la placa** → empieza por *Port al ESP32-S3 Zero*
   (conexionado, compilacion y carga del firmware).
2. **Conectar el dispositivo a la nube** → continua con *Registro del
   dispositivo y backend* (alta de la MAC, credenciales y eleccion de modo).

## Regenerar los PDF

Los PDF se generan a partir de los `.md` con el skill `gen-pdf-doc`
(usa `python3` con `markdown` y `weasyprint`). Si editas un `.md`, vuelve a
generar su PDF para mantenerlos sincronizados.

## Resumen del port (referencia rapida)

| Aspecto | Valor |
|---|---|
| Placa | Waveshare ESP32-S3 Zero (ESP32-S3FH4R2) |
| Flash / PSRAM | 4 MB / 2 MB quad (QSPI) |
| LED RGB | WS2812 integrado en GPIO21 (Adafruit NeoPixel) |
| Boton | Fisico en GPIO2 (KY-004; touch descartado por no fiable en protoboard) |
| Microfono I2S | INMP441 (SD=GPIO8, WS=4, SCK=1) |
| Altavoz I2S | MAX98357A + 8 Ohm (LRC=5, BCLK=6, DIN=7, SD=10) |
| USB | USB-C nativo (USB-CDC para el monitor; flasheo con `--no-stub`) |
| Uso de flash tras compilar | ~62 % (1.23 MB / 1.875 MB por slot OTA) |

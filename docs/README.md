# Documentacion — ElatoAI sobre ESP32-S3 Zero

Indice de la documentacion tecnica del port de ElatoAI a la placa
**Waveshare ESP32-S3 Zero**, con LED RGB WS2812 integrado y boton tactil.

Cada documento esta disponible en **Markdown** (editable) y **PDF** (estilizado
para imprimir/compartir).

## Documentos

| Documento | Descripcion | Markdown | PDF |
|---|---|---|---|
| **Port al ESP32-S3 Zero** | Adaptacion de hardware: LED WS2812 (GPIO21), boton tactil (GPIO2), tabla de particiones de 4 MB, `platformio.ini`, mapa de pines y **diagrama de conexionado**. | [port-esp32-s3-zero.md](port-esp32-s3-zero.md) | [port-esp32-s3-zero.pdf](port-esp32-s3-zero.pdf) |
| **Registro del dispositivo y backend** | Logica de servidor: los 3 componentes (frontend/Supabase, servidor edge, ESP32), alta de la MAC, flujo del token JWT y modos `DEV`/`PROD`/`ELATO`. | [registro-dispositivo-backend.md](registro-dispositivo-backend.md) | [registro-dispositivo-backend.pdf](registro-dispositivo-backend.pdf) |

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
| Boton | Tactil en GPIO2 (`TOUCH_MODE`) |
| USB | USB-C nativo (USB-CDC para el monitor serie) |
| Uso de flash tras compilar | 62.4 % (1.225 MB / 1.875 MB por slot OTA) |

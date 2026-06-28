# ESP32 WebSocket Audio Client

For more details, visit the [Elato Firmware Docs](https://www.elatoai.com/docs/blog/firmware).

## Port a la ESP32-S3 Zero (Waveshare)

Este firmware esta adaptado a la **Waveshare ESP32-S3 Zero**, con LED RGB WS2812
integrado (GPIO21) y boton tactil (GPIO2). Documentacion del port (Markdown + PDF):

- **[Port al ESP32-S3 Zero](../docs/port-esp32-s3-zero.md)** ([PDF](../docs/port-esp32-s3-zero.pdf)) — adaptacion de hardware: LED WS2812, boton tactil, tabla de particiones de 4 MB, `platformio.ini`, mapa de pines y diagrama de conexionado.
- **[Registro del dispositivo y backend](../docs/registro-dispositivo-backend.md)** ([PDF](../docs/registro-dispositivo-backend.pdf)) — logica de servidor: alta de la MAC, flujo del token JWT y modos `DEV`/`PROD`/`ELATO`.

Indice completo en [`docs/`](../docs/README.md).

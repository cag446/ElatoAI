# Port del firmware al ESP32-S3 Zero — ElatoAI

---

## Descripcion general

Este documento describe la **adaptacion (port) del firmware de ElatoAI** desde
la placa de referencia (ESP32-S3 DevKitC-1 con 16 MB de flash y LED RGB de 3
pines) a la **Waveshare ESP32-S3 Zero**, con dos variaciones de hardware
solicitadas:

1. **LED RGB integrado WS2812** del propio Zero (en GPIO21) en lugar del LED RGB
   de 3 pines analogicos (anodo comun) del proyecto original.
2. **Boton tactil** (touch) en lugar del interruptor / pulsador mecanico.

La placa es una **ESP32-S3FH4R2**: doble nucleo Xtensa LX7 a 240 MHz, **4 MB de
flash** y **2 MB de PSRAM (quad/QSPI)**, WiFi + BLE5, y **solo USB-C nativo**
(no tiene chip conversor UART). El firmware sigue usando WebSockets seguros y el
codec Opus; **no cambia nada de la logica de red ni de audio** respecto al
original: el port es puramente de hardware y de configuracion de compilacion.

> **NOTA:** El cambio mas critico no son el LED ni el touch, sino la **flash de
> 4 MB**. El proyecto original estaba configurado para 16 MB (particiones de
> 2M+2M de app + 3M de SPIFFS), lo que **no cabe** en el Zero. Hubo que rehacer
> la tabla de particiones a 4 MB con OTA dual.

---

## Flujo de operacion principal

```flowchart
        platformio.ini
        (env esp32-s3-zero)
               │
               ▼
        ¿Flash de 4 MB?
        /             \
      SI               NO
       │                │
       ▼                ▼
  partition.csv     (no aplica:
  4MB OTA dual       el Zero es 4MB)
       │
       ▼
  Compilacion ──► Flash 62.4% / RAM 16.3%
       │
       ▼
  Modo descarga (BOOT + USB-C)
       │
       ▼
  pio run -t upload   +   uploadfs (startup.mp3)
       │
       ▼
  Arranque: ledTask -> WS2812 (GPIO21)
            touchTask -> TOUCH_PAD_NUM2 (GPIO2)
```

---

## Arquitectura

```
┌──────────────────────────────────────────────────────────────────┐
│  CONFIGURACION DE COMPILACION                                      │
│  platformio.ini   -> board S3, flash 4MB, PSRAM quad, USB-CDC,    │
│                      libreria Adafruit NeoPixel                    │
│  partition.csv    -> nvs + otadata + app0/app1 (1.875MB) + spiffs  │
└───────────────┬──────────────────────────────────────────────────┘
                │ define pines y constantes
                ▼
┌──────────────────────────────────────────────────────────────────┐
│  CAPA DE CONFIGURACION DE HARDWARE                                 │
│  Config.h / Config.cpp                                             │
│   - RGB_LED_PIN = 21, NUM_LEDS = 1, LED_BRIGHTNESS = 50           │
│   - BUTTON_PIN = GPIO_NUM_2 (touch)                               │
│   - I2S mic (SD/WS/SCK) y altavoz (WS/BCK/DATA/SD)               │
└───────────────┬───────────────────────────┬──────────────────────┘
                │                            │
                ▼                            ▼
┌───────────────────────────┐   ┌───────────────────────────────────┐
│  LEDHandler.cpp/.h         │   │  main.cpp                         │
│  Adafruit_NeoPixel ──►     │   │  touchTask -> TOUCH_PAD_NUM2      │
│  WS2812 en GPIO21          │   │  ledTask  -> maquina de estados   │
│  setLEDColor(), ledTask()  │   │  (TOUCH_MODE activo)             │
└───────────────────────────┘   └───────────────────────────────────┘
```

---

## Archivos involucrados

| Archivo | Rol |
|---|---|
| `firmware-arduino/platformio.ini` | Entorno `esp32-s3-zero`: flash 4 MB, PSRAM quad, USB-CDC nativo, libreria NeoPixel, tabla de particiones. |
| `firmware-arduino/partition.csv` | Nueva tabla de 4 MB con OTA dual (2x ~1.875 MB) + SPIFFS (128 KB). |
| `firmware-arduino/src/Config.h` | Declara `RGB_LED_PIN`, `NUM_LEDS`, `LED_BRIGHTNESS`; mantiene `BUTTON_PIN`; activa `TOUCH_MODE`. |
| `firmware-arduino/src/Config.cpp` | Define `RGB_LED_PIN = 21`, `NUM_LEDS = 1`, `LED_BRIGHTNESS = 50`, pines I2S y `BUTTON_PIN`. |
| `firmware-arduino/src/LEDHandler.cpp` | Reescrito para manejar el WS2812 unico via Adafruit NeoPixel (misma API publica). |
| `firmware-arduino/src/LEDHandler.h` | Cabecera de la API del LED (sin cambios de firma). |
| `firmware-arduino/src/main.cpp` | `touchTask` (lectura de `TOUCH_PAD_NUM2`), `ledTask`, despertar por touch. |

---

## Funcionamiento interno

### Cambio 1 — LED RGB de 3 pines  →  WS2812 integrado (GPIO21)

El proyecto original controlaba un LED RGB de **anodo comun** con tres pines
analogicos (R=9, G=8, B=13) usando `analogWrite` / `digitalWrite` con logica
invertida. El Zero trae un unico **LED direccionable WS2812** en **GPIO21**, que
se controla por protocolo de un solo hilo (RMT). Se reescribio
`LEDHandler.cpp` para usar la libreria **Adafruit NeoPixel**, manteniendo
**identica la API publica** (`setLEDColor`, `turnOffLED`, `ledTask`, etc.), de
modo que el resto del firmware no necesito tocarse.

Antes (3 pines, anodo comun):

```cpp
void setLEDColor(uint8_t r, uint8_t g, uint8_t b) {
    analogWrite(RED_LED_PIN, r);
    analogWrite(GREEN_LED_PIN, g);
    analogWrite(BLUE_LED_PIN, b);
}
```

Despues (WS2812 unico):

```cpp
static Adafruit_NeoPixel rgbLed(NUM_LEDS, RGB_LED_PIN, NEO_GRB + NEO_KHZ800);

void setLEDColor(uint8_t r, uint8_t g, uint8_t b) {
    rgbLed.setPixelColor(0, rgbLed.Color(r, g, b));
    rgbLed.show();
}
```

Los colores ahora son **directos** (sin la inversion del anodo comun). La
maquina de estados de `ledTask` se conserva igual:

| Estado del dispositivo | Color |
|---|---|
| IDLE | Verde |
| SOFT_AP (portal WiFi) | Magenta |
| PROCESSING | Rojo |
| SPEAKING | Azul |
| LISTENING | Amarillo |
| OTA | Cian |

> **TIP:** El WS2812 ciega a brillo maximo. Se añadio `LED_BRIGHTNESS = 50`
> (tope 0-255) aplicado una vez en `setupRGBLED()` con `rgbLed.setBrightness()`.
> Sube o baja ese valor en `Config.cpp` para ajustar la intensidad.

### Cambio 2 — Interruptor  →  boton tactil (GPIO2)

Esta variacion **ya estaba implementada** en el firmware y solo hubo que
confirmarla: `Config.h` define `TOUCH_MODE`, lo que activa en `main.cpp` la
tarea `touchTask`, que lee el canal tactil **`TOUCH_PAD_NUM2` = GPIO2**:

```cpp
#ifdef TOUCH_MODE
  xTaskCreate(touchTask, "Touch Task", 4096, NULL, configMAX_PRIORITIES - 2, NULL);
#else
  // ... pulsador mecanico con esp_sleep_enable_ext0_wakeup(BUTTON_PIN, LOW)
#endif
```

El `touchTask` detecta un **toque largo (500 ms)** para dormir el dispositivo, y
el deep sleep se reanuda con `touchSleepWakeUpEnable(TOUCH_PAD_NUM2, ...)` (tap
para despertar). El umbral esta en `TOUCH_THRESHOLD = 28000` (valor tipico del
ESP32-S3, donde el valor de `touchRead` **sube** al tocar).

> **TIP:** Para calibrar el umbral en tu montaje real usa `test/touch_test.cpp`,
> que imprime los valores de `touchRead(2)` por el monitor serie. Ajusta
> `TOUCH_THRESHOLD` en `main.cpp` segun lo que veas.

### Cambio 3 — Flash de 16 MB  →  4 MB (tabla de particiones)

El Zero solo tiene **4 MB**. La nueva `partition.csv` mantiene OTA dual (dos
slots de app) con un SPIFFS pequeño (el `startup.mp3` pesa solo ~40 KB):

```
# Name,     Type, SubType,  Offset,   Size,    Flags
nvs,        data, nvs,      0x9000,   0x5000,
otadata,    data, ota,      0xe000,   0x2000,
app0,       app,  ota_0,    0x10000,  0x1E0000,
app1,       app,  ota_1,    0x1F0000, 0x1E0000,
spiffs,     data, spiffs,   0x3D0000, 0x20000,
coredump,   data, coredump, 0x3F0000, 0x10000,
```

Cada slot de app es **0x1E0000 = 1.875 MB**. La compilacion real confirmo que
entra con holgura:

```
RAM:   [==        ]  16.3% (used 53516 bytes from 327680 bytes)
Flash: [======    ]  62.4% (used 1225853 bytes from 1966080 bytes)
```

> **NOTA:** Quedan ~37 % de margen en cada slot de app, suficiente para futuras
> ampliaciones y para que el OTA dual siga funcionando como en la placa de
> 16 MB.

### Cambio 4 — platformio.ini (placa, PSRAM y USB)

```ini
[env:esp32-s3-zero]
board = esp32-s3-devkitc-1          ; base S3 generica; flash/psram se ajustan abajo
board_build.flash_mode = qio
board_build.arduino.memory_type = qio_qspi   ; PSRAM del FH4R2 es QUAD (no octal/opi)
board_upload.flash_size = 4MB
board_upload.maximum_size = 4194304
board_build.partitions = partition.csv

lib_deps =
    adafruit/Adafruit NeoPixel@^1.12.3
    ; ... (resto de librerias del proyecto)

build_flags =
    -D BOARD_HAS_PSRAM             ; activa los 2 MB de PSRAM
    -D ARDUINO_USB_MODE=1          ; USB nativo (el Zero no tiene chip UART)
    -D ARDUINO_USB_CDC_ON_BOOT=1   ; expone Serial por USB-C para el monitor
```

> **ADVERTENCIA:** El FH4R2 lleva PSRAM **quad (QSPI)**. Configurar `memory_type`
> como octal (`qio_opi`) provocaria fallos de arranque. Usa `qio_qspi`.

> **NOTA:** Sin `ARDUINO_USB_CDC_ON_BOOT=1` no veras nada en el monitor serie,
> porque el Zero solo tiene USB-C nativo (no hay puente UART como en el DevKit).

---

## Manual de usuario

### Requisitos previos

- Placa **Waveshare ESP32-S3 Zero** (ESP32-S3FH4R2, 4 MB flash / 2 MB PSRAM).
- **PlatformIO** instalado (CLI o extension de VS Code).
- Cable **USB-C** de datos.
- Microfono I2S y altavoz/amplificador I2S del proyecto.
- Superficie o pad conductor para el **boton tactil**.

### Mapa de pines (ESP32-S3 Zero)

| Funcion | Señal | GPIO |
|---|---|---|
| LED RGB integrado | WS2812 (DIN) | 21 |
| Boton tactil | Touch (TOUCH2) | 2 |
| Microfono I2S | SD / DOUT | 14 |
| Microfono I2S | WS / LRCLK | 4 |
| Microfono I2S | SCK / BCLK | 1 |
| Altavoz I2S | WS / LRCLK | 5 |
| Altavoz I2S | BCK / BCLK | 6 |
| Altavoz I2S | DATA / DIN | 7 |
| Altavoz I2S | SD (shutdown) | 10 |

> **NOTA:** Todos los pines I2S caen en el rango GPIO 1-14, que el Zero expone en
> sus cabeceras. Los antiguos pines del LED (8, 9, 13) quedan **libres** para
> otros usos.

### Diagrama de conexionado

Conexionado de los perifericos a la ESP32-S3 Zero. El WS2812 ya viene **soldado
en la placa** (GPIO21), no hay que cablearlo. El microfono usa un modulo I2S
(p. ej. INMP441) y el altavoz un amplificador I2S (p. ej. MAX98357A).

```flowchart
                     ┌───────────────────────────────┐
                     │     ESP32-S3 ZERO (Waveshare)  │
                     │                                │
   MICROFONO I2S     │  GPIO1  ◄────── SCK / BCLK     │
  ┌──────────────┐   │  GPIO4  ◄────── WS  / LRCLK    │
  │ INMP441      │   │  GPIO14 ──────► SD  / DOUT     │
  │  SCK ───────────►│                                │
  │  WS  ───────────►│  GPIO5  ──────► WS  / LRCLK    │   ALTAVOZ I2S
  │  SD  ───────────►│  GPIO6  ──────► BCK / BCLK    ┌┼──────────────┐
  │  VDD ── 3V3      │  GPIO7  ──────► DATA / DIN    ││ MAX98357A    │
  │  GND ── GND      │  GPIO10 ──────► SD (shutdown) ││  LRC ◄───────│
  └──────────────┘   │                                ││  BCLK◄───────│
                     │  GPIO2  ◄────── PAD TACTIL    ─┼┼─►DIN         │
   PAD TACTIL        │  GPIO21 ─── WS2812 (en placa)  ││  SD  ◄───────│
   (cable a una      │                                ││  VIN ── 5V   │
    superficie  ─────►│  3V3 ── alimentacion 3.3V     ││  GND ── GND  │
    conductora)      │  5V  ── alimentacion altavoz   ││  +  ─► altavoz│
                     │  GND ── comun                  │└──────────────┘
                     └───────────────────────────────┘
```

Resumen de conexiones (una fila por cable):

| Desde (periferico) | Pin periferico | Hacia (Zero) |
|---|---|---|
| Microfono I2S | SCK / BCLK | GPIO1 |
| Microfono I2S | WS / LRCLK | GPIO4 |
| Microfono I2S | SD / DOUT | GPIO14 |
| Microfono I2S | VDD / GND | 3V3 / GND |
| Amplificador I2S | LRC / WS | GPIO5 |
| Amplificador I2S | BCLK | GPIO6 |
| Amplificador I2S | DIN / DATA | GPIO7 |
| Amplificador I2S | SD (shutdown) | GPIO10 |
| Amplificador I2S | VIN / GND | 5V / GND |
| Pad tactil | cable unico | GPIO2 |
| WS2812 | (integrado) | GPIO21 (no cablear) |

> **ADVERTENCIA:** El modulo de microfono I2S se alimenta a **3.3 V**, no a 5 V.
> El amplificador MAX98357A si admite 5 V en `VIN` y entrega mas potencia al
> altavoz a ese voltaje. Verifica el pinout de tu modulo concreto, ya que los
> nombres de las señales (DOUT/SD, LRC/WS) varian entre fabricantes.

> **NOTA:** Este conexionado refleja el mapa de pines definido en `Config.cpp`.
> Si tu placa de prototipos exige otros pines, cambialos alli y vuelve a
> compilar; cualquier GPIO libre del rango expuesto sirve para I2S.

### Compilar y flashear

1. **Entra en modo descarga** (el Zero no tiene boton RESET/EN separado para
   esto): manten pulsado **BOOT (GPIO0)**, conecta el USB-C y suelta BOOT.

2. Compila y sube el firmware:

   ```bash
   cd firmware-arduino
   pio run -t upload
   ```

3. Sube el sistema de archivos (incluye `startup.mp3`):

   ```bash
   pio run -t uploadfs
   ```

4. Abre el monitor serie (funciona por USB-CDC nativo):

   ```bash
   pio device monitor
   ```

> **TIP:** Tras flashear, pulsa el boton **RESET** o reconecta el USB-C. Con
> USB-CDC el puerto se re-enumera al reiniciar; es normal que el monitor se
> reconecte solo.

### Personalizacion

| Quieres cambiar... | Archivo / linea |
|---|---|
| Pin del LED WS2812 | `Config.cpp` -> `RGB_LED_PIN` |
| Brillo del LED | `Config.cpp` -> `LED_BRIGHTNESS` |
| Colores por estado | `LEDHandler.cpp` -> `ledTask()` / `setStaticColor()` |
| Pin / umbral del touch | `main.cpp` -> `TOUCH_PAD_NUM2`, `TOUCH_THRESHOLD` |
| Touch vs pulsador | `Config.h` -> comentar/descomentar `#define TOUCH_MODE` |
| Pines I2S | `Config.cpp` -> bloque de pines I2S |

> **ADVERTENCIA:** Si cambias `RGB_LED_PIN` a un pin distinto del 21, recuerda
> que el WS2812 *soldado en la placa* seguira en GPIO21; solo tiene sentido
> cambiarlo si conectas una tira WS2812 externa.

---

## Solucion de problemas

| Sintoma | Causa probable | Solucion |
|---|---|---|
| No se ve nada en el monitor serie | Falta USB-CDC (el Zero no tiene chip UART) | Confirma `ARDUINO_USB_MODE=1` y `ARDUINO_USB_CDC_ON_BOOT=1` en `platformio.ini` |
| `pio run -t upload` no detecta la placa | No esta en modo descarga | Manten BOOT pulsado, conecta USB-C, suelta BOOT |
| Arranca en bucle / panic al inicio | `memory_type` de PSRAM incorrecto | Usa `qio_qspi` (PSRAM quad del FH4R2), no `qio_opi` |
| El LED no enciende o color erroneo | Pin u orden de color del WS2812 | Verifica `RGB_LED_PIN = 21` y `NEO_GRB` en `LEDHandler.cpp` |
| El LED ciega / consume mucho | Brillo al maximo | Baja `LED_BRIGHTNESS` en `Config.cpp` |
| El touch no responde o salta solo | Umbral mal calibrado | Mide con `test/touch_test.cpp` y ajusta `TOUCH_THRESHOLD` |
| Error de compilacion: la app no cabe | Particion de app demasiado pequeña | Usa la `partition.csv` de 4 MB de este port (slots de 1.875 MB) |
| Error al flashear: tamaño excede flash | `flash_size` configurado a 16 MB | Pon `board_upload.flash_size = 4MB` |
| No conecta a la nube tras flashear | Falta registro/credenciales | Ver el documento "Registro del dispositivo y backend" |

---

## Referencia rapida

```
PLACA:  Waveshare ESP32-S3 Zero (ESP32-S3FH4R2)
        4 MB flash · 2 MB PSRAM quad · WS2812 GPIO21 · BOOT GPIO0 · USB-C nativo

PINES CLAVE
  WS2812 (LED)     GPIO21
  Touch            GPIO2  (TOUCH_PAD_NUM2)
  Mic  I2S         SD=14  WS=4   SCK=1
  Spk  I2S         WS=5   BCK=6  DATA=7  SD=10
  Libres tras port GPIO 8, 9, 13 (antiguo LED RGB)

ARCHIVOS DEL PORT
  platformio.ini   env esp32-s3-zero · flash 4MB · PSRAM quad · USB-CDC · NeoPixel
  partition.csv    OTA dual 1.875MB + SPIFFS 128KB (4MB total)
  Config.h/.cpp    RGB_LED_PIN=21 · NUM_LEDS=1 · LED_BRIGHTNESS=50 · TOUCH_MODE
  LEDHandler.cpp   Adafruit_NeoPixel (WS2812)
  main.cpp         touchTask (GPIO2) · ledTask

RESULTADO DE COMPILACION
  Flash 62.4% (1.225 MB / 1.875 MB)   RAM 16.3% (53.5 KB / 327 KB)

COMANDOS
  pio run                  # compilar
  pio run -t upload        # flashear (BOOT + USB-C primero)
  pio run -t uploadfs      # subir startup.mp3 a SPIFFS
  pio device monitor       # logs por USB-CDC

ESTADOS DEL LED:  IDLE=verde  SOFT_AP=magenta  PROCESSING=rojo
                  SPEAKING=azul  LISTENING=amarillo  OTA=cian
```

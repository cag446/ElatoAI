# Asistente de voz full-duplex sobre ESP32-S3 — contexto del proyecto

> **Para quién es este documento.** Para un agente (o una persona) que llega sin
> contexto previo y quiere **reusar este trabajo en otro proyecto**, combinándolo
> con software open source ya existente. No es un runbook de instalación —para
> eso están `runbook-hermes-bridge.md` y `firmware-binarios-y-flasheo.md`—, es la
> **fuente de conocimiento**: qué se construyó, sobre qué se apoya, qué se midió,
> qué funciona, qué no, y dónde están las costuras para enchufar otra cosa.
>
> Última actualización: **2026-09-22**. Licencia del repo: MIT.

---

## 1. De qué se trata en un párrafo

Un altavoz inteligente casero: una placa **ESP32-S3 Zero** con micrófono y
parlante que mantiene una conversación por voz con un agente LLM que corre en
**una máquina de la LAN doméstica**, sin nube, sin cuentas y sin límites de uso.
El aporte propio del proyecto —y la razón de este documento— es que se le agregó
**barge-in**: la capacidad de **interrumpir al asistente mientras habla**, por
botón y por voz, que es lo que separa un juguete de algo que se puede usar a
diario.

Todo el stack de voz (STT, LLM, TTS) es local y open source. El único hardware
no reemplazable es el ESP32.

---

## 2. Sobre qué repositorios se construyó

### 2.1 La base: ElatoAI

| | |
|---|---|
| **Upstream** | https://github.com/akdeb/ElatoAI (MIT) |
| **Fork con todo este trabajo** | https://github.com/cag446/ElatoAI |
| **Qué aporta** | Firmware Arduino/PlatformIO para ESP32 que hace streaming de audio bidireccional por WebSocket con Opus, máquina de estados de conversación, LED de estado, OTA, gestión de WiFi |
| **Qué asume** | Que del otro lado está la **nube de Elato** (`talkedge.deno.dev` + Cloudflare + OpenAI Realtime), con cuenta, JWT y un límite de 30 min/mes en el plan gratuito |

ElatoAI es el punto de partida correcto si lo que se quiere es el **transporte de
audio embebido resuelto**: I2S de entrada y salida, Opus, WebSocket, y una
máquina de estados que ya funciona. Lo que no aporta es autonomía: sin su nube,
el dispositivo es un ladrillo.

### 2.2 Lo que se reemplazó: el servidor

Se sustituyó toda la nube por un puente local propio, **`bridge.py`**, que vive
en este mismo fork en [`server/hermes-bridge/`](../server/hermes-bridge/). Es un
único archivo Python (~1000 líneas) que habla el **mismo protocolo** que
esperaba el firmware, de modo que el firmware casi no hubo que tocarlo para el
cambio de backend (sí para el barge-in, que es otra historia).

Piezas open source sobre las que se apoya el puente:

| Pieza | Para qué | Notas de integración |
|---|---|---|
| **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** | STT | `device="cpu"`, `compute_type="int8"`. En un i5-3210M de 2012 tarda 2,5-7 s por frase; es el cuello de botella del turno |
| **[Piper](https://github.com/rhasspy/piper)** | TTS | Se invoca como binario externo (`PIPER_BIN`), voz `es_ES-davefx-medium.onnx`. Sale a 22050 Hz y hay que remuestrear a 24 k |
| **[webrtcvad](https://github.com/wiseman/py-webrtcvad)** | Detección de voz / fin de frase | Frames de 30 ms a 16 kHz |
| **[opuslib](https://github.com/orion-labs/opuslib)** | Codec | Requiere `libopus` del sistema (`brew install opus`) |
| **aiohttp** | WebSocket + HTTP | Servidor asyncio; ver §7, tiene trampas |
| **numpy** | Remuestreo | |

El **LLM** es intercambiable por diseño: el puente le habla a un endpoint
**compatible con la API de OpenAI** (`POST /v1/chat/completions`). En esta
instalación es un agente propio llamado *Hermes* en `127.0.0.1:8642`, pero
cualquier cosa que exponga ese contrato sirve: Ollama, llama.cpp server,
LM Studio, vLLM, LocalAI. **Este es el punto de integración más limpio del
proyecto.**

### 2.3 Librerías del firmware

De `firmware-arduino/platformio.ini`:

| Librería | Uso |
|---|---|
| `links2004/WebSockets` | Cliente WS. **Ojo:** ver §7, su `write()` reintenta hasta 5 s |
| `pschatzmann/arduino-audio-tools` | Cadena de audio, I2S, `VolumeStream` |
| `pschatzmann/arduino-libopus` | Decodificador Opus |
| `rjsachse/ESP32-SpeexDSP` | **AEC** (cancelación de eco) — el núcleo de la Fase C |
| `adafruit/Adafruit NeoPixel` | LED WS2812 de estado |
| `esp-arduino-libs/ESP32_Button` | Botón. **Ojo:** ver §7, clasifica las pulsaciones de forma poco fiable |

---

## 3. Arquitectura

```
   ┌──────────────────────────┐         WiFi / LAN          ┌────────────────────────────┐
   │      ESP32-S3 Zero       │      (WS sin TLS)           │   Máquina local (Mac mini) │
   │                          │                             │                            │
   │  INMP441 ──I2S──► micTask│ ──── PCM 16 kHz mono ──────►│  webrtcvad  (fin de frase) │
   │                    │     │      (WebSocket binario)    │      │                     │
   │                    ▼     │                             │      ▼                     │
   │                  [AEC]   │                             │  faster-whisper  (STT)     │
   │                    │     │                             │      │                     │
   │              ¿voz sobre  │                             │      ▼                     │
   │               la resp.?  │                             │  LLM vía /v1/chat/         │
   │                    │     │                             │  completions (OpenAI-compat)│
   │                    ▼     │                             │      │                     │
   │            {"server_     │ ─── BARGE (texto JSON) ─────►│      ▼                     │
   │             action":     │                             │  Piper  (TTS) → PCM 22 k   │
   │             "BARGE"}     │                             │      │                     │
   │                          │                             │      ▼ resample 24 k       │
   │  audioStreamTask ◄──I2S──│◄─── Opus 24 kHz, paquetes ──│  Opus enc, 1 paquete de    │
   │      │                   │     de 120 ms c/110 ms      │  120 ms cada 110 ms        │
   │      ▼                   │                             │                            │
   │  MAX98357A ──► altavoz   │                             │                            │
   │      │                   │                             └────────────────────────────┘
   │      └──► tee de referencia ──► AEC  (lazo de cancelación)
   └──────────────────────────┘
```

**Protocolo** (heredado de ElatoAI, extendido por este proyecto):

- **Audio device → servidor**: frames binarios PCM 16-bit mono 16 kHz.
- **Audio servidor → device**: paquetes binarios Opus, 24 kHz, 120 ms por
  paquete, enviados **uno cada 110 ms** (`PACKET_PACE_S`). El ritmo es **por
  tiempo, no por bitrate**: subir o bajar la calidad no cambia la latencia.
- **Control**: mensajes de texto JSON. El proyecto agregó
  `{"type":"server_action","msg":"BARGE","via":"button|voice"}` en sentido
  device → servidor.

**Puertos**: 8000 WS (audio), 3000 HTTP (token), 8642 el LLM (solo localhost).

---

## 4. Hardware

| Componente | Modelo | Alimentación |
|---|---|---|
| MCU | Waveshare **ESP32-S3 Zero** (ESP32-S3FH4R2, 4 MB flash, sin PSRAM) | USB |
| Micrófono | **INMP441** (I2S) | **3.3 V — nunca 5 V** |
| Amplificador | **MAX98357A** (I2S) | 5 V recomendado para 8 Ω |
| Parlante | 8 Ω | A las salidas del amp |
| Botón | KY-004 físico | 3.3 V |
| LED | WS2812 integrado | GPIO21, interno |

**Pinout** (ya reflejado en el firmware):

| Señal | GPIO | | Señal | GPIO |
|---|---|---|---|---|
| INMP441 SCK | 1 | | MAX98357A LRC | 5 |
| INMP441 WS | 4 | | MAX98357A BCLK | 6 |
| INMP441 SD | **8** | | MAX98357A DIN | 7 |
| INMP441 L/R | GND | | MAX98357A SD (enable) | 10 |
| Botón | 2 | | WS2812 | 21 |

> El SD del micrófono va a **GPIO8, no a GPIO14**: en el ESP32-S3 Zero el GPIO14
> está en los pads de la cara inferior (paso 2.00 mm) y no llega a la protoboard.

El diagrama completo está en [`conexionado-por-componente.md`](conexionado-por-componente.md)
y como imagen en [`conexionado-modulos.svg`](conexionado-modulos.svg).

---

## 5. El aporte propio: barge-in en tres fases

Interrumpir a un asistente que está hablando parece trivial y no lo es: el
dispositivo tiene **un solo micrófono a centímetros de su propio parlante**, y
mientras reproduce audio se oye a sí mismo mucho más fuerte de lo que oye al
usuario.

### Fase A — que el servidor sepa cortarse (bridge)

Cambios en `bridge.py` para que una respuesta en curso se pueda abortar limpio:

- `turn_lock`: serializa un turno a la vez; los turnos nuevos **se encolan, no se
  descartan** (descartarlos perdía audio del usuario).
- El audio del usuario que llega **mientras el bridge habla** se acumula en
  `barge_buf` y, si llega un BARGE, **se reencola como la frase nueva**. Esto es
  lo que hace que puedas interrumpir *con* tu pregunta en vez de interrumpir y
  después repetirla.
- Manejo del mensaje de control `server_action/BARGE`.

### Fase B — barge por botón (firmware)

Pulsar el botón durante la respuesta manda el BARGE. Suena simple; el detalle
está en §7 (el clasificador de pulsaciones de la librería no es fiable y hubo que
enrutar **por estado**, no por tipo de gesto).

**Funciona siempre, en cualquier condición.** Es el camino robusto.

### Fase C — barge por voz (firmware, AEC)

La parte difícil. `Aec.h` / `Aec.cpp` implementan:

1. Un **tee de referencia** transparente (`AecReferenceTee`, un `Print` insertado
   entre el `VolumeStream` y la salida I2S) que copia lo que va al parlante.
2. Remuestreo de esa referencia 24 kHz → 16 kHz y un **ring buffer lock-free**.
3. **Medición automática del retardo** por correlación cruzada mic-vs-referencia
   al empezar cada respuesta (el retardo real es DMA de salida + acústica + DMA
   de entrada; adivinarlo no funciona).
4. **Cancelador lineal** `speex_echo_cancellation()` + **supresor de eco
   residual** `speex_preprocess_run()` con `SPEEX_PREPROCESS_SET_ECHO_STATE`.
5. **Detector de voz sostenida**: umbral absoluto (−32 dBFS) **más**
   persistencia (160 ms / 40 frames). Un umbral relativo al piso disparaba con
   cualquier pico.

---

## 6. Cómo quedó funcionando (estado verificado)

| Camino | Estado | Condición |
|---|---|---|
| Conversación completa local, sin nube | ✅ | — |
| **Barge por botón** | ✅ **Siempre** | Ninguna |
| **Barge por voz** | ✅ **Condicionado** | Usuario a **20-30 cm del micrófono** a volumen 70; o a distancia normal con el volumen bajo a 25 |
| Barge por voz manos libres a volumen alto | ❌ | Requiere separación física (ver abajo) |

### Los números medidos — esta es la parte reusable

Todo esto se midió en hardware, no se estimó:

| Magnitud | Valor |
|---|---|
| Voz del usuario en el mic, distancia normal | **−34,8 dBFS** (mediana), −29,4 (p90) |
| Eco del propio parlante | **−41,7 dBFS** (mediana), −30,6 (p90), −24,4 (máx) |
| Voz del usuario a 20-30 cm | **−23,3 dBFS** |
| Cancelación del AEC lineal sobre el eco *medio* | ~16 dB |
| Cancelación sobre los **picos** | **~3 dB** ← el problema |
| Aporte del supresor de residuo | +8,6 dB en el pico (−27,1 → −35,7 dB) |
| Memoria del AEC (frame 64 / filter 512) | ~48 KB |
| Modelo de memoria de speex (verificado contra `mdf.c`) | `~24 × filter_length + ~104 × frame_size + ~14 KB` |
| Bloque contiguo que libopus reserva **en el primer decode** | **60 KB** (`GLOBAL_STACK_SIZE`) |
| Pico de stack de speex dentro de la tarea del mic | ~2,5 KB |
| Caída del acople acústico | **6 dB al duplicar la distancia** |
| Eco vs. volumen | **Proporcional** (5,2 dB medidos al pasar de vol 50 a 25, contra 6,0 teóricos) → el eco viaja **por aire** |
| STT (faster-whisper int8, i5-3210M) | 2,5-7 s por frase |
| Codificación Opus | 2,3 ms por paquete de 120 ms, igual a 24 que a 48 kbps |

### La conclusión que importa para quien reuse esto

**El límite del barge por voz no es el algoritmo: es la relación voz/eco en el
micrófono.** Si la voz del usuario llega más débil que los picos del eco, ningún
AEC las separa —ni el detector de doble-habla de speex—, porque el eco de los
picos es **no lineal** (distorsión del amplificador clase D + vibración del
montaje) y un cancelador lineal no puede cancelarlo.

Se llegó a esta conclusión después de darla por cerrada **dos veces mal**: una
vez "cerrada por software" (se había medido a una sola distancia) y otra
reabierta al descubrir que hablando cerca funcionaba perfecto. La lección
metodológica: **medir a varias distancias y volúmenes antes de concluir que algo
es un límite del software.**

Lo que resuelve el caso manos libres es **layout físico**: separar micrófono y
parlante 10+ cm, parlante apuntando lejos, micrófono desacoplado mecánicamente.
Eso está pendiente (unidad soldada).

---

## 7. Trampas verificadas — leé esto antes de tocar el código

Cada una de estas costó horas de depuración. Son transferibles a cualquier
proyecto parecido.

**Embebido / FreeRTOS**

1. **La tarea del parlante debe ser la de mayor prioridad de su core.** Si algo
   pesado (el AEC en la tarea del micrófono) la preempta, el buffer de salida se
   vacía y **se oye como carraspeo/crujido**. Configuración final:
   `audioStreamTask` prioridad **6**, `micTask` prioridad **3**, ambas en el core 1.
2. **Nunca deshabilitar interrupciones (`portENTER_CRITICAL`) en la ruta del
   audio.** Con un único productor y un único consumidor alcanza con escribir los
   datos y **publicar el índice volátil después**. Un ring buffer con sección
   crítica producía el mismo carraspeo.
3. **speexdsp usa el *stack de la tarea*, no el heap**, para sus temporales. 4 KB
   desbordaban; hicieron falta 10240 B en `micTask`.
4. **libopus reserva un bloque contiguo de 60 KB de forma diferida, en el primer
   decode.** Si el AEC ya se llevó el heap, no falla al inicializar: **crashea
   después**, en `silk_decode_frame`, con `StoreProhibited`. Por eso el
   guardarrail de `Aec.h` no mira el heap *antes* de inicializar sino el que
   **queda después** (`AEC_MIN_FREE_AFTER = 80 KB`).
5. **`links2004/WebSockets`: `write()` reintenta hasta 5 s** manteniendo tomado el
   mutex del llamador. Si se manda audio del micrófono mientras el socket está
   saturado, el `EAGAIN` bloquea todo el cliente WS (síntoma: LED azul y mudez).
   Solución: el micrófono solo transmite en estado LISTENING, con backoff de
   250 ms y `xSemaphoreTake` con timeout corto.
6. **`ESP32_Button` clasifica las pulsaciones de forma impredecible** y
   `attachPressDownEventCb` no siempre dispara. Un "crash" de la Fase B resultó
   ser `Button long press end → Going to sleep...`. Solución: enrutar **todos**
   los gestos según el estado de la conversación, no según el tipo de pulsación.
7. **USB-CDC nativo del ESP32-S3**: flashear requiere `--no-stub`.
8. **El volumen del firmware es ganancia lineal**, no logarítmica: 70 → 50 son
   solo −3 dB. Para −9 dB hay que bajar a 25.

**Python / asyncio**

9. **Cualquier llamada sincrónica lenta en un handler async congela el bridge
   entero**: no lee el WebSocket (ni el BARGE que ya está en el socket) ni envía
   audio. Medido: `voice_history` con sqlite sincrónico congelaba el loop
   **540 ms**; movido a `run_in_executor`, **7 ms**.
10. **`time.sleep()` dentro de un reintento async** es la peor variante del punto
    anterior: hasta 300 ms de bloqueo duro por turno.
11. **`aiohttp` limita el body a 1 MB por default.** Si hay un `MAX_UPLOAD_BYTES`
    mayor, hay que pasar `client_max_size` explícitamente o el 413 llega antes
    que la validación propia.
12. **Cuidado al mover código a un executor**: convertir en `async def` una
    función que después se pasa a `run_in_executor` hace que el executor reciba
    una **corrutina sin ejecutar**. No falla: **deja de persistir en silencio**.
13. **Un tramo sincrónico no tiene puntos de suspensión; al partirlo en dos
    `await` aparece una ventana de cancelación** que antes no existía. Al mover
    dos escrituras a un executor hay que hacerlas **en un solo viaje**, o una
    cancelación a mitad deja el estado incompleto.

**Método**

14. **Ante un síntoma nuevo, preguntar PRIMERO desde cuándo pasa.** Esa pregunta
    resolvió el carraspeo después de tres hipótesis erradas.
15. **Un contador crudo no es evidencia; importa *cuándo* cae el evento.** Los
    mismos 17 timeouts de PONG dicen "el servidor está roto" si solo se cuentan,
    y "el firmware duerme mal" si se mira que ninguno cae durante un turno activo.
16. **"Cero errores" sin tráfico real no vale nada.** Un log limpio sobre una
    ventana en la que el dispositivo no se conectó ni una vez es evidencia de
    vacío, no de salud.

---

## 8. Qué está abierto

| Ítem | Estado |
|---|---|
| **Unidad soldada con mic y parlante separados** | Pendiente. Es lo único que daría volumen alto **y** barge manos libres a la vez |
| **Timeouts de PONG en reposo** | 17 en ocho días, ninguno durante un turno. Evidencia apunta a la rama IDLE del firmware (`delay()` largo, espera bloqueante de I2S, o modem-sleep de WiFi que no despierta al task de red), no al servidor. Sin confirmar |
| `_rest_session` a async | El barrido de expirados corre en cada turno REST |
| El bridge no valida el `Bearer` | `BRIDGE_AUTH_TOKEN` no se chequea. Aceptable en LAN, **no** si se expone |
| `voice_history.py` en el repo es un **stub** | La implementación real (persistencia + contexto) corre solo en la máquina local |

---

## 9. Dónde enchufar otro software open source

Esta sección es el encargo: **qué es reusable, qué está acoplado, y por dónde
entra otra cosa.** Los proyectos externos que se nombran son **pistas a
verificar**, no integraciones probadas.

### 9.1 Las costuras limpias (bajo esfuerzo)

| Costura | Contrato | Qué se puede enchufar |
|---|---|---|
| **LLM** | `POST /v1/chat/completions` (OpenAI-compat) | Ollama, llama.cpp server, vLLM, LocalAI, LM Studio. **Cambio de una variable de entorno** |
| **TTS** | Binario que recibe texto por stdin y devuelve PCM | Cualquier TTS con CLI; Piper es solo el default |
| **STT** | Función que recibe PCM y devuelve texto | whisper.cpp, Vosk, o un servicio remoto |
| **Transporte de control** | JSON por WebSocket | Cualquier evento nuevo se agrega igual que `BARGE` |

### 9.2 Lo que está acoplado (alto esfuerzo)

- **El ritmo de envío y el tamaño de paquete Opus** (120 ms / 110 ms / 24 kHz)
  están acordados entre firmware y servidor. Cambiarlos requiere tocar los dos.
- **El tee de referencia del AEC** depende de la cadena de `arduino-audio-tools`.
  Portarlo a otro framework de audio implica reimplementarlo.
- **La máquina de estados de la conversación** está repartida entre firmware y
  bridge.

### 9.3 Direcciones que vale la pena investigar

Para quien quiera **no repetir la Fase C**:

- **ESP-SR / ESP-ADF de Espressif** exponen un AFE con AEC pensado para estos
  SoC y, según su documentación, para escenarios de *wake word* con audio de
  salida simultáneo. Es la alternativa natural a speexdsp en Arduino, y la
  pregunta a responder es si su AEC maneja mejor el **eco no lineal** que fue el
  límite acá. **Vale la pena medirlo con los mismos números de §6** antes de
  asumir que sí.
- **microWakeWord / openWakeWord**: hoy el proyecto no tiene palabra de
  activación; se despierta por botón. Una wake word cambiaría el modelo de
  interacción y, combinada con el barge, daría manos libres real.
- **ESPHome + Home Assistant Assist** y el protocolo **Wyoming (Rhasspy)**
  resuelven un problema muy parecido con un ecosistema mucho más grande. Si el
  objetivo es integrarse con domótica, es probable que convenga **portar los
  hallazgos de este proyecto a ese stack** en vez de al revés. Lo que este
  proyecto aportaría ahí son las mediciones de §6 y las trampas de §7.
- **Pipecat / LiveKit Agents** son frameworks de orquestación conversacional que
  ya modelan interrupción y turnos. Si el bridge creciera, reemplazarlo por uno
  de esos evitaría reimplementar `turn_lock`, encolado y barge.

### 9.4 Lo verdaderamente transferible

Si nada del código sirve, **lo que sí se transfiere es §6 y §7**: los números del
eco, el modelo de memoria de speex, el bloque diferido de 60 KB de libopus, el
orden de prioridades de las tareas, y la conclusión de que el barge por voz es un
problema de **física del montaje** antes que de algoritmo. Eso es lo caro de
conseguir y no está escrito en la documentación de ninguna de las librerías.

---

## 10. Mapa de archivos

| Archivo | Qué hay adentro |
|---|---|
| `firmware-arduino/src/Aec.h` | **Empezar acá para el AEC.** Toda la historia de la Fase C, los tres intentos, los números medidos y el modelo de memoria, en comentarios |
| `firmware-arduino/src/Aec.cpp` | AEC, remuestreo, ring de referencia, detector |
| `firmware-arduino/src/Audio.cpp` | `AecReferenceTee`, compuerta del micrófono, backoff |
| `firmware-arduino/src/main.cpp` | Tareas, prioridades, gestos del botón |
| `firmware-arduino/src/Config.h` / `.cpp` | Modo (`DEV_MODE` + `VOICE_SERVER_DENO`), IP del servidor, pines |
| `firmware-arduino/platformio.ini` | Dependencias, particiones de 4 MB |
| `server/hermes-bridge/bridge.py` | Todo el servidor |
| `server/hermes-bridge/README.md` | Arranque rápido y puertos |
| `docs/runbook-hermes-bridge.md` | Instalación paso a paso |
| `docs/propuesta-hermes-bridge.md` | Diseño del puente con referencias `archivo:línea` |
| `docs/conexionado-por-componente.md` | Conexionado componente por componente |
| `docs/firmware-binarios-y-flasheo.md` | Binarios, offsets, `--no-stub`, respaldos |
| `docs/port-esp32-s3-zero.md` | Adaptación al ESP32-S3 Zero |

**Volver a la configuración original de Elato** es trivial: invertir los dos
`#define` de `Config.h` y reflashear. Nada de la configuración de la nube se
borró.

---

## 11. Advertencias para un agente que corra esto en la nube

- **No hay acceso a la LAN ni a la máquina local.** El servidor, el agente LLM y
  los logs viven en una red doméstica. Todo lo verificable desde afuera está en
  este repositorio.
- **El hardware no es simulable.** Las mediciones de §6 salieron de una placa
  física con un montaje concreto. Cualquier conclusión acústica nueva necesita
  hardware; lo que se puede hacer sin él es análisis de código, diseño de
  integración y comparación con otros proyectos.
- **`voice_history.py` es un stub** y el `plist` del repo lleva un placeholder en
  lugar de la API key real.
- **Los comentarios del código son documentación de primera clase** en este
  repo —especialmente `Aec.h`— y están fechados. Si se cambia el comportamiento,
  hay que actualizarlos: un índice que miente es peor que no tener índice.

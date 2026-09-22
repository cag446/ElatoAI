# ¿Seguir sobre esta base o apoyarse en OSS maduro?

> **Encargo.** Decidir si conviene seguir desarrollando el barge-in sobre la base actual (ElatoAI + `bridge.py` + speexdsp) o apoyarse en proyectos open source ya maduros. Investigación **sin acceso a hardware**: todo lo acústico que se afirma acá sale de `contexto-proyecto-barge-in.md` §6 o de documentación de terceros, nunca de una medición nueva.
>
> Fecha: **2026-09-22**. Rama: `feature/esp32-s3-zero-port`.
>
> **Convención:** cada afirmación va marcada **[V]** (verificado, con fuente citable) o **[H]** (hipótesis, razonamiento no comprobado). Donde no encontré evidencia, lo digo con todas las letras: **[SIN EVIDENCIA]**.
>
> **Limitación de esta investigación:** el proxy de red de este sandbox bloquea `docs.espressif.com` y `esphome.io`. Todo lo de Espressif y ESPHome que cito lo saqué de los **fuentes `.rst` y `.h` del repositorio oficial en GitHub**, que es la misma fuente de la que se genera esa documentación. Lo digo porque cambia la forma de auditar las citas, no el valor de la evidencia.

> **Nota al incorporar este informe al repo (2026-09-22).** Lo escribió un agente en la nube de Anthropic; se commitea acá textual, sin editar. Una aclaración necesaria: su §2 cita el documento de contexto diciendo "sin PSRAM", que era el texto **al momento de leerlo**. Esa premisa ya fue corregida en `docs/contexto-proyecto-barge-in.md` (commit `4315e73`) justamente a raíz de este hallazgo. La cita describe el documento como estaba, no como está.

---

## 1. Recomendación, en tres frases

**Quedate en la base actual: ninguno de los candidatos resuelve el eco no lineal, y el que más cerca está (ESP-SR de Espressif) es un cambio de *componente* dentro de este firmware, no un cambio de *stack*.** El hallazgo que cambia el planteo es que la restricción dura n.º 1 está mal enunciada: el ESP32-S3FH4R2 **tiene 2 MB de PSRAM** —está deshabilitada en el build, no ausente en el chip— y habilitarla abre la puerta al AEC de ESP-SR, que en ESP32-S3 consume **menos RAM interna que speexdsp** (20-31 KB contra ~48 KB) porque mueve ~90-126 KB a PSRAM, y además trae el post-procesado no lineal (NLP) que speex no tiene. **Pero antes de escribir una línea de código, hacé la unidad soldada:** el ecosistema más grande de todos —Home Assistant— resolvió este mismo problema poniendo un DSP dedicado XMOS en su hardware insignia y *aun así* no tiene barge-in por voz libre, lo cual es la confirmación externa más fuerte posible de la conclusión que ya sacaste en §6.

---

## 2. El hallazgo que cambia el planteo: la placa SÍ tiene PSRAM

La restricción dura n.º 1 del encargo dice "**4 MB de flash y SIN PSRAM**". Eso es incorrecto a nivel de silicio, y el propio repo ya lo dice:

- **[V]** El **ESP32-S3FH4R2** lleva **4 MB de flash Quad SPI y 2 MB de PSRAM Quad SPI *dentro del encapsulado***. La nomenclatura de Espressif es explícita: `H4` = 4 MB flash in-package, `R2` = 2 MB PSRAM in-package. Fuente: [Datasheet ESP32-S3 Series](https://www.espressif.com/sites/default/files/documentation/esp32-s3_datasheet_en.pdf), corroborado por [Mouser](https://www.mouser.com/ProductDetail/Espressif-Systems/ESP32-S3FH4R2?qs=tlsG%2FOw5FFjPrwkmZSBQNA%3D%3D) y [LCSC](https://www.lcsc.com/product-detail/C3013940.html).
- **[V]** El propio `firmware-arduino/platformio.ini:13` lo documenta: `;   - 4MB QSPI Flash, 2MB QSPI PSRAM`. Y en la línea 60 explica por qué no se usa: `BOARD_HAS_PSRAM intentionally NOT defined. (...) enabling it routed I2S DMA buffers into PSRAM, causing a LoadProhibited crash in i2s_write`.

O sea: **la PSRAM no falta, se desactivó** porque activarla rompió el I2S. Eso convierte "sin PSRAM" de un límite físico a un **bug de configuración conocido y documentado**:

- **[V]** En el ESP32-S3 el **GDMA no puede leer ni escribir PSRAM**: su espacio de direcciones DMA cubre sólo la DRAM interna. Fuente: [espressif/qemu#180](https://github.com/espressif/qemu/issues/180).
- **[V]** Con PSRAM habilitada, `malloc()` en ESP32-S3 **prefiere la PSRAM por defecto**, y eso hace que los buffers y el contexto del driver I2S caigan en PSRAM, donde el DMA no los alcanza → falla la inicialización I2S. Fuente: [sensorium/Mozzi#327](https://github.com/sensorium/Mozzi/issues/327) (abril 2026, mismo síntoma, IDF5 + ESP32-S3 + PSRAM).
- **[H]** Por lo tanto el crash de `i2s_write` se arregla forzando que los buffers DMA se pidan con `MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL` (o bajando el umbral `SPIRAM_MALLOC_ALWAYSINTERNAL` para que las asignaciones chicas queden internas), no desactivando la PSRAM. **No lo probé**: no tengo la placa, y `arduino-audio-tools` puede o no exponer ese control.

**Por qué importa tanto.** Con 2 MB de PSRAM disponibles, el presupuesto de memoria (restricción dura n.º 2) deja de ser el filtro eliminatorio del informe y pasa a ser una decisión de arquitectura. Y el bloque contiguo de 60 KB que libopus reserva en el primer decode —la trampa n.º 4 de §7— **dejaría de competir con el AEC**, porque con PSRAM activa una asignación de ese tamaño se sirve de la PSRAM. **[H]** — el contrapeso es que decodificar desde PSRAM es más lento por fallos de caché, y este firmware ya tuvo carraspeo por robo de CPU a la tarea del parlante (§7.1). Es un experimento, no una certeza.

> **Advertencia metodológica, aplicando la lección de §6.** No estoy diciendo "habilitá PSRAM y anda". Estoy diciendo que la premisa "sin PSRAM" se midió en una sola condición (un intento de activarla que crasheó) y se convirtió en un límite del proyecto, que es exactamente el patrón que §6 pide no repetir.

---

## 3. Tabla comparativa de candidatos

Las columnas "PSRAM" y "Flash" están evaluadas contra **la placa real**: 4 MB de flash con particiones dual-OTA ya llenas (`partition.csv`: app0 y app1 de 0x1E0000 cada una) y 2 MB de PSRAM presente pero hoy apagada.

| Candidato | Qué pieza reemplaza | ¿Pasa PSRAM? | ¿Pasa flash 4 MB? | Licencia | Madurez / actividad | ¿Resuelve el eco NO LINEAL? |
|---|---|---|---|---|---|---|
| **ESP-SR — sólo AEC** (`esp_aec`) | `Aec.cpp` (speexdsp) | **Sí, si activás la PSRAM.** 18-31 KB internos + 64-126 KB PSRAM **[V]**. Sin PSRAM habría que forzar todo a interno (~121-146 KB) y **no entra** junto al bloque de 60 KB de Opus **[H]** | **Sí.** El AEC es código puro, **no necesita partición `model`** **[V]** | ESPRESSIF MIT — libre **sólo sobre productos Espressif** **[V]** | Alta. Componente oficial, releases frecuentes (v1.9.0 en el registry) | **Parcialmente, y sin garantía.** Tiene NLP (`aec_nlp_level_t`: NORMAL/AGGR/VERYAGGR) que speex no expone como tal **[V]**. Espressif **no publica ni un solo número en dB** de cuánto suprime **[V, por ausencia]** |
| **ESP-SR — AFE completo** (`esp_afe`) | `Aec.cpp` + VAD + NS + AGC | Sólo con PSRAM: **739-822 KB de PSRAM** (1 mic) **[V]** | Sí para el AFE solo; **no** si además querés WakeNet | ESPRESSIF MIT **[V]** | Alta | Igual que arriba, pero pagando 700+ KB por piezas que ya tenés resueltas en el servidor |
| **WakeNet** (wake word de ESP-SR) | Nada hoy (hoy despierta por botón) | 16 KB RAM + **324 KB PSRAM** (WakeNet9, 2 canales) **[V]** | **NO.** Requiere una partición `model` dedicada; el esquema de referencia de arduino-esp32 le asigna **0x3E0000 ≈ 3,9 MB** sobre flash de 16 MB **[V]**. Un modelo real pesa ~122 KB (`wn9s_nihaoxiaozhi`) **[V]**, así que cabría, **pero hay que sacrificar el OTA dual** **[H]** | ESPRESSIF MIT **[V]** | Alta | No aplica (no es AEC) |
| **microWakeWord** (ESPHome) | Nada hoy | Guarda el modelo **en PSRAM** y ofrece `task_stack_in_psram` **[V, código fuente]** | **NO en la práctica.** Las configs de referencia usan 16 MB de flash **[V]** | Apache-2.0 / MIT (ESPHome) | Alta, es el default de HA | No aplica |
| **openWakeWord** (servidor) | Nada hoy | N/A (corre en el servidor) | N/A | Apache-2.0 | Alta | No aplica. **Choca con la restricción n.º 3**: la Mac mini ya está saturada por faster-whisper |
| **ESPHome + HA Assist** | **Todo el firmware** + `bridge.py` | Requiere PSRAM y sólo soporta **S3 y P4** en su stack de audio **[V]** | **NO.** Voice assistant + micro_wake_word + media player en 4 MB de flash no es una configuración soportada **[H, fuerte]** | Apache-2.0 / GPL (HA) | Muy alta, el ecosistema más grande | **No.** Su hardware insignia (Voice PE) usa un **DSP XMOS XU316 dedicado** para AEC **[V]**, y el barge-in por voz del pipeline Assist es un **PR abierto, no mergeado** (home-assistant/core [#182776](https://github.com/home-assistant/core/pull/182776), al 2026-09-22) **[V]** |
| **Wyoming (Rhasspy)** | El protocolo WS device↔bridge | N/A (protocolo) | N/A | MIT | Alta | No aplica. El barge-in llega recién con el PR #182776, que lo implementa como **stop/start de buffers**, es decir **exactamente lo que ya hace tu Fase A** **[V]** |
| **`matt123p/esphome-aec`** | `Aec.cpp` | No documenta cifras **[V, por ausencia]** | N/A | — | Componente externo de un solo autor | **No.** Usa **SpeexDSP**, igual que vos **[V]**. Independiente y llegó a la misma pieza |
| **`benklop/esphome-audio-stack`** | Cadena de audio completa + AEC | **PSRAM obligatoria**: "esp_audio_stack, esp_aec y esp_afe require the ESPHome psram: component" **[V]** | Sólo S3/P4 **[V]** | — | Componente externo | Expone `esp_aec` con modos FD y NLP: misma respuesta que ESP-SR |
| **Pipecat** | `turn_lock` + encolado + barge de `bridge.py` | N/A (servidor) | N/A | BSD-2 | Alta, muy activa | **No** (es orquestación). Modela interrupción y turnos; su Smart Turn v3 declara ~12 ms de inferencia CPU **[V]** |
| **LiveKit Agents** | Ídem | N/A | N/A | Apache-2.0 | Muy alta | **No.** Y su interrupción adaptativa (Krisp VIVA) **no está disponible self-hosted**: en self-host cae a interrupción por VAD **[V]**, [livekit/agents#6033](https://github.com/livekit/agents/issues/6033) |
| **Willow** | Todo el firmware | Apunta a la familia **ESP32-S3-BOX** (8 MB PSRAM) **[V]** | No | Apache-2.0 | Media. Sigue con releases, pero su fundador falleció y el proyecto perdió tracción **[V]** | No documenta AEC propio |
| **whisper.cpp** | faster-whisper | N/A | N/A | MIT | Muy alta | No aplica. **Ojo con la restricción n.º 3**: el i5-3210M es Ivy Bridge, **tiene AVX pero NO AVX2** **[V]**, y whisper.cpp saca su ventaja justamente de AVX2/FMA → **no esperes una mejora grande** **[H]** |
| **Vosk** | faster-whisper | N/A | N/A | Apache-2.0 | Media-alta | No aplica. ASR **streaming**, mucho más barato que Whisper |
| **sherpa-onnx (k2-fsa)** | faster-whisper | N/A | N/A | Apache-2.0 | Alta y en crecimiento; proyectos están migrando **de Vosk a sherpa-onnx** **[V]** | No aplica. **Es el candidato más interesante del lado servidor**: ASR streaming, modelos en español, pensado para CPU modesta |

### Lo que dice Espressif, en su propia documentación de hardware

Esto es la corroboración externa más importante del informe, y no viene de un foro sino de la guía de diseño de Espressif **[V]** ([Espressif Microphone Design Guidelines](https://docs.espressif.com/projects/esp-sr/en/latest/esp32s3/audio_front_end/Espressif_Microphone_Design_Guidelines.html), citada vía resultados de búsqueda porque el dominio está bloqueado acá):

> "From an AEC standpoint, the non-linear distortion of the entire system (loudspeaker, amplifier, enclosure, and microphone) should be minimized. Speaker systems output some non-linear distortion along with the linear signal which causes non-linear echo to appear on the microphone, and **since there is no reference signal for the non-linear echo**, the residual output of the AEC contains amounts of non-linear distortion."

Y sobre el montaje:

> "the microphone should be placed **far away from the speaker** and other objects that can produce noise or vibration, and be **isolated and buffered by rubber pads** from the speaker sound cavity."

Es, palabra por palabra, la conclusión de `Aec.h` del 2026-09-16 y de §6. El fabricante del AEC que estás evaluando como reemplazo **dice que el problema se arregla en el layout**. Eso no significa que su AEC no vaya a andar mejor que speex —el NLP puede dar varios dB más—, significa que **no es razonable esperar que lo resuelva**.

---

## 4. Caminos de integración

### Camino A — "Física primero, servidor después". Conservar el 100 % del firmware

**Qué se conserva:** absolutamente todo el firmware, incluido `Aec.cpp` con `AEC_ENABLED=1`. Todo `bridge.py`. El protocolo.

**Qué se tira:** nada de código. Se tira la protoboard.

**Qué se hace:**
1. La unidad soldada que ya está en §8: mic y parlante separados 10+ cm, parlante apuntando en dirección contraria, mic desacoplado mecánicamente (almohadilla de goma, como pide la guía de Espressif).
2. Re-medir con el criterio de §6 **antes de tocar software** (ver §6 de este informe para el protocolo exacto).
3. En paralelo y sin relación con el eco: evaluar **sherpa-onnx** como ASR streaming en el servidor, para atacar los 2,5-7 s de STT.

**Esfuerzo:** 1 día de soldadura + 2 h de medición. El tramo de sherpa-onnx, 2-4 días de trabajo de servidor, independiente y reversible (la costura de STT es una función que recibe PCM y devuelve texto, §9.1).

**Riesgo específico:** que la separación de 10 cm **no alcance**. El acople cae 6 dB al duplicar la distancia (§6); de 3 cm a 12 cm son 4x = **~12 dB**. Con el pico de eco en -24,4 dBFS pasarías a ~-36 dBFS, contra una voz de usuario en -34,8 dBFS de mediana: ganás separación, pero **queda al filo**, no holgado **[H, aritmética sobre los números medidos de §6]**. La otra mitad del problema —la saturación del clase D— **no mejora con la distancia**, sólo con bajar el volumen o poner un parlante que no sature.

**Por qué es el camino recomendado:** es el único que ataca la causa que ya demostraste que es la causa, y es el más barato de todos por un orden de magnitud.

---

### Camino B — Habilitar PSRAM y cambiar speexdsp por el AEC de ESP-SR

**Qué se conserva:**
- El **tee de referencia** (`AecReferenceTee` en `Audio.cpp`) — se reusa tal cual.
- La **medición del retardo por correlación cruzada** — se reusa tal cual, y es más necesaria que nunca (ver riesgo).
- El **detector de voz sostenida** (umbral absoluto + persistencia) — se reusa, recalibrando la constante de frames.
- Todo `bridge.py`, el protocolo y la Fase A/B.

**Qué se tira:**
- `rjsachse/ESP32-SpeexDSP` y las llamadas `dsp.processAEC()` / `speex_preprocess_run()`.
- El remuestreador de speex (ESP-SR trae el suyo, o se mantiene el de speex).
- El guardarrail `AEC_MIN_FREE_AFTER` tal como está: con PSRAM el modelo de memoria cambia y hay que rehacerlo.

**Qué hay que hacer, en orden:**
1. **Migrar de plataforma.** `platform = espressif32 @ 6.10.0` es arduino-esp32 2.x, que **no incluye ESP-SR**. La librería `ESP_SR` existe a partir de **arduino-esp32 3.x** **[V]**, y PlatformIO oficial no soporta Arduino 3.x: hay que pasar al fork comunitario [pioarduino/platform-espressif32](https://github.com/pioarduino/platform-espressif32) **[V]**. Esto es un cambio de toolchain que toca **todas** las dependencias.
2. **Habilitar PSRAM y arreglar el DMA.** `BOARD_HAS_PSRAM` + `memory_type=qio_qspi` (la PSRAM del FH4R2 es **Quad**, no Octal), y forzar que los buffers I2S se asignen internos. Éste es el paso que ya falló una vez (§ platformio.ini:40); ahora hay una causa documentada (§2).
3. **Integrar `esp_aec`**, no el AFE completo. En ESP32-S3 a 16 kHz, 1 canal **[V, tabla oficial de esp-sr]**:

   | Modo | RAM interna | PSRAM | ms/frame | CPU |
   |---|---|---|---|---|
   | `SR_LOW_COST` | 18,8 KB | 64,0 KB | 2,29 / 32 | 7,2 % |
   | `SR_HIGH_PERF` | 8,2 KB | 100,1 KB | 4,51 / 32 | 14,1 % |
   | `FD_LOW_COST` | 30,9 KB | 90,0 KB | 6,28 / 32 | 19,6 % |
   | `FD_HIGH_PERF` | 20,3 KB | 126,2 KB | 8,08 / 32 | 25,3 % |
   | `VOIP_LOW_COST` | 26,9 KB | 64,1 KB | 4,37 / 16 | 27,3 % |

   Para barge-in el modo correcto es **`FD_*` (full-duplex)**. Compará contra speexdsp hoy: **~48 KB, todos internos**. `FD_LOW_COST` usa **30,9 KB internos**, o sea **libera ~17 KB de RAM interna** además de traer NLP.
4. **Cablear el NLP**: `aec_nlp_process()` + `aec_set_nlp_level()` **[V, `esp_aec.h`]**. Empezar en `NORMAL`, no en `VERYAGGR`: tu propia medición del 2026-09-16 mostró que subir la agresividad del supresor durante doble-habla borra la voz del usuario junto con el eco.
5. **Recalibrar el detector.** ESP-SR trabaja con frames de 16 o 32 ms; el tuyo usa 8 ms (`AEC_FRAME=64`). Los `DETECT_FRAMES = 40` (= 320 ms) pasan a ser 10 frames de 32 ms. El umbral absoluto de -32 dBFS hay que **volver a medirlo**, no trasladarlo.

**Esfuerzo:** **3-6 días** de trabajo efectivo, más iteraciones con hardware en la mano. El grueso no es el AEC: es la migración de toolchain (paso 1) y el debugging del DMA (paso 2).

**Riesgos específicos, en orden de gravedad:**

1. **El riesgo que nadie documenta: la sincronía de la referencia.** El AEC de ESP-SR está pensado para diseños donde la señal de referencia entra por el **mismo I2S que el micrófono** (códecs tipo ES7210 con canales de eco), o sea muestra a muestra. Acá la referencia sale de un **tee por software** y llega **~90 ms adelantada** (medido, `AEC_DEFAULT_DELAY = 1440`). **[SIN EVIDENCIA]**: no encontré en la documentación de ESP-SR ninguna declaración sobre cuánta desalineación tolera, ni ningún caso de uso con referencia por software. `matt123p/esphome-aec` advierte que su componente requiere "synchronized microphone channels, a usable playback reference, and compatible TDM ADC/DAC hardware" **[V]** — que es justo lo que el MAX98357A no da. **Éste es el riesgo que puede matar el camino entero**, y es barato de testear primero (ver experimento E2 abajo).
2. **CPU.** `FD_LOW_COST` declara **19,6 %** de un core. La trampa n.º 1 de §7 dice que el AEC robándole CPU a `audioStreamTask` produce carraspeo audible. Con prioridad 6 vs 3 debería estar cubierto, pero es un margen menor que hoy.
3. **Migración de toolchain.** arduino-esp32 3.x rompe APIs respecto de 2.x, y `arduino-audio-tools`, `links2004/WebSockets` y `ESP32_Button` tendrían que revalidarse. Podés perder días acá sin haber tocado el AEC.
4. **Licencia.** ESPRESSIF MIT permite el uso **sólo sobre productos Espressif** **[V]**. Para este proyecto es irrelevante (es un ESP32), pero deja de ser MIT puro y cambia la licencia efectiva del firmware compilado.
5. **Que igual no alcance.** Espressif no publica ni un dB de rendimiento, y su propia guía de hardware dice que la distorsión no lineal se minimiza en el diseño físico. Si el NLP aporta, digamos, otros 6-8 dB sobre los 8,6 dB que ya te dio el supresor de speex, llegás a un pico de residuo de ~-42 a -44 dB contra el criterio de -45 dB: **rozando el objetivo, no superándolo** **[H, extrapolación sin base empírica — tratala como una conjetura, no como una estimación]**.

---

### Camino C — Portar todo a ESPHome + Home Assistant Assist

**Qué se conserva:** los **hallazgos** (§6 y §7), no el código. El agente Hermes, si se lo expone como conversation agent de HA.

**Qué se tira:** el firmware entero, `bridge.py` entero, el protocolo, `Aec.cpp`, la Fase A/B/C. Todo.

**Esfuerzo:** semanas, y con un final incierto.

**Por qué lo desaconsejo, con la evidencia en la mano:**

- **[V]** El stack de audio de ESPHome que trae AEC (`esphome-audio-stack`) **exige PSRAM** y soporta **sólo S3 y P4**. La PSRAM la podrías dar; el flash de 4 MB con voice assistant + micro_wake_word + media player es otra historia: las configuraciones de referencia usan **16 MB** **[V]**.
- **[V]** El barge-in en el pipeline Assist de Home Assistant es **[PR #182776](https://github.com/home-assistant/core/pull/182776), abierto y no mergeado** al 2026-09-22. Y cuando entre, lo que implementa es un callback de interrupción que **descarta el audio en cola en el satélite** — es decir, **funcionalmente lo mismo que tu Fase A**, que funciona desde hace meses.
- **[V]** El satélite oficial (Home Assistant Voice PE) usa un **XMOS XU316 dedicado** para AEC. Es decir: HA no resolvió el eco por software en el ESP32; lo resolvió **poniendo otro chip**.
- **[V]** Aun con ese DSP, el barge-in real de la Voice PE es *por palabra de activación*, y hay que decirla **dos veces**: una para cortar, otra para que vuelva a escuchar. **Tu barge por botón es funcionalmente superior a eso hoy.**

**Único escenario donde este camino gana:** si el objetivo pasa a ser **domótica** (controlar luces, sensores, escenas). Ahí el valor no es el barge-in, es el ecosistema. En ese caso lo que hay que portar son los números de §6 y las trampas de §7 hacia ESPHome, exactamente como anticipa §9.3 — posiblemente como una contribución al componente `esphome-aec`, que hoy usa SpeexDSP y por lo tanto tiene **el mismo techo que vos ya caracterizaste**.

---

## 5. Qué NO hay que hacer

1. **No migres a ESPHome/HA esperando barge-in resuelto.** No lo está: es un PR abierto, e implementa lo que vos ya tenés **[V]**.
2. **No metas el AFE completo de ESP-SR.** Son 739-822 KB de PSRAM y 48-91 KB internos **[V]** por VAD, NS y AGC que ya resolviste **en el servidor**, donde son gratis. Si vas a ESP-SR, andá a `esp_aec` solo.
3. **No agregues wake word on-device antes de arreglar el layout.** Una wake word escuchando *mientras el asistente habla* es **el mismo problema del eco no lineal**, con un detector más caro. Y encima WakeNet te obliga a una partición `model` que en 4 MB **te cuesta el OTA dual** **[V]**. El botón es hoy una solución mejor que una wake word que dispara sola.
4. **No pongas openWakeWord en la Mac mini.** Restricción dura n.º 3: la CPU ya es el cuello de botella con faster-whisper. Agregarle un detector always-on empeora lo que más duele.
5. **No cambies faster-whisper por whisper.cpp esperando un salto.** El i5-3210M es Ivy Bridge: **AVX sí, AVX2 no** **[V]**. Ambos runtimes sacan su velocidad de AVX2/FMA. **[H]** vas a mover el número un 10-30 %, no a partirlo por cuatro. Si querés atacar la latencia en serio, el cambio es de **arquitectura** (ASR *streaming*: sherpa-onnx o Vosk), no de runtime.
6. **No subas la agresividad del NLP durante doble-habla.** Ya lo mediste con speex el 2026-09-16: suprime la voz del usuario junto con el eco. El default de ESP-SR ya es `AGGR`; si acaso, bajalo a `NORMAL` **[V, `esp_aec.h`]**.
7. **No fuerces `memory_type=qio_qspi` sin arreglar antes la asignación DMA.** Vas a reproducir exactamente el crash de `i2s_write` de la vez pasada. La causa está identificada (§2): el DMA del S3 no llega a la PSRAM.
8. **No traslades a LiveKit Agents buscando interrupción adaptativa.** Esa función es de su capa cloud (Krisp VIVA); **en self-hosted cae a interrupción por VAD** **[V]**, que es menos de lo que ya tenés.
9. **No des por muerto el AEC lineal.** El supresor de residuo te dio **+8,6 dB en el pico** (§6). Eso es real y está andando. La discusión no es "speex sirve o no", es "cuántos dB más hacen falta y de dónde salen".
10. **No concluyas nada nuevo sobre acústica sin medir a varias distancias y varios volúmenes.** Es la lección de §6 y este informe no la puede sustituir: yo no tengo hardware y no medí nada.

---

## 6. Preguntas abiertas que requieren hardware

Todas usan el **mismo criterio de medición de §6** para que los resultados sean comparables con lo ya medido:

> **Criterio.** Pico y mediana del residuo en ventanas de ~0,5 s (`residualPeakDb` / `residualSum` de `Aec.cpp` ya lo imprimen), en dBFS. **Objetivo: pico del residuo ≤ -45 dBFS**, para que la voz del usuario (-34,8 dBFS mediana, -29,4 p90) quede ≥10 dB por encima. Y siempre **barriendo condiciones**: distancias {20 cm, 50 cm, 100 cm} × volúmenes {25, 50, 70}.

### E1 — ¿Se puede habilitar la PSRAM sin romper el I2S?

**Por qué importa:** es la puerta de entrada al Camino B, y también le saca a Opus el problema del bloque contiguo de 60 KB.
**Experimento:** compilar con `BOARD_HAS_PSRAM` + `memory_type=qio_qspi`, forzando los buffers I2S a interno. Reproducir 60 s de TTS.
**Métrica de éxito:** cero crashes en `i2s_write`, cero carraspeo audible, `ESP.getFreeHeap()` y `ESP.getFreePsram()` reportados al arranque.
**Métrica de fracaso:** `LoadProhibited` en `i2s_write` → confirmaría que `arduino-audio-tools` no permite dirigir la asignación, y mataría el Camino B antes de gastar los días de migración de toolchain.
**Costo:** medio día. **Hacelo primero: es el guardián de todo el Camino B.**

### E2 — ¿El AEC de ESP-SR tolera una referencia por software desalineada 90 ms?

**Por qué importa:** es el riesgo n.º 1 del Camino B y **no encontré ninguna evidencia** de que funcione ni de que no.
**Experimento:** con `esp_aec` en modo `FD_LOW_COST`, alimentar el mismo tee de referencia pre-retardado con `lockedDelay` (la infraestructura ya existe). Barrer el pre-retardo en ±30 ms alrededor de 1440 muestras.
**Métrica:** curva de pico de residuo vs. pre-retardo. Si hay un mínimo claro y profundo → el AEC de ESP-SR se engancha con referencia por software. Si la curva es plana → no se engancha y el Camino B muere acá.
**Costo:** 1 día una vez resuelto E1.

### E3 — ¿Cuánto da realmente el NLP de ESP-SR contra el supresor de speex?

**Por qué importa:** es **la** pregunta del encargo, y nadie publica el número.
**Experimento:** A/B en la misma sesión y el mismo montaje, mismo criterio:
(a) speexdsp + `speex_preprocess_run` (lo de hoy, pico -35,7 dB);
(b) `esp_aec` FD_LOW_COST + `aec_nlp_process` en `NORMAL`;
(c) ídem en `AGGR`.
Barrer las 9 condiciones de distancia × volumen.
**Métrica:** pico del residuo en dBFS, y —tanto o más importante— **la separación entre el pico del residuo y la mediana de la voz del usuario** en cada condición.
**Éxito:** (b) o (c) llegan a pico ≤ -45 dB en al menos la condición {50 cm, vol 50}. **Fracaso interesante:** si (c) baja el pico pero también baja la voz del usuario, reproduce el hallazgo del 2026-09-16 y confirma que el techo es físico.
**Costo:** 1 día. **Éste es el experimento que responde el encargo.**

### E4 — ¿Cuánto del eco es no lineal, en números?

**Por qué importa:** hoy "es no lineal" está inferido de que el AEC no lo cancela. Nunca se midió **la fracción**, y de eso depende si vale la pena cambiar de amplificador/parlante en vez de tocar software.
**Experimento:** medir el eco crudo (pico y mediana) a volúmenes 25, 50 y 70 a distancia fija. El modelo lineal predice proporcionalidad (§6 ya midió 5,2 dB reales contra 6,0 teóricos al ir de 50 a 25).
**Métrica:** el **exceso sobre la predicción lineal** al subir a 70. Si a volumen 70 el pico crece *más* que lo que predice la proporcionalidad, ese exceso es la distorsión.
**Corolario accionable:** si el exceso aparece recién por encima de vol 50, la solución más barata del proyecto entero es **fijar el volumen en 50** (que es lo que ya hace `BRIDGE_DEVICE_VOLUME=50`) y **no tocar nada más**.
**Costo:** 2 horas, y **no requiere ningún software nuevo**. Es el experimento con mejor relación valor/esfuerzo del informe.

### E5 — ¿Cuánta separación da realmente la unidad soldada?

**Por qué importa:** es la apuesta del Camino A, y arriba calculé ~12 dB con aritmética, no con medición.
**Experimento:** antes de soldar, montar mic y parlante sobre una regla y medir el eco (pico, p90, mediana) a separaciones de 3, 6, 12 y 24 cm, con el parlante apuntando (a) al mic y (b) en contra.
**Métrica:** pico del eco vs. separación, contra la ley de 6 dB por duplicación ya medida. **El dato que decide es dónde el pico del eco cae por debajo de -45 dBFS**, porque ahí el barge por voz anda a distancia normal sin tocar software.
**Costo:** 2 horas. **Hacelo ANTES de soldar**: define la geometría de la unidad.

### E6 — ¿El bloque de 60 KB de Opus en PSRAM degrada el decode?

**Por qué importa:** si E1 sale bien, Opus va a decodificar desde PSRAM y ahí aparece un riesgo nuevo (latencia de caché) donde antes había uno viejo (memoria).
**Experimento:** medir ms por paquete de 120 ms decodificado, con y sin PSRAM, y contar underruns de `audioStreamTask`.
**Métrica:** tiempo de decode por paquete y **cero carraspeo**, usando el método de §7.14 (preguntar *desde cuándo* pasa, no sólo *si* pasa).
**Costo:** medio día, junto con E1.

### E7 — (Fuera del eco) ¿Cuánto baja la latencia un ASR streaming?

**Por qué importa:** los 2,5-7 s de STT son el peor número del proyecto, y **con mejor latencia se necesita menos barge-in**.
**Experimento:** correr sherpa-onnx (modelo streaming en español) contra faster-whisper `base` int8 sobre **las mismas grabaciones** del probe de eco que ya guarda `bridge.py` (`BRIDGE_ECHO_PROBE`).
**Métrica:** tiempo hasta transcripción final, WER aproximado sobre 20 frases conocidas, y carga de CPU de la Mac mini.
**Nota:** no requiere hardware de audio, sólo la máquina local. Es el único experimento que podés correr sin la placa.

---

## 7. Resumen ejecutivo de la decisión

| | Camino A (física + servidor) | Camino B (PSRAM + ESP-SR AEC) | Camino C (ESPHome/HA) |
|---|---|---|---|
| Esfuerzo | 1 día + 2-4 días servidor | 3-6 días + iteración HW | Semanas |
| Código que se tira | Nada | `Aec.cpp` (la mitad) | Todo |
| Probabilidad de resolver el barge manos libres | **Media-alta** **[H]** | Baja-media **[H]** | Baja **[V: HA no lo tiene]** |
| Riesgo de perder lo que ya funciona | **Nulo** | Medio (toolchain, DMA, CPU) | Total |
| Desbloquea algo más | Latencia del turno | Wake word a futuro, memoria | Domótica |

**Orden que propongo:** **E4 → E5 → Camino A → E1 → E2 → E3 → Camino B.**

E4 y E5 cuestan **4 horas en total, no requieren escribir una línea de código**, y pueden hacer innecesario todo el resto del informe. Si después de la unidad soldada el barge por voz anda a distancia normal, el Camino B queda como una optimización de memoria interesante, no como una necesidad.

---

## 8. Lo que no pude verificar

Lo digo explícitamente para que nadie tome estas cosas por ciertas:

- **[SIN EVIDENCIA]** Cuánta desalineación de la señal de referencia tolera el AEC de ESP-SR. No hay nada en su documentación ni encontré un caso de uso con referencia por software.
- **[SIN EVIDENCIA]** Cualquier cifra en dB del rendimiento del AEC de ESP-SR. Espressif publica consumo de RAM y CPU, pero **ningún número de supresión**.
- **[SIN EVIDENCIA]** Que alguien haya corrido ESP-SR en un S3 de 4 MB de flash con 2 MB de PSRAM Quad bajo Arduino. Todos los ejemplos y configs que encontré asumen 8-16 MB de flash y 8 MB de PSRAM Octal.
- **[SIN EVIDENCIA]** Que ESPHome mainline no tenga componente de AEC. Los resultados de búsqueda lo afirman y la existencia de dos componentes externos (`esphome-aec`, `esphome-audio-stack`) lo hace muy plausible **[H]**, pero `esphome.io` está bloqueado desde este sandbox y **no lo pude confirmar en la documentación oficial**.
- **[SIN EVIDENCIA]** El tamaño exacto en flash de un modelo WakeNet9 para ESP32-S3. El único dato concreto que encontré es `wn9s_nihaoxiaozhi` en ~122 KB, que es la variante **9s** (la de menor consumo, pensada para chips sin PSRAM).
- **Nada de lo acústico de este informe fue medido.** Las únicas mediciones reales siguen siendo las de §6 del documento de contexto.

---

## 9. Fuentes

**Hardware y memoria**
- [Datasheet ESP32-S3 Series (Espressif)](https://www.espressif.com/sites/default/files/documentation/esp32-s3_datasheet_en.pdf)
- [ESP32-S3FH4R2 en Mouser](https://www.mouser.com/ProductDetail/Espressif-Systems/ESP32-S3FH4R2?qs=tlsG%2FOw5FFjPrwkmZSBQNA%3D%3D) · [en LCSC](https://www.lcsc.com/product-detail/C3013940.html)
- [espressif/qemu#180 — GDMA no puede acceder a PSRAM en S3](https://github.com/espressif/qemu/issues/180)
- [sensorium/Mozzi#327 — fallo de init de I2S DMA con PSRAM en S3](https://github.com/sensorium/Mozzi/issues/327)

**ESP-SR**
- [espressif/esp-sr (GitHub)](https://github.com/espressif/esp-sr)
- [`include/esp32s3/esp_aec.h` — API, modos, NLP, campo `caps`](https://raw.githubusercontent.com/espressif/esp-sr/master/include/esp32s3/esp_aec.h)
- [`docs/en/acoustic_echo_cancellation/README.rst` — tabla de recursos ESP32-S3](https://raw.githubusercontent.com/espressif/esp-sr/master/docs/en/acoustic_echo_cancellation/README.rst)
- [`docs/en/benchmark/README.rst` — consumo del AFE y WakeNet](https://raw.githubusercontent.com/espressif/esp-sr/master/docs/en/benchmark/README.rst)
- [`LICENSE` — ESPRESSIF MIT](https://raw.githubusercontent.com/espressif/esp-sr/master/LICENSE)
- [Espressif Microphone Design Guidelines — distorsión no lineal y layout](https://docs.espressif.com/projects/esp-sr/en/latest/esp32s3/audio_front_end/Espressif_Microphone_Design_Guidelines.html)

**Arduino / PlatformIO**
- [`tools/partitions/esp_sr_16.csv` — partición `model` de 0x3E0000](https://raw.githubusercontent.com/espressif/arduino-esp32/master/tools/partitions/esp_sr_16.csv)
- [espressif/arduino-esp32#9820 — ESP_SR necesita partición especial](https://github.com/espressif/arduino-esp32/issues/9820)
- [pioarduino/platform-espressif32 — fork para Arduino 3.x](https://github.com/pioarduino/platform-espressif32)

**ESPHome / Home Assistant**
- [home-assistant/core#182776 — Barge-In en el pipeline Assist (ABIERTO)](https://github.com/home-assistant/core/pull/182776)
- [Home Assistant Voice Preview Edition (XMOS XU316)](https://www.home-assistant.io/voice-pe/)
- [matt123p/esphome-aec — AEC con SpeexDSP](https://github.com/matt123p/esphome-aec)
- [benklop/esphome-audio-stack — `esp_aec`/`esp_afe`, PSRAM obligatoria](https://github.com/benklop/esphome-audio-stack)
- [esphome/esphome — `micro_wake_word` (modelos en PSRAM)](https://github.com/esphome/esphome/blob/dev/esphome/components/micro_wake_word/micro_wake_word.cpp)
- [Wyoming Protocol (Home Assistant)](https://www.home-assistant.io/integrations/wyoming/)

**Orquestación y ASR**
- [livekit/agents#6033 — interrupción adaptativa no disponible self-hosted](https://github.com/livekit/agents/issues/6033)
- [LiveKit — Adaptive interruption handling](https://docs.livekit.io/agents/logic/turns/adaptive-interruption-handling/)
- [k2-fsa/sherpa-onnx](https://k2-fsa.github.io/sherpa/onnx/index.html)
- [dscripka/openWakeWord](https://github.com/dscripka/openWakeWord)
- [HeyWillow/willow](https://github.com/HeyWillow/willow)
- [Intel ARK — Core i5-3210M (AVX sí, AVX2 no)](https://www.intel.com/content/www/us/en/products/sku/67355/intel-core-i53210m-processor-3m-cache-up-to-3-10-ghz-rpga/specifications.html)
- [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)

# Informe de mejora — ElatoAI (fork `cag446/ElatoAI`, PR #1)

> **Tipo:** informe técnico (hallazgos + recomendaciones priorizadas; no es un runbook).
> **Fecha:** 2026-09-22.
> **Base analizada:** rama `feature/esp32-s3-zero-port` @ `b1889e2` (PR #1, 46 commits, +5588/−158, 44 archivos).
> **Insumos:** `docs/investigacion-integracion-oss.md` (informe del agente cloud, copia local en `elatoai-investigacion-integracion-oss-2026-09-22.md`), `docs/contexto-proyecto-barge-in.md`, y el código del PR: `Aec.h/.cpp`, `Audio.cpp`, `main.cpp`, `platformio.ini`, `partition.csv`, `bridge.py`.
> **Convención:** **[V]** verificado contra código o fuente primaria · **[H]** hipótesis derivada del código, sin medición en hardware.

> **Nota al incorporarlo al repo (2026-09-24).** Se commitea textual, sin editar.
> Estado de sus recomendaciones a esa fecha:
>
> | Ítem | Estado |
> |---|---|
> | **§4.1** — el barge por botón descartaba la pregunta del usuario | ✅ **Aplicado** (`f6708b3`) |
> | **§4.3** — el botón durante el STT no cancelaba nada | ✅ **Aplicado** (`f6708b3`) |
> | §3.2 / §4.7 — comentarios desactualizados en `bridge.py` | ✅ Aplicado (`f6708b3`) |
> | **§4.2** — cancelar la tarea del turno (reacción de 1-3 s) | ⏳ Pendiente a propósito: abre ventanas de cancelación como la que ya falló en `_save_turn`; va en commit propio |
> | §3.2 — comentarios de `Aec.h` / `Aec.cpp` con el doble del valor real | ⏳ Pendiente (firmware) |
> | §4.4, §4.5, §4.6, carrera de §4.7 | ⏳ Pendientes (firmware, requieren reflashear) |
> | §6 — correcciones a `investigacion-integracion-oss.md` y `contexto-proyecto-barge-in.md` | ⏳ Pendientes |
>
> Verificación de los fixes aplicados: `tests/test_barge_state.py` (14/14 PASS),
> y `tests/check_barge_contract.py` sin regresiones. Falta probarlos con el
> hardware.

---

## 1. Conclusión

La recomendación de fondo del informe OSS **se sostiene**: seguir sobre la base actual y atacar primero la física del montaje con E4/E5. Pero el cruce con el código cambia tres cosas:

1. **La explicación del crash de PSRAM que el informe da por "causa documentada" es falsa para este toolchain.** En ESP-IDF 4.4, que es lo que compila este proyecto, el driver I2S pide sus buffers DMA con `MALLOC_CAP_DMA`, así que nunca pueden caer en PSRAM. La causa real del `LoadProhibited` en `i2s_write` **nunca se aisló**. E1 hay que replantearlo como diagnóstico, no como aplicar un fix que ya se sabe cuál es (§3.1).
2. **Hay bugs de software en el camino del barge-in** que el informe no ve porque no leyó el flujo de estados firmware↔bridge. Uno de ellos puede **tirar el comienzo de la pregunta del usuario** en cada barge por botón. Los tres principales (§4.1-§4.3) se corrigen **solo en `bridge.py`**, sin reflashear el firmware, en horas.
3. **Hay constantes del AEC documentadas con el doble de su valor real** (frames de 4 ms, no de 8 ms). Eso invalida la recalibración que el informe propone para el Camino B, y deja una duda sobre el filtro de 32 ms que conviene despejar **antes** de concluir que el eco de los picos es no lineal (§3.2 y §4.4).

**Orden de trabajo revisado:** fixes de §4.1-§4.3 (solo `bridge.py`, sin reflashear) → prueba de alineación del AEC (§4.4, reflashear) → E4 → E5 → Camino A → E1' (diagnóstico de PSRAM) → E2 → E3 → Camino B. Detalle en §5.

---

## 2. Qué se verificó y qué no

| Verificado | Cómo |
|---|---|
| Flujo de estados device↔bridge durante un turno y un barge | Lectura de `Audio.cpp` (transiciones, `micTask`, `networkTask`) y de `bridge.py` (`feed_audio`, `request_barge`, `_handle_barge`, `process_utterance`) |
| Cuándo dejó el firmware de subir audio durante SPEAKING | `git show 1cdbae2`: se eliminó `wsStream.write(cleanFrame…)` y la compuerta pasó a `deviceState != LISTENING` |
| Asignación de los buffers DMA del I2S | Fuente de ESP-IDF v4.4.7, `components/driver/i2s.c:740-859`: todas las asignaciones usan `MALLOC_CAP_DMA` |
| Versión del core Arduino que compila el proyecto | `platform-espressif32 @ 6.10.0` → `framework-arduinoespressif32 ~3.20017.0` (Arduino 2.0.17 / IDF 4.4) |
| Qué cambió cuando se "desactivó la PSRAM" | `git show f02116b` |

**No verificado:** nada acústico ni de timing en hardware. Los hallazgos de §4 salen del código y de los números que ya están en los logs de los commits, no de una prueba nueva.

---

## 3. Validación del informe OSS contra el código

### 3.1 La PSRAM: la premisa está bien, el diagnóstico no

| Afirmación del informe | Veredicto | Evidencia |
|---|---|---|
| El FH4R2 tiene 2 MB de PSRAM Quad y está desactivada en el build | **[V] Correcto** | `platformio.ini:13` y `:60-62` |
| "La PSRAM se desactivó porque routeó los buffers DMA del I2S a la PSRAM" (causa "documentada") | **[V] Falso para IDF 4.4** | El driver I2S legacy (el que usa `arduino-audio-tools` sobre Arduino 2.x) asigna `dma_obj->buf[]` y `desc[]` con `heap_caps_calloc(…, MALLOC_CAP_DMA)`, que garantiza RAM interna apta para DMA sin importar si la PSRAM está habilitada. Las fuentes que cita el informe (Mozzi#327, qemu#180) son de **IDF 5**, otro driver |
| El crash se arregla forzando `MALLOC_CAP_DMA \| MALLOC_CAP_INTERNAL` | **[H] Improbable** | Eso ya es lo que hace el driver |
| "No fuerces `memory_type=qio_qspi` sin arreglar antes el DMA" | **Sin base** | Ver abajo |

**Lo que realmente pasó (`f02116b`, 2026-07-18):** se revirtieron **tres** cambios juntos: `board_build.flash_mode = qio`, `board_build.arduino.memory_type = qio_qspi` y `-D BOARD_HAS_PSRAM`. El mensaje del commit dice que la PSRAM era **"a suspect"**, no la causa. Además, los dos comentarios que quedaron en `platformio.ini` se contradicen: el de la línea 39 culpa a `memory_type=qio_qspi` ("broke the I2S output driver install") y el de la línea 61 culpa a `BOARD_HAS_PSRAM` ("routed I2S DMA buffers into PSRAM"). La placa base `esp32-s3-devkitc-1` ya trae `flash_mode: qio`, así que ese override no cambiaba nada. **[H]** El único cambio efectivo probablemente fue `BOARD_HAS_PSRAM`: en Arduino 2.x hace que `initArduino()` llame a `psramInit()` y active `heap_caps_malloc_extmem_enable()`, y a partir de ahí todo `malloc()` de más de 4 KB va a PSRAM. Eso afecta los buffers de **`arduino-audio-tools` y de libopus**, no los del DMA.

Un `LoadProhibited` dentro de `i2s_write` es un puntero inválido. Eso es más compatible con un **handle de driver no instalado**, como sugiere el comentario de la línea 39 ("broke the I2S output driver install"), que con un buffer DMA mal ubicado.

**Consecuencia:** E1 del informe ("forzar buffers a interno y probar") no ataca la causa. Hay que replantearlo así:

> **E1' — Diagnóstico del crash con PSRAM.** Habilitar **solo** `BOARD_HAS_PSRAM`, sin tocar `memory_type` ni `flash_mode`. Capturar el backtrace completo con `esp32_exception_decoder` (ya está en `monitor_filters`) y el valor de retorno de `i2s_driver_install` / `i2s.begin()`. Loguear `ESP.getPsramSize()` y `heap_caps_get_free_size(MALLOC_CAP_INTERNAL)` al arranque. Si el crash es por un handle nulo, mirar qué asignación falló. Medio día, igual que el E1 original, pero produce una causa en vez de una prueba a ciegas.

### 3.2 Constantes del AEC: el informe heredó comentarios desactualizados

`AEC_FRAME = 64` muestras a 16 kHz son **4 ms**, no 8 ms. El comentario de `Aec.h:127` todavía dice "128 = 8 ms", de la configuración anterior. Esto arrastra errores:

| Constante | Comentario en el código | Valor real | Dónde se propagó el error |
|---|---|---|---|
| `AEC_FRAME = 64` | "128 = 8 ms" (`Aec.h:127`) | **4 ms** | Informe OSS, Camino B paso 5 |
| `AEC_FILTER = 512` | "1024 = 64 ms" (`Aec.h:129`) | **32 ms** de cola | — |
| `DETECT_FRAMES = 40` | "40 x 8 ms = 320 ms" (`Aec.cpp:64`) | **160 ms** | Informe OSS: "320 ms → 10 frames de 32 ms". Lo correcto son **5 frames** de 32 ms |
| `SETTLE_FRAMES = 60` | "~0.5 s" (`Aec.cpp:65`) | **240 ms** | — |
| `MEASURE_FRAMES = 120` | "~1 s" (`Aec.cpp:35`) | ≥ 1,9 s (solo cuenta 1 de cada 4 frames y solo los que tienen energía) | — |

`contexto-proyecto-barge-in.md` §5 dice 160 ms y está bien. El error está en los comentarios del código y en el informe OSS. Como `contexto-proyecto-barge-in.md` §11 declara que "los comentarios del código son documentación de primera clase", estos hay que corregirlos.

### 3.3 Resto del informe

| Afirmación | Veredicto |
|---|---|
| Particiones dual-OTA de 0x1E0000 cada una, sin lugar para una partición `model` | **[V]** `partition.csv` |
| `platform = espressif32 @ 6.10.0` es Arduino 2.x y no trae `ESP_SR` | **[V]** `~3.20017.0` = Arduino 2.0.17 |
| El tee de referencia se reusa en el Camino B | **[V]** con una salvedad (§4.5): hoy no cubre el camino de pitch-shift |
| E7 "sobre las mismas grabaciones del probe de eco" | **Parcial.** `probe_dump("voz-usuario", …)` guarda las frases del usuario, pero **sin transcripción de referencia**. Para medir WER hay que grabar las 20 frases conocidas aparte |
| Riesgo n.º 1 del Camino B: sincronía de la referencia por software | **[V] Correcto, y es más grave de lo que dice el informe:** ver §4.4 |

---

## 4. Hallazgos nuevos en el PR (no cubiertos por el informe)

Ordenados por impacto. **§4.1, §4.2 y §4.3 se corrigen solo en `bridge.py`** (sin reflashear). **§4.4, §4.5, §4.6 y la carrera de §4.7 tocan el firmware** y requieren reflashear. Ninguno requiere hardware nuevo.

### 4.1 El bridge descarta la pregunta del usuario en el barge por botón — **alta**

**Mecanismo [V, código]:**
- Desde `1cdbae2` (2026-09-15) el firmware **no sube audio del mic en SPEAKING** (`Audio.cpp:230, 246`: sube solo en `LISTENING`).
- Al hacer barge, el device manda `BARGE` y pasa a `LISTENING` (`Audio.cpp:489-504`): desde ese momento sube **mic crudo con el parlante ya apagado**. O sea, la voz del usuario.
- El bridge sigue con `speaking=True` hasta que procesa el barge. Todo lo que llega mientras tanto va a `barge_buf` (`bridge.py:502-507`).
- `_handle_barge` **descarta** ese buffer si `via=button`, con el argumento de que "es eco de Deb, press-then-talk" (`bridge.py:672-681`). Ese argumento era cierto con el firmware de `26e1c12`/`8a55dc6` (15-09, 00:07), que subía mic en SPEAKING. **Dejó de ser cierto 21 h después con `1cdbae2`**, y el bridge no se actualizó.

**Cuánto audio se pierde [V, logs de los commits]:** el propio log de verificación muestra cuánto tarda el bridge en procesar el barge, porque es lo que se acumula en `barge_buf`: **36 864 B = 1,15 s** (`1cdbae2`) y **93 184 B = 2,9 s** (`Aec.h:98`). Con el barge por botón, esos 1-3 s de lo que el usuario dijo después de apretar se tiran y Whisper recibe la pregunta sin el principio.

**Fix:** en `_handle_barge`, **conservar el buffer para ambos `via`**. Con el firmware actual el contenido de `barge_buf` siempre es audio posterior al corte. El `via` sigue sirviendo para los logs. Actualizar los comentarios de `Audio.cpp:494-496` y `bridge.py:563-564, 660-681`.

### 4.2 El bridge tarda 1-3 s en reaccionar a un barge — **media**

**[V, código]** `barge_event` se revisa en tres lugares: por token del LLM (`bridge.py:763`), después de cada `synthesize()` (`:789`) y por paquete en `_send_packets` (`:629`). Si el bridge está **esperando el próximo token** o **sintetizando con Piper** (0,5-2 s según el propio comentario de `:786`), el barge espera. Los 1,15 s y 2,9 s de §4.1 son esa latencia medida.

Con el fix de §4.1 esto deja de perder audio, porque el buffer se reencola. Pero el turno nuevo arranca 1-3 s tarde. **Mejora:** que `handle_device_text` cancele la tarea del turno en curso (`self.turn_task.cancel()`, ojo que puede haber turnos encolados en `self.turn_tasks`), en vez de esperar a que el turno llegue a un punto de chequeo. El bridge ya maneja cancelación limpia en `shutdown()`, y la trampa n.º 14 del contexto dice dónde están las ventanas de cancelación. **[H]** Esfuerzo: medio día con el test `tests/test_barge_in.py` como red.

### 4.3 El botón durante "pensando" (STT) no cancela nada — **media**

**[V, código]** El firmware trata `PROCESSING` como turno activo y manda `BARGE` (`main.cpp:100`, `Audio.cpp:493`). Pero el bridge pone `speaking=True` recién **después** del STT (`bridge.py:758`), y `request_barge` ignora el BARGE si `not self.speaking` (`:550-552`). Durante el STT, que es la fase más larga del turno (2,5-7 s):

1. El device pasa a `LISTENING` creyendo que cortó.
2. El bridge termina el STT, consulta al LLM y manda `RESPONSE.CREATED`.
3. El device vuelve a `SPEAKING` y **reproduce la respuesta que el usuario quiso cancelar**.
4. Si el usuario habló después de apretar, esa frase entra al VAD como un turno nuevo y se encola detrás del `turn_lock`: se responden **las dos**.

**Fix:** un flag `self.cancel_pending` que `request_barge` active aunque `speaking` sea `False` mientras hay un turno en curso, y que `process_utterance` revise después del STT y antes de mandar `RESPONSE.CREATED`. O, con la mejora de §4.2, cancelar la tarea del turno directamente.

### 4.4 El retardo puede salirse de la cola del filtro — **media, afecta la tesis del "eco no lineal"**

**[V, código]** `AEC_FILTER = 512` cubre **32 ms** de incertidumbre alrededor de `lockedDelay`. Pero el retardo medido **varió entre 90, 130 y 146 ms** entre arranques (`Aec.h:53-55`). Además, el refinamiento se **acepta** con un umbral bajo (`REFINE_RATIO = 1.25`) y, si no lo supera, se queda en 90 ms. Si el retardo real de ese arranque es 130-146 ms, la desalineación es de **40-56 ms, fuera de la cola del filtro**. En ese caso speex no puede converger sobre la parte principal del eco.

**Por qué importa [H]:** la conclusión central del proyecto es que el AEC cancela ~16 dB del eco medio pero solo ~3 dB de los picos, "porque los picos son no lineales". Esa conclusión se apoya en parte en que el retardo sale inestable, y una desalineación variable también podría explicarla. No invalida la hipótesis física, porque la saturación del clase D es real y el usuario la escuchó. Pero hoy **no se puede separar** cuánto de los 3 dB es no linealidad y cuánto es desalineación.

**Qué hacer (barato, antes de E4):**
- Loguear en cada arranque `lockedDelay`, el ratio pico/media y si se aceptó o no. Ya se imprime casi todo; falta correlacionarlo con el pico de residuo de esa sesión.
- Probar una vez con `AEC_FILTER = 1024` (64 ms). Según el modelo de memoria de `Aec.h:12` son **+12 KB**. Con 80 KB de guardarrail puede no entrar junto al supresor, así que medir el heap primero. Si los picos bajan varios dB, parte del problema era alineación y no física.
- Esto también define E2 del informe. Si speex ya es sensible a la alineación, ESP-SR con frames de 16/32 ms lo será tanto o más.

### 4.5 El camino de pitch-shift no alimenta la referencia del AEC — **baja, latente**

**[V, código]** Con `currentPitchFactor != 1.0f` la salida va `volumePitch → pitchShift → i2s` (`Audio.cpp:81-82, 199-203`) y **no pasa por `aecTee`**. En ese modo el AEC corre sin referencia (ring en cero) y el detector ve el eco crudo, con picos de −24 dBFS contra un umbral de −32. Es la receta de falsos positivos del 2026-09-15. Hoy no pasa si el pitch es 1.0. **Fix:** insertar el tee también en ese camino (`PitchShiftFixedOutput pitchShift(aecTee)`), o desactivar el barge por voz cuando `currentPitchFactor != 1.0f`.

### 4.6 El barge por voz pierde el comienzo de la interrupción — **baja**

**[V, código]** Los ≥160 ms de voz que disparan el detector, y todo lo que se dijo en SPEAKING antes de eso, se procesan localmente y **no se suben** (`Audio.cpp:309-319`). Lo que se reencola en el bridge es solo lo que se dice después del corte. **[H]** Si el usuario interrumpe con una frase corta ("pará", "no, esperá"), puede llegar vacía o mutilada al STT. Opción: mantener en el firmware un ring de ~500 ms de `cleanFrame` y subirlo inmediatamente después del `BARGE`. Son ~16 KB de RAM, así que queda sujeto al presupuesto del AEC. Otra opción, gratis: aceptarlo y documentarlo.

### 4.7 Menores

| Ítem | Dónde | Detalle |
|---|---|---|
| Carrera en `aecResetReference()` | `Audio.cpp:106, 131` | Corre en `networkTask` y hace `memset` + `refWritten=0` mientras `audioStreamTask` escribe. Rompe el invariante de productor/consumidor único que documenta `Aec.cpp:168-172`. Impacto bajo (sucede en transiciones), pero contradice el comentario. Mover el reset a `audioStreamTask` con un flag, igual que `i2sOutputFlushScheduled` |
| Comentario desactualizado | `bridge.py:504` | "Sin AEC viene sucio… el AEC es Fase E": ya no llega audio en SPEAKING |
| Comentario desactualizado | `Audio.cpp:415-418` | Dice que el handler `BARGE` del server está "inerte". Sigue siendo cierto, pero la detección server-side quedó descartada por diseño, no pendiente |
| `BRIDGE_AUTH_TOKEN` no se valida | `bridge.py:49, 869-872` | Ya figura en §8 del contexto. Se repite porque `/voice` (REST) acepta hasta 10 MB y dispara STT+LLM+TTS: en LAN es aceptable, pero **no exponerlo nunca** |

---

## 5. Plan de mejora priorizado

| # | Acción | Qué se toca | ¿Reflashear? | ¿Placa para validar? | Esfuerzo | Por qué en este orden |
|---|---|---|---|---|---|---|
| 1 | **Fix §4.1**: conservar `barge_buf` en ambos `via` | Solo `bridge.py` | No | Prueba final sí (botón durante la respuesta) | 1 h | Bug activo en el camino "robusto" (botón). Riesgo nulo |
| 2 | **Fix §4.3**: cancelar el turno si llega BARGE durante el STT | Solo `bridge.py` | No | Prueba final sí | 2-3 h | Bug activo de UX en la fase más larga del turno |
| 3 | Corregir comentarios de §3.2 y §4.7, y los dos comentarios contradictorios de `platformio.ini` | `bridge.py` + comentarios de `Aec.h`, `Aec.cpp`, `Audio.cpp`, `platformio.ini` | No (los comentarios no cambian el binario) | No | 1 h | Son "documentación de primera clase" y hoy desorientan (ya desorientaron al informe OSS) |
| 4 | **§4.2**: cancelación inmediata de la tarea del turno | Solo `bridge.py` | No | Prueba final sí | ½ día | Baja 1-3 s la reacción al barge. Cubre también el #2 |
| 5 | **§4.4**: telemetría de retardo + prueba con `AEC_FILTER=1024` | Firmware (`Aec.h`, `Aec.cpp`) | **Sí** | Sí (montaje actual) | 2 h | Separa "desalineación" de "no linealidad" **antes** de gastar en E4/E5 |
| 6 | **E4** del informe: eco crudo vs. volumen 25/50/70 | Nada (medición) | No | Sí (montaje actual) | 2 h | Sin cambios |
| 7 | **E5** del informe: eco vs. separación antes de soldar | Nada (medición) | No | Sí (montaje actual) | 2 h | Define la geometría de la unidad soldada |
| 8 | **Camino A**: unidad soldada + re-medir con el criterio de §6 | Hardware | No | Sí | 1 día | Sin cambios |
| 9 | **E7**: sherpa-onnx vs faster-whisper (con 20 frases de referencia grabadas aparte) | Servidor (STT del bridge) | No | No | 2-4 días | Independiente. Se puede adelantar a cualquier punto |
| 10 | **E1'** (diagnóstico de PSRAM, §3.1) → E2 → E3 → Camino B | Firmware + toolchain | **Sí** | Sí | 3-6 días | Solo si después del #8 el barge manos libres sigue sin andar |
| — | §4.5 (pitch-shift sin referencia), §4.6 (inicio de la interrupción por voz), carrera de §4.7 | Firmware (`Audio.cpp`) | **Sí** | Sí | ½-1 día | Baja prioridad; agrupar con el #5 para reflashear una sola vez |

Los puntos 1 a 4 son **1-2 días de trabajo solo en `bridge.py`** (más comentarios): no cambian el comportamiento del firmware ni requieren reflashear. Se escriben y se prueban en parte con `tests/test_barge_in.py`; la confirmación final es una prueba real con el dispositivo. Mejoran el barge que ya funciona (botón) y el que funciona a medias (voz). El informe OSS no los contempla porque evaluó alternativas de stack, no el flujo actual.

---

## 6. Correcciones sugeridas a la documentación del PR

| Documento | Corrección |
|---|---|
| `docs/investigacion-integracion-oss.md` §2 y §5.7 | Agregar nota: el mecanismo "DMA en PSRAM" no aplica a IDF 4.4 (driver con `MALLOC_CAP_DMA`). La causa no se aisló. Reemplazar E1 por E1' |
| `docs/investigacion-integracion-oss.md` §4 Camino B paso 5 | `DETECT_FRAMES = 40` son 160 ms → **5** frames de 32 ms, no 10 |
| `docs/contexto-proyecto-barge-in.md` §4 (nota PSRAM) y trampa n.º 9 | Quitar "la causa está documentada": es una hipótesis refutada para este toolchain |
| `firmware-arduino/platformio.ini:38-40, 60-62` | Unificar en un solo comentario que diga qué se revirtió en `f02116b` y que la causa sigue abierta |
| Descripción del PR #1 | Agregar los pendientes de §5 #1-#3 como checklist o como issues |

---

## 7. Lo que no se pudo verificar

- **[H]** Que `BOARD_HAS_PSRAM` sea el cambio efectivo de `f02116b`. Depende de qué `memory_type` aplica por defecto el builder de PlatformIO para `esp32-s3-devkitc-1`, y no lo revisé.
- **[H]** La latencia de barge de §4.2 se infiere del tamaño de `barge_buf` en dos logs. Podría incluir también audio de la ventana de `SPEAK_TAIL_GRACE_S`, aunque en ambos logs el barge ocurrió a mitad de respuesta.
- **[H]** Que la desalineación de §4.4 explique una parte relevante de los ~3 dB de cancelación en picos. Es una hipótesis alternativa que el código hace plausible, no una medición.
- Nada de este informe se probó en hardware ni se ejecutó el bridge.

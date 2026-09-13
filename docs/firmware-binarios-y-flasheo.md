# Firmware, binarios y flasheo — ESP32-S3 Zero

---

## Descripcion general

Guia sobre los **binarios** que produce la compilacion del firmware de ElatoAI
para la **Waveshare ESP32-S3 Zero**, que representa cada uno, en que direccion
(offset) de la flash va, y como flashear la placa (incluido el **binario merged**
de respaldo que se graba de una sola vez).

> **NOTA:** El USB del Zero es nativo (USB-JTAG/serial). Por eso todo flasheo
> con esptool en esta placa usa **`--no-stub`** (ya configurado en
> `platformio.ini`). Sin eso, la subida se corta a mitad. Ver [[port-esp32-s3-zero]].

---

## Los binarios del build

Tras `pio run`, los binarios quedan en
`firmware-arduino/.pio/build/esp32-s3-zero/`:

| Binario | Offset flash | Tamano aprox | Que es |
|---|---|---|---|
| `bootloader.bin` | **0x0** | ~15 KB | Segundo gestor de arranque (en el S3 va en 0x0, no en 0x1000) |
| `partitions.bin` | **0x8000** | ~3 KB | Tabla de particiones (de `partition.csv`, 4 MB) |
| `boot_app0.bin` | **0xe000** | 8 KB | Selector OTA (indica que app arrancar). Viene del framework |
| `firmware.bin` | **0x10000** | ~1.2 MB | **La aplicacion** (tu firmware) |

> **NOTA (S3):** A diferencia del ESP32 clasico (bootloader en 0x1000), en el
> **ESP32-S3 el bootloader va en 0x0**. El merged de este doc ya lo coloca bien.

---

## `firmware.bin` vs binario merged

Hay dos formas de guardar/flashear el firmware. Es la duda mas comun:

```flowchart
        ¿Que quieres hacer?
        /                    \
   Actualizar solo         Reinstalar TODO
   la app                  desde cero / respaldo
        │                        │
        ▼                        ▼
   firmware.bin            firmware_merged.bin
   -> offset 0x10000       -> offset 0x0
   (necesita que el        (incluye bootloader +
    bootloader+parts        particiones + boot_app0
    ya esten en la placa)   + app, todo en uno)
```

| | `firmware.bin` (solo app) | `..._merged.bin` (todo) |
|---|---|---|
| Contiene | Solo la aplicacion | bootloader + particiones + boot_app0 + app |
| Offset de grabado | `0x10000` | `0x0` |
| Cuando usar | Actualizar la app en una placa ya inicializada | Reinstalar desde cero, clonar, o **respaldo** |
| Sirve solo | No para primer flasheo | Si, deja la placa lista de un tiro |

> **TIP:** Para un **respaldo** o para reinstalar todo con un solo comando, usa el
> **merged**. Para el dia a dia (recompilar y subir), PlatformIO ya graba los 4
> binarios en sus offsets automaticamente con `pio run -t upload`.

---

## Respaldos generados

En el home del usuario hay **dos juegos de respaldos**, uno por cada version del
firmware. **No son intercambiables: hablan con servidores distintos.**

### Version Hermes bridge (la actual — usar esta)

Firmware que habla con `bridge.py` en la Mac Mini (`$HERMES_SERVER_IP:8000`, ver [[runbook-hermes-bridge]]),
pipeline local: VAD -> Whisper -> Hermes -> Piper. Sin nube.

| Archivo | Que es | Offset | MD5 |
|---|---|---|---|
| `~/ElatoAI_ESP32_S3_Bridge.py.bin` | Solo la app | 0x10000 | `075e982cc43c1a370a35ec61e07ba28c` |
| `~/ElatoAI_ESP32_S3_Bridge.py_merged.bin` | Todo junto | 0x0 | `f2aede4cb8f14dddeda29cff684134ba` |

- **Origen:** build del 2026-07-18 23:19, rama `feature/esp32-s3-zero-port`,
  commit `6bb603b`. Es la version de produccion validada (ver `runbook-hermes-bridge.md`).
- **Exportados:** 2026-08-03.

### Version Elato cloud (anterior — historica)

Firmware original que hablaba con la nube de ElatoAI. Se conserva por si hace
falta volver atras.

| Archivo | Que es | Offset |
|---|---|---|
| `~/Firmware_ELATO_ok_limpio.bin` | Solo la app | 0x10000 |
| `~/Firmware_ELATO_ok_limpio_merged.bin` | Todo junto | 0x0 |

- **Origen:** build del 2026-07-18 18:20 (anterior a los cambios del bridge).

> **COMO DISTINGUIRLOS:** el nombre `Bridge.py` indica el firmware que habla con
> `bridge.py` (local). El `ELATO` es el de la nube. Ante la duda, usar el
> **Bridge.py**, que es lo que corre hoy en la placa.

---

## Placa nueva (unidad soldada)

Para una placa **virgen** —el caso de la unidad definitiva con los componentes
soldados— hay que grabar el **merged a `0x0`**, porque una placa de fabrica no
trae bootloader ni tabla de particiones:

```bash
esptool --chip esp32s3 --no-stub --port /dev/ttyACM0 write-flash \
  0x0 ~/ElatoAI_ESP32_S3_Bridge.py_merged.bin
```

Despues de flashear, la placa necesita configuracion de red igual que el
prototipo. El conexionado de los componentes esta en
`conexionado-por-componente.md` (mic, parlante, boton, LED).

> **ANTES DE SOLDAR:** verificar contra `conexionado-por-componente.md` que los
> pines coinciden con los que espera este firmware. El binario esta compilado
> con un pinout fijo — si la placa soldada cablea distinto, hay que recompilar,
> no basta con reflashear.

---

## Como flashear

### Opcion A — desde el proyecto (lo normal)

Recompila y graba los 4 binarios en sus offsets automaticamente:

```bash
cd firmware-arduino
pio run -t upload --upload-port /dev/ttyACM0
```

### Opcion B — grabar el binario merged (respaldo, de un tiro)

Graba TODO a offset `0x0` con esptool. **Es la opcion para una placa nueva.**

```bash
esptool --chip esp32s3 --no-stub --port /dev/ttyACM0 write-flash \
  0x0 ~/ElatoAI_ESP32_S3_Bridge.py_merged.bin
```

### Opcion C — grabar solo la app

Solo si el bootloader y las particiones ya estan en la placa:

```bash
esptool --chip esp32s3 --no-stub --port /dev/ttyACM0 write-flash \
  0x10000 ~/ElatoAI_ESP32_S3_Bridge.py.bin
```

> **ADVERTENCIA:** Si el Zero no entra en modo descarga, **manten pulsado BOOT
> (GPIO0) mientras conectas el USB-C**, suelta, y reintenta. Tras grabar, la
> placa se reinicia y el puerto USB-CDC se re-enumera (es normal que
> `/dev/ttyACM0` desaparezca y vuelva).

---

## Como regenerar los respaldos

Si recompilas y quieres exportar un juego nuevo. Los dos pasos, tal como se
generaron los respaldos actuales (verificado 2026-08-03):

**1. Copiar la app sola:**

```bash
cp firmware-arduino/.pio/build/esp32-s3-zero/firmware.bin \
   ~/ElatoAI_ESP32_S3_Bridge.py.bin
```

**2. Generar el merged:**

```bash
cd firmware-arduino/.pio/build/esp32-s3-zero
BOOTAPP0=~/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin
esptool --chip esp32s3 merge-bin -o ~/ElatoAI_ESP32_S3_Bridge.py_merged.bin \
  --flash-mode dio --flash-freq 80m --flash-size 4MB \
  0x0 bootloader.bin \
  0x8000 partitions.bin \
  0xe000 "$BOOTAPP0" \
  0x10000 firmware.bin
```

Salida esperada: `Wrote 0x13c6f0 bytes ... ready to flash to offset 0x0`.

> **TIP:** Verifica que la copia es identica al original con `md5sum`, y anota
> el nuevo MD5 en la tabla de respaldos de este documento.

> **NOTA:** `merge-bin` NO necesita la placa conectada — solo junta archivos.
> El `--no-stub` hace falta al **grabar**, no al generar el merged.

---

## Solucion de problemas

| Sintoma | Causa probable | Solucion |
|---|---|---|
| `Packet content transfer stopped` al subir | Falta `--no-stub` (USB nativo del Zero) | Ya esta en `platformio.ini`; si usas esptool a mano, anade `--no-stub` |
| `Could not open /dev/ttyACM0` | El puerto se re-enumero tras un upload | Reconecta el USB-C y reintenta |
| `No module named 'rich_click'` con `esptool.py` | Esa copia de esptool no tiene deps | Usa el comando `esptool` (v5.x) instalado por separado |
| La placa arranca en bucle tras grabar solo `firmware.bin` | Bootloader/particiones ausentes o incompatibles | Graba el **merged** a `0x0` |
| Bootloader no arranca (S3) | Se grabo el bootloader en `0x1000` | En el S3 el bootloader va en **0x0** |

---

## Referencia rapida

```
OFFSETS (ESP32-S3):
  0x0      bootloader.bin
  0x8000   partitions.bin
  0xe000   boot_app0.bin
  0x10000  firmware.bin        <- la app

RESPALDOS (~/):
  [Hermes bridge — LA ACTUAL]
  ElatoAI_ESP32_S3_Bridge.py.bin         -> app sola, offset 0x10000
  ElatoAI_ESP32_S3_Bridge.py_merged.bin  -> todo, offset 0x0
  [Elato cloud — historica]
  Firmware_ELATO_ok_limpio.bin           -> app sola, offset 0x10000
  Firmware_ELATO_ok_limpio_merged.bin    -> todo, offset 0x0

FLASHEAR:
  pio run -t upload                                  # normal (proyecto)
  esptool --chip esp32s3 write-flash 0x0 <merged>    # placa nueva / respaldo
  esptool --chip esp32s3 write-flash 0x10000 <app>   # solo app

SIEMPRE en el Zero: --no-stub (USB nativo). Modo descarga: BOOT + conectar USB-C.
```

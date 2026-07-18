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

## Respaldo generado

En el home del usuario hay dos respaldos del firmware "limpio" (modo boton, sin
diagnosticos), identico a lo grabado en la placa:

| Archivo | Que es | Offset |
|---|---|---|
| `~/Firmware_ELATO_ok_limpio.bin` | Solo la app | 0x10000 |
| `~/Firmware_ELATO_ok_limpio_merged.bin` | Todo junto (bootloader+parts+boot_app0+app) | 0x0 |

---

## Como flashear

### Opcion A — desde el proyecto (lo normal)

Recompila y graba los 4 binarios en sus offsets automaticamente:

```bash
cd firmware-arduino
pio run -t upload --upload-port /dev/ttyACM0
```

### Opcion B — grabar el binario merged (respaldo, de un tiro)

Graba TODO a offset `0x0` con esptool:

```bash
esptool --chip esp32s3 --port /dev/ttyACM0 write-flash \
  0x0 ~/Firmware_ELATO_ok_limpio_merged.bin
```

### Opcion C — grabar solo la app (`firmware.bin`)

Solo si el bootloader y las particiones ya estan en la placa:

```bash
esptool --chip esp32s3 --port /dev/ttyACM0 write-flash \
  0x10000 ~/Firmware_ELATO_ok_limpio.bin
```

> **ADVERTENCIA:** Si el Zero no entra en modo descarga, **manten pulsado BOOT
> (GPIO0) mientras conectas el USB-C**, suelta, y reintenta. Tras grabar, la
> placa se reinicia y el puerto USB-CDC se re-enumera (es normal que
> `/dev/ttyACM0` desaparezca y vuelva).

---

## Como regenerar el binario merged

Si recompilas y quieres un nuevo merged de respaldo:

```bash
cd firmware-arduino/.pio/build/esp32-s3-zero
BOOTAPP0=~/.platformio/packages/framework-arduinoespressif32/tools/partitions/boot_app0.bin
esptool --chip esp32s3 merge-bin -o ~/Firmware_ELATO_ok_limpio_merged.bin \
  --flash-mode dio --flash-freq 80m --flash-size 4MB \
  0x0 bootloader.bin \
  0x8000 partitions.bin \
  0xe000 "$BOOTAPP0" \
  0x10000 firmware.bin
```

> **TIP:** Verifica que una copia es identica al original con `md5sum`.

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
  Firmware_ELATO_ok_limpio.bin         -> app sola, offset 0x10000
  Firmware_ELATO_ok_limpio_merged.bin  -> todo, offset 0x0

FLASHEAR:
  pio run -t upload                                  # normal (proyecto)
  esptool --chip esp32s3 write-flash 0x0 <merged>    # respaldo completo
  esptool --chip esp32s3 write-flash 0x10000 <app>   # solo app

SIEMPRE en el Zero: --no-stub (USB nativo). Modo descarga: BOOT + conectar USB-C.
```

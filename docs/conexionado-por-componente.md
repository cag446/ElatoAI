# Conexionado por componente — ESP32-S3 Zero

---

## Descripcion general

Diagramas de conexion **uno por componente** para tu montaje concreto de ElatoAI
sobre la **Waveshare ESP32-S3 Zero**, con tus piezas:

- Microfono **INMP441** (I2S MEMS)
- Amplificador **MAX98357A** (I2S) + **altavoz de 8 Ω**
- **Boton tactil** (en vez de pulsador mecanico)
- LED **WS2812 integrado** (en vez de LED RGB de 3 pines)

> **NOTA:** Respecto al diagrama oficial del proyecto (`assets/pcb-design.png`),
> tu montaje cambia dos cosas y **ambas quitan cableado**: no cableas el LED RGB
> (usas el WS2812 de la placa) ni el pulsador (usas un cable tactil). El micro,
> el amplificador y el altavoz se conectan igual que en el diagrama oficial.

---

## Componentes y alimentacion

| Componente | Modelo | Alimentacion |
|---|---|---|
| Microfono | INMP441 | **3.3 V** (¡nunca 5 V!) |
| Amplificador | MAX98357A | **5 V** recomendado (para 8 Ω) |
| Altavoz | 8 Ω | Va a las salidas del amp, no a la placa |
| Boton | Tactil (cable) | — |
| LED | WS2812 integrado | Interno (GPIO21) |

> **ADVERTENCIA:** El **INMP441 se alimenta a 3.3 V**. Conectarlo a 5 V puede
> dañarlo. El **MAX98357A** si admite 5 V y con un altavoz de 8 Ω conviene
> alimentarlo a 5 V para tener volumen suficiente.

---

## Diagrama general (todo junto)

```flowchart
        ESP32-S3 ZERO
        ┌──────────────────────────┐
  3V3 ──┤ 3V3                       │
        │                     GPIO1 ├──► SCK   ┐
        │                     GPIO4 ├──► WS    │  INMP441
        │                    GPIO14 ├──► SD    │  (microfono)
        │                       GND ├──► GND / VDD=3V3 / L/R=GND ┘
        │                          │
        │                     GPIO5 ├──► LRC   ┐
        │                     GPIO6 ├──► BCLK  │  MAX98357A
        │                     GPIO7 ├──► DIN   │  (amplificador)
        │                    GPIO10 ├──► SD    │   + altavoz 8Ω
   5V ──┤ 5V ──────────────────────┼──► Vin   │
        │                       GND ├──► GND   ┘
        │                          │
        │                     GPIO2 ├──► cable tactil (superficie conductora)
        │                    GPIO21 ├── WS2812 integrado (no cablear)
        └──────────────────────────┘
```

---

## 1. Microfono INMP441

```flowchart
   INMP441                         ESP32-S3 Zero
   ┌─────────────┐
   │ VDD ────────┼──────────────► 3V3      (¡3.3 V, NO 5 V!)
   │ GND ────────┼──────────────► GND
   │ SD  ────────┼──────────────► GPIO14
   │ L/R ────────┼──────────────► GND      (selecciona canal izquierdo)
   │ WS  ────────┼──────────────► GPIO4
   │ SCK ────────┼──────────────► GPIO1
   └─────────────┘
```

| Pin INMP441 | Va a | Nota |
|---|---|---|
| VDD | 3V3 | Alimentacion 3.3 V |
| GND | GND | Comun |
| SD | GPIO14 | Datos I2S (salida del micro) |
| L/R | GND | A GND = canal izquierdo (el que espera el firmware) |
| WS | GPIO4 | Word Select / LRCLK |
| SCK | GPIO1 | Reloj de bits / BCLK |

> **TIP:** Si el micro no capta o capta muy bajo, revisa que **L/R este a GND**.
> Si lo dejas al aire o a 3.3 V, el micro habla en el canal contrario y el
> firmware "no oye".

---

## 2. Amplificador MAX98357A + altavoz 8 Ω

```flowchart
   MAX98357A                       ESP32-S3 Zero
   ┌─────────────┐
   │ Vin ────────┼──────────────► 5V       (recomendado para 8 Ω)
   │ GND ────────┼──────────────► GND
   │ SD  ────────┼──────────────► GPIO10    (enable/mute del amp)
   │ GAIN ───────┼── (sin conectar = 9 dB por defecto)
   │ DIN ────────┼──────────────► GPIO7
   │ BCLK ───────┼──────────────► GPIO6
   │ LRC ────────┼──────────────► GPIO5
   │             │
   │  +  ────────┼───────┐
   │  -  ────────┼──┐    │   ALTAVOZ 8 Ω
   └─────────────┘  │    │   ┌───────────┐
                    └────┼──►│ -       + │◄─┐
                         │   └───────────┘  │
                         └──────────────────┘
```

| Pin MAX98357A | Va a | Nota |
|---|---|---|
| Vin | 5V | 5 V para mas volumen con 8 Ω (tambien funciona a 3.3 V, mas flojo) |
| GND | GND | Comun |
| SD | GPIO10 | El firmware lo pone en HIGH para activar y LOW para silenciar |
| GAIN | (libre) | Sin conectar = ganancia 9 dB. Ver nota para cambiarla |
| DIN | GPIO7 | Datos I2S (audio hacia el amp) |
| BCLK | GPIO6 | Reloj de bits |
| LRC | GPIO5 | Word Select / LRCLK |
| + / − | Altavoz 8 Ω | Salida al altavoz (sin polaridad critica) |

> **TIP (ganancia):** El pin `GAIN` ajusta el volumen del amp. Dejalo **sin
> conectar** para 9 dB (equilibrado). A GND = 12 dB (mas alto); a Vin = 6 dB
> (mas bajo). Empieza sin conectar.

> **NOTA:** El pin `SD` del MAX98357A NO es una señal I2S: es de encendido/apagado
> (y seleccion de canal). Por eso va a un GPIO normal (GPIO10) que el firmware
> controla para silenciar el altavoz cuando toca.

---

## 3. Boton tactil (GPIO2)

En tu variante no hay pulsador: un **cable** desde GPIO2 hasta cualquier
superficie conductora hace de sensor tactil capacitivo.

```flowchart
   ESP32-S3 Zero           Superficie tactil
   ┌───────────┐
   │     GPIO2 ├─────────► cable ──► lamina/tornillo/papel aluminio/
   └───────────┘                     pista de cobre... (lo que toques)

   (NO lleva resistencia ni GND: el touch es capacitivo interno del ESP32)
```

| Conexion | Detalle |
|---|---|
| GPIO2 → superficie | Un solo cable a algo conductor que vayas a tocar |
| Resistencia | Ninguna |
| GND | No se conecta |

> **TIP:** Cuanto mas grande la superficie, mas sensible. Si dispara solo o no
> responde, calibra `TOUCH_THRESHOLD` con `test/touch_test.cpp` (ver doc del port).
> Recuerda: toque de ~0.5 s duerme la placa; un toque la despierta.

---

## 4. LED WS2812 (integrado — nada que cablear)

```flowchart
   ESP32-S3 Zero
   ┌───────────┐
   │    GPIO21 ├── WS2812 (ya soldado en la placa)  ✔ NADA que cablear
   └───────────┘
```

El Zero trae el LED direccionable en GPIO21. El firmware ya lo maneja. Solo
tenlo a la vista para ver los colores de estado (magenta=portal, verde=lista,
amarillo=escucha, rojo=piensa, azul=responde, cian=OTA).

---

## 5. Alimentacion (rieles comunes)

```flowchart
   3V3 (del Zero) ──► VDD del INMP441
   5V  (del Zero) ──► Vin del MAX98357A
   GND (del Zero) ──► GND de INMP441 + GND de MAX98357A + L/R del INMP441
```

> **NOTA:** El `5V` del Zero sale del USB-C. Alimentando por USB (500 mA o mas)
> hay de sobra para el micro y el amplificador con un altavoz de 8 Ω. Si luego
> usas bateria, asegura una fuente de 5 V capaz de dar picos de ~0.5 A.

---

## Tabla resumen (todos los cables)

| Desde | Pin | Hacia (Zero) |
|---|---|---|
| INMP441 | VDD | 3V3 |
| INMP441 | GND | GND |
| INMP441 | L/R | GND |
| INMP441 | SD | GPIO14 |
| INMP441 | WS | GPIO4 |
| INMP441 | SCK | GPIO1 |
| MAX98357A | Vin | 5V |
| MAX98357A | GND | GND |
| MAX98357A | SD | GPIO10 |
| MAX98357A | DIN | GPIO7 |
| MAX98357A | BCLK | GPIO6 |
| MAX98357A | LRC | GPIO5 |
| MAX98357A | + / − | Altavoz 8 Ω |
| Superficie tactil | cable | GPIO2 |
| WS2812 | (integrado) | GPIO21 (no cablear) |

---

## Consejos de montaje y problemas

| Sintoma | Causa probable | Solucion |
|---|---|---|
| No se oye nada | Vin del amp sin 5 V, o SD sin GPIO10 | Revisa Vin→5V y SD→GPIO10 |
| Volumen muy bajo | Amp a 3.3 V con altavoz 8 Ω | Alimenta el amp a **5 V** |
| Sonido distorsionado | Ganancia muy alta o altavoz suelto | Deja `GAIN` sin conectar; fija bien el altavoz |
| El micro no capta | `L/R` no esta a GND | Conecta `L/R` del INMP441 a GND |
| Micro con ruido | Cables largos de I2S o sin GND comun | Cables cortos; comparte GND con la placa |
| El touch no va | Umbral mal / superficie pequeña | Calibra `TOUCH_THRESHOLD`; agranda la superficie |
| Se resetea al subir volumen | Fuente insuficiente | Fuente de 5 V con >0.5 A de pico |

---

## Referencia rapida

```
INMP441 (micro, 3.3V)     MAX98357A (amp, 5V) + altavoz 8Ω
  VDD -> 3V3                Vin  -> 5V
  GND -> GND                GND  -> GND
  L/R -> GND                SD   -> GPIO10
  SD  -> GPIO14             DIN  -> GPIO7
  WS  -> GPIO4              BCLK -> GPIO6
  SCK -> GPIO1              LRC  -> GPIO5
                           GAIN -> (libre = 9 dB)
                           +/-  -> altavoz 8Ω

TOUCH:  GPIO2 -> superficie conductora (1 cable)
LED:    WS2812 integrado en GPIO21 (no cablear)

ALIMENTACION:  3V3->micro | 5V->amp | GND comun (incluye L/R del micro)
```

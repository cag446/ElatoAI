# Etapa 1: poner a hablar el ESP32-S3 Zero con la nube de Elato

---

## Descripcion general

Receta paso a paso para dejar tu placa **hablando** usando el **servidor gratuito
de Elato** (30 min/mes), sin montar nada tú. Sirve para **confirmar que todo el
hardware funciona** (micro, altavoz, LED WS2812, touch y el ida-y-vuelta de voz)
antes de pasar a tu propio servidor y a tu agente Hermes.

- **Tiempo estimado:** 15-20 minutos.
- **El firmware ya esta flasheado y listo** (modo `ELATO_MODE`, apunta a la nube de Elato). **No hay que reflashear nada.**
- **MAC de tu placa:** `B8:F8:62:D7:59:C4`

> **NOTA:** Esta etapa usa la nube de Elato solo para **probar**. Tus datos y tu
> agente Hermes NO entran aqui todavia; eso es la Etapa 3.

---

## Ingredientes (lo que necesitas antes de empezar)

| Ingrediente | Detalle |
|---|---|
| La placa ESP32-S3 Zero | Ya flasheada [ON] · conectada por USB-C o a una fuente USB |
| La MAC de la placa | `B8:F8:62:D7:59:C4` |
| Un movil o PC con WiFi | Para configurar la red y usar la web de Elato |
| Tu WiFi de casa | **Obligatorio 2.4 GHz** (el ESP32 no ve redes de 5 GHz) |
| Cuenta en elatoai.com | Gratis |
| Una API key de OpenAI | De `platform.openai.com` (unico requisito con coste, minimo) |

> **ADVERTENCIA:** Si tu router emite una sola red que mezcla 2.4 y 5 GHz y la
> placa no la encuentra o no conecta, separa la banda de 2.4 GHz o usa el
> hotspot de otro movil en 2.4 GHz. Es el fallo mas comun.

---

## Flujo de la receta

```flowchart
   1. API key de OpenAI
            │
            ▼
   2. Cuenta en elatoai.com
            │
            ▼
   3. Guardas tu API key en Elato
            │
            ▼
   4. Creas tu agente (personalidad + voz)
            │
            ▼
   5. Registras la placa por su MAC  ──► la vinculas al agente
            │
            ▼
   6. Conectas la placa a tu WiFi (portal ELATO-DEVICE)
            │
            ▼
   7. LED verde = lista ──► HABLAS ──► te responde
```

---

## Receta paso a paso

### Paso 1 — Consigue tu API key de OpenAI

1. Entra en **https://platform.openai.com** e inicia sesion (o crea cuenta).
2. Ve a **API keys** → **Create new secret key**.
3. Copia la clave (empieza por `sk-...`) y guardala; **no se vuelve a mostrar**.
4. Asegurate de tener saldo/credito en **Billing** (con unos pocos dolares sobra
   para pruebas).

> **TIP:** Guarda la clave en un sitio seguro (gestor de contraseñas). Si la
> pierdes, tendras que generar otra.

### Paso 2 — Crea tu cuenta en Elato

1. Entra en **https://www.elatoai.com** y **regístrate** (puedes usar Google).
2. Confirma el correo si te lo pide y entra al panel (dashboard).

### Paso 3 — Guarda tu API key de OpenAI en Elato

1. En el panel de Elato, busca la seccion de **ajustes de cuenta / API keys**
   (normalmente en **Settings**).
2. Pega tu clave de OpenAI (`sk-...`) y **guarda**.

> **NOTA:** Elato guarda tu clave cifrada para usarla en su servidor cuando tu
> placa hable. La clave es **tuya**: tu pagas solo tu propio uso de OpenAI.

### Paso 4 — Crea tu agente (la personalidad y la voz)

1. En el panel, entra en **Agents / Characters** y pulsa **crear** un agente.
2. Define:
   - **Nombre**.
   - **Personalidad / prompt** (como quieres que hable y se comporte).
   - **Voz** (elige una de las disponibles).
3. **Guarda** el agente.

### Paso 5 — Registra la placa por su MAC y vinculala al agente

1. En el panel, entra en **Devices** (o **Settings → Devices**).
2. Pulsa **añadir dispositivo** e introduce la **MAC**:
   ```
   B8:F8:62:D7:59:C4
   ```
3. **Asigna** a ese dispositivo el **agente** que creaste en el Paso 4.
4. Guarda.

> **TIP:** Si la web pide un **`user code`** en vez de la MAC directamente,
> copia ese codigo y pegalo donde te indique (Settings) para enlazar el
> dispositivo con tu cuenta. Es el mismo objetivo: dejar la MAC asociada a ti.

### Paso 6 — Conecta la placa a tu WiFi

1. Enciende la placa (por USB). El LED debe estar en **magenta** (modo portal).
2. En tu movil/PC, abre la lista de redes WiFi y conectate a:
   ```
   ELATO-DEVICE
   ```
3. Se abrira sola una ventana de configuracion. Si no, abre el navegador en:
   ```
   http://192.168.4.1
   ```
4. Elige **tu red WiFi de casa (2.4 GHz)**, escribe la **contraseña** y **guarda**.
5. La placa se reiniciara y se conectara a tu WiFi. Al conectar, pide su token a
   Elato con la MAC (que ya registraste) y queda lista.

### Paso 7 — Habla con tu agente

1. Cuando el LED se ponga **verde** (IDLE), la placa esta conectada y lista.
2. **Habla directamente**, en voz normal. No hay que pulsar nada: el servidor
   detecta cuando hablas y cuando terminas.
3. El agente te respondera por el altavoz. Los colores del LED te dicen el estado
   (ver tabla abajo).
4. Para **dormir** la placa: manten el dedo en el **pad tactil ~0.5 s**. Para
   **despertarla**: da un **toque**.

---

## Guia de colores del LED (WS2812)

| Color | Estado | Que significa |
|---|---|---|
| Magenta | SOFT_AP | Portal WiFi abierto (aun sin configurar tu red) |
| Verde | IDLE | Conectada y lista, esperando que hables |
| Amarillo | LISTENING | Te esta escuchando |
| Rojo | PROCESSING | Pensando la respuesta |
| Azul | SPEAKING | Hablando (respondiendo) |
| Cian | OTA | Actualizacion de firmware en curso |

---

## El plato listo (verificacion)

Todo funciona si:

- [ON] El LED pasa de **magenta** → **verde** tras configurar el WiFi.
- [ON] Al hablar, el LED cambia a **amarillo** (escucha) y luego **azul** (responde).
- [ON] Oyes la respuesta del agente por el altavoz.

Si llegas hasta aqui, **tu hardware (micro, altavoz, LED, touch y red) esta
100% validado** y puedes pasar con confianza a la Etapa 2 (servidor local) y la
Etapa 3 (tu agente Hermes).

---

## Si algo sale mal (solucion de problemas)

| Sintoma | Causa probable | Solucion |
|---|---|---|
| La placa no ve tu WiFi | Red de 5 GHz | Usa una red de **2.4 GHz** |
| El LED se queda en magenta | No completaste el portal | Reconecta a `ELATO-DEVICE` y abre `http://192.168.4.1` |
| Conecta al WiFi pero no habla | MAC no registrada o sin agente | Revisa el Paso 5 (MAC `B8:F8:62:D7:59:C4` + agente asignado) |
| Conecta pero responde con error | Falta la API key de OpenAI o sin saldo | Revisa Paso 1 y 3; comprueba credito en OpenAI Billing |
| No responde tras unos segundos | Agotaste los 30 min/mes gratis | Espera al mes siguiente o pasa a servidor propio |
| No oigo nada / muy bajo | Cableado del altavoz I2S | Revisa el diagrama de conexionado (doc del port) |
| No me escucha bien | Cableado del micro I2S | Revisa el conexionado del micro |
| El toque no duerme/despierta | Umbral tactil | Calibra `TOUCH_THRESHOLD` (ver doc del port) |
| No conecta al servidor de voz | Backend de voz distinto (Deno vs Cloudflare) | Ver nota avanzada abajo |

> **NOTA (avanzada):** El firmware apunta al servidor **Cloudflare** de Elato
> (`elato.akash-b25.workers.dev`). Si Elato te indica usar su servidor **Deno**,
> cambia en `src/Config.h` la linea a `#define VOICE_SERVER_DENO` (comentando la
> de Cloudflare) y vuelve a flashear con `pio run -t upload` (recuerda:
> `upload_flags = --no-stub` ya esta puesto para el USB del Zero).

---

## Referencia rapida

```
DATOS DE TU PLACA
  MAC:            B8:F8:62:D7:59:C4
  Firmware:       ELATO_MODE + Cloudflare (ya flasheado)
  Portal WiFi:    ELATO-DEVICE  ->  http://192.168.4.1

RECETA (7 pasos)
  1. API key OpenAI      -> platform.openai.com
  2. Cuenta Elato        -> elatoai.com
  3. Guardar API key en Elato (Settings)
  4. Crear agente (personalidad + voz)
  5. Registrar MAC + asignar agente (Devices)
  6. Conectar WiFi por el portal (2.4 GHz!)
  7. LED verde -> hablar

COLORES LED
  Magenta=portal  Verde=lista  Amarillo=escucha
  Rojo=piensa     Azul=responde  Cian=OTA

LIMITES
  Nube gratis: 30 min / mes
  Necesitas: tu propia API key de OpenAI (con saldo)
```

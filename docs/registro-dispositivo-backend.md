# Registro del dispositivo y backend — ElatoAI sobre ESP32-S3 Zero

---

## Descripcion general

Este documento explica la pieza del proyecto **ElatoAI** que queda *fuera*
del firmware del hardware: **el registro del dispositivo y la configuracion del
backend / credenciales**. Es la respuesta a la frase "falta lo de siempre del
proyecto: registrar el dispositivo y configurar el backend (modo `ELATO_MODE`),
pero eso es logica de servidor, ajena al hardware".

ElatoAI no es solo firmware. Es un sistema de **tres componentes** que se
comunican por HTTPS y WebSockets seguros (WSS):

1. **Frontend** — aplicacion web Next.js desplegada en Vercel, con base de
   datos **Supabase** (Postgres). Aqui creas tu cuenta, defines agentes de IA
   (personalidad + voz) y **das de alta tu dispositivo por su direccion MAC**.
2. **Servidor edge** — funciones **Deno Edge** o **Cloudflare Workers** que
   reciben el WebSocket del ESP32 y hablan con el proveedor de IA (OpenAI,
   Gemini, xAI, ElevenLabs, Hume...).
3. **Cliente ESP32** — tu placa (el ESP32-S3 Zero adaptado). Solo guarda
   *URLs de configuracion* y un *token de autenticacion*; no contiene ninguna
   clave de IA ni la personalidad del agente.

El firmware **no usa MQTT ni Bluetooth para datos**: toda la conversacion viaja
por WebSocket seguro hacia el servidor edge.

> **NOTA:** El ESP32 nunca almacena claves de OpenAI/Gemini ni la personalidad
> del agente. Solo guarda en su memoria NVS (flash) un **token JWT** que el
> backend le entrega tras reconocer su MAC. Por eso esta configuracion es
> identica para cualquier placa: el LED WS2812, el touch o el ESP32-S3 Zero no
> cambian nada de este flujo.

---

## Flujo de operacion principal

```flowchart
        Arranque del ESP32
               │
               ▼
        ¿Hay WiFi guardado?
        /                  \
      NO                    SI
       │                     │
       ▼                     ▼
  Portal cautivo        Conecta al WiFi
  SoftAP                     │
  "ELATO-DEVICE"             │
       │                     │
       └─────────┬───────────┘
                 ▼
        isDeviceRegistered()
                 │
                 ▼
   GET /api/generate_auth_token?macAddress=MAC
                 │
                 ▼
     ¿MAC dada de alta en tabla "devices"?
        /                          \
      NO                            SI
       │                            │
       ▼                            ▼
  Error: "User not found"     Backend firma JWT
  (sin token, no conecta)     con JWT_SECRET_KEY
                                    │
                                    ▼
                         Token guardado en NVS
                         (namespace "auth")
                                    │
                                    ▼
                    websocketSetup() con cabecera
                    "Authorization: Bearer <token>"
                                    │
                                    ▼
                       Conversacion por WSS con
                       el servidor edge
```

---

## Arquitectura

```
┌──────────────────────────────────────────────────────────────────┐
│  FRONTEND  (Next.js en Vercel)                                     │
│  - Crear cuenta / OAuth Google                                     │
│  - Crear agentes de IA (personalidad, voz)                        │
│  - Registrar dispositivo (MAC + user_code)  ── tabla "devices"    │
│  - Endpoint: app/api/generate_auth_token/route.ts                 │
└───────────────┬───────────────────────────────┬──────────────────┘
                │ (Postgres / RLS)               │ HTTPS GET (MAC)
                ▼                                 │
┌──────────────────────────────┐                 │
│  SUPABASE (Postgres)          │                 │
│  - tabla users               │                 │
│  - tabla devices             │◄────────────────┘
│  - transcripciones, RLS      │   firma JWT con JWT_SECRET_KEY
└──────────────────────────────┘
                ▲
                │ valida JWT
┌───────────────┴──────────────────────────────────────────────────┐
│  SERVIDOR EDGE  (Deno Edge  o  Cloudflare Workers)                │
│  - Recibe WSS del ESP32 (ws_path: "/" o "/ws/esp32")             │
│  - Habla con el proveedor de IA (OpenAI/Gemini/xAI/...)          │
└───────────────▲──────────────────────────────────────────────────┘
                │ WSS  "Authorization: Bearer <token>"
                │      "X-Device-Mac: <MAC>"
┌───────────────┴──────────────────────────────────────────────────┐
│  ESP32-S3 ZERO  (firmware-arduino)                                │
│  - Config.h / Config.cpp : modo + URLs + certificados            │
│  - WifiManager.cpp       : isDeviceRegistered() obtiene el token  │
│  - Audio.cpp             : websocketSetup() abre el WSS           │
│  - NVS ("auth")          : guarda auth_token de forma persistente │
└──────────────────────────────────────────────────────────────────┘
```

---

## Archivos involucrados

| Archivo | Rol |
|---|---|
| `firmware-arduino/src/Config.h` | Selecciona el modo (`DEV`/`PROD`/`ELATO`) y el backend de voz (`DENO`/`CLOUDFLARE`). Declara URLs y certificados. |
| `firmware-arduino/src/Config.cpp` | Define las URLs (`ws_server`, `backend_server`), puertos, rutas WSS y los certificados CA segun el modo elegido. |
| `firmware-arduino/src/WifiManager.cpp` | `isDeviceRegistered()`: pide el token al backend con la MAC y lo guarda en NVS. Portal cautivo SoftAP. |
| `firmware-arduino/src/Audio.cpp` | `websocketSetup()`: abre el WSS con la cabecera `Authorization: Bearer <token>` y `X-Device-Mac`. |
| `firmware-arduino/src/main.cpp` | `getAuthTokenFromNVS()` carga el token al arrancar; nombre del SoftAP `"ELATO-DEVICE"`. |
| `frontend-nextjs/app/api/generate_auth_token/route.ts` | Endpoint del backend que busca la MAC en `devices`, firma el JWT con `JWT_SECRET_KEY` y lo devuelve. |
| `frontend-nextjs/.env.example` | Variables del frontend: Supabase, `JWT_SECRET_KEY`, `NEXT_PUBLIC_SKIP_DEVICE_REGISTRATION`, claves de IA. |
| `server/deno/.env.example` | Variables del servidor edge Deno: Supabase, `JWT_SECRET_KEY`, claves de los proveedores de IA. |
| `server/cloudflare/.dev.vars.example` | Variables del Worker de Cloudflare (incluye `JWT_SECRET_KEY`). |

---

## Funcionamiento interno

### Que significa "registrar el dispositivo"

"Registrar el dispositivo" **no es algo que se hace en el hardware**. Es dar de
alta tu placa en la base de datos para que el backend sepa a que usuario y a que
agente de IA pertenece. Segun el comentario del propio endpoint
(`generate_auth_token/route.ts`):

```
Steps to register your device:
1: Register the device `mac_address` and `user_code` in the `devices` table.
2: Make sure the user adds the `user_code` to their account in Settings
   to link the device to their `user_id`.
3: When NEXT_PUBLIC_SKIP_DEVICE_REGISTRATION is false, we fetch the user
   by `mac_address`.
```

En la practica, en el frontend:

1. Se inserta una fila en la tabla **`devices`** con la **MAC** del ESP32 y un
   **`user_code`**.
2. El usuario introduce ese `user_code` en **Settings** de la webapp, lo que
   asocia el dispositivo a su `user_id` (y por tanto a su agente de IA).

> **TIP:** Para saber la MAC de tu ESP32-S3 Zero, abre el monitor serie
> (`pio device monitor`) y reinicia: el firmware la imprime. Tambien existe el
> test `test/print_mac_address_test.cpp`. Recuerda: el Zero solo expone USB-C
> nativo, asi que el monitor funciona gracias a `ARDUINO_USB_CDC_ON_BOOT=1`.

### Flujo de autenticacion del firmware

`WifiManager.cpp -> isDeviceRegistered()` es el corazon del proceso. Tras
conectarse al WiFi, el dispositivo pide su token:

```cpp
// Modo produccion/ELATO (HTTPS con certificado CA)
WiFiClientSecure client;
client.setCACert(Vercel_CA_cert);
http.begin(client, "https://" + String(backend_server) +
            "/api/generate_auth_token?macAddress=" + WiFi.macAddress());
...
String authToken = doc["token"];
preferences.begin("auth", false);
preferences.putString("auth_token", authToken); // <- persiste en NVS (flash)
```

El backend (`generate_auth_token/route.ts`) busca la MAC, obtiene el usuario y
firma un JWT de Supabase (algoritmo `HS256`) con `JWT_SECRET_KEY`, **sin
expiracion** (`expireDays = null`):

```ts
const user = await getUserByMacAddress(macAddress);     // tabla "devices"
const token = createSupabaseToken(process.env.JWT_SECRET_KEY!, payload, null);
return NextResponse.json({ token });
```

Si la MAC **no** esta registrada, el endpoint responde `"User not found"` y el
dispositivo se queda sin token (no podra conectar al WebSocket).

### Uso del token en el WebSocket

Una vez obtenido, el token viaja en cada conexion WSS como cabecera HTTP
(`Audio.cpp -> websocketSetup()`):

```cpp
const String headers =
    "Authorization: Bearer " + String(authTokenGlobal) + "\r\n" +
    "X-Wifi-Rssi: " + String(WiFi.RSSI()) + "\r\n" +
    "X-Device-Mac: " + WiFi.macAddress();
...
webSocket.beginSslWithCA(server_domain.c_str(), port, path.c_str(), CA_cert);
```

El servidor edge valida ese JWT contra el mismo `JWT_SECRET_KEY` antes de
aceptar la conversacion.

> **ADVERTENCIA:** El `JWT_SECRET_KEY` debe ser **identico** en el frontend
> (quien firma el token) y en el servidor edge (quien lo valida). Si no
> coinciden, el dispositivo obtiene token pero el WebSocket lo rechaza.

### El "atajo" para desarrollo: saltar el registro

Si en el frontend defines `NEXT_PUBLIC_SKIP_DEVICE_REGISTRATION=True`, el
endpoint **ignora la MAC** y usa siempre un usuario de desarrollo
(`admin@elatoai.com`):

```ts
if (skipDeviceRegistration) {
    user = await getDevUser();          // admin@elatoai.com
} else {
    user = await getUserByMacAddress(macAddress);
}
```

> **TIP:** Usa `SKIP_DEVICE_REGISTRATION=True` para probar rapido tu placa sin
> dar de alta la MAC. Ponlo en `False` para produccion / multiples
> dispositivos.

### Persistencia (NVS)

El token se guarda en la particion **NVS** de la flash (namespace `"auth"`,
clave `"auth_token"`). Al arrancar, `main.cpp -> getAuthTokenFromNVS()` lo
carga en `authTokenGlobal`. Por eso solo se pide al backend la primera vez (o
tras un *factory reset*).

> **NOTA:** La nueva tabla de particiones de 4 MB del ESP32-S3 Zero
> (`partition.csv`) mantiene la particion `nvs`, asi que el token sobrevive a
> reinicios y actualizaciones OTA igual que en la placa original de 16 MB.

---

## Manual de usuario

### Requisitos previos

- Una cuenta y proyecto en **Supabase** (o Supabase local con CLI).
- El **frontend Next.js** desplegado (Vercel) o corriendo en local.
- Un **servidor edge** desplegado: Deno Deploy o Cloudflare Workers.
- Claves de API del proveedor de IA que vayas a usar (p. ej. `OPENAI_API_KEY`).
- La **direccion MAC** de tu ESP32-S3 Zero.

> **NOTA:** Si solo quieres probar sin desplegar nada propio, puedes usar
> `ELATO_MODE`, que apunta a los servidores ya hospedados de Elato
> (`www.elatoai.com`). En ese caso el registro se hace en la web de Elato.

### Eleccion del modo (paso clave)

El modo se elige **descomentando una sola linea** en `firmware-arduino/src/Config.h`:

```cpp
// #define DEV_MODE       // servidores locales (tu PC), sin SSL
// #define PROD_MODE      // TUS servidores propios desplegados, con TUS certificados
#define ELATO_MODE        // servidores hospedados por Elato (por defecto)

// Backend de voz (independiente del modo):
// #define VOICE_SERVER_DENO
#define VOICE_SERVER_CLOUDFLARE
```

Diferencias resumidas (valores reales de `Config.cpp`):

| Modo | `ws_server` / `backend_server` | Puerto WSS | SSL | Para quien |
|---|---|---|---|---|
| `DEV_MODE` | IP local (ej. `192.168.1.33`) | 8000 (Deno) / 8787 (CF) | [OFF] (http/ws) | Desarrollo en tu PC |
| `PROD_MODE` | `<tu>.deno.dev` / `<tu>.workers.dev` + tu backend Vercel | 443 | [ON] (tus certs) | Tu propio despliegue |
| `ELATO_MODE` | `talkedge.deno.dev` / `elato.akash-b25.workers.dev` + `www.elatoai.com` | 443 | [ON] (certs incluidos) | Usar la nube de Elato |

| Backend de voz | `ws_path` | Notas |
|---|---|---|
| `VOICE_SERVER_DENO` | `/` | Funciones Deno Edge |
| `VOICE_SERVER_CLOUDFLARE` | `/ws/esp32` | Cloudflare Workers + Durable Objects |

### Setup paso a paso (tu propio backend: PROD_MODE)

1. **Supabase**: crea el proyecto y aplica el esquema (tablas `users`,
   `devices`, etc.). Apunta tu `SUPABASE_URL` y `ANON_KEY`.

2. **Frontend** (`frontend-nextjs/.env`): copia `.env.example` y rellena:

   ```bash
   NEXT_PUBLIC_SUPABASE_URL=<URL de tu proyecto Supabase>
   NEXT_PUBLIC_SUPABASE_ANON_KEY=<anon key>
   JWT_SECRET_KEY=<una clave de 32+ caracteres>
   NEXT_PUBLIC_SKIP_DEVICE_REGISTRATION=False
   OPENAI_API_KEY=<tu clave>
   ```

3. **Servidor edge** (`server/deno/.env` o `server/cloudflare/.dev.vars`):
   usa **el mismo** `JWT_SECRET_KEY` y las claves de IA.

4. **Registra tu dispositivo** en la webapp: da de alta la **MAC** del ESP32
   (tabla `devices`) y vincula el `user_code` en *Settings*. Asocia un agente.

5. **Firmware** (`Config.h`): selecciona `PROD_MODE` y el backend de voz.
   En `Config.cpp`, sustituye las URLs `<your-...>` por las tuyas y pega tus
   certificados CA (`CA_cert` y `Vercel_CA_cert`).

6. **Compila y flashea** (recuerda el modo descarga del Zero: manten **BOOT**
   pulsado, conecta USB-C, suelta):

   ```bash
   cd firmware-arduino
   pio run -t upload
   pio run -t uploadfs        # sube startup.mp3 a SPIFFS
   pio device monitor
   ```

7. **Conecta el WiFi**: en el primer arranque el ESP32 crea el SoftAP
   **`ELATO-DEVICE`**. Conectate desde el movil, elige tu red y guarda. El
   dispositivo pedira el token automaticamente.

> **TIP:** El error mas comun no es de hardware: es que el `JWT_SECRET_KEY` del
> frontend y del servidor edge no coinciden, o que la MAC no esta dada de alta.

### Setup rapido sin desplegar nada (ELATO_MODE)

1. En `Config.h` deja `#define ELATO_MODE` (ya viene por defecto).
2. Compila y flashea como arriba.
3. Conecta el WiFi por el SoftAP `ELATO-DEVICE`.
4. Registra el dispositivo en **www.elatoai.com** con su MAC y vinculalo a tu
   cuenta/agente. Los certificados ya estan incluidos en `Config.cpp`.

### Personalizacion

- **Nombre del portal WiFi**: `firmware-arduino/src/main.cpp`, llamada a
  `WifiManager.startBackgroundTask("ELATO-DEVICE")`.
- **URLs y puertos**: `firmware-arduino/src/Config.cpp` (bloques por modo).
- **Certificados CA**: `Config.cpp`, variables `CA_cert` y `Vercel_CA_cert`.

> **ADVERTENCIA:** No publiques tu `JWT_SECRET_KEY` ni claves de IA en un
> repositorio. Mantenlas en archivos `.env` / variables de entorno, nunca en
> codigo commiteado.

---

## Solucion de problemas

| Sintoma | Causa probable | Solucion |
|---|---|---|
| El dispositivo nunca conecta al WebSocket | La MAC no esta registrada en la tabla `devices` | Da de alta la MAC y vincula el `user_code`, o pon `SKIP_DEVICE_REGISTRATION=True` para probar |
| Respuesta `"User not found"` en el monitor | `getUserByMacAddress` no encuentra la MAC | Verifica que la MAC en `devices` coincide exactamente con la del ESP32 |
| Obtiene token pero el WSS se rechaza/desconecta | `JWT_SECRET_KEY` distinto entre frontend y servidor edge | Usa la **misma** clave en ambos `.env` |
| Fallo de handshake SSL / no conecta por HTTPS | Certificado `CA_cert` / `Vercel_CA_cert` incorrecto o caducado | Pega el certificado CA correcto del servidor en `Config.cpp` |
| No aparece el portal `ELATO-DEVICE` | Ya hay un WiFi guardado, o no arranco el SoftAP | Haz un factory reset; revisa `fallbackToSoftAp(true)` en `main.cpp` |
| No se ve nada en el monitor serie del Zero | Falta USB-CDC nativo (el Zero no tiene chip UART) | Confirma `ARDUINO_USB_CDC_ON_BOOT=1` en `platformio.ini` |
| Cambie de cuenta/agente y sigue usando el viejo | El token antiguo persiste en NVS | Haz factory reset para borrar `auth_token` y volver a pedirlo |
| En DEV_MODE no conecta | IP local incorrecta o servidor no levantado | Ajusta la IP en `Config.cpp` (DEV) y arranca el servidor edge local |

---

## Referencia rapida

```
SELECCION DE MODO  (firmware-arduino/src/Config.h)
  #define DEV_MODE        -> servidores locales, sin SSL
  #define PROD_MODE       -> tus servidores + tus certificados
  #define ELATO_MODE      -> nube de Elato (por defecto)
  #define VOICE_SERVER_DENO        -> ws_path "/"
  #define VOICE_SERVER_CLOUDFLARE  -> ws_path "/ws/esp32"

REGISTRO DEL DISPOSITIVO  (frontend / Supabase)
  1. Insertar MAC + user_code en tabla "devices"
  2. Vincular user_code en Settings (webapp)
  3. SKIP_DEVICE_REGISTRATION=False en produccion

CREDENCIALES CLAVE  (.env)
  JWT_SECRET_KEY        -> IGUAL en frontend y servidor edge
  SUPABASE_URL / ANON   -> proyecto Supabase
  OPENAI_API_KEY / ...  -> proveedor de IA (solo servidor/frontend)

FLUJO DEL TOKEN  (firmware)
  WiFi -> isDeviceRegistered()
       -> GET /api/generate_auth_token?macAddress=MAC
       -> token JWT -> NVS ("auth"/"auth_token")
       -> websocketSetup() "Authorization: Bearer <token>"

COMANDOS
  pio run -t upload        # flashear firmware
  pio run -t uploadfs      # subir startup.mp3 a SPIFFS
  pio device monitor       # ver MAC y logs (USB-CDC)

SoftAP de configuracion WiFi:  ELATO-DEVICE
```

# ElatoAI — asistente de voz ESP32-S3 + bridge local

Fork de [`akdeb/ElatoAI`](https://github.com/akdeb/ElatoAI) (MIT) adaptado para
correr **sin la nube de Elato**: una placa ESP32-S3 Zero conversa por WiFi con un
bridge propio en una Mac mini de la LAN, que hace STT/LLM/TTS local. Lo agregado
sobre el upstream es el **barge-in**: poder interrumpir al asistente mientras
habla, por botón y por voz.

**Rama de trabajo: `feature/esp32-s3-zero-port`.** `main` es el upstream sin
tocar. El remoto propio se llama `fork` (`github.com/cag446/ElatoAI`).

## Empezá acá

**`docs/contexto-proyecto-barge-in.md`** — escrito para quien llega sin contexto:
arquitectura, hardware, las tres fases del barge-in, **todos los números medidos
en hardware** y 17 trampas verificadas. Leelo entero antes de proponer cambios.

Después, según el caso:

| Para | Leé |
|---|---|
| Tocar el AEC | `firmware-arduino/src/Aec.h` (los comentarios son la historia completa) |
| Evaluar alternativas OSS | `docs/investigacion-integracion-oss.md` |
| Entender el bridge | `docs/propuesta-hermes-bridge.md`, `server/hermes-bridge/README.md` |
| Instalar o desplegar | `docs/runbook-hermes-bridge.md` |
| Flashear | `docs/firmware-binarios-y-flasheo.md` |
| Conexionado | `docs/conexionado-por-componente.md` |

## Reglas del proyecto

**Quién es dueño de qué.** El **firmware** se compila y flashea **solo desde esta
VM**. El **bridge** corre en la Mac mini, pero el archivo maestro es el de este
repo: se edita acá y se copia allá. Nunca al revés, y nunca editarlo directo en
la Mac mini — esa desincronización ya mordió dos veces.

**Antes de tocar `bridge.py`**, respaldo con fecha y hora en la Mac mini:
`cp -p bridge.py bridge.py.bak-$(date +%Y-%m-%d_%H%M%S)`, y verificar el md5
contra el del repo antes de empezar.

**Reiniciar el bridge:** `launchctl unload` + `sleep` + `launchctl load -w`.
**Nunca `bootstrap`**: por SSH falla con error 5 y deja el servicio caído.

**Acceso a la Mac mini (`wilbot@192.168.100.23`)** con la clave
`~/.ssh/id_ed25519`. Si la VM no está en la LAN, se llega por **Tailscale**: una
Raspberry es subnet router y alcanza con `sudo tailscale set --accept-routes`
(lo corre Carlos, pide contraseña). Guía completa en
`../sessions/2026-09-23-tailscale-subnet-router-acceso-lan-remota.md`.

**Fechas:** usar el reloj de la VM (`date`), no la fecha del contexto de sesión,
que puede ir un día adelantada.

**Los comentarios del código son documentación de primera clase.** Si cambiás el
comportamiento, actualizá el comentario en el mismo commit. Un índice que miente
es peor que no tener índice: ya mandó a un agente en la dirección equivocada.

**No inventes mediciones.** Todo lo acústico de este proyecto está medido en
hardware. Si algo no se midió, decí que no se midió. Y antes de declarar un
límite, preguntá **bajo qué condiciones se midió**: dos conclusiones se dieron
por cerradas mal por haber medido en una sola condición.

**Ante un síntoma nuevo, preguntá PRIMERO desde cuándo pasa.** Es la pregunta que
resolvió el carraspeo después de tres hipótesis erradas.

## El otro repo

La Mac mini tiene el repo propio de Deb en
`~/Proyectos/experimentos/hermes-bridge` (su `TODO.md`, `CHANGELOG` y los avisos
que nos dejamos en `docs/`). **Deb no toca `bridge.py` ni `voice_history.py`**:
eso es de esta VM. Para hablarle: POST a `/v1/chat/completions` en
`127.0.0.1:8642` con la key del plist — mensajes cortos.

## Probar

```bash
cd ~/Proyectos/experimentos/hermes-bridge        # en la Mac mini
.venv/bin/python3 tests/test_barge_state.py      # máquina de estados, segundos
.venv/bin/python3 tests/check_barge_contract.py  # contrato firmware↔bridge
```

`check_barge_contract.py` da **18/20 y está bien así**: los 2 FAIL son del propio
checker, que quedó viejo el 2026-09-15 al agregarse el `via`. Compará siempre
contra el respaldo antes de culpar a un cambio nuevo.

`tests/test_barge_in.py` (integración con audio real) **no corre**: falta
`ffmpeg` en la Mac mini.

## Estado

Ver las memorias del proyecto (se cargan solas) y
`/home/cgomez/Documentos/ElatoAI/estado-y-pendientes-*.md` para lo hecho y lo que
falta.

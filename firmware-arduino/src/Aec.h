#ifndef AEC_H
#define AEC_H

// Fase C (paso 2, 2do intento): cancelacion de eco acustico on-device para el
// barge-in por voz.
//
// QUE CAMBIO RESPECTO DEL 1er INTENTO (que crasheo la placa):
//
// 1) MEMORIA. El AEC con frame=256/filter=2048 reservaba 92 KB y dejaba al
//    decodificador Opus sin heap (StoreProhibited en silk_decode_frame).
//    El consumo de speex escala asi (medido y verificado contra mdf.c):
//        ~24 * filter_length  +  ~104 * frame_size  +  ~14 KB fijos
//    Con frame=128/filter=1024 son ~52 KB en vez de 92 KB.
//
// 2) PRE-RETARDO DE LA REFERENCIA. El error conceptual del 1er intento fue
//    poner un filtro larguisimo (128 ms) para "alcanzar" el eco. La referencia
//    se toma en el tee, ANTES del DMA de salida, asi que llega al AEC
//    ADELANTADA respecto del eco que oye el mic (DMA de salida + acustica +
//    DMA de entrada). Lo correcto es RETRASAR la referencia ese tanto y usar
//    un filtro corto, que es lo que abarata la memoria.
//
// 3) EL RETARDO SE MIDE SOLO. En vez de adivinarlo, al empezar cada respuesta
//    se corre una correlacion cruzada mic-vs-referencia sobre un rango de
//    lags; el pico da el retardo real. Despues se fija y arranca la
//    cancelacion. Asi no depende de constantes del DMA que pueden cambiar.
//
// Numeros del eco medidos el 2026-09-14 con el mic abierto: voz del usuario
// med -34.8 dBFS, eco de Deb med -41.7 dBFS (7 dB POR DEBAJO de la voz). El
// eco es cancelable; el problema son los picos (p90 -30.6), que se solapan con
// la voz y confunden a un detector por energia pura.
//
// Tasas: parlante 24 kHz, mic 16 kHz. La referencia se remuestrea 24k -> 16k.

// ⛔ DESACTIVADO (2026-09-15) — FALSOS POSITIVOS, no por memoria.
//
// El AEC arranca bien (48 KB, sin crash) y el barge por voz llego a funcionar
// end-to-end. Pero en uso real dispara solo: corta a Deb apenas empieza a
// hablar, sin que el usuario diga nada. Medido en hardware:
//
//   [AEC] voz sobre la respuesta: -27.1 dB (piso -58.9) -> BARGE
//
// Ese -27.1 dB es el nivel de los PICOS del eco de Deb (medidos: max -24.4,
// p90 -30.6), no la voz del usuario. Lo que pasa:
//   - El AEC cancela bien el eco MEDIO: piso de residuo -58.9 dB contra un eco
//     crudo de -41.7 dB de mediana => ~16 dB de cancelacion.
//   - Pero NO cancela los PICOS: -24 crudo -> -27 residual, apenas 3 dB. Eso es
//     eco NO LINEAL (distorsion del amplificador clase D + vibracion de la
//     protoboard), que un AEC lineal como speex no puede cancelar.
//   - El detector compara contra un piso lento, y un pico 31 dB sobre el piso
//     lo dispara. La voz del usuario esta ~27 dB sobre el piso: NO hay
//     separacion entre "pico de eco" y "voz del usuario" por energia sola.
//
// Sintoma secundario que lo confirma: el retardo refinado por correlacion sale
// distinto en cada arranque (90 -> 130 -> 146 ms) con ratios de pico flojos
// (~1.5), que es lo que pasa cuando buena parte del eco no es copia lineal de
// la referencia.
//
// COMO SEGUIR (en orden de costo):
//  1. Supresor de eco RESIDUAL: enlazar un SpeexPreprocessState al echo state
//     con SPEEX_PREPROCESS_SET_ECHO_STATE (preprocess.c:1162 lo soporta y la
//     lib expone getEchoState()). Es la pieza estandar que falta: speex separa
//     el AEC lineal de la supresion del residuo no lineal.
//  2. Detector de doble-habla comparando residuo vs nivel de referencia, en vez
//     de un piso absoluto.
//  3. LAYOUT: separar mic y parlante 10+ cm en la unidad soldada. El eco no
//     lineal se arregla con fisica, no con software.
//
// Mientras tanto el barge por BOTON (Fase B) funciona y no depende de esto.
// ⛔ DESACTIVADO (2026-09-16) — CONCLUSION MEDIDA, no supuesta.
//
// Se implemento el diseno COMPLETO de speex: cancelador lineal
// (speex_echo_cancellation) + SUPRESOR DE ECO RESIDUAL (speex_preprocess con
// el echo state enlazado, SPEEX_PREPROCESS_SET_ECHO_STATE). El supresor
// FUNCIONA y se midio cuanto aporta:
//
//   pico del residuo, solo cancelador lineal : -27.1 dB
//   pico del residuo, + supresor             : -35.7 dB   (mejora de 8.6 dB)
//   piso del residuo                         : -58.9 -> -66.2 dB (7 dB mejor)
//   voz del usuario en el mic                : -34.8 dB (mediana)
//
// El criterio fijado de antemano era pico <= -45 dB, para que la voz del
// usuario quedara 10 dB por encima del residuo. Se llego a -35.7 dB: el
// residuo quedo EMPATADO con la voz. Sin separacion, ningun umbral distingue
// las dos senales. Subir la agresividad del supresor tampoco sirve: durante
// el doble-habla no puede distinguir la voz del eco y suprimiria ambas.
//
// => El limite es FISICO: eco no lineal (distorsion del amplificador clase D +
//    vibracion) con mic y parlante a centimetros en la misma protoboard. Se
//    arregla con LAYOUT en la unidad soldada (separar 10+ cm, parlante
//    apuntando lejos, mic aislado), no con software.
//
// El codigo queda completo y probado: reactivar con AEC_ENABLED=1 cuando el
// montaje cambie, y volver a medir el pico del residuo con el mismo criterio.
// El barge por BOTON (Fase B) no depende de esto y funciona.
// REACTIVADO (2026-09-16) para probar la variante de UMBRAL ABSOLUTO.
// Los dos falsos positivos medidos dieron picos de residuo en -35.7 y -41.6 dB,
// y la voz del usuario va de -34.8 (mediana) a -29.4 (p90). Hay ~6 dB de
// ventana en la parte alta. El detector anterior usaba umbral RELATIVO al piso
// (piso + 12 dB): con el piso en -66 disparaba con cualquier cosa sobre -54.
// Ahora exige ADEMAS superar un umbral ABSOLUTO calibrado con esos numeros.
// Es una ventana estrecha: exige hablar a volumen normal-alto y puede fallar
// si cambia la distancia o el volumen. La solucion de fondo sigue siendo el
// LAYOUT (separar mic y parlante).
#define AEC_ENABLED 1

#include <stdint.h>
#include <stddef.h>

// Frame del AEC en muestras a 16 kHz. 128 = 8 ms.
constexpr int AEC_FRAME = 64;
// Cola de eco que cubre el filtro, en muestras a 16 kHz. 1024 = 64 ms.
// Con la referencia ya pre-retardada, solo tiene que cubrir la INCERTIDUMBRE
// del retardo, no el retardo entero.
constexpr int AEC_FILTER = 512;

// Heap libre minimo (bytes) exigido antes de inicializar el AEC. Si no se
// llega, el AEC no arranca y el firmware sigue funcionando sin cancelar:
// preferimos un asistente sano sin barge por voz que un crash-loop.
// libopus reserva un bloque CONTIGUO de 60 KB (GLOBAL_STACK_SIZE) de forma
// DIFERIDA, en el primer decode — despues de que el AEC ya tomo lo suyo. Si no
// entra, global_stack queda invalido y crashea en silk_decode_frame. Por eso el
// guardarraii no mira el heap ANTES de inicializar sino el que QUEDA DESPUES, y
// exige margen para ese bloque.
constexpr uint32_t OPUS_SCRATCH_BYTES = 60000;
constexpr uint32_t AEC_MIN_FREE_AFTER = OPUS_SCRATCH_BYTES + 20 * 1024;  // 80 KB

bool aecBegin();                 // inicializa; imprime el desglose de heap
void aecFeedReference(const uint8_t *pcm24k, size_t bytes);  // tee de salida
void aecResetReference();        // al entrar/salir de SPEAKING
void aecResetDetector();         // reinicia medicion de retardo + detector
bool aecProcessMicFrame(const int16_t *mic, int16_t *out);   // true = hay voz

#endif

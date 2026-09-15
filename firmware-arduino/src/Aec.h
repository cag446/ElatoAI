#ifndef AEC_H
#define AEC_H

// Fase C (paso 2): cancelacion de eco acustico on-device para el barge-in por voz.
//
// Idea: mientras Deb habla (SPEAKING), el mic capta a Deb saliendo por el
// parlante (eco) ademas de la voz del usuario. El AEC de speexdsp resta el eco
// usando como REFERENCIA el mismo audio que se manda al parlante. Sobre la
// senal ya cancelada se decide si el usuario esta hablando encima (barge).
//
// Numeros medidos el 2026-09-14 con el mic abierto: voz del usuario med
// -34.8 dBFS, eco de Deb med -41.7 dBFS (7 dB por debajo). Un AEC lineal de
// ~20 dB deja el eco muy por debajo del residuo; el problema son los PICOS
// del eco (p90 -30.6), que se solapan con la voz -> por eso no alcanza con un
// umbral de energia y hace falta cancelar.
//
// Tasas: el parlante suena a 24 kHz y el mic a 16 kHz. La referencia se
// remuestrea 24k -> 16k antes de entrar al AEC.

// ⛔ DESACTIVADO (2026-09-15). Con frame=256/filter=2048 el AEC reserva
// 92 KB de heap ([AEC] ready: heap used=94692) y deja ~44 KB libres en
// operacion: el decodificador Opus se queda sin memoria y la placa crashea
// con StoreProhibited dentro de silk_decode_frame apenas Deb empieza a hablar
// (bucle de reinicio verificado en hardware). Para reactivarlo hay que bajar
// el consumo: frame=128 + filtro corto + PRE-RETARDO de la referencia (el
// ring debe mantener ~64 ms de cola, la latencia del DMA de salida, para que
// un filtro corto alcance a cubrir el eco). Ver la bitacora del 2026-09-15.
#define AEC_ENABLED 0

#include <stdint.h>
#include <stddef.h>

// Frame del AEC en muestras a 16 kHz. 256 = 16 ms.
constexpr int AEC_FRAME = 256;
// Largo del filtro (cola de eco que puede cancelar) en muestras a 16 kHz.
// El DMA de salida (6 x 512 B a 24 kHz) mete ~64 ms entre "referencia" y
// "eco en el mic"; 2048 = 128 ms deja margen.
constexpr int AEC_FILTER = 2048;

// Inicializa AEC + resampler + ring de referencia. Imprime el heap usado.
bool aecBegin();

// Tee de salida: llamar con el PCM 16-bit mono a 24 kHz que va al parlante.
// Lo remuestrea a 16 kHz y lo guarda como referencia. Barato, no bloquea.
void aecFeedReference(const uint8_t *pcm24k, size_t bytes);

// Descarta la referencia acumulada (al entrar/salir de SPEAKING).
void aecResetReference();

// Procesa UN frame de mic (AEC_FRAME muestras, 16 kHz) contra la referencia.
// Escribe la senal cancelada en `out` (mismo tamano). Devuelve true si, sobre
// la senal cancelada, hay voz sostenida del usuario -> pedir barge.
bool aecProcessMicFrame(const int16_t *mic, int16_t *out);

// Reinicia el detector de voz sostenida (al empezar cada respuesta).
void aecResetDetector();

#endif

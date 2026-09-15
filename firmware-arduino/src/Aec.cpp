#include "Aec.h"
#include <Arduino.h>
#include <ESP32-SpeexDSP.h>
#include <math.h>
#include <string.h>

// --- Estado ---------------------------------------------------------------
static ESP32SpeexDSP dsp;
static bool ready = false;

// Ring de referencia a 16 kHz. Tiene que cubrir la latencia entre "lo que
// mandamos al I2S" y "lo que el mic oye": DMA (~64 ms) + acustica (<5 ms a
// 40 cm) + jitter de scheduling. 4096 muestras = 256 ms de margen holgado.
constexpr int REF_RING = 4096;
static int16_t refRing[REF_RING];
static volatile int refHead = 0;   // escribe audioStreamTask
static volatile int refTail = 0;   // lee micTask
static portMUX_TYPE refMux = portMUX_INITIALIZER_UNLOCKED;

// Buffer temporal del resampler: un bloque de salida del copier (1024 B =
// 512 muestras a 24 kHz) da ~342 muestras a 16 kHz.
static int16_t refTmp[1024];

// --- Detector de voz sostenida sobre la senal cancelada -------------------
// Mide el RMS del residuo y lo compara con un piso que se adapta despacio
// (tracking del residuo del AEC). Cuando el RMS supera el piso por un margen
// durante N frames seguidos, hay voz encima de la respuesta.
//
// Numeros (medidos 2026-09-14): la voz del usuario esta en ~-35 dBFS med, el
// eco crudo en ~-42. Tras ~20 dB de AEC el residuo queda en ~-60. Un margen
// de 12 dB sobre el piso separa voz de residuo con holgura.
constexpr float DETECT_MARGIN_DB = 12.0f;
constexpr int   DETECT_FRAMES    = 20;      // 20 x 16 ms = 320 ms de voz sostenida
constexpr int   WARMUP_FRAMES    = 30;      // ~0.5 s para que el AEC converja
static float floorDb = -60.0f;
static int   voiceRun = 0;
static int   warmup = 0;

static float rmsDb(const int16_t *x, int n) {
    double acc = 0;
    for (int i = 0; i < n; i++) acc += (double)x[i] * x[i];
    float rms = sqrtf((float)(acc / n)) / 32768.0f;
    return 20.0f * log10f(rms + 1e-9f);
}

// --- API --------------------------------------------------------------------
bool aecBegin() {
#if !AEC_ENABLED
    Serial.println("[AEC] deshabilitado (AEC_ENABLED=0): mic sin cancelar");
    return false;
#else
    uint32_t before = ESP.getFreeHeap();
    if (!dsp.beginAEC(AEC_FRAME, AEC_FILTER, 16000)) {
        Serial.println("[AEC] beginAEC FAILED");
        return false;
    }
    dsp.enableAEC(true);
    if (!dsp.beginResampler(24000, 16000, 3)) {   // calidad 3: barata, basta
        Serial.println("[AEC] beginResampler FAILED");
        return false;
    }
    uint32_t after = ESP.getFreeHeap();
    Serial.printf("[AEC] ready: frame=%d filter=%d heap used=%u free=%u\n",
                  AEC_FRAME, AEC_FILTER, (unsigned)(before - after), (unsigned)after);
    ready = true;
    aecResetReference();
    aecResetDetector();
    return true;
#endif
}

void aecFeedReference(const uint8_t *pcm24k, size_t bytes) {
    if (!ready || bytes < 2) return;
    int n24 = bytes / 2;
    if (n24 > 1024) n24 = 1024;
    int n16 = dsp.resample((int16_t *)pcm24k, n24, refTmp, 1024);
    portENTER_CRITICAL(&refMux);
    for (int i = 0; i < n16; i++) {
        refRing[refHead] = refTmp[i];
        refHead = (refHead + 1) % REF_RING;
        if (refHead == refTail) refTail = (refTail + 1) % REF_RING;  // overrun: pisar lo viejo
    }
    portEXIT_CRITICAL(&refMux);
}

void aecResetReference() {
    portENTER_CRITICAL(&refMux);
    refHead = refTail = 0;
    portEXIT_CRITICAL(&refMux);
    memset(refRing, 0, sizeof(refRing));
}

void aecResetDetector() {
    voiceRun = 0;
    warmup = 0;
    floorDb = -60.0f;
}

bool aecProcessMicFrame(const int16_t *mic, int16_t *out) {
    if (!ready) { memcpy(out, mic, AEC_FRAME * 2); return false; }

    // Sacar AEC_FRAME muestras de referencia (ceros si no hay: el AEC lo tolera).
    int16_t ref[AEC_FRAME];
    portENTER_CRITICAL(&refMux);
    for (int i = 0; i < AEC_FRAME; i++) {
        if (refTail != refHead) {
            ref[i] = refRing[refTail];
            refTail = (refTail + 1) % REF_RING;
        } else {
            ref[i] = 0;
        }
    }
    portEXIT_CRITICAL(&refMux);

    dsp.processAEC((int16_t *)mic, ref, out);

    // Detector sobre el residuo.
    float db = rmsDb(out, AEC_FRAME);
    if (warmup < WARMUP_FRAMES) {          // dejar converger el filtro
        warmup++;
        floorDb = 0.9f * floorDb + 0.1f * db;
        return false;
    }
    if (db > floorDb + DETECT_MARGIN_DB) {
        voiceRun++;
    } else {
        voiceRun = 0;
        floorDb = 0.98f * floorDb + 0.02f * db;   // el piso sigue al residuo, lento
    }
    if (voiceRun >= DETECT_FRAMES) {
        Serial.printf("[AEC] voice over playback: %.1f dB (floor %.1f) -> BARGE\n", db, floorDb);
        voiceRun = 0;
        return true;
    }
    return false;
}

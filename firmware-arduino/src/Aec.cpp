#include "Aec.h"
#include <Arduino.h>
#include <ESP32-SpeexDSP.h>
#include "speex/speex_preprocess.h"
#include <math.h>
#include <string.h>

// --- Estado ---------------------------------------------------------------
static ESP32SpeexDSP dsp;
static SpeexPreprocessState *prep = nullptr;  // supresor de eco residual
static bool ready = false;        // AEC + resampler inicializados
static bool refReady = false;     // resampler listo (el ring se puede llenar)

// Ring de referencia a 16 kHz. Tiene que cubrir el retardo maximo que
// buscamos (LAG_MAX) mas un frame. 4096 muestras = 256 ms.
constexpr int REF_RING = 4096;
static int16_t refRing[REF_RING];
// Contador monotono de muestras escritas. Con el, "la referencia de hace D
// muestras" es simplemente la ventana que termina en (refWritten - D): no
// hacen falta punteros head/tail ni FIFO.
static volatile uint32_t refWritten = 0;

// Salida del resampler: el copier escribe bloques de 1024 B = 512 muestras a
// 24 kHz -> 342 a 16 kHz. 512 sobra.
static int16_t refTmp[512];

// --- Medicion del retardo mic<->referencia --------------------------------
// Rango de busqueda: 0 a 200 ms. El DMA de salida (6 x 512 B a 24 kHz) mete
// hasta ~64 ms y el de entrada otro tanto, asi que el pico deberia caer entre
// 60 y 140 ms; el rango es holgado a proposito.
constexpr int LAG_MIN   = 0;
constexpr int LAG_MAX   = 3200;   // 200 ms a 16 kHz
constexpr int LAG_STEP  = 16;     // 1 ms de resolucion
constexpr int LAG_BINS  = (LAG_MAX - LAG_MIN) / LAG_STEP;
constexpr int MEASURE_FRAMES = 120;  // ~1 s de audio para promediar

// Retardo por defecto: 1440 muestras = 90 ms, MEDIDO en hardware el 2026-09-14
// ([AEC] retardo medido: 1440 muestras, pico 16.53 vs media 5.15). Es una
// propiedad del pipeline (DMA de salida + acustica + DMA de entrada), asi que
// no cambia entre arranques. Se usa de entrada y se cancela desde el primer
// frame; la correlacion queda solo como REFINAMIENTO en segundo plano.
//
// Por que no se bloquea esperando la medicion (error del intento anterior):
// la ventana de correlacion es de 1 frame = 8 ms, demasiado corta para
// discriminar lags en voz (el habla se parece a si misma en lags cortos), asi
// que el pico nunca destacaba lo suficiente y el AEC no arrancaba nunca.
constexpr int AEC_DEFAULT_DELAY = 1440;
constexpr float REFINE_RATIO    = 1.25f;  // umbral para ACEPTAR un refinamiento
constexpr int   REFINE_EVERY    = 4;      // correlacionar 1 de cada 4 frames

static float lagScore[LAG_BINS];
static int   measureFrames = 0;
static int   refineTick = 0;
static bool  refineDone = false;
static int   lockedDelay = AEC_DEFAULT_DELAY;   // se cancela desde el arranque

// --- Detector de voz sostenida sobre el residuo ---------------------------
// Umbral ABSOLUTO: por debajo de esto no se considera voz del usuario, sin
// importar cuan bajo este el piso. Calibrado con la medicion del 2026-09-16:
//   picos de residuo (falsos positivos) : -35.7 y -41.6 dB  -> deben quedar afuera
//   voz del usuario                     : -34.8 med, -29.4 p90 -> debe pasar
constexpr float DETECT_ABS_DB    = -32.0f;
constexpr float DETECT_MARGIN_DB = 10.0f;
constexpr int   DETECT_FRAMES    = 40;   // 40 x 8 ms = 320 ms de voz sostenida
constexpr int   SETTLE_FRAMES    = 60;   // ~0.5 s para que el filtro converja
static float floorDb = -60.0f;
static int   voiceRun = 0;
static float residualPeakDb = -120.0f;   // medicion del criterio de exito
static float residualSum = 0;
static int   residualFrames = 0;
static int   settle = 0;

static float rmsDb(const int16_t *x, int n) {
    float acc = 0;                      // float: el S3 emula doubles por software
    for (int i = 0; i < n; i++) acc += (float)x[i] * x[i];
    float rms = sqrtf(acc / n) / 32768.0f;
    return 20.0f * log10f(rms + 1e-9f);
}

// Copia las `n` muestras de referencia que terminan en `endPos` (exclusivo).
static void readRefEndingAt(uint32_t endPos, int16_t *out, int n) {
    uint32_t start = endPos - (uint32_t)n;
    for (int i = 0; i < n; i++) out[i] = refRing[(start + i) % REF_RING];
}

// --- API ------------------------------------------------------------------
bool aecBegin() {
#if !AEC_ENABLED
    Serial.println("[AEC] deshabilitado (AEC_ENABLED=0): mic sin cancelar");
    return false;
#else
    uint32_t h0 = ESP.getFreeHeap();

    if (!dsp.beginAEC(AEC_FRAME, AEC_FILTER, 16000)) {
        Serial.println("[AEC] beginAEC FAILED");
        return false;
    }
    uint32_t h1 = ESP.getFreeHeap();

    if (!dsp.beginResampler(24000, 16000, 0)) {   // calidad 0: la mas barata
        Serial.println("[AEC] beginResampler FAILED");
        return false;
    }
    uint32_t h2 = ESP.getFreeHeap();

    // 2da etapa: supresor de eco RESIDUAL. Es la mitad del diseno de speex que
    // faltaba: el cancelador lineal deja pasar el eco no lineal (distorsion del
    // clase D, vibracion), y esto lo suprime espectralmente usando el estado
    // del propio AEC como referencia de cuanto residuo esperar.
    prep = speex_preprocess_state_init(AEC_FRAME, 16000);
    if (prep) {
        speex_preprocess_ctl(prep, SPEEX_PREPROCESS_SET_ECHO_STATE, dsp.getEchoState());
        int on = 1;
        speex_preprocess_ctl(prep, SPEEX_PREPROCESS_SET_DENOISE, &on);
        // supp: cuanto suprimir cuando SOLO hay eco (agresivo, funciona bien).
        // suppActive: cuanto suprimir cuando detecta DOBLE-HABLA. Aca iba -30 y
        // era el error: borraba la voz del usuario junto con el eco, por eso el
        // barge no disparaba nunca. -15 es el default de speex, pensado
        // justamente para preservar la voz cercana durante el doble-habla.
        int supp = -50, suppActive = -15;
        speex_preprocess_ctl(prep, SPEEX_PREPROCESS_SET_ECHO_SUPPRESS, &supp);
        speex_preprocess_ctl(prep, SPEEX_PREPROCESS_SET_ECHO_SUPPRESS_ACTIVE, &suppActive);
    } else {
        Serial.println("[AEC] preprocess_state_init FALLO: sin supresor de residuo");
    }
    uint32_t h3 = ESP.getFreeHeap();

    Serial.printf("[AEC] frame=%d filter=%d | aec=%u B resampler=%u B "
                  "supresor=%u B total=%u B | heap libre %u -> %u\n",
                  AEC_FRAME, AEC_FILTER,
                  (unsigned)(h0 - h1), (unsigned)(h1 - h2), (unsigned)(h2 - h3),
                  (unsigned)(h0 - h3), (unsigned)h0, (unsigned)h3);

    // Guardarrail: si lo que queda no alcanza para el scratch de 60 KB que
    // Opus va a pedir en el primer decode, se libera el supresor (lo mas caro
    // y lo menos esencial) y se reevalua. Preferimos un asistente sano sin
    // barge por voz antes que un crash-loop.
    if (h3 < AEC_MIN_FREE_AFTER && prep) {
        speex_preprocess_state_destroy(prep);
        prep = nullptr;
        uint32_t h4 = ESP.getFreeHeap();
        Serial.printf("[AEC] heap tras init (%u) < %u: supresor LIBERADO, "
                      "quedan %u B (solo cancelador lineal)\n",
                      (unsigned)h3, (unsigned)AEC_MIN_FREE_AFTER, (unsigned)h4);
        h3 = h4;
    }
    if (h3 < AEC_MIN_FREE_AFTER) {
        Serial.printf("[AEC] heap insuficiente tras init (%u < %u): el AEC queda "
                      "inactivo para no dejar sin memoria a Opus\n",
                      (unsigned)h3, (unsigned)AEC_MIN_FREE_AFTER);
        return false;   // ready=false -> aecProcessMicFrame hace memcpy
    }

    refReady = true;
    ready = true;
    aecResetReference();
    aecResetDetector();
    return true;
#endif
}

void aecFeedReference(const uint8_t *pcm24k, size_t bytes) {
    if (!refReady || bytes < 2) return;
    int n24 = bytes / 2;
    if (n24 > 768) n24 = 768;                 // cabe en refTmp tras 3:2
    int n16 = dsp.resample((int16_t *)pcm24k, n24, refTmp, 512);
    if (n16 <= 0) return;
    // Un solo productor (audioStreamTask) y un solo consumidor (micTask): se
    // escriben los datos y DESPUES se publica el indice. No hace falta seccion
    // critica, y meterla aca era un error: deshabilitar interrupciones en el
    // camino del parlante cortaba el audio (se oia como carraspeo). Tampoco se
    // usa modulo por muestra: se envuelve el indice a mano.
    uint32_t w = refWritten;
    int idx = (int)(w % REF_RING);
    for (int i = 0; i < n16; i++) {
        refRing[idx] = refTmp[i];
        if (++idx >= REF_RING) idx = 0;
    }
    refWritten = w + n16;       // volatile: publica el avance al consumidor
}

void aecResetReference() {
    refWritten = 0;
    memset(refRing, 0, sizeof(refRing));
}

void aecResetDetector() {
    residualPeakDb = -120.0f;
    residualSum = 0;
    residualFrames = 0;
    memset(lagScore, 0, sizeof(lagScore));
    measureFrames = 0;
    refineTick = 0;
    refineDone = false;
    lockedDelay = AEC_DEFAULT_DELAY;
    voiceRun = 0;
    settle = 0;
    floorDb = -60.0f;
}

bool aecProcessMicFrame(const int16_t *mic, int16_t *out) {
    if (!ready) { memcpy(out, mic, AEC_FRAME * 2); return false; }

    uint32_t w = refWritten;    // uint32 volatil: lectura atomica, sin bloquear

    // --- Refinamiento del retardo (NO bloquea la cancelacion) --------------
    // Corre en paralelo, 1 de cada REFINE_EVERY frames, con floats (el S3 tiene
    // FPU de simple precision; los doubles son emulados por software y caros).
    // Si encuentra un pico claro distinto del valor actual, lo ajusta. Si no,
    // se sigue con el retardo por defecto, que ya es el medido en hardware.
    if (!refineDone && w >= (uint32_t)(LAG_MAX + AEC_FRAME) &&
        (++refineTick % REFINE_EVERY) == 0) {
        float micEnergy = 0;
        for (int i = 0; i < AEC_FRAME; i++) micEnergy += (float)mic[i] * mic[i];
        if (micEnergy > 1e4f) {
            int16_t r[AEC_FRAME];
            for (int b = 0; b < LAG_BINS; b++) {
                readRefEndingAt(w - (uint32_t)(LAG_MIN + b * LAG_STEP), r, AEC_FRAME);
                float dot = 0, refEnergy = 0;
                for (int i = 0; i < AEC_FRAME; i++) {
                    dot += (float)mic[i] * r[i];
                    refEnergy += (float)r[i] * r[i];
                }
                if (refEnergy > 1e3f)
                    lagScore[b] += fabsf(dot) / sqrtf(refEnergy * micEnergy);
            }
            if (++measureFrames >= MEASURE_FRAMES) {
                int best = 0; float bestVal = 0, sum = 0;
                for (int b = 0; b < LAG_BINS; b++) {
                    sum += lagScore[b];
                    if (lagScore[b] > bestVal) { bestVal = lagScore[b]; best = b; }
                }
                float mean = sum / LAG_BINS;
                int cand = LAG_MIN + best * LAG_STEP;
                if (bestVal > mean * REFINE_RATIO) {
                    Serial.printf("[AEC] retardo refinado: %d -> %d muestras (%.0f ms) "
                                  "pico=%.2f media=%.2f\n",
                                  lockedDelay, cand, cand * 1000.0f / 16000.0f,
                                  bestVal, mean);
                    lockedDelay = cand;
                } else {
                    Serial.printf("[AEC] refinamiento sin pico claro (%.2f vs %.2f): "
                                  "se mantiene el retardo por defecto %d (%.0f ms)\n",
                                  bestVal, mean, lockedDelay,
                                  lockedDelay * 1000.0f / 16000.0f);
                }
                refineDone = true;
                Serial.printf("[AEC] stack libre en micTask: %u B\n",
                              (unsigned)(uxTaskGetStackHighWaterMark(NULL) * sizeof(StackType_t)));
            }
        }
    }

    // --- Fase 2: cancelar y detectar --------------------------------------
    int16_t ref[AEC_FRAME];
    readRefEndingAt(w - (uint32_t)lockedDelay, ref, AEC_FRAME);
    dsp.processAEC((int16_t *)mic, ref, out);
    if (prep) speex_preprocess_run(prep, out);   // 2da etapa: suprime el residuo

    float db = rmsDb(out, AEC_FRAME);

    // --- Medicion del criterio de exito ---------------------------------
    // Pico del residuo mientras Deb habla. Con solo el cancelador lineal daba
    // -27 dB (nivel de los picos del eco) y disparaba falsos positivos. El
    // objetivo del supresor es llevarlo a <= -45 dB, bien por debajo de la voz
    // del usuario (-31 a -35 dB), para que haya separacion real.
    if (db > residualPeakDb) residualPeakDb = db;
    residualSum += db;
    if (++residualFrames >= 125) {              // ~0.5 s
        Serial.printf("[AEC] residuo: pico=%.1f dB medio=%.1f dB (objetivo pico <= -45)\n",
                      residualPeakDb, residualSum / residualFrames);
        residualPeakDb = -120.0f;
        residualSum = 0;
        residualFrames = 0;
    }
    if (settle < SETTLE_FRAMES) {             // dejar converger el filtro
        settle++;
        floorDb = 0.9f * floorDb + 0.1f * db;
        return false;
    }
    // Se exigen LAS DOS cosas: destacar sobre el piso (evita disparar con ruido
    // estacionario) y superar el umbral absoluto (evita los picos del residuo).
    if (db > floorDb + DETECT_MARGIN_DB && db > DETECT_ABS_DB) {
        voiceRun++;
    } else {
        voiceRun = 0;
        floorDb = 0.98f * floorDb + 0.02f * db;   // el piso sigue al residuo
    }
    if (voiceRun >= DETECT_FRAMES) {
        Serial.printf("[AEC] voz sobre la respuesta: %.1f dB (piso %.1f, abs %.1f) -> BARGE\n",
                      db, floorDb, DETECT_ABS_DB);
        voiceRun = 0;
        return true;
    }
    return false;
}

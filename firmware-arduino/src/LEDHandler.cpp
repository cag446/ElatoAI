#include "LEDHandler.h"
#include <Adafruit_NeoPixel.h>

// Single on-board WS2812 of the ESP32-S3-Zero (GPIO21).
// NEO_GRB + NEO_KHZ800 is the standard timing/order for the WS2812B.
static Adafruit_NeoPixel rgbLed(NUM_LEDS, RGB_LED_PIN, NEO_GRB + NEO_KHZ800);

int brightness = 0;
int fadeAmount = 5;
static unsigned long lastToggle = 0;
static bool ledState = false;

// Core helper: push an RGB color to the single addressable LED.
// Values are 0-255 per channel; master brightness is capped in setupRGBLED().
void setLEDColor(uint8_t r, uint8_t g, uint8_t b)
{
    rgbLed.setPixelColor(0, rgbLed.Color(r, g, b));
    rgbLed.show();
}

enum class StaticColor : uint8_t
{
    RED,
    GREEN,
    BLUE,
    YELLOW,
    MAGENTA,
    CYAN,
};

void setStaticColor(StaticColor color)
{
    switch (color)
    {
    case StaticColor::RED:
        setLEDColor(255, 0, 0);
        break;
    case StaticColor::GREEN:
        setLEDColor(0, 255, 0);
        break;
    case StaticColor::BLUE:
        setLEDColor(0, 0, 255);
        break;
    case StaticColor::YELLOW:
        setLEDColor(255, 255, 0);
        break;
    case StaticColor::MAGENTA:
        setLEDColor(255, 0, 255);
        break;
    case StaticColor::CYAN:
        setLEDColor(0, 255, 255);
        break;
    default:
        setLEDColor(255, 255, 255);
        break;
    }
}

void pulseWhite()
{
    setLEDColor(brightness, brightness, brightness);
    brightness += fadeAmount;
    if (brightness <= 0 || brightness >= 255)
    {
        fadeAmount = -fadeAmount;
    }
}

void pulseMagenta()
{
    setLEDColor(brightness, 0, brightness);
    brightness += fadeAmount;
    if (brightness <= 0 || brightness >= 255)
    {
        fadeAmount = -fadeAmount;
    }
}

void pulseYellow()
{
    setLEDColor(brightness, brightness, 0);
    brightness += fadeAmount;
    if (brightness <= 0 || brightness >= 255)
    {
        fadeAmount = -fadeAmount;
    }
}

void pulseBlue()
{
    setLEDColor(0, 0, brightness);
    brightness += fadeAmount;
    if (brightness <= 0 || brightness >= 255)
    {
        fadeAmount = -fadeAmount;
    }
}

void blinkWhite()
{
    uint8_t v = ledState ? 255 : 0;
    setLEDColor(v, v, v);
}

void blinkGreen()
{
    setLEDColor(0, ledState ? 255 : 0, 0);
}

void blinkYellow()
{
    uint8_t v = ledState ? 255 : 0;
    setLEDColor(v, v, 0);
}

void blinkBlue()
{
    setLEDColor(0, 0, ledState ? 255 : 0);
}

void turnOffLED()
{
    setLEDColor(0, 0, 0);
}

void turnOnLED()
{
    setLEDColor(255, 255, 255);
}

void turnOnBlueLED()
{
    setLEDColor(0, 0, 255);
}

void turnOnRedLEDFlash()
{
    setLEDColor(255, 0, 0);
}

void setupRGBLED()
{
    rgbLed.begin();
    rgbLed.setBrightness(LED_BRIGHTNESS); // master cap so 255-per-channel isn't blinding
    turnOffLED(); // Turn off the LED initially
}

void blinkCyanPulse()
{
    setLEDColor(0, brightness, brightness);
    brightness += fadeAmount;
    if (brightness <= 0 || brightness >= 255)
    {
        fadeAmount = -fadeAmount;
    }
}

void staticYellow()
{
    setLEDColor(255, 255, 0);
}

static const uint8_t colorSequence[][3] = {
    {0, 255, 255}, // Cyan   (R=0,   G=255, B=255)
    {255, 0, 255}, // Pink   (R=255, G=0,   B=255)
    {255, 255, 0}, // Yellow (R=255, G=255, B=0)
};

static const int NUM_COLORS = sizeof(colorSequence) / sizeof(colorSequence[0]);

void loopCyanPinkYellowPulse(unsigned long currentTime)
{
    // Duration of each color fade
    const unsigned long transitionDuration = 1000; // ms per fade

    static int colorIndex = 0;
    static uint8_t startColor[3];
    static uint8_t endColor[3];
    static unsigned long transitionStartTime = 0;
    static bool initialized = false;

    if (!initialized)
    {
        memcpy(startColor, colorSequence[colorIndex], 3);
        int nextIndex = (colorIndex + 1) % NUM_COLORS;
        memcpy(endColor, colorSequence[nextIndex], 3);

        transitionStartTime = currentTime;
        initialized = true;
    }

    unsigned long elapsed = currentTime - transitionStartTime;
    float t = (float)elapsed / (float)transitionDuration;
    if (t > 1.0f)
    {
        t = 1.0f;
    }

    uint8_t r = startColor[0] + (endColor[0] - startColor[0]) * t;
    uint8_t g = startColor[1] + (endColor[1] - startColor[1]) * t;
    uint8_t b = startColor[2] + (endColor[2] - startColor[2]) * t;

    setLEDColor(r, g, b);

    if (elapsed >= transitionDuration)
    {
        colorIndex = (colorIndex + 1) % NUM_COLORS;
        memcpy(startColor, endColor, 3);

        int nextIndex = (colorIndex + 1) % NUM_COLORS;
        memcpy(endColor, colorSequence[nextIndex], 3);

        transitionStartTime = currentTime;
    }
}

void ledTask(void *parameter)
{
    setupRGBLED();
    unsigned long currentTime = 0;
    while (1)
    {
        currentTime += 20; // Track time based on vTaskDelay

        // Toggle LED state every 200ms for blinking functions
        if (currentTime - lastToggle >= 200)
        {
            ledState = !ledState;
            lastToggle = currentTime;
        }

        switch (deviceState)
        {
        case IDLE:
            setStaticColor(StaticColor::GREEN);
            break;
        case SOFT_AP:
            setStaticColor(StaticColor::MAGENTA);
            break;
        case PROCESSING:
            setStaticColor(StaticColor::RED);
            break;
        case SPEAKING:
            setStaticColor(StaticColor::BLUE);
            break;
        case LISTENING:
            setStaticColor(StaticColor::YELLOW);
            break;
        case OTA:
            setStaticColor(StaticColor::CYAN);
            break;
        default:
            setStaticColor(StaticColor::GREEN); // LED on
            break;
        }

        // Delay for smoother LED transitions
        vTaskDelay(20 / portTICK_PERIOD_MS);
    }
}

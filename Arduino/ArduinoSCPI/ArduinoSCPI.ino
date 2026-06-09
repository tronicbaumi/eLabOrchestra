/*
 * ArduinoSCPI - SCPI interface for digital and analog pin control
 *
 * Supported commands:
 *
 *   *IDN?                        -> returns instrument identification
 *   *RST                         -> resets all pins to input/low
 *
 *   DIG:DIR <pin>,<IN|OUT>       -> set digital pin direction
 *   DIG:DIR? <pin>               -> query digital pin direction
 *   DIG:WRITE <pin>,<0|1>        -> write digital pin
 *   DIG:READ? <pin>              -> read digital pin
 *
 *   ANA:READ? <pin>              -> read analog pin (A0-A5 => 0-5)
 *   ANA:WRITE <pin>,<0-255>      -> write PWM value to pin
 *
 *   PORT:DIR <0-255>             -> set direction of pins 0-7 as bitmask
 *   PORT:DIR?                    -> query direction bitmask of pins 0-7
 *   PORT:WRITE <0-255>           -> write bitmask to pins 0-7
 *   PORT:READ?                   -> read bitmask from pins 0-7
 */

#define BAUD_RATE      115200
#define BUF_SIZE       128
#define MAX_PINS       20

static char    buf[BUF_SIZE];
static uint8_t bufLen = 0;

// Track direction of each pin (0=INPUT, 1=OUTPUT)
static uint8_t pinDir[MAX_PINS] = {0};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static void sendError(const char *msg) {
    Serial.print("ERR:");
    Serial.println(msg);
}

static int parsePin(const char *s) {
    int p = atoi(s);
    if (p < 0 || p >= MAX_PINS) return -1;
    return p;
}

// Case-insensitive prefix check
static bool startsWith(const char *str, const char *prefix) {
    while (*prefix) {
        if (toupper((uint8_t)*str++) != toupper((uint8_t)*prefix++)) return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Command handlers
// ---------------------------------------------------------------------------

static void handleIDN() {
    Serial.println("Arduino,ArduinoSCPI,1,1.0.0");
}

static void handleRST() {
    for (int i = 0; i < MAX_PINS; i++) {
        pinDir[i] = 0;
        pinMode(i, INPUT);
    }
    Serial.println("OK");
}

// DIG:DIR <pin>,<IN|OUT>
static void handleDigDir(char *args) {
    char *comma = strchr(args, ',');
    if (!comma) { sendError("SYNTAX"); return; }
    *comma = '\0';
    int pin = parsePin(args);
    if (pin < 0) { sendError("PIN"); return; }
    char *dir = comma + 1;
    if (startsWith(dir, "OUT")) {
        pinDir[pin] = 1;
        pinMode(pin, OUTPUT);
        Serial.println("OK");
    } else if (startsWith(dir, "IN")) {
        pinDir[pin] = 0;
        pinMode(pin, INPUT);
        Serial.println("OK");
    } else {
        sendError("DIR");
    }
}

// DIG:DIR? <pin>
static void handleDigDirQ(char *args) {
    int pin = parsePin(args);
    if (pin < 0) { sendError("PIN"); return; }
    Serial.println(pinDir[pin] ? "OUT" : "IN");
}

// DIG:WRITE <pin>,<0|1>
static void handleDigWrite(char *args) {
    char *comma = strchr(args, ',');
    if (!comma) { sendError("SYNTAX"); return; }
    *comma = '\0';
    int pin = parsePin(args);
    if (pin < 0) { sendError("PIN"); return; }
    int val = atoi(comma + 1);
    if (pinDir[pin] == 0) {
        pinDir[pin] = 1;
        pinMode(pin, OUTPUT);
    }
    digitalWrite(pin, val ? HIGH : LOW);
    Serial.println("OK");
}

// DIG:READ? <pin>
static void handleDigRead(char *args) {
    int pin = parsePin(args);
    if (pin < 0) { sendError("PIN"); return; }
    Serial.println(digitalRead(pin));
}

// ANA:READ? <pin>  (0=A0 .. 5=A5)
static void handleAnaRead(char *args) {
    int ch = atoi(args);
    if (ch < 0 || ch > 5) { sendError("PIN"); return; }
    Serial.println(analogRead(A0 + ch));
}

// ANA:WRITE <pin>,<0-255>   (PWM)
static void handleAnaWrite(char *args) {
    char *comma = strchr(args, ',');
    if (!comma) { sendError("SYNTAX"); return; }
    *comma = '\0';
    int pin = parsePin(args);
    if (pin < 0) { sendError("PIN"); return; }
    int val = atoi(comma + 1);
    val = constrain(val, 0, 255);
    if (pinDir[pin] == 0) {
        pinDir[pin] = 1;
        pinMode(pin, OUTPUT);
    }
    analogWrite(pin, val);
    Serial.println("OK");
}

// PORT:DIR <bitmask>   pins 0-7
static void handlePortDir(char *args) {
    uint8_t mask = (uint8_t)atoi(args);
    for (int i = 0; i < 8; i++) {
        if (mask & (1 << i)) {
            pinDir[i] = 1;
            pinMode(i, OUTPUT);
        } else {
            pinDir[i] = 0;
            pinMode(i, INPUT);
        }
    }
    Serial.println("OK");
}

// PORT:DIR?
static void handlePortDirQ() {
    uint8_t mask = 0;
    for (int i = 0; i < 8; i++) {
        if (pinDir[i]) mask |= (1 << i);
    }
    Serial.println(mask);
}

// PORT:WRITE <bitmask>
static void handlePortWrite(char *args) {
    uint8_t mask = (uint8_t)atoi(args);
    for (int i = 0; i < 8; i++) {
        if (pinDir[i] == 0) {
            pinDir[i] = 1;
            pinMode(i, OUTPUT);
        }
        digitalWrite(i, (mask & (1 << i)) ? HIGH : LOW);
    }
    Serial.println("OK");
}

// PORT:READ?
static void handlePortRead() {
    uint8_t mask = 0;
    for (int i = 0; i < 8; i++) {
        if (digitalRead(i)) mask |= (1 << i);
    }
    Serial.println(mask);
}

// ---------------------------------------------------------------------------
// Command dispatcher
// ---------------------------------------------------------------------------

static void dispatch(char *cmd) {
    // Trim trailing whitespace
    int len = strlen(cmd);
    while (len > 0 && (cmd[len-1] == '\r' || cmd[len-1] == '\n' || cmd[len-1] == ' '))
        cmd[--len] = '\0';

    if (len == 0) return;

    // Split at first space to get verb + args
    char *args = strchr(cmd, ' ');
    if (args) { *args = '\0'; args++; } else { args = cmd + len; }

    if (startsWith(cmd, "*IDN?"))            { handleIDN(); }
    else if (startsWith(cmd, "*RST"))        { handleRST(); }
    else if (startsWith(cmd, "DIG:DIR?"))    { handleDigDirQ(args); }
    else if (startsWith(cmd, "DIG:DIR"))     { handleDigDir(args); }
    else if (startsWith(cmd, "DIG:WRITE"))   { handleDigWrite(args); }
    else if (startsWith(cmd, "DIG:READ?"))   { handleDigRead(args); }
    else if (startsWith(cmd, "ANA:READ?"))   { handleAnaRead(args); }
    else if (startsWith(cmd, "ANA:WRITE"))   { handleAnaWrite(args); }
    else if (startsWith(cmd, "PORT:DIR?"))   { handlePortDirQ(); }
    else if (startsWith(cmd, "PORT:DIR"))    { handlePortDir(args); }
    else if (startsWith(cmd, "PORT:WRITE"))  { handlePortWrite(args); }
    else if (startsWith(cmd, "PORT:READ?"))  { handlePortRead(); }
    else                                     { sendError("UNKNOWN"); }
}

// ---------------------------------------------------------------------------
// Arduino entry points
// ---------------------------------------------------------------------------

void setup() {
    Serial.begin(BAUD_RATE);
    while (!Serial) {}
    Serial.println("READY");
}

void loop() {
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\n') {
            buf[bufLen] = '\0';
            dispatch(buf);
            bufLen = 0;
        } else if (c != '\r' && bufLen < BUF_SIZE - 1) {
            buf[bufLen++] = c;
        }
    }
}

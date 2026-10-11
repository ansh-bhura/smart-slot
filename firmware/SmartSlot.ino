/*
 * SMART SLOT - ESP32 firmware
 * 3-slot two-wheeler occupancy detection with overhead HC-SR04 sensors.
 *
 * Features
 *   - Slot-specific auto baseline (button / dashboard / serial 'c')
 *   - PROPOSED ALGORITHM (v3, logic in slot_logic.h): temporal state machine + adaptive baseline + pair rule.
 *   - Threshold-only method (Review-1 baseline) also runs all the time,
 *     both run all the time; a mode switch picks which one is displayed
 *   - States: FREE, OCCUPIED, CHECK, MISPARKED  (+ internal OBSERVING)
 *   - 16x2 parallel LCD (HD44780, 4-bit mode, 16-pin), optional LEDs, optional recalibrate button
 *   - ESP32 WiFi access point + self-hosted dashboard (no internet needed)
 *   - Serial CSV log of raw data + both methods' decisions (for testing)
 *
 * Libraries (Arduino IDE): "LiquidCrystal" by Arduino/Adafruit (built in / Library Manager). Nothing else to install
 * for the LCD. (The old "LiquidCrystal I2C" library is NOT needed any more.)
 * Board: "ESP32 Dev Module". WiFi / WebServer / ESPmDNS come with the ESP32 core.
 */

#include <WiFi.h>
#include <WebServer.h>
#include <ESPmDNS.h>

// =====================================================================
//  1. HARDWARE CONFIG  (edit to match your wiring)
// =====================================================================
#define MAX_SLOTS 3              // sensors described by the pin arrays below
#define NUM_SLOTS 3              // sensors actually CONNECTED right now (set to 3 when the third arrives)
#define USE_LCD   1              // 16x2 parallel LCD (16-pin, 4-bit mode) fitted

// Height profile: pick the one that matches your box
//   1 = low box (~17.5 cm lid-to-table), small models about 6-8 cm tall
//   2 = raised box (~30 cm lid-to-table), models about 12-15 cm tall
#define PROFILE 1
// Order is reversed so slot 1 = LEFT sensor on the LCD/dashboard (trig/echo pairs: 27/34, 26/33, 25/32)
const uint8_t TRIG_PIN[MAX_SLOTS] = {27, 26, 25};
const uint8_t ECHO_PIN[MAX_SLOTS] = {34, 33, 32};   // through 1k/2k dividers if 5 V sensors

// 16-pin LCD, 4-bit mode (RW -> GND, D0-D3 unconnected)
#define LCD_RS 13
#define LCD_E  14
#define LCD_D4 21
#define LCD_D5 22
#define LCD_D6 19
#define LCD_D7 15

#define BTN_PIN  -1              // optional recalibrate push-button to GND (INPUT_PULLUP). -1 = not fitted.
                                 // Recalibrate instead from the dashboard, or send 'c' on the Serial Monitor.

#define USE_LEDS 0               // set to 1 if LEDs are fitted
const uint8_t LED_G[MAX_SLOTS] = {16, 17, 18};
const uint8_t LED_R[MAX_SLOTS] = {23, 4, 5};

// Optional extras (see "Hardware upgrades" in the plan). Both default to OFF.
#define USE_SERVO  0             // SG90 boom barrier at the entrance: opens only while a slot is FREE
#define SERVO_PIN  2             // needs library "ESP32Servo" (Kevin Harrington) when enabled
#define SERVO_OPEN_DEG   90
#define SERVO_CLOSED_DEG 0
#define USE_BUZZER 0             // active buzzer: short chirp when a bike is confirmed, long beep on MISPARKED
#define BUZZER_PIN 12            // not fitted; pick a free pin before enabling (LCD uses 13,14,15,19,21,22)

#define CSV_LOG 1                // 1 = print CSV rows on Serial (115200 baud)

const char* AP_SSID = "SmartSlot";
const char* AP_PASS = "smartslot123";   // min 8 chars

#if USE_LCD
#include <LiquidCrystal.h>
#endif
#if USE_SERVO
#include <ESP32Servo.h>
Servo barrier;
#endif

#include "slot_logic.h"           // detection logic + tuning values (same file is tested on a PC)

const uint32_t PING_GAP_MS     = 50;     // gap between pings of different sensors (avoid crosstalk)
const uint32_t PING_TIMEOUT_US = 30000;  // ~5 m max, we never need that much
const float    MIN_VALID_CM    = 2.0f;
const float    MAX_VALID_CM    = 300.0f;
const uint32_t CAL_TIMEOUT_MS  = 20000;

enum Mode  : uint8_t { MODE_THRESHOLD = 0, MODE_TEMPORAL = 1 };
Mode mode = MODE_TEMPORAL;
bool calibrating = false;
uint32_t calStart = 0;

WebServer server(80);
#if USE_LCD
LiquidCrystal lcd(LCD_RS, LCD_E, LCD_D4, LCD_D5, LCD_D6, LCD_D7);
#endif

uint32_t beepUntil = 0;
uint8_t pingIdx = 0;
uint32_t lastPing = 0, lastUi = 0;

// =====================================================================
//  5. SENSOR READING
// =====================================================================
// returns distance in cm, or -1 for no echo / out of range
float pingCm(uint8_t i) {
  digitalWrite(TRIG_PIN[i], LOW);  delayMicroseconds(3);
  digitalWrite(TRIG_PIN[i], HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_PIN[i], LOW);
  unsigned long dur = pulseIn(ECHO_PIN[i], HIGH, PING_TIMEOUT_US);
  if (dur == 0) return -1;
  float cm = dur * 0.01715f;               // 343 m/s, round trip
  if (cm < MIN_VALID_CM || cm > MAX_VALID_CM) return -1;
  return cm;
}

// =====================================================================
//  6. CALIBRATION
// =====================================================================
void startCalibration() {
  calibrating = true;
  calStart = millis();
  for (int i = 0; i < NUM_SLOTS; i++) { slots[i].calCnt = 0; resetSlotLogic(slots[i], millis()); }
  Serial.println("# calibrating: keep ALL slots empty");
}

void finishCalibrationIfDone(bool force) {
  bool all = true;
  for (int i = 0; i < NUM_SLOTS; i++) if (slots[i].calCnt < CAL_N) all = false;
  if (!all && !force) return;
  for (int i = 0; i < NUM_SLOTS; i++) {
    Slot &s = slots[i];
    if (s.calCnt >= 10) {
      s.baseline0 = s.baseline = medianOf(s.calBuf, s.calCnt);
      Serial.printf("# slot %d baseline = %.1f cm (%d samples)\n", i + 1, s.baseline, s.calCnt);
    } else {
      Serial.printf("# slot %d CALIBRATION FAILED (only %d valid readings), keeping %.1f cm. Check wiring/aim.\n",
                    i + 1, s.calCnt, s.baseline);
    }
    s.rawCnt = 0; s.rawIdx = 0; s.filtValid = false; s.invalidStreak = 0;
    resetSlotLogic(s, millis());
  }
  calibrating = false;
}

// =====================================================================
//  7. DETECTION LOGIC  (all of it lives in slot_logic.h)
// =====================================================================
void updateSlot(uint8_t i, float raw) {
  Slot &s = slots[i];
  slotPush(s, raw);
  if (calibrating) {
    if (raw > 0 && s.calCnt < CAL_N) s.calBuf[s.calCnt++] = raw;
    return;
  }
  if (!s.filtValid) return;
  slotDecide(s, millis());
}

uint8_t dispCode(uint8_t i) {
  const Slot &s = slots[i];
  if (mode == MODE_THRESHOLD) return s.naiveOcc ? 1 : 0;
  return s.finalCode;
}

// temporal-method code regardless of display mode (for the CSV log)
uint8_t mis_or_temporal(uint8_t i) { return slots[i].finalCode; }

// call once per completed sweep of all sensors
void sweepDone() {
  if (calibrating) {
    finishCalibrationIfDone(millis() - calStart > CAL_TIMEOUT_MS);
    return;
  }
  uint32_t now = millis();
  boardUpdate(now);
  for (int i = 0; i < NUM_SLOTS; i++) {
    uint8_t c = dispCode(i);
    if (c != slots[i].lastCode) {
      slots[i].lastCode = c; slots[i].shownSince = now;
      if (c == 3) beepUntil = now + 400;                 // mis-parked: long beep
      else if (c == 1 && mode == MODE_TEMPORAL) beepUntil = now + 100;   // parked: chirp
    }
  }
#if CSV_LOG
  Serial.printf("%lu,", (unsigned long)now);
  for (int i = 0; i < NUM_SLOTS; i++) Serial.printf("%.1f,", slots[i].lastRaw);
  for (int i = 0; i < NUM_SLOTS; i++) Serial.printf("%d,", mis_or_temporal(i));
  for (int i = 0; i < NUM_SLOTS; i++) Serial.printf("%d%s", slots[i].naiveOcc ? 1 : 0, i < NUM_SLOTS - 1 ? "," : "\n");
#endif
}

// =====================================================================
//  8. LCD / LEDs / BUTTON / SERIAL
// =====================================================================
char lcdPrev[2][17];

void lcdWrite(uint8_t row, const char* txt) {
#if USE_LCD
  char buf[17];
  snprintf(buf, sizeof(buf), "%-16s", txt);
  if (strcmp(buf, lcdPrev[row]) != 0) {
    lcd.setCursor(0, row); lcd.print(buf);
    strcpy(lcdPrev[row], buf);
  }
#else
  (void)row; (void)txt;
#endif
}

void updateLcd() {
  if (calibrating) { lcdWrite(0, "Calibrating..."); lcdWrite(1, "Keep slots EMPTY"); return; }
  const char sym[4] = {'F', 'B', '?', 'M'};
  char l1[20]; int freeCnt = 0, n = 0;
  for (int i = 0; i < NUM_SLOTS; i++) {
    uint8_t c = dispCode(i);
    if (c == 0) freeCnt++;
    n += snprintf(l1 + n, sizeof(l1) - n, "%d%c%s", i + 1, sym[c], i < NUM_SLOTS - 1 ? " " : "");
  }
  char l2[20];
  snprintf(l2, sizeof(l2), "Free:%d/%d  Mode:%c", freeCnt, NUM_SLOTS, mode == MODE_TEMPORAL ? 'T' : 'N');
  lcdWrite(0, l1); lcdWrite(1, l2);
}

void updateLeds() {
#if USE_LEDS
  bool blink = (millis() / 250) % 2;
  for (int i = 0; i < NUM_SLOTS; i++) {
    uint8_t c = calibrating ? 255 : dispCode(i);
    bool g = false, r = false;
    if (c == 0) g = true;
    else if (c == 1) r = true;
    else if (c == 2) { g = true; r = true; }
    else if (c == 3) { g = blink; r = blink; }
    digitalWrite(LED_G[i], g); digitalWrite(LED_R[i], r);
  }
#endif
}

void updateActuators() {
#if USE_BUZZER
  digitalWrite(BUZZER_PIN, millis() < beepUntil);
#endif
#if USE_SERVO
  static int lastDeg = -1;
  if (!calibrating) {
    int freeCnt = 0;
    for (int i = 0; i < NUM_SLOTS; i++) if (dispCode(i) == 0) freeCnt++;
    int deg = freeCnt > 0 ? SERVO_OPEN_DEG : SERVO_CLOSED_DEG;
    if (deg != lastDeg) { barrier.write(deg); lastDeg = deg; }
  }
#endif
}

void handleButton() {
  if (BTN_PIN < 0) return;
  static uint32_t downSince = 0; static bool fired = false;
  if (digitalRead(BTN_PIN) == LOW) {
    if (!downSince) downSince = millis();
    if (!fired && millis() - downSince > 800) { fired = true; startCalibration(); }   // hold ~1 s
  } else { downSince = 0; fired = false; }
}

void handleSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == 'c') startCalibration();
    else if (c == 'm') { mode = (mode == MODE_TEMPORAL) ? MODE_THRESHOLD : MODE_TEMPORAL; Serial.printf("# mode=%d\n", mode); }
  }
}

// =====================================================================
//  9. WEB DASHBOARD
// =====================================================================
const char INDEX_HTML[] PROGMEM = R"rawliteral(
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Smart Slot</title>
<style>
body{font-family:system-ui,Arial,sans-serif;margin:0;background:#0f172a;color:#e2e8f0}
header{padding:12px 16px;background:#1e293b;display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between}
h1{font-size:18px;margin:0}
button{background:#334155;color:#e2e8f0;border:0;border-radius:6px;padding:8px 12px;font-size:14px}
#grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;padding:12px}
.card{background:#1e293b;border-radius:10px;padding:12px;border-top:6px solid #2e9e5b}
.big{font-size:22px;font-weight:700}
.small{font-size:12px;color:#94a3b8}
canvas{width:100%;height:90px;background:#0b1220;border-radius:6px;margin-top:6px}
#log{padding:0 12px 16px;font-size:13px;color:#94a3b8}
#banner{display:none;background:#e0a21a;color:#000;padding:8px 16px;font-weight:600}
</style></head><body>
<div id="banner">Calibrating... keep all slots EMPTY</div>
<header><h1>Smart Slot &middot; <span id="free">-</span> free</h1>
<div><button id="mode">Mode</button> <button id="cal">Recalibrate</button></div></header>
<div id="grid"></div>
<div id="log"><b>Events</b><div id="ev"></div></div>
<script>
const NAMES=['FREE','OCCUPIED','CHECK','MISPARKED'],COL=['#2e9e5b','#d64545','#e0a21a','#c0392b'];
const H=120,hist=[],prev=[];let modeNow=1,built=false;
const g=document.getElementById('grid');
function build(n){for(let i=0;i<n;i++){hist.push([]);prev.push(-1);g.insertAdjacentHTML('beforeend','<div class="card" id="c'+i+'"><div class="small">Slot '+(i+1)+'</div><div class="big" id="s'+i+'">-</div><div class="small" id="i'+i+'"></div><canvas id="g'+i+'" width="600" height="180"></canvas></div>');}built=true;}
function mmss(t){return String(Math.floor(t/60)).padStart(2,'0')+':'+String(t%60).padStart(2,'0');}
function log(m){const e=document.getElementById('ev');e.insertAdjacentHTML('afterbegin','<div>'+new Date().toLocaleTimeString()+'&nbsp; '+m+'</div>');while(e.children.length>10)e.removeChild(e.lastChild);}
function draw(i,j){
 const cv=document.getElementById('g'+i),x=cv.getContext('2d'),w=cv.width,h=cv.height,s=j.s[i];
 let ymax=1;j.s.forEach(q=>{ymax=Math.max(ymax,q.b)});ymax*=1.15;
 const Y=v=>h-(v/ymax)*h;
 x.clearRect(0,0,w,h);x.lineWidth=2;
 x.setLineDash([8,8]);x.strokeStyle='#64748b';x.beginPath();x.moveTo(0,Y(s.b));x.lineTo(w,Y(s.b));x.stroke();
 x.strokeStyle='#e0a21a';x.beginPath();x.moveTo(0,Y(s.b-j.trig));x.lineTo(w,Y(s.b-j.trig));x.stroke();
 x.setLineDash([]);x.strokeStyle=COL[s.st];x.lineWidth=3;x.beginPath();
 let pen=false;
 hist[i].forEach((v,k)=>{if(v===null){pen=false;return;}const px=k*(w/(H-1));if(!pen){x.moveTo(px,Y(v));pen=true;}else x.lineTo(px,Y(v));});
 x.stroke();
}
function render(j){
 if(!built)build(j.s.length);
 modeNow=j.mode;
 document.getElementById('free').textContent=j.free;
 document.getElementById('mode').textContent='Mode: '+(j.mode?'Temporal':'Threshold-only')+' (tap to switch)';
 document.getElementById('banner').style.display=j.cal?'block':'none';
 j.s.forEach((s,i)=>{
  document.getElementById('c'+i).style.borderTopColor=COL[s.st];
  document.getElementById('s'+i).textContent=NAMES[s.st]+(s.obs?' \u00b7 observing':'');
  document.getElementById('i'+i).textContent=(s.st==1?'Parked ':'For ')+mmss(s.t)+'  |  d='+(s.d<0?'no echo':s.d.toFixed(1)+' cm')+'  base='+s.b.toFixed(1);
  hist[i].push(s.d<0?null:s.d);if(hist[i].length>H)hist[i].shift();
  draw(i,j);
  if(prev[i]!==s.st){if(prev[i]!==-1)log('Slot '+(i+1)+' \u2192 '+NAMES[s.st]);prev[i]=s.st;}
 });
}
async function poll(){
 try{const r=await fetch('/api/state');render(await r.json());}catch(e){document.getElementById('free').textContent='offline';}
 setTimeout(poll,500);
}
document.getElementById('mode').onclick=()=>fetch('/api/mode?m='+(modeNow?0:1),{method:'POST'});
document.getElementById('cal').onclick=()=>{if(confirm('Make sure ALL slots are empty. Recalibrate?'))fetch('/api/calibrate',{method:'POST'});};
poll();
</script></body></html>
)rawliteral";

void handleRoot() { server.send_P(200, "text/html; charset=utf-8", INDEX_HTML); }

void handleState() {
  char buf[800];
  uint32_t now = millis();
  int freeCnt = 0;
  for (int i = 0; i < NUM_SLOTS; i++) if (dispCode(i) == 0) freeCnt++;
  int n = snprintf(buf, sizeof(buf), "{\"mode\":%d,\"cal\":%d,\"free\":%d,\"trig\":%.1f,\"s\":[",
                   (int)mode, calibrating ? 1 : 0, freeCnt, T_TRIGGER);
  for (int i = 0; i < NUM_SLOTS; i++) {
    Slot &s = slots[i];
    bool noecho = s.invalidStreak >= NOECHO_N;
    float d = (noecho || !s.filtValid) ? -1.0f : s.filt;
    bool obs = (mode == MODE_TEMPORAL) && s.state == ST_OBS;
    n += snprintf(buf + n, sizeof(buf) - n,
                  "%s{\"st\":%d,\"obs\":%d,\"d\":%.1f,\"b\":%.1f,\"dl\":%.1f,\"t\":%lu}",
                  i ? "," : "", calibrating ? 0 : dispCode(i), obs ? 1 : 0, d, s.baseline, s.delta,
                  (unsigned long)((now - s.shownSince) / 1000));
  }
  snprintf(buf + n, sizeof(buf) - n, "]}");
  server.send(200, "application/json", buf);
}

void handleCalibrate() { startCalibration(); server.send(200, "text/plain", "ok"); }
void handleMode() {
  mode = server.arg("m").toInt() ? MODE_TEMPORAL : MODE_THRESHOLD;
  server.send(200, "text/plain", "ok");
}

// =====================================================================
//  10. SETUP / LOOP
// =====================================================================
void setup() {
  Serial.begin(115200);
  delay(200);

  for (int i = 0; i < NUM_SLOTS; i++) {
    pinMode(TRIG_PIN[i], OUTPUT); digitalWrite(TRIG_PIN[i], LOW);
    pinMode(ECHO_PIN[i], INPUT);
    resetSlotAll(slots[i], millis());
#if USE_LEDS
    pinMode(LED_G[i], OUTPUT); pinMode(LED_R[i], OUTPUT);
#endif
  }
  if (BTN_PIN >= 0) pinMode(BTN_PIN, INPUT_PULLUP);
#if USE_BUZZER
  pinMode(BUZZER_PIN, OUTPUT); digitalWrite(BUZZER_PIN, LOW);
#endif
#if USE_SERVO
  barrier.attach(SERVO_PIN); barrier.write(SERVO_CLOSED_DEG);
#endif

#if USE_LCD
  lcd.begin(16, 2);                              // backlight is wired straight to 5 V (no software control)
  lcd.clear();
  memset(lcdPrev, 0, sizeof(lcdPrev));
#endif
  lcdWrite(0, "Smart Slot");
  lcdWrite(1, "Clear all slots!");
  delay(3000);                                   // time to take hands/bikes away

  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID, AP_PASS);
  if (MDNS.begin("smartslot")) Serial.println("# mDNS: http://smartslot.local");
  Serial.printf("# WiFi AP '%s'  password '%s'  ->  http://%s\n", AP_SSID, AP_PASS, WiFi.softAPIP().toString().c_str());

  server.on("/", HTTP_GET, handleRoot);
  server.on("/api/state", HTTP_GET, handleState);
  server.on("/api/calibrate", HTTP_POST, handleCalibrate);
  server.on("/api/mode", HTTP_POST, handleMode);
  server.begin();

#if CSV_LOG
  Serial.print("# ms");
  for (int k = 0; k < 3; k++) for (int i = 0; i < NUM_SLOTS; i++) Serial.printf(",%c%d", "rtn"[k], i + 1);
  Serial.println("   (r=raw cm, -1 no echo | t=temporal code 0F 1B 2CHECK 3MIS | n=threshold-only 0/1)");
#endif
  startCalibration();
}

void loop() {
  server.handleClient();
  handleButton();
  handleSerial();

  uint32_t now = millis();
  if (now - lastPing >= PING_GAP_MS) {
    lastPing = now;
    updateSlot(pingIdx, pingCm(pingIdx));
    pingIdx++;
    if (pingIdx >= NUM_SLOTS) { pingIdx = 0; sweepDone(); }
  }
  updateActuators();
  if (now - lastUi >= 250) { lastUi = now; updateLcd(); updateLeds(); }
}

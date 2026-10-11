/*
 * SMART SLOT - hardware bring-up test (run this BEFORE the main firmware)
 *
 * Prints the distance of every connected sensor ~3 times per second.
 *   Serial Monitor: 115200 baud.
 *   -1.0 = no echo (wiring problem, nothing in range, or a soft/angled surface)
 *
 * Settings below:
 *   NUM_SLOTS : how many sensors are connected right now
 *   USE_LCD   : 1 once the 16-pin parallel LCD is fitted
 *   BTN_PIN   : set to -1 if no button is fitted
 * Use the same pins as SmartSlot.ino.
 */
#define MAX_SLOTS 3
#define NUM_SLOTS 3
#define USE_LCD   1
#define BTN_PIN   -1              // no button fitted
#define LCD_RS 13
#define LCD_E  14
#define LCD_D4 21
#define LCD_D5 22
#define LCD_D6 19
#define LCD_D7 15
// Reversed so S1 = LEFT sensor (trig/echo pairs: 27/34, 26/33, 25/32)
const uint8_t TRIG_PIN[MAX_SLOTS] = {27, 26, 25};
const uint8_t ECHO_PIN[MAX_SLOTS] = {34, 33, 32};

#if USE_LCD
#include <LiquidCrystal.h>
LiquidCrystal lcd(LCD_RS, LCD_E, LCD_D4, LCD_D5, LCD_D6, LCD_D7);
#endif

float pingCm(uint8_t i) {
  digitalWrite(TRIG_PIN[i], LOW);  delayMicroseconds(3);
  digitalWrite(TRIG_PIN[i], HIGH); delayMicroseconds(10);
  digitalWrite(TRIG_PIN[i], LOW);
  unsigned long dur = pulseIn(ECHO_PIN[i], HIGH, 30000);
  return dur == 0 ? -1.0f : dur * 0.01715f;
}

void setup() {
  Serial.begin(115200);
  delay(300);
  for (int i = 0; i < NUM_SLOTS; i++) {
    pinMode(TRIG_PIN[i], OUTPUT); digitalWrite(TRIG_PIN[i], LOW);
    pinMode(ECHO_PIN[i], INPUT);
  }
  if (BTN_PIN >= 0) pinMode(BTN_PIN, INPUT_PULLUP);
#if USE_LCD
  lcd.begin(16, 2);
  lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Smart Slot test");
#endif
  Serial.println("Smart Slot hardware test. Distances in cm (-1.0 = no echo).");
}

void loop() {
  float d[MAX_SLOTS];
  for (int i = 0; i < NUM_SLOTS; i++) { d[i] = pingCm(i); delay(60); }   // one at a time, 60 ms apart
  for (int i = 0; i < NUM_SLOTS; i++) Serial.printf("S%d %6.1f   ", i + 1, d[i]);
  Serial.println("");

#if USE_LCD
  char l2[20] = "";
  int n = 0;
  for (int i = 0; i < NUM_SLOTS; i++) n += snprintf(l2 + n, sizeof(l2) - n, "%3.0f ", d[i]);
  lcd.setCursor(0, 1); lcd.print("                ");
  lcd.setCursor(0, 1); lcd.print(l2);
#endif
  if (BTN_PIN >= 0 && digitalRead(BTN_PIN) == LOW) Serial.println(">>> BUTTON PRESSED");
}

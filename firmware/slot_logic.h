/*
 * slot_logic.h - Smart Slot detection logic ("proposed algorithm", firmware v3).
 *
 * Hardware-independent on purpose: no Arduino calls in here, time is passed in. The same file is compiled
 * on a PC (host_test/) and checked against the Python evaluation replay, so what was evaluated is what runs.
 *
 *   per slot : median-of-5 filter -> delta = baseline - distance
 *              + ADAPTIVE BASELINE (slow running average, only while the slot is FREE and delta is small)
 *              -> band classifier (none / small / bike / tall)
 *              -> temporal state machine FREE / OBSERVING / OCCUPIED / CHECK (stability + dwell time)
 *   board    : neighbour rule 1 (small partial signal next to an OCC / small-CHECK slot)
 *              neighbour rule 2 "sync-MIS": two adjacent slots turn OCCUPIED within SYNC_WINDOW_MS -> one
 *              vehicle spanning both -> MISPARKED
 *
 * Needs MAX_SLOTS and NUM_SLOTS defined before inclusion. Tuned values come from the 5-fold CV (report/).
 */
#ifndef SLOT_LOGIC_H
#define SLOT_LOGIC_H

#include <stdint.h>
#include <string.h>
#include <math.h>

#ifndef USE_ADAPTIVE
#define USE_ADAPTIVE 1               // 1 = running-average baseline while FREE, 0 = fixed baseline from calibration
#endif
#ifndef USE_SYNC_MIS
#define USE_SYNC_MIS 1               // 1 = pair rule "two adjacent slots occupied together", 0 = off
#endif
#ifndef MIS_INCLUDE_OCC_NEIGHBOUR
#define MIS_INCLUDE_OCC_NEIGHBOUR 1  // rule 1 also counts (OCCUPIED + small CHECK) neighbours
#endif

// ---------------------------------------------------------------- tuning
#define MIN_VALID_CM_LOGIC 2.0f
#ifndef PROFILE
#define PROFILE 1
#endif

#if PROFILE == 1      // low box, ~17.5 cm, small models ~6-8 cm tall
const float    DEFAULT_BASELINE = 17.0f;
const float    T_TRIGGER  = 1.25f;   // delta at or below this: nothing there
const float    BIKE_MIN   = 2.2f;    // delta of a bike is at least this...
const float    BIKE_MAX   = 5.5f;    // ...and at most this (taller = person / hand, not a bike)
const float    BIKE_HYST  = 1.0f;    // hysteresis when leaving OCCUPIED
const float    SIGMA_MAX  = 1.2f;    // cm, std-dev over the last STAB_N deltas = "stable"
const float    T_NAIVE    = 2.5f;    // threshold-only method (Review-1 baseline)
#else                // raised box, ~30 cm, models ~12-15 cm tall (not re-tuned: scale from the profile-1 values)
const float    DEFAULT_BASELINE = 30.0f;
const float    T_TRIGGER  = 5.0f;
const float    BIKE_MIN   = 10.0f;
const float    BIKE_MAX   = 22.0f;
const float    BIKE_HYST  = 2.0f;
const float    SIGMA_MAX  = 1.5f;
const float    T_NAIVE    = 8.0f;
#endif

const uint32_t STABLE_MS       = 1500;  // must be stable this long to be classified
const uint32_t ABSENT_MS       = 1500;  // must be back at baseline this long to be FREE
const uint32_t UNSTABLE_MAX_MS = 8000;  // present but never stable this long -> CHECK
const uint32_t LEAVE_MS        = 800;   // OCCUPIED -> OBSERVING debounce
const uint8_t  PRESENT_N       = 2;     // consecutive samples above trigger to start observing
const uint8_t  NOECHO_N        = 6;     // consecutive no-echo pings = "no echo" signal

const float    ADAPT_ALPHA     = 0.03f; // baseline pull per sweep while FREE (time constant ~ 33 sweeps ~ 5 s)
const float    ADAPT_BAND      = 1.0f;  // only adapt while |delta| < this (a bike / hand can never be "learned")
const uint32_t SYNC_WINDOW_MS  = 2000;  // two neighbours turning OCC within this = one vehicle over both

#define STAB_N 10
#define CAL_N  40

// ---------------------------------------------------------------- data
enum State : uint8_t { ST_FREE, ST_OBS, ST_OCC, ST_CHECK };
// display codes: 0 FREE, 1 OCCUPIED, 2 CHECK, 3 MISPARKED

struct Slot {
  float rawBuf[5]; uint8_t rawIdx, rawCnt;
  float filt; bool filtValid;
  uint8_t invalidStreak;
  float lastRaw;
  float baseline;              // current baseline (adaptive); baseline0 = value from the last calibration
  float baseline0;
  float dBuf[STAB_N]; uint8_t dIdx, dCnt;
  float delta;
  uint8_t cls;                 // 0 none, 1 small, 2 bike-size, 3 too tall, 4 no echo
  State state, lastStable;
  uint32_t obsStart, candSince, absentSince, belowSince;
  uint8_t candCls, presentStreak;
  bool mis, naiveOcc;
  uint8_t lastCode; uint32_t shownSince;
  float calBuf[CAL_N]; int calCnt;
  // pair-rule bookkeeping
  uint8_t baseCode, finalCode, prevBase;
  uint32_t occStart; bool hasOccStart;
};

Slot slots[MAX_SLOTS];

// ---------------------------------------------------------------- helpers
inline float medianOf(const float* a, int n) {
  float t[CAL_N];
  if (n > CAL_N) n = CAL_N;
  for (int i = 0; i < n; i++) t[i] = a[i];
  for (int i = 1; i < n; i++) {            // insertion sort
    float k = t[i]; int j = i - 1;
    while (j >= 0 && t[j] > k) { t[j + 1] = t[j]; j--; }
    t[j + 1] = k;
  }
  return (n % 2) ? t[n / 2] : 0.5f * (t[n / 2 - 1] + t[n / 2]);
}

inline float stdDev(const float* a, int n) {
  if (n < 2) return 99.0f;
  float m = 0; for (int i = 0; i < n; i++) m += a[i]; m /= n;
  float v = 0; for (int i = 0; i < n; i++) v += (a[i] - m) * (a[i] - m);
  return sqrtf(v / n);
}

inline void resetSlotLogic(Slot &s, uint32_t now) {
  s.state = ST_FREE; s.lastStable = ST_FREE;
  s.obsStart = s.candSince = s.absentSince = s.belowSince = 0;
  s.candCls = 0; s.presentStreak = 0;
  s.mis = false; s.naiveOcc = false;
  s.dIdx = 0; s.dCnt = 0; s.delta = 0; s.cls = 0;
  s.lastCode = 0; s.shownSince = now;
  s.baseCode = s.finalCode = s.prevBase = 0; s.hasOccStart = false; s.occStart = 0;
  s.baseline = s.baseline0;            // forget whatever the baseline had drifted to
}

inline void resetSlotAll(Slot &s, uint32_t now) {
  memset(&s, 0, sizeof(Slot));
  s.baseline = s.baseline0 = DEFAULT_BASELINE;
  resetSlotLogic(s, now);
}

// ---------------------------------------------------------------- per-slot pipeline
// 1) feed one raw reading (cm, or <= 0 for no echo) into the filter
inline void slotPush(Slot &s, float raw) {
  s.lastRaw = raw;
  if (raw > 0) {
    s.invalidStreak = 0;
    s.rawBuf[s.rawIdx] = raw;
    s.rawIdx = (s.rawIdx + 1) % 5;
    if (s.rawCnt < 5) s.rawCnt++;
    s.filt = medianOf(s.rawBuf, s.rawCnt);   // median of the last 5 valid readings
    s.filtValid = true;
  } else if (s.invalidStreak < 255) {
    s.invalidStreak++;
  }
}

// 2) decision step (not called while calibrating, and only once filt is valid)
inline void slotDecide(Slot &s, uint32_t now) {
  bool noecho = s.invalidStreak >= NOECHO_N;
  if (!noecho) {
    s.delta = s.baseline - s.filt;
#if USE_ADAPTIVE
    // slow baseline tracking: only while the slot is idle (FREE, nothing detected on the previous sweep)
    // and the reading is close to the baseline. Frozen in every other state.
    if (s.state == ST_FREE && s.cls == 0 && fabsf(s.delta) < ADAPT_BAND) {
      s.baseline -= ADAPT_ALPHA * s.delta;
      s.delta = s.baseline - s.filt;
    }
#endif
    s.dBuf[s.dIdx] = s.delta;
    s.dIdx = (s.dIdx + 1) % STAB_N;
    if (s.dCnt < STAB_N) s.dCnt++;
  }

  // ---- Method A: threshold-only (Review-1 baseline) ----
  s.naiveOcc = (!noecho && s.delta > T_NAIVE);

  // ---- Method B: temporal state machine ----
  uint8_t cls;
  if (noecho)                    cls = 4;
  else if (s.delta <= T_TRIGGER) cls = 0;
  else if (s.delta <  BIKE_MIN)  cls = 1;
  else if (s.delta <= BIKE_MAX)  cls = 2;
  else                           cls = 3;
  s.cls = cls;

  bool stable = noecho || (s.dCnt >= STAB_N && stdDev(s.dBuf, s.dCnt) < SIGMA_MAX);

  if (cls == 0) {
    s.presentStreak = 0;
    if (!s.absentSince) s.absentSince = now ? now : 1;
  } else {
    if (s.presentStreak < 255) s.presentStreak++;
    s.absentSince = 0;
  }

  if (cls != 0 && stable) {
    if (s.candCls != cls || !s.candSince) { s.candCls = cls; s.candSince = now ? now : 1; }
  } else {
    s.candSince = 0; s.candCls = 0;
  }
  bool candDone   = s.candSince && (now - s.candSince >= STABLE_MS);
  bool absentLong = s.absentSince && (now - s.absentSince >= ABSENT_MS);

  switch (s.state) {
    case ST_FREE:
      if (s.presentStreak >= PRESENT_N) {
        s.lastStable = ST_FREE; s.state = ST_OBS; s.obsStart = now;
      }
      break;

    case ST_OBS:
      if (cls == 0 && absentLong)               s.state = ST_FREE;      // transient: person / hand passed
      else if (candDone)                        s.state = (s.candCls == 2) ? ST_OCC : ST_CHECK;
      else if (cls != 0 && now - s.obsStart >= UNSTABLE_MAX_MS) s.state = ST_CHECK;
      break;

    case ST_OCC:
      if (!noecho && s.delta < BIKE_MIN - BIKE_HYST) {
        if (!s.belowSince) s.belowSince = now ? now : 1;
        if (now - s.belowSince >= LEAVE_MS) {
          s.lastStable = ST_OCC; s.state = ST_OBS; s.obsStart = now; s.belowSince = 0;
        }
      } else s.belowSince = 0;
      break;

    case ST_CHECK:
      if (cls == 0 && absentLong)               s.state = ST_FREE;
      else if (candDone && s.candCls == 2)      s.state = ST_OCC;
      break;
  }
}

// temporal code of one slot (0..2); OBSERVING shows the previous stable state
inline uint8_t temporalCode(const Slot &s) {
  State st = (s.state == ST_OBS) ? s.lastStable : s.state;
  if (st == ST_OCC) return 1;
  if (st == ST_CHECK) return 2;
  return 0;
}

// ---------------------------------------------------------------- board-level rules
// call once per completed sweep of all sensors (after every slotDecide of that sweep)
inline void boardUpdate(uint32_t now) {
  // rule 1: small partial signal next to an occupied / small-CHECK neighbour
  for (int i = 0; i < NUM_SLOTS; i++) slots[i].mis = false;
  for (int i = 0; i < NUM_SLOTS - 1; i++) {
    Slot &a = slots[i], &b = slots[i + 1];
    bool aSmall = (a.state == ST_CHECK && a.cls == 1);
    bool bSmall = (b.state == ST_CHECK && b.cls == 1);
    bool aOcc = (a.state == ST_OCC), bOcc = (b.state == ST_OCC);
    bool flag = (aSmall && bSmall);
#if MIS_INCLUDE_OCC_NEIGHBOUR
    flag = flag || (aSmall && bOcc) || (aOcc && bSmall);
#endif
    if (flag) a.mis = b.mis = true;
  }
  for (int i = 0; i < NUM_SLOTS; i++) {
    slots[i].baseCode = slots[i].mis ? 3 : temporalCode(slots[i]);
    slots[i].finalCode = slots[i].baseCode;
  }
#if USE_SYNC_MIS
  // rule 2 (sync-MIS): remember when each slot's current OCC episode began; two neighbours that are both
  // OCC and began within SYNC_WINDOW_MS of each other are one vehicle lying across both slots.
  for (int i = 0; i < NUM_SLOTS; i++) {
    Slot &s = slots[i];
    if (s.baseCode == 1 && s.prevBase != 1) { s.occStart = now; s.hasOccStart = true; }
    s.prevBase = s.baseCode;
  }
  for (int i = 0; i < NUM_SLOTS - 1; i++) {
    Slot &a = slots[i], &b = slots[i + 1];
    if (a.baseCode == 1 && b.baseCode == 1 && a.hasOccStart && b.hasOccStart) {
      uint32_t gap = (a.occStart > b.occStart) ? a.occStart - b.occStart : b.occStart - a.occStart;
      if (gap <= SYNC_WINDOW_MS) { a.finalCode = 3; b.finalCode = 3; }
    }
  }
#endif
}

#endif

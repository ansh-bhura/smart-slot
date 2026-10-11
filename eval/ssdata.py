"""
ssdata.py - load recorded trials, build ground truth, score detector output.

Codes used everywhere: 0 FREE, 1 OCCUPIED, 2 CHECK, 3 MISPARKED.

Ground truth (per slot, per sweep) comes from the trial's scenario and the two Enter-key marks:
  FREE scenarios (idle, swipe, swipe2, pass_slow): FREE for the whole trial in every slot.
  Object scenarios (bike, bike_offset -> OCC;  hand_hold, low_obj, tall_obj -> CHECK):
      tested slot is FREE before the object is placed, the object's class during the hold window,
      FREE again after it is removed; short grace periods around the two marks are "don't care" (-1)
      because the human press and the algorithm's confirmation delay make the exact instant ambiguous.
  Non-tested slots are FREE controls (they should never react).
  clutter / mis are reported separately (tiny n / needs the pair rule).
"""
import os, json, glob
import numpy as np

FREE, OCC, CHECK, MIS = 0, 1, 2, 3
CODE_NAME = {0: "FREE", 1: "OCC", 2: "CHECK", 3: "MIS"}

FREE_SCEN = ("idle", "swipe", "swipe2", "pass_slow")
OBJ_TRUTH = {"bike": OCC, "bike_offset": OCC, "hand_hold": CHECK, "low_obj": CHECK, "tall_obj": CHECK}
CORE = FREE_SCEN + tuple(OBJ_TRUTH)          # scenarios used in the headline numbers
SPECIAL = ("clutter", "mis", "two_bikes")

G_ON_BEFORE = 500      # ms before mark 1 treated as don't-care
G_ON = 4000            # ms after mark 1 before the object's class must be shown
G_OFF_BEFORE = 500     # ms before mark 2
G_OFF = 3000           # ms after mark 2 before FREE must be shown
TAIL_MS = 2000         # "final state" = majority over the last 2 s of the hold window


class Trial:
    pass


def load_trials(folder):
    out = []
    for jf in sorted(glob.glob(os.path.join(folder, "*.json"))):
        m = json.load(open(jf))
        lines = [l.strip() for l in open(jf[:-5] + ".csv") if l.strip()]
        rows = np.array([[float(x) for x in l.split(",")] for l in lines[2:]])
        k = (rows.shape[1] - 1) // 3
        t = Trial()
        t.name = m["scenario"]
        t.idx = int(os.path.basename(jf)[:-5].rsplit("_", 1)[1])
        t.id = f"{t.name}_{t.idx}"
        t.slot = int(m["slot"])                      # 1-based, 0 = whole board
        t.session = m["session"]
        t.first_after_cal = bool(m.get("first_after_cal", False))
        t.base = np.array(m["baselines"], float)
        t.marks = m["marks_ms"]
        t.ms = rows[:, 0]
        t.raw = rows[:, 1:1 + k]
        t.t_log = rows[:, 1 + k:1 + 2 * k].astype(int)   # on-board temporal decision (for validation)
        t.n_log = rows[:, 1 + 2 * k:1 + 3 * k].astype(int)
        t.k = k
        t.truth = make_truth(t)
        t.raw_truth = t.truth.copy()
        apply_quality_mask(t)
        t.view = make_view(t)
        out.append(t)
    return out


PERSIST_S = 3.0        # a "FREE" slot showing presence this long (cm > 1.5) was not empty -> protocol violation
PERSIST_PAD_S = 1.0    # extra don't-care margin around such a stretch


def apply_quality_mask(t):
    """Offline (non-causal) data cleaning, independent of every algorithm under test:
    wherever the protocol says a slot is FREE but the sensor shows continuous presence for >= PERSIST_S,
    something (a hand, the tall prop parked in a neighbouring slot, ...) was physically there, so the FREE
    label is wrong. Those stretches (+/- PERSIST_PAD_S) become don't-care (-1).  Short excursions
    (swipes, slow passes) are NOT masked: they are exactly what must be classed as FREE."""
    n, k = t.raw.shape
    dt = float(np.median(np.diff(t.ms)))
    need = int(PERSIST_S / (dt / 1000.0)); pad = int(PERSIST_PAD_S / (dt / 1000.0))
    t.masked = np.zeros((n, k), dtype=bool)
    for i in range(k):
        buf, d = [], np.zeros(n)
        for j in range(n):                      # same causal median-5 as the firmware, used only for cleaning
            if t.raw[j, i] > 0: buf = (buf + [t.raw[j, i]])[-5:]
            d[j] = t.base[i] - float(np.median(buf)) if buf else 0.0
        hi = d > 1.5
        j = 0
        while j < n:
            if hi[j]:
                e = j
                while e + 1 < n and hi[e + 1]: e += 1
                if e - j + 1 >= need:
                    lo, up = max(0, j - pad), min(n, e + 1 + pad)
                    seg = t.truth[lo:up, i] == 0
                    t.truth[lo:up, i][seg] = -1
                    t.masked[lo:up, i] = True
                j = e + 1
            else:
                j += 1


MIN_UNIT_SWEEPS = 12   # a FREE unit needs at least this many unmasked sweeps (~1.8 s) to be scored

PRE = 40   # sweeps of synthetic "empty slot" history placed before each trial (the real system is already running)

def make_view(t):
    """Same trial with PRE empty-slot sweeps in front, so filters / stability windows start full like on the board."""
    v = Trial()
    dt = float(np.median(np.diff(t.ms)))
    v.ms = np.concatenate([t.ms[0] - dt * np.arange(PRE, 0, -1), t.ms])
    v.raw = np.vstack([np.tile(t.base, (PRE, 1)), t.raw])
    v.base, v.k, v.id, v.pre = t.base, t.k, t.id, PRE
    return v


def run_method(method, t):
    """Run a streaming method over the pre-rolled view and return codes aligned with the trial rows."""
    return method.run(t.view)[PRE:]


def make_truth(t):
    n, k = t.raw.shape
    truth = np.zeros((n, k), dtype=int)
    if t.name in FREE_SCEN:
        return truth
    if t.name in OBJ_TRUTH:
        lab = OBJ_TRUTH[t.name]
        i = t.slot - 1
        a, b = t.marks
        col = np.zeros(n, dtype=int)
        col[(t.ms >= a - G_ON_BEFORE) & (t.ms < a + G_ON)] = -1
        col[(t.ms >= a + G_ON) & (t.ms < b - G_OFF_BEFORE)] = lab
        col[(t.ms >= b - G_OFF_BEFORE) & (t.ms < b + G_OFF)] = -1
        truth[:, i] = col
        return truth
    if t.name == "clutter":                         # CHECK or MIS both acceptable -> sentinel handled in scoring
        i = t.slot - 1
        a, b = t.marks
        col = np.zeros(n, dtype=int)
        col[(t.ms >= a - G_ON_BEFORE) & (t.ms < a + G_ON)] = -1
        col[(t.ms >= a + G_ON) & (t.ms < b - G_OFF_BEFORE)] = -2
        col[(t.ms >= b - G_OFF_BEFORE) & (t.ms < b + G_OFF)] = -1
        truth[:, i] = col
        return truth
    if t.name in ("mis", "two_bikes"):
        a, b = t.marks
        lab = MIS if t.name == "mis" else OCC           # two_bikes: second bike arrives ~3-4 s later -> longer grace
        g_on = G_ON if t.name == "mis" else 9000
        for i in (t.slot - 1, t.slot):
            col = np.zeros(n, dtype=int)
            col[(t.ms >= a - G_ON_BEFORE) & (t.ms < a + g_on)] = -1
            col[(t.ms >= a + g_on) & (t.ms < b - G_OFF_BEFORE)] = lab
            col[(t.ms >= b - G_OFF_BEFORE) & (t.ms < b + G_OFF)] = -1
            truth[:, i] = col
        return truth
    raise ValueError(t.name)


def tested_slots(t):
    """0-based slots that carry the scenario's label (the rest are FREE controls)."""
    if t.name == "idle":
        return list(range(t.k))
    if t.name in ("mis", "two_bikes"):
        return [t.slot - 1, t.slot]
    return [t.slot - 1]


def _majority(x):
    vals, c = np.unique(x, return_counts=True)
    return vals[np.argmax(c)]


def score_trial(t, codes):
    """codes: (n, k) int array of algorithm output. Returns a list of per-unit result dicts.
    A unit is one (trial, slot).  kind: 'free' (should stay FREE), 'obj' (should reach a class
    and then return to FREE), 'ctrl' (non-tested slot, should stay FREE)."""
    n, k = codes.shape
    res = []
    warm = t.ms >= t.ms[0] + 1500                     # ignore first 1.5 s while filters fill
    for i in range(k):
        c = codes[:, i]
        tr = t.truth[:, i]
        r = dict(trial=t.id, scenario=t.name, slot=i + 1, first_after_cal=t.first_after_cal)
        is_tested = i in tested_slots(t)
        if t.name in FREE_SCEN or not is_tested:
            r["kind"] = "free" if t.name in FREE_SCEN and is_tested else "ctrl"
            cw = c[warm & (tr == 0)]
            if len(cw) < MIN_UNIT_SWEEPS:          # too little clean data in this slot-trial
                continue
            r["n_sweeps"] = int(len(cw))
            r["correct"] = bool(np.all(cw == FREE))
            r["false_occ"] = bool(np.any(cw == OCC))
            r["false_alert"] = bool(np.any(cw != FREE))
            r["frac_nonfree"] = float(np.mean(cw != FREE))
            res.append(r)
            continue
        # object / special scenario, tested slot
        r["kind"] = "obj"
        a, b = t.marks
        tail = (t.ms >= b - G_OFF_BEFORE - TAIL_MS) & (t.ms < b - G_OFF_BEFORE)
        truth_label = int(np.max(tr))                 # >=0 label, or -2 for clutter
        end = _majority(c[tail]) if tail.any() else -9
        if truth_label == -2:
            r["correct"] = bool(end in (CHECK, MIS))
            r["truth"] = "2|3"
        else:
            r["correct"] = bool(end == truth_label)
            r["truth"] = CODE_NAME[truth_label]
        r["end_code"] = int(end)
        # settling latency: earliest time after mark1 such that output is right until the end of the hold
        ok = (c == truth_label) if truth_label >= 0 else np.isin(c, (CHECK, MIS))
        hold = (t.ms >= a) & (t.ms < b - G_OFF_BEFORE)
        idx = np.where(hold)[0]
        lat = np.nan
        if r["correct"] and len(idx):
            wrong = np.where(~ok[idx])[0]
            if wrong.size == 0:
                lat = (t.ms[idx[0]] - a) / 1000.0
            elif wrong[-1] + 1 < len(idx):
                lat = (t.ms[idx[wrong[-1] + 1]] - a) / 1000.0
        r["settle_on_s"] = lat
        # release: after mark2 the slot should return to FREE and stay there
        after = np.where((t.ms >= b) & (tr == 0))[0]
        rel = np.nan
        r["release_ok"] = False
        if len(after):
            nonfree = np.where(c[after] != FREE)[0]
            if len(nonfree) == 0:
                rel = 0.0; r["release_ok"] = True
            elif nonfree[-1] + 1 < len(after):
                rel = (t.ms[after[nonfree[-1] + 1]] - b) / 1000.0; r["release_ok"] = True
        r["release_s"] = rel
        # also: did the unit ever show OCC while the truth was not OCC (false occupied)?
        r["false_occ"] = bool(np.any((c == OCC) & (tr >= 0) & (tr != OCC)))
        res.append(r)
    return res


def frame_confusion(trials, codes_by_trial, classes=(0, 1, 2, 3)):
    cm = np.zeros((4, 4), dtype=int)
    for t in trials:
        c = codes_by_trial[t.id]
        for i in range(t.k):
            tr = t.truth[:, i]
            m = tr >= 0
            for a, p in zip(tr[m], c[m, i]):
                cm[a, p] += 1
    return cm


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)

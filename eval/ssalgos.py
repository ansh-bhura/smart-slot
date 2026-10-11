"""
ssalgos.py - filters and decision algorithms for Smart Slot, all causal / streaming.

Every Method exposes   run(trial) -> codes (n_sweeps, n_slots) of {0 FREE, 1 OCC, 2 CHECK, 3 MIS}
so the offline replay behaves exactly like a sample-by-sample run on the ESP32 (no look-ahead).

Distance filters      : Median5 (current firmware), EMA, Kalman (local-level)
Decision algorithms   : ThresholdOnly (Review-1), BandsOnly (no time logic), Rules (port of the firmware
                        state machine), Cusum, FeatHMM (supervised GMM-HMM over window features),
                        Tree (small decision tree over window features), KalmanGated.
Post-processor        : sync_mis  (two neighbouring slots turn OCC together -> one vehicle across both)
"""
from collections import deque
import numpy as np

FREE, OCC, CHECK, MIS = 0, 1, 2, 3

# ============================================================== filters
class Median5:
    def __init__(self, n=5):
        self.buf = deque(maxlen=n); self.x = None
    def update(self, y):
        if y > 0:
            self.buf.append(y); self.x = float(np.median(self.buf))
        return self.x

class EMA:
    def __init__(self, alpha=0.4):
        self.a = alpha; self.x = None
    def update(self, y):
        if y > 0:
            self.x = y if self.x is None else self.x + self.a * (y - self.x)
        return self.x

class Kalman:
    """Local-level Kalman filter on distance. q = process var (cm^2/step), r = measurement var."""
    def __init__(self, q=0.05, r=0.04):
        self.q, self.r = q, r; self.x = None; self.P = 1.0
    def update(self, y):
        if y <= 0:
            return self.x
        if self.x is None:
            self.x, self.P = y, self.r; return self.x
        self.P += self.q
        K = self.P / (self.P + self.r)
        self.x += K * (y - self.x); self.P *= (1 - K)
        return self.x

FILTERS = {"median5": Median5, "ema": EMA, "kalman": Kalman}

def make_filter(spec):
    if isinstance(spec, tuple):
        return FILTERS[spec[0]](**spec[1])
    return FILTERS[spec]()

# ============================================================== helpers
def band_code(delta, prev, P):
    """Pure amplitude classification with hysteresis around the bike band (no timers)."""
    if prev == OCC and P["bike_min"] - P["hyst"] <= delta <= P["bike_max"] + P["hyst"]:
        return OCC
    if delta <= P["t_trig"]:
        return FREE
    if P["bike_min"] <= delta <= P["bike_max"]:
        return OCC
    return CHECK

def deltas(trial, i, filt_spec):
    f = make_filter(filt_spec); b = trial.base[i]; out = np.full(len(trial.ms), np.nan)
    for j, y in enumerate(trial.raw[:, i]):
        x = f.update(y)
        if x is not None:
            out[j] = b - x
    return out

# ============================================================== Method base
class PerSlot:
    """Run an independent per-slot detector over every slot of a trial."""
    name = "?"
    def run(self, trial):
        n, k = trial.raw.shape
        codes = np.zeros((n, k), dtype=int)
        for i in range(k):
            codes[:, i] = self.run_slot(trial, i)
        return self.post(trial, codes)
    def post(self, trial, codes):
        return codes

# ------------------------------------------------------------ Review-1 baseline
class ThresholdOnly(PerSlot):
    name = "Threshold-only"
    def __init__(self, T=2.5, filt="median5"):
        self.T, self.filt = T, filt
    def run_slot(self, trial, i):
        d = deltas(trial, i, self.filt)
        return np.where(np.nan_to_num(d, nan=0.0) > self.T, OCC, FREE)

class BandsOnly(PerSlot):
    """Amplitude bands + hysteresis, but no timers / stability logic."""
    name = "Bands-only"
    def __init__(self, t_trig=1.5, bike_min=3.0, bike_max=5.8, hyst=1.0, filt="median5"):
        self.P = dict(t_trig=t_trig, bike_min=bike_min, bike_max=bike_max, hyst=hyst); self.filt = filt
    def run_slot(self, trial, i):
        d = deltas(trial, i, self.filt); out = np.zeros(len(d), dtype=int); prev = FREE
        for j, x in enumerate(d):
            if not np.isnan(x):
                prev = band_code(x, prev, self.P)
            out[j] = prev
        return out

# ------------------------------------------------------------ port of the firmware
DEFAULT_RULES = dict(t_trig=1.5, bike_min=3.0, bike_max=5.8, hyst=1.0, sigma_max=0.8,
                     stable_ms=2000, absent_ms=1500, unstable_max_ms=8000, leave_ms=800,
                     present_n=2, stab_n=10)

class _RuleSlot:
    S_FREE, S_OBS, S_OCC, S_CHECK = 0, 1, 2, 3
    def __init__(self, P):
        self.P = P; self.state = 0; self.last_stable = 0; self.cls = 0
        self.d = deque(maxlen=P["stab_n"]); self.cand_cls = 0; self.cand_since = 0
        self.absent_since = 0; self.below_since = 0; self.present = 0; self.obs_start = 0
    def step(self, now, delta):
        P = self.P; s = self
        s.d.append(delta)
        cls = 0 if delta <= P["t_trig"] else 1 if delta < P["bike_min"] else 2 if delta <= P["bike_max"] else 3
        s.cls = cls
        stable = len(s.d) >= P["stab_n"] and float(np.std(s.d)) < P["sigma_max"]
        if cls == 0:
            s.present = 0
            if not s.absent_since: s.absent_since = now or 1
        else:
            s.present = min(255, s.present + 1); s.absent_since = 0
        if cls != 0 and stable:
            if s.cand_cls != cls or not s.cand_since: s.cand_cls = cls; s.cand_since = now or 1
        else:
            s.cand_since = 0; s.cand_cls = 0
        cand_done = bool(s.cand_since) and now - s.cand_since >= P["stable_ms"]
        absent_long = bool(s.absent_since) and now - s.absent_since >= P["absent_ms"]
        if s.state == 0:
            if s.present >= P["present_n"]:
                s.last_stable = 0; s.state = 1; s.obs_start = now
        elif s.state == 1:
            if cls == 0 and absent_long: s.state = 0
            elif cand_done: s.state = 2 if s.cand_cls == 2 else 3
            elif cls != 0 and now - s.obs_start >= P["unstable_max_ms"]: s.state = 3
        elif s.state == 2:
            if delta < P["bike_min"] - P["hyst"]:
                if not s.below_since: s.below_since = now or 1
                if now - s.below_since >= P["leave_ms"]:
                    s.last_stable = 2; s.state = 1; s.obs_start = now; s.below_since = 0
            else:
                s.below_since = 0
        elif s.state == 3:
            if cls == 0 and absent_long: s.state = 0
            elif cand_done and s.cand_cls == 2: s.state = 2
    def code(self):
        st = self.last_stable if self.state == 1 else self.state
        return OCC if st == 2 else CHECK if st == 3 else FREE

class Rules:
    name = "Rules (firmware)"
    def __init__(self, filt="median5", use_mis=True, **over):
        self.P = dict(DEFAULT_RULES); self.P.update(over); self.filt = filt; self.use_mis = use_mis
    def run(self, trial):
        n, k = trial.raw.shape
        slots = [_RuleSlot(self.P) for _ in range(k)]
        dl = np.stack([deltas(trial, i, self.filt) for i in range(k)], axis=1)
        codes = np.zeros((n, k), dtype=int)
        for j in range(n):
            now = int(trial.ms[j] - trial.ms[0])
            for i in range(k):
                if not np.isnan(dl[j, i]):
                    slots[i].step(now, dl[j, i])
            mis = [False] * k
            if self.use_mis:
                for i in range(k - 1):
                    a, b = slots[i], slots[i + 1]
                    a_small = a.state == 3 and a.cls == 1; b_small = b.state == 3 and b.cls == 1
                    a_occ = a.state == 2; b_occ = b.state == 2
                    if (a_small and b_small) or (a_small and b_occ) or (a_occ and b_small):
                        mis[i] = mis[i + 1] = True
            for i in range(k):
                codes[j, i] = MIS if mis[i] else slots[i].code()
        return codes

# ------------------------------------------------------------ CUSUM
class Cusum(PerSlot):
    """Presence by upward CUSUM on clipped delta; level classified from the post-change samples;
    departure by a downward CUSUM against the current object level."""
    name = "CUSUM"
    def __init__(self, k=0.8, h=8.0, cap=6.0, h_leave=6.0, sigma_max=0.8, t_trig=1.5,
                 bike_min=3.0, bike_max=5.8, hyst=1.0, filt="median5"):
        self.__dict__.update(k=k, h=h, cap=cap, h_leave=h_leave, sigma_max=sigma_max, filt=filt)
        self.P = dict(t_trig=t_trig, bike_min=bike_min, bike_max=bike_max, hyst=hyst)
    def run_slot(self, trial, i):
        d = deltas(trial, i, self.filt); n = len(d); out = np.zeros(n, dtype=int)
        S = 0.0; since = 0; present = False; Sn = 0.0; buf = deque(maxlen=15); prev = FREE
        for j in range(n):
            x = d[j]
            if np.isnan(x): out[j] = prev; continue
            buf.append(x)
            if not present:
                S = max(0.0, S + min(max(x, 0.0), self.cap) - self.k)
                since = since + 1 if S > 0 else 0
                if S > self.h:
                    present = True; Sn = 0.0
                prev = FREE
            if present:
                m = max(3, min(len(buf), max(since, 3)))
                recent = list(buf)[-min(len(buf), 15):]
                level = float(np.median(recent)); L = min(max(level, 0.0), self.cap)
                Sn = max(0.0, Sn + L / 2.0 - min(max(x, 0.0), self.cap))
                if Sn > self.h_leave:
                    present = False; S = 0.0; since = 0; Sn = 0.0; prev = FREE
                else:
                    sd = float(np.std(recent)) if len(recent) >= 5 else 0.0
                    if sd > self.sigma_max * 2.0: prev = CHECK
                    else:
                        c = band_code(level, prev if prev in (OCC,) else FREE, self.P)
                        prev = CHECK if c == FREE else c
            out[j] = prev
        return out

# ------------------------------------------------------------ Kalman with innovation gating
class KalmanGated(PerSlot):
    """Kalman estimate that treats large innovations as outliers until they persist for n_acc sweeps,
    then accepts the change. Decision = amplitude bands + hysteresis on the estimate (no timers)."""
    name = "Kalman-gated"
    def __init__(self, q=0.05, r=0.04, gate=4.0, n_acc=10, t_trig=1.5, bike_min=3.0, bike_max=5.8, hyst=1.0):
        self.__dict__.update(q=q, r=r, gate=gate, n_acc=n_acc)
        self.P = dict(t_trig=t_trig, bike_min=bike_min, bike_max=bike_max, hyst=hyst)
    def run_slot(self, trial, i):
        b = trial.base[i]; n = trial.raw.shape[0]; out = np.zeros(n, dtype=int)
        x = None; P = self.r; streak = 0; prev = FREE
        for j, y in enumerate(trial.raw[:, i]):
            if y <= 0: out[j] = prev; continue
            if x is None: x = y; P = self.r
            else:
                P += self.q; S = P + self.r; e = y - x
                if abs(e) / np.sqrt(S) > self.gate:
                    streak += 1
                    if streak >= self.n_acc: x = y; P = self.r; streak = 0
                else:
                    streak = 0; K = P / S; x += K * e; P *= (1 - K)
            prev = band_code(b - x, prev, self.P)
            out[j] = prev
        return out

# ============================================================== window features (HMM + Tree)
FEAT_NAMES = ["cur", "med2s", "std2s", "frac2s", "max4s", "frac4s", "run_s"]

def trial_features(trial, filt="median5", dt_s=0.15):
    """Causal window features per sweep and slot. 2 s ~ 13 sweeps, 4 s ~ 27 sweeps."""
    n, k = trial.raw.shape; F = np.zeros((n, k, len(FEAT_NAMES)))
    for i in range(k):
        d = np.nan_to_num(deltas(trial, i, filt), nan=0.0)
        run = 0
        for j in range(n):
            w2 = d[max(0, j - 12): j + 1]; w4 = d[max(0, j - 26): j + 1]
            run = run + 1 if d[j] > 1.5 else 0
            F[j, i] = [d[j], np.median(w2), np.std(w2), np.mean(w2 > 1.5), np.max(w4), np.mean(w4 > 1.5), min(run, 60) * dt_s]
    return F

_FEAT_CACHE = {}
def features_cached(trial, filt="median5"):
    key = (trial.id, str(filt))
    if key not in _FEAT_CACHE:
        _FEAT_CACHE[key] = trial_features(trial, filt)
    return _FEAT_CACHE[key]

def training_frames(trials, filt="median5", classes=(0, 1, 2)):
    X, y = [], []
    for t in trials:
        F = features_cached(t.view, filt)[t.view.pre:]
        for i in range(t.k):
            m = np.isin(t.truth[:, i], classes)
            X.append(F[m, i]); y.append(t.truth[m, i])
    return np.vstack(X), np.concatenate(y)

class FeatHMM:
    """3-state HMM (FREE, OCC, CHECK). Emissions: per-state Gaussian mixture over window features,
    learned from labelled frames. Transitions: sticky (p_stay), forward filtering = causal posterior."""
    name = "Feature-HMM"
    def __init__(self, p_stay=0.97, comps=(2, 2, 3), filt="median5", reg=0.02):
        self.p_stay, self.comps, self.filt, self.reg = p_stay, comps, filt, reg; self.gmm = None
    def fit(self, trials):
        from sklearn.mixture import GaussianMixture
        X, y = training_frames(trials, self.filt)
        self.gmm = []
        for c in (0, 1, 2):
            Xc = X[y == c]
            g = GaussianMixture(n_components=min(self.comps[c], max(1, len(Xc) // 20)), covariance_type="diag",
                                reg_covar=self.reg, random_state=0, n_init=2).fit(Xc)
            self.gmm.append(g)
        self.logprior = np.log(np.bincount(y.astype(int), minlength=3) / len(y))
        return self
    def run(self, trial):
        F = features_cached(trial, self.filt); n, k, _ = F.shape
        codes = np.zeros((n, k), dtype=int)
        lt = np.log(np.full((3, 3), (1 - self.p_stay) / 2)); np.fill_diagonal(lt, np.log(self.p_stay))
        for i in range(k):
            ll = np.stack([g.score_samples(F[:, i]) for g in self.gmm], axis=1)
            a = self.logprior + ll[0]
            codes[0, i] = int(np.argmax(a))
            for j in range(1, n):
                m = a[:, None] + lt
                a = np.logaddexp.reduce(m, axis=0) + ll[j]
                a -= a.max()
                codes[j, i] = int(np.argmax(a))
        return codes

class Tree:
    """Small decision tree over the same window features, followed by a majority vote of the last m outputs."""
    name = "Decision tree"
    def __init__(self, depth=4, vote=5, filt="median5", leaf=30):
        self.depth, self.vote, self.filt, self.leaf = depth, vote, filt, leaf; self.clf = None
    def fit(self, trials):
        from sklearn.tree import DecisionTreeClassifier
        X, y = training_frames(trials, self.filt)
        self.clf = DecisionTreeClassifier(max_depth=self.depth, min_samples_leaf=self.leaf,
                                          class_weight="balanced", random_state=0).fit(X, y)
        return self
    def run(self, trial):
        F = features_cached(trial, self.filt); n, k, _ = F.shape
        codes = np.zeros((n, k), dtype=int)
        for i in range(k):
            p = self.clf.predict(F[:, i]); w = deque(maxlen=self.vote)
            for j in range(n):
                w.append(int(p[j])); codes[j, i] = max(set(w), key=list(w).count)
        return codes

# ============================================================== pair rule (post-processor)
def sync_mis(codes, ms, window_ms=2000):
    """Causal rule: when two neighbouring slots are OCC and their current OCC episodes started within
    window_ms of each other, report MIS in both (one vehicle spanning two slots)."""
    n, k = codes.shape; out = codes.copy(); start = [None] * k; prev = [FREE] * k
    for j in range(n):
        for i in range(k):
            if codes[j, i] == OCC and prev[i] != OCC: start[i] = ms[j]
            prev[i] = codes[j, i]
        for i in range(k - 1):
            if codes[j, i] == OCC and codes[j, i + 1] == OCC and start[i] is not None and start[i + 1] is not None:
                if abs(start[i] - start[i + 1]) <= window_ms:
                    out[j, i] = out[j, i + 1] = MIS
    return out

class WithSync:
    def __init__(self, base, window_ms=2000):
        self.base, self.w = base, window_ms; self.name = base.name + " + sync-MIS"
    def run(self, trial):
        return sync_mis(self.base.run(trial), trial.ms, self.w)


# ============================================================== adaptive baseline (drift / calibration-error correction)
class RulesAdaptive(Rules):
    """Firmware state machine + slow baseline tracking: while a slot is FREE and the reading is within
    +/- adapt_band of the baseline, the baseline is pulled toward the filtered distance (EMA, rate alpha
    per sweep). Frozen in every other state, so a parked bike can never be 'learned' as the floor."""
    name = "Rules + adaptive baseline"
    def __init__(self, filt="median5", alpha=0.03, adapt_band=1.0, use_mis=True, **over):
        super().__init__(filt=filt, use_mis=use_mis, **over); self.alpha, self.band = alpha, adapt_band
    def run(self, trial):
        n, k = trial.raw.shape
        slots = [_RuleSlot(self.P) for _ in range(k)]
        filt = [make_filter(self.filt) for _ in range(k)]
        base = [float(b) for b in trial.base]
        codes = np.zeros((n, k), dtype=int)
        for j in range(n):
            now = int(trial.ms[j] - trial.ms[0])
            for i in range(k):
                x = filt[i].update(trial.raw[j, i])
                if x is None: continue
                d = base[i] - x
                if slots[i].state == 0 and slots[i].cls == 0 and abs(d) < self.band:
                    base[i] -= self.alpha * d; d = base[i] - x
                slots[i].step(now, d)
            mis = [False] * k
            if self.use_mis:
                for i in range(k - 1):
                    a, b = slots[i], slots[i + 1]
                    a_small = a.state == 3 and a.cls == 1; b_small = b.state == 3 and b.cls == 1
                    if (a_small and b_small) or (a_small and b.state == 2) or (a.state == 2 and b_small):
                        mis[i] = mis[i + 1] = True
            for i in range(k):
                codes[j, i] = MIS if mis[i] else slots[i].code()
        return codes

"""
run_cv.py - 5-fold cross-validated comparison (trial-grouped, stratified by scenario).

Inside every training split each method is tuned (random search for the parametric ones, inner 3-fold
for the learned ones) and then evaluated ONCE on the held-out trials. Nothing is tuned on a test trial.
Outputs (in OUT dir): oof_units.csv, oof_codes.pkl, chosen_params.json
"""
import sys, os, json, pickle, time, random, itertools
import numpy as np, pandas as pd
from multiprocessing import Pool
import ssdata as D, ssalgos as A, evalcore as E

DATA, OUT = sys.argv[1], sys.argv[2]
os.makedirs(OUT, exist_ok=True)
SEED, K_OUT, N_SEARCH = 7, 5, 45
TRIALS = D.load_trials(DATA)
BY_ID = {t.id: t for t in TRIALS}
for t in TRIALS: A.features_cached(t.view, "median5")      # warm the cache before forking

# ---------------------------------------------------------------- folds
def make_folds(trials, k, seed):
    rng = random.Random(seed); fold = {}
    by = {}
    for t in trials: by.setdefault(t.name, []).append(t.id)
    off = 0
    for name, ids in sorted(by.items()):
        rng.shuffle(ids)
        for j, i in enumerate(ids): fold[i] = (j + off) % k
        off += len(ids)
    return fold
FOLD = make_folds(TRIALS, K_OUT, SEED)

def loss(df):
    s = E.summarize(df)
    lat = s["settle_on_s"] if not np.isnan(s["settle_on_s"]) else 10.0
    return -(s["macro_acc"] - 0.01 * lat)

def random_search(make, space, train, n, seed, default=None):
    rng = random.Random(seed); cands = []
    if default is not None: cands.append(default)
    keys = list(space)
    for _ in range(n): cands.append({k: rng.choice(space[k]) for k in keys})
    best, bl = None, 1e9
    for c in cands:
        l = loss(E.evaluate(make(**c), train))
        if l < bl: best, bl = c, l
    return best

def inner_select(fit_make, grid, train, seed):
    """inner 3-fold CV over `grid` (list of param dicts) for learned methods."""
    inner = make_folds(train, 3, seed + 1); best, bl = None, 1e9
    for p in grid:
        dfs = []
        for f in range(3):
            tr = [t for t in train if inner[t.id] != f]; te = [t for t in train if inner[t.id] == f]
            m = fit_make(**p).fit([t for t in tr if t.name in D.CORE])
            dfs.append(E.evaluate(m, te))
        l = loss(pd.concat(dfs))
        if l < bl: best, bl = p, l
    return best

# ---------------------------------------------------------------- method definitions
SP_THR = {"T": [round(x, 2) for x in np.arange(1.5, 5.01, 0.25)]}
SP_BANDS = dict(t_trig=[1.0, 1.25, 1.5, 2.0], bike_min=[2.0, 2.2, 2.5, 2.8, 3.0], bike_max=[5.0, 5.5, 5.8, 6.0, 6.5], hyst=[0.5, 1.0])
SP_RULES = dict(t_trig=[1.0, 1.25, 1.5], bike_min=[2.0, 2.2, 2.5, 2.8, 3.0], bike_max=[5.0, 5.5, 5.8, 6.0, 6.5],
                sigma_max=[0.5, 0.8, 1.2, 1.6], stable_ms=[1000, 1500, 2000], hyst=[0.5, 1.0])
SP_CUSUM = dict(k=[0.5, 0.8, 1.2], h=[4.0, 6.0, 8.0, 12.0, 16.0], cap=[5.0, 6.0, 7.0], h_leave=[3.0, 6.0, 10.0],
                sigma_max=[0.8, 1.2, 1.6], bike_min=[2.2, 2.5, 3.0], bike_max=[5.8, 6.2], t_trig=[1.0, 1.5])
SP_KG = dict(gate=[3.0, 4.0, 6.0], n_acc=[6, 10, 14, 20], q=[0.02, 0.05, 0.2], bike_min=[2.2, 2.5, 3.0],
             bike_max=[5.8, 6.2], t_trig=[1.0, 1.5], hyst=[0.5, 1.0])
GRID_HMM = [dict(p_stay=p) for p in (0.9, 0.95, 0.97, 0.99, 0.995)]
GRID_TREE = [dict(depth=d, vote=v) for d in (2, 3, 4, 5) for v in (1, 5, 9)]

def fit_fold(args):
    f, = args
    train = [t for t in TRIALS if FOLD[t.id] != f]; test = [t for t in TRIALS if FOLD[t.id] == f]
    core_train = [t for t in train if t.name in D.CORE]
    chosen, methods = {}, {}
    methods["Threshold-only (T=2.5, Review-1)"] = A.ThresholdOnly(2.5)
    methods["Rules (firmware default)"] = A.Rules()
    p = random_search(lambda **c: A.ThresholdOnly(**c), SP_THR, core_train, 0, SEED, default={"T": 2.5}) if False else None
    best = min(SP_THR["T"], key=lambda T: loss(E.evaluate(A.ThresholdOnly(T), core_train)))
    chosen["Threshold-only (tuned)"] = {"T": best}; methods["Threshold-only (tuned)"] = A.ThresholdOnly(best)
    c = random_search(lambda **c: A.BandsOnly(**c), SP_BANDS, core_train, N_SEARCH, SEED + f, dict(t_trig=1.5, bike_min=3.0, bike_max=5.8, hyst=1.0))
    chosen["Bands-only (tuned)"] = c; methods["Bands-only (tuned)"] = A.BandsOnly(**c)
    c = random_search(lambda **c: A.Rules(**c), SP_RULES, core_train, N_SEARCH, SEED + f, dict(t_trig=1.5, bike_min=3.0, bike_max=5.8, sigma_max=0.8, stable_ms=2000, hyst=1.0))
    chosen["Rules (tuned)"] = c; methods["Rules (tuned)"] = A.Rules(**c)
    c = random_search(lambda **c: A.Cusum(**c), SP_CUSUM, core_train, N_SEARCH, SEED + f)
    chosen["CUSUM"] = c; methods["CUSUM"] = A.Cusum(**c)
    c = random_search(lambda **c: A.KalmanGated(**c), SP_KG, core_train, N_SEARCH, SEED + f)
    chosen["Kalman-gated"] = c; methods["Kalman-gated"] = A.KalmanGated(**c)
    c = inner_select(lambda **p: A.FeatHMM(**p), GRID_HMM, core_train, SEED + f)
    chosen["Feature-HMM"] = c; methods["Feature-HMM"] = A.FeatHMM(**c).fit(core_train)
    c = inner_select(lambda **p: A.Tree(**p), GRID_TREE, core_train, SEED + f)
    chosen["Decision tree"] = c; methods["Decision tree"] = A.Tree(**c).fit(core_train)
    # pair rule on top of the strongest per-slot methods
    for base in ("Rules (tuned)", "Feature-HMM", "Decision tree"):
        methods[base + " + sync-MIS"] = A.WithSync(methods[base])
    units, codes = [], {}
    for name, m in methods.items():
        cc = {}
        df = E.evaluate(m, test, keep_codes=cc); df["method"] = name; df["fold"] = f; units.append(df)
        for tid, c_ in cc.items(): codes[(name, tid)] = c_
    return units, codes, chosen, f

if __name__ == "__main__":
    t0 = time.time()
    with Pool(2) as pool:
        res = pool.map(fit_fold, [(f,) for f in range(K_OUT)])
    units = pd.concat([u for r in res for u in r[0]], ignore_index=True)
    codes = {}; chosen = {}
    for r in res:
        codes.update(r[1]); chosen[f"fold{r[3]}"] = r[2]
    units.to_csv(os.path.join(OUT, "oof_units.csv"), index=False)
    pickle.dump(codes, open(os.path.join(OUT, "oof_codes.pkl"), "wb"))
    json.dump(chosen, open(os.path.join(OUT, "chosen_params.json"), "w"), indent=1, default=float)
    json.dump({"fold_of_trial": FOLD, "seed": SEED}, open(os.path.join(OUT, "folds.json"), "w"))
    print("done in %.0f s; unit rows: %d" % (time.time() - t0, len(units)))

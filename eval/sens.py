"""sens.py - how much does a calibration (baseline) error hurt each method?  usage: sens.py DATA OUTDIR"""
import sys, os, copy, numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
import ssdata as D, ssalgos as A, evalcore as E
DATA, OUT = sys.argv[1:3]; os.makedirs(OUT, exist_ok=True)
T = [t for t in D.load_trials(DATA) if t.name in D.CORE]
CONS = dict(t_trig=1.25, bike_min=2.2, bike_max=5.5, sigma_max=1.2, stable_ms=1500, hyst=1.0)   # consensus of the 5 tuned folds
for t in T: A.features_cached(t.view, "median5")
tree = A.Tree(depth=4, vote=9).fit(T); hmm = A.FeatHMM(p_stay=0.99).fit(T)
METHODS = {"Rules (firmware default)": A.Rules(), "Rules (tuned thresholds)": A.Rules(**CONS),
           "Rules + adaptive baseline (default)": A.RulesAdaptive(), "Rules + adaptive baseline (tuned)": A.RulesAdaptive(**CONS),
           "Decision tree*": tree, "Feature-HMM*": hmm}

def shifted(t, s):
    t2 = copy.copy(t); t2.id = f"{t.id}@{s:+.1f}"; t2.base = t.base + s
    v = copy.copy(t.view); v.id = t2.id; v.base = t.base + s; t2.view = v     # raw (incl. pre-roll) unchanged = true distances
    return t2

SH = [-0.6, -0.4, -0.2, 0.0, 0.2, 0.4, 0.6]
rows = []
for s in SH:
    Ts = [shifted(t, s) for t in T]
    for name, m in METHODS.items():
        r = E.summarize(E.evaluate(m, Ts))
        rows.append(dict(shift=s, method=name, macro=100 * r["macro_acc"], bike=100 * r["bike_recall"], check=100 * r["check_acc"],
                         false_alert=100 * r["false_alert_rate"]))
df = pd.DataFrame(rows); df.to_csv(os.path.join(OUT, "baseline_sensitivity.csv"), index=False)
print(df.pivot(index="method", columns="shift", values="macro").round(0).to_string())
print(df.pivot(index="method", columns="shift", values="bike").round(0).to_string())
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#4a3aa7"]; S_ = "#fcfcfb"
fig, ax = plt.subplots(figsize=(8.6, 4.6), facecolor=S_); ax.set_facecolor(S_)
for (name, g), c in zip(df.groupby("method", sort=False), CAT):
    ax.plot(g["shift"], g.macro, color=c, lw=2, marker="o", ms=5, label=name)
ax.set_xlabel("error in calibrated baseline (cm)   [+ = baseline too far]", color="#52514e"); ax.set_ylabel("macro accuracy (%)", color="#52514e")
ax.grid(color="#e6e5e1"); ax.set_axisbelow(True)
for s_ in ("top", "right"): ax.spines[s_].set_visible(False)
ax.set_title("How a calibration error hurts each method  (*trained on all data)", loc="left", fontsize=10.5)
ax.legend(frameon=False, fontsize=8.5, loc="lower center", ncol=2)
fig.tight_layout(); fig.savefig(os.path.join(OUT, "fig4_baseline_sensitivity.png"), dpi=180, facecolor=S_)

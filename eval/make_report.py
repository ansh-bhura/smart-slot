"""make_report.py - tables + figures from run_cv.py output.  usage: make_report.py DATA CVDIR OUTDIR"""
import sys, os, json, pickle
import numpy as np, pandas as pd
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import ssdata as D, ssalgos as A, evalcore as E

DATA, CV, OUT = sys.argv[1:4]
os.makedirs(OUT, exist_ok=True)
T = D.load_trials(DATA); BY = {t.id: t for t in T}
U = pd.read_csv(os.path.join(CV, "oof_units.csv"))
codes = pickle.load(open(os.path.join(CV, "oof_codes.pkl"), "rb"))
chosen = json.load(open(os.path.join(CV, "chosen_params.json")))


def md(df, floatfmt=2):
    cols = list(df.columns)
    w = ["| " + " | ".join(map(str, [df.index.name or ""] + cols)) + " |", "|" + "---|" * (len(cols) + 1)]
    for idx, r in df.iterrows():
        cells = [str(idx)] + [("" if (isinstance(v, float) and np.isnan(v)) else
                               (f"{v:.{floatfmt}f}" if isinstance(v, (float, np.floating)) else str(v))) for v in r]
        w.append("| " + " | ".join(cells) + " |")
    return "\n".join(w)


MAIN = ["Threshold-only (T=2.5, Review-1)", "Threshold-only (tuned)", "Bands-only (tuned)", "Rules (firmware default)",
        "Rules (tuned)", "CUSUM", "Kalman-gated", "Feature-HMM", "Decision tree"]
PAIR = ["Rules (tuned) + sync-MIS", "Feature-HMM + sync-MIS", "Decision tree + sync-MIS"]
SHORT = {"Threshold-only (T=2.5, Review-1)": "Threshold-only (Review 1)", "Threshold-only (tuned)": "Threshold-only (tuned)",
         "Bands-only (tuned)": "Bands-only (tuned)", "Rules (firmware default)": "Rules (firmware)", "Rules (tuned)": "Rules (tuned)",
         "CUSUM": "CUSUM", "Kalman-gated": "Kalman-gated", "Feature-HMM": "Feature-HMM", "Decision tree": "Decision tree"}

# ------------------------------------------------------------------ headline table
rows = {}
for m in MAIN:
    df = U[U.method == m]; s = E.summarize(df)
    free = df[(df.kind == "free") & df.scenario.isin(D.CORE)]
    fo = D.wilson(int(free.false_occ.sum()), len(free))
    rows[SHORT[m]] = {
        "false-OCC on transients %": 100 * s["false_occ_rate"], "(95% CI)": f"{100*fo[0]:.0f}-{100*fo[1]:.0f}",
        "any false alert %": 100 * s["false_alert_rate"],
        "bike recall %": 100 * s["bike_recall"], "CHECK acc %": 100 * s["check_acc"],
        "macro acc %": 100 * s["macro_acc"], "ctrl-slot alert %": 100 * s["ctrl_false_alert"],
        "settle (s)": s["settle_on_s"], "release (s)": s["release_s"]}
head = pd.DataFrame(rows).T
base = U[U.method == MAIN[0]]
n_free = int(((base.kind == "free") & base.scenario.isin(D.CORE)).sum())
n_bike = int(((base.kind == "obj") & base.scenario.isin(["bike", "bike_offset"])).sum())
n_chk = int(((base.kind == "obj") & base.scenario.isin(["low_obj", "tall_obj", "hand_hold"])).sum())

# ------------------------------------------------------------------ per scenario
sc_order = ["swipe", "swipe2", "pass_slow", "idle", "bike", "bike_offset", "low_obj", "tall_obj", "hand_hold"]
per = {}
for m in MAIN:
    df = U[(U.method == m) & (U.kind != "ctrl")]
    g = df.groupby("scenario").correct.mean() * 100
    per[SHORT[m]] = {s: g.get(s, np.nan) for s in sc_order}
per_t = pd.DataFrame(per).T
ncount = base[base.kind != "ctrl"].groupby("scenario").size()
per_t.loc["n units"] = [float(ncount.get(s, 0)) for s in sc_order]


# ------------------------------------------------------------------ mis-parking
def mis_table():
    out = {}
    for m in ["Rules (tuned)", "Feature-HMM", "Decision tree"] + PAIR:
        d = U[(U.method == m) & (U.scenario == "mis") & (U.kind == "obj")]
        spur = tot = 0
        for (name, tid), c in codes.items():
            if name != m or BY[tid].name == "mis": continue
            tot += 1; spur += int((c == D.MIS).any())
        out[m] = {"mis units correct": f"{int(d.correct.sum())}/{len(d)}",
                  "OCC shown instead": f"{int((d.end_code == 1).sum())}/{len(d)}",
                  "non-mis trials with a MIS output": f"{spur}/{tot}"}
    return pd.DataFrame(out).T


# ------------------------------------------------------------------ fixed-parameter filter ablation (all data)
def filter_ablation():
    idle = [t for t in T if t.name == "idle"]
    res = np.concatenate([t.raw[:, i] - pd.Series(t.raw[:, i]).rolling(5, center=True, min_periods=1).median().values
                          for t in idle for i in range(t.k)])
    r = float(np.var(res[np.abs(res) < 1.0])) + 1e-3
    fspec = {"median5 (firmware)": "median5", "EMA a=0.4": ("ema", {"alpha": 0.4}), "EMA a=0.2": ("ema", {"alpha": 0.2}),
             "Kalman q=0.05": ("kalman", {"q": 0.05, "r": r}), "Kalman q=0.01": ("kalman", {"q": 0.01, "r": r})}
    rows = {}
    core = [t for t in T if t.name in D.CORE]
    for fn, fs in fspec.items():
        for mn, mk in (("Threshold-only T=2.5", lambda f: A.ThresholdOnly(2.5, f)), ("Rules (default)", lambda f: A.Rules(f))):
            s = E.summarize(E.evaluate(mk(fs), core))
            rows[f"{mn} / {fn}"] = {"false-OCC %": 100 * s["false_occ_rate"], "any false alert %": 100 * s["false_alert_rate"],
                                    "bike recall %": 100 * s["bike_recall"], "CHECK acc %": 100 * s["check_acc"],
                                    "macro acc %": 100 * s["macro_acc"], "settle (s)": s["settle_on_s"]}
    return pd.DataFrame(rows).T, r


def first_cal():
    d = U[(U.method == "Rules (firmware default)") & (U.kind == "obj") & U.scenario.isin(["bike", "bike_offset"])]
    g = d.groupby("first_after_cal").correct.agg(["mean", "count"]); g["mean"] *= 100
    return g


def delta_summary():
    rows = []
    for t in T:
        if t.name not in D.OBJ_TRUTH: continue
        i = t.slot - 1; d = A.deltas(t, i, "median5"); a, b = t.marks
        m = (t.ms >= a + D.G_ON) & (t.ms < b - D.G_OFF_BEFORE)
        if m.any(): rows.append(dict(scenario=t.name, slot=t.slot, trial=t.id, delta=float(np.median(d[m])), sd=float(np.std(d[m]))))
    return pd.DataFrame(rows)


# ================================================================== figures
LIGHT = dict(surface="#fcfcfb", text="#0b0b0b", muted="#52514e", grid="#e6e5e1")
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#7a7a76", "#c0392b"]


def style(ax):
    ax.set_facecolor(LIGHT["surface"]); ax.grid(axis="y", color=LIGHT["grid"], lw=0.8); ax.set_axisbelow(True)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    for s in ("left", "bottom"): ax.spines[s].set_color("#bdbcb6")
    ax.tick_params(colors=LIGHT["muted"], labelsize=9)


def fig_delta(ds):
    fig, ax = plt.subplots(figsize=(8, 4.2), facecolor=LIGHT["surface"]); style(ax)
    order = ["low_obj", "bike_offset", "bike", "tall_obj", "hand_hold"]
    lab = {"low_obj": "low prop", "bike_offset": "bike\noff-centre", "bike": "bike\ncentred", "tall_obj": "tall prop", "hand_hold": "hand held"}
    col = {"low_obj": CAT[3], "bike_offset": CAT[0], "bike": CAT[0], "tall_obj": CAT[1], "hand_hold": CAT[1]}
    ax.axhspan(3.0, 5.8, color=CAT[0], alpha=0.10, lw=0); ax.axhline(1.5, color=LIGHT["muted"], lw=1, ls="--")
    ax.text(4.45, 1.6, "trigger 1.5", color=LIGHT["muted"], fontsize=8, ha="right", va="bottom")
    ax.text(-0.4, 5.7, "firmware bike band 3.0-5.8 cm", color=CAT[0], fontsize=8.5, va="top")
    rng = np.random.default_rng(1)
    for k, s in enumerate(order):
        v = ds[ds.scenario == s].delta.values
        ax.scatter(k + rng.uniform(-0.18, 0.18, len(v)), v, s=34, color=col[s], edgecolor=LIGHT["surface"], lw=1.2, zorder=3)
        ax.hlines(np.median(v), k - 0.3, k + 0.3, color=LIGHT["text"], lw=2, zorder=4)
    ax.set_xticks(range(len(order))); ax.set_xticklabels([lab[s] for s in order])
    ax.set_ylabel("steady-state delta (cm closer than empty)", color=LIGHT["muted"])
    ax.set_title("What the sensor reports for each prop (dot = one trial, bar = median)", loc="left", color=LIGHT["text"], fontsize=11)
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "fig1_delta_by_prop.png"), dpi=180, facecolor=LIGHT["surface"]); plt.close(fig)


def fig_headline(head):
    mets = [("false-OCC on transients %", "False OCCUPIED on swipes/passes (lower is better)"),
            ("bike recall %", "Bike shown OCCUPIED (higher is better)"),
            ("CHECK acc %", "Odd objects shown CHECK (higher is better)")]
    names = list(head.index); y = np.arange(len(names))[::-1]
    fig, axs = plt.subplots(1, 3, figsize=(12.5, 4.4), sharey=True, facecolor=LIGHT["surface"])
    for ax, (c, t) in zip(axs, mets):
        ax.set_facecolor(LIGHT["surface"]); ax.grid(axis="x", color=LIGHT["grid"], lw=0.8); ax.set_axisbelow(True)
        for s in ("top", "right", "left"): ax.spines[s].set_visible(False)
        ax.spines["bottom"].set_color("#bdbcb6"); ax.tick_params(colors=LIGHT["muted"], labelsize=9)
        v = head[c].values
        ax.barh(y, v, color=[CAT[i] for i in range(len(names))], height=0.62)
        for yy, vv in zip(y, v):
            if vv >= 88: ax.text(vv - 1.5, yy, f"{vv:.0f}", va="center", ha="right", fontsize=8.5, color="white", fontweight="bold")
            else: ax.text(vv + 1.5, yy, f"{vv:.0f}", va="center", fontsize=8.5, color=LIGHT["text"])
        ax.set_xlim(0, 105); ax.set_title(t, loc="left", fontsize=9.5, color=LIGHT["text"]); ax.set_xlabel("%", color=LIGHT["muted"])
    axs[0].set_yticks(y); axs[0].set_yticklabels(names, fontsize=9, color=LIGHT["text"])
    fig.tight_layout(); fig.savefig(os.path.join(OUT, "fig2_headline.png"), dpi=180, facecolor=LIGHT["surface"]); plt.close(fig)


def fig_traces():
    picks = [("swipe", "Hand swipe (should stay FREE)"), ("bike", "Bike parks, then leaves (OCCUPIED, then FREE)")]
    meths = ["Threshold-only (T=2.5, Review-1)", "Rules (tuned)", "Feature-HMM", "Decision tree"]
    fig, axs = plt.subplots(2, 1, figsize=(9.5, 7), facecolor=LIGHT["surface"])
    for ax, (sc, title) in zip(axs, picks):
        cand = [t for t in T if t.name == sc and (sc != "swipe" or not t.masked[:, t.slot - 1].any())]
        t = cand[1] if len(cand) > 1 else cand[0]; i = t.slot - 1
        d = A.deltas(t, i, "median5"); x = (t.ms - t.ms[0]) / 1000
        style(ax); ax.plot(x, d, color=LIGHT["text"], lw=1.6); ax.set_ylim(-3.6, max(8, np.nanmax(d) + 1.0))
        ax.axhline(0, color="#bdbcb6", lw=0.8)
        for mk in t.marks: ax.axvline((mk - t.ms[0]) / 1000, color=LIGHT["muted"], lw=1, ls=":")
        for k_, m in enumerate(meths):
            c = codes[(m, t.id)][:, i]; yb = -0.8 - 0.7 * k_
            for code, colr in ((1, CAT[1]), (2, CAT[3])):
                msk = c == code
                if msk.any(): ax.fill_between(x, yb - 0.27, yb + 0.27, where=msk, color=colr, step="mid", lw=0)
            ax.text(x[-1] + 0.15, yb, SHORT.get(m, m), fontsize=8, va="center", color=LIGHT["text"])
        ax.set_title(f"{title}   [{t.id}]", loc="left", fontsize=10, color=LIGHT["text"]); ax.set_ylabel("delta (cm)", color=LIGHT["muted"])
        ax.set_xlim(x[0], x[-1] + 0.1)
    axs[1].set_xlabel("time (s)   dotted = your Enter-key marks   orange = OCCUPIED, yellow = CHECK, blank = FREE", color=LIGHT["muted"], fontsize=9)
    fig.tight_layout(rect=(0, 0, 0.84, 1)); fig.savefig(os.path.join(OUT, "fig3_example_traces.png"), dpi=180, facecolor=LIGHT["surface"]); plt.close(fig)


if __name__ == "__main__":
    ds = delta_summary(); ab, rvar = filter_ablation(); mt = mis_table(); fc = first_cal()
    dsum = ds.groupby("scenario").delta.describe()[["count", "mean", "std", "min", "50%", "max"]]
    lines = ["# Smart Slot - cross-validated results", "",
             f"Data: {len(T)} trials, {sum(len(t.ms) for t in T)} sweeps. 5-fold CV grouped by trial, stratified by scenario; every method tuned inside its training folds only.",
             f"Scored units: {n_free} transient/idle units (should stay FREE), {n_bike} bike units (OCC), {n_chk} CHECK units.", "",
             "## Table 1 - headline", md(head, 1), "", "## Table 2 - accuracy per scenario (% of units)", md(per_t, 0), "",
             "## Table 3 - mis-parking (pair rule)", md(mt), "",
             f"## Table 4 - filter ablation (fixed parameters, all data; Kalman r={rvar:.3f})", md(ab, 1), "",
             "## Table 5 - bike units: first trial after calibration (Rules, firmware default)", md(fc, 1), "",
             "## Steady-state delta per prop (cm)", md(dsum, 2), "",
             "## Chosen parameters per fold", "```", json.dumps(chosen, indent=1, default=float)[:7000], "```"]
    open(os.path.join(OUT, "results.md"), "w").write("\n".join(lines))
    head.to_csv(os.path.join(OUT, "table1_headline.csv")); per_t.to_csv(os.path.join(OUT, "table2_per_scenario.csv"))
    ab.to_csv(os.path.join(OUT, "table4_filter_ablation.csv")); ds.to_csv(os.path.join(OUT, "delta_by_trial.csv"), index=False)
    fig_delta(ds); fig_headline(head); fig_traces()
    print("\n".join(lines[:70]))

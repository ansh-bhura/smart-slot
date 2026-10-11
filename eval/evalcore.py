"""evalcore.py - run a method over trials, collect unit results, summarise."""
import numpy as np, pandas as pd
import ssdata as D

def evaluate(method, trials, keep_codes=None):
    rows = []
    for t in trials:
        c = D.run_method(method, t)
        if keep_codes is not None: keep_codes[t.id] = c
        for r in D.score_trial(t, c):
            rows.append(r)
    return pd.DataFrame(rows)

def macro_accuracy(df, scen=D.CORE):
    """mean over scenarios of tested-unit accuracy (+ small penalty for control-slot false alerts)."""
    d = df[(df.kind != "ctrl") & df.scenario.isin(scen)]
    per = d.groupby("scenario").correct.mean()
    ctrl = df[df.kind == "ctrl"]
    pen = ctrl.false_alert.mean() if len(ctrl) else 0.0
    return float(per.mean()) - 0.5 * pen

def summarize(df):
    out = {}
    core = df[df.scenario.isin(D.CORE)]
    free = core[core.kind == "free"]; obj = core[core.kind == "obj"]; ctrl = df[df.kind == "ctrl"]
    out["free_units"] = len(free)
    out["false_occ_rate"] = free.false_occ.mean()
    out["false_alert_rate"] = free.false_alert.mean()
    for sc in ("bike", "bike_offset", "low_obj", "tall_obj", "hand_hold"):
        d = obj[obj.scenario == sc]; out["acc_" + sc] = d.correct.mean() if len(d) else np.nan
    b = obj[obj.scenario.isin(("bike", "bike_offset"))]
    out["bike_recall"] = b.correct.mean()
    out["settle_on_s"] = b.settle_on_s.mean()
    out["release_ok"] = b[b.release_ok == True].shape[0] / max(1, len(b)) if "release_ok" in b else np.nan
    out["release_s"] = b.release_s.mean()
    chk = obj[obj.scenario.isin(("low_obj", "tall_obj", "hand_hold"))]
    out["check_acc"] = chk.correct.mean()
    out["ctrl_false_alert"] = ctrl.false_alert.mean()
    out["macro_acc"] = macro_accuracy(df)
    return out

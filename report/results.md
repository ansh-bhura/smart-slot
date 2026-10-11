# Smart Slot - cross-validated results

Data: 94 trials, 12738 sweeps. 5-fold CV grouped by trial, stratified by scenario; every method tuned inside its training folds only.
Scored units: 29 transient/idle units (should stay FREE), 24 bike units (OCC), 33 CHECK units.

## Table 1 - headline
|  | false-OCC on transients % | (95% CI) | any false alert % | bike recall % | CHECK acc % | macro acc % | ctrl-slot alert % | settle (s) | release (s) |
|---|---|---|---|---|---|---|---|---|---|
| Threshold-only (Review 1) | 89.7 | 74-96 | 89.7 | 95.8 | 0.0 | 20.0 | 24.3 | 1.1 | 0.0 |
| Threshold-only (tuned) | 89.7 | 74-96 | 89.7 | 100.0 | 0.0 | 21.2 | 24.3 | 1.1 | 0.0 |
| Bands-only (tuned) | 72.4 | 54-85 | 89.7 | 95.8 | 100.0 | 52.7 | 25.4 | 1.1 | 0.0 |
| Rules (firmware) | 0.0 | 0-12 | 0.0 | 91.7 | 84.8 | 92.0 | 1.7 | 4.5 | 0.1 |
| Rules (tuned) | 0.0 | 0-12 | 0.0 | 100.0 | 97.0 | 98.2 | 1.7 | 3.9 | 0.1 |
| CUSUM | 3.4 | 1-17 | 58.6 | 95.8 | 90.9 | 61.4 | 22.5 | 2.2 | 0.0 |
| Kalman-gated | 0.0 | 0-12 | 3.4 | 91.7 | 90.9 | 90.3 | 5.2 | 3.6 | 3.2 |
| Feature-HMM | 0.0 | 0-12 | 10.3 | 95.8 | 97.0 | 86.5 | 9.2 | 2.9 | 0.0 |
| Decision tree | 0.0 | 0-12 | 6.9 | 95.8 | 100.0 | 93.7 | 1.2 | 3.6 | 0.0 |

## Table 2 - accuracy per scenario (% of units)
|  | swipe | swipe2 | pass_slow | idle | bike | bike_offset | low_obj | tall_obj | hand_hold |
|---|---|---|---|---|---|---|---|---|---|
| Threshold-only (Review 1) | 0 | 0 | 0 | 100 | 100 | 89 | 0 | 0 | 0 |
| Threshold-only (tuned) | 0 | 0 | 0 | 100 | 100 | 100 | 0 | 0 | 0 |
| Bands-only (tuned) | 0 | 0 | 0 | 100 | 100 | 89 | 100 | 100 | 100 |
| Rules (firmware) | 100 | 100 | 100 | 100 | 100 | 78 | 58 | 100 | 100 |
| Rules (tuned) | 100 | 100 | 100 | 100 | 100 | 100 | 92 | 100 | 100 |
| CUSUM | 40 | 50 | 0 | 100 | 100 | 89 | 75 | 100 | 100 |
| Kalman-gated | 100 | 83 | 100 | 100 | 100 | 78 | 92 | 83 | 100 |
| Feature-HMM | 100 | 100 | 40 | 100 | 100 | 89 | 92 | 100 | 100 |
| Decision tree | 100 | 100 | 60 | 100 | 100 | 89 | 100 | 100 | 100 |
| n units | 15 | 6 | 5 | 3 | 15 | 9 | 12 | 12 | 9 |

## Table 3 - mis-parking (pair rule)
|  | mis units correct | OCC shown instead | non-mis trials with a MIS output |
|---|---|---|---|
| Rules (tuned) | 0/12 | 12/12 | 0/88 |
| Feature-HMM | 0/12 | 12/12 | 0/88 |
| Decision tree | 0/12 | 12/12 | 0/88 |
| Rules (tuned) + sync-MIS | 12/12 | 0/12 | 0/88 |
| Feature-HMM + sync-MIS | 12/12 | 0/12 | 0/88 |
| Decision tree + sync-MIS | 12/12 | 0/12 | 0/88 |

## Table 4 - filter ablation (fixed parameters, all data; Kalman r=0.015)
|  | false-OCC % | any false alert % | bike recall % | CHECK acc % | macro acc % | settle (s) |
|---|---|---|---|---|---|---|
| Threshold-only T=2.5 / median5 (firmware) | 89.7 | 89.7 | 95.8 | 0.0 | 19.1 | 1.1 |
| Rules (default) / median5 (firmware) | 0.0 | 0.0 | 91.7 | 84.8 | 92.0 | 4.5 |
| Threshold-only T=2.5 / EMA a=0.4 | 89.7 | 89.7 | 95.8 | 0.0 | 18.2 | 1.0 |
| Rules (default) / EMA a=0.4 | 0.0 | 0.0 | 91.7 | 93.9 | 95.4 | 4.3 |
| Threshold-only T=2.5 / EMA a=0.2 | 86.2 | 86.2 | 95.8 | 0.0 | 20.5 | 1.5 |
| Rules (default) / EMA a=0.2 | 0.0 | 0.0 | 91.7 | 93.9 | 95.4 | 4.3 |
| Threshold-only T=2.5 / Kalman q=0.05 | 89.7 | 89.7 | 95.8 | 0.0 | 14.8 | 1.1 |
| Rules (default) / Kalman q=0.05 | 0.0 | 0.0 | 91.7 | 93.9 | 95.1 | 4.2 |
| Threshold-only T=2.5 / Kalman q=0.01 | 89.7 | 89.7 | 95.8 | 0.0 | 15.4 | 0.9 |
| Rules (default) / Kalman q=0.01 | 0.0 | 0.0 | 91.7 | 93.9 | 95.1 | 4.2 |

## Table 5 - bike units: first trial after calibration (Rules, firmware default)
| first_after_cal | mean | count |
|---|---|---|
| False | 91.7 | 24.0 |

## Steady-state delta per prop (cm)
| scenario | count | mean | std | min | 50% | max |
|---|---|---|---|---|---|---|
| bike | 15.00 | 3.80 | 0.39 | 3.30 | 4.10 | 4.20 |
| bike_offset | 9.00 | 3.41 | 0.49 | 2.40 | 3.70 | 3.80 |
| hand_hold | 9.00 | 8.33 | 0.76 | 6.90 | 8.40 | 9.50 |
| low_obj | 12.00 | 1.79 | 0.22 | 1.50 | 1.90 | 2.00 |
| tall_obj | 12.00 | 7.04 | 0.71 | 6.20 | 6.60 | 7.90 |

## Chosen parameters per fold
```
{
 "fold0": {
  "Threshold-only (tuned)": {
   "T": 1.5
  },
  "Bands-only (tuned)": {
   "t_trig": 1.25,
   "bike_min": 2.5,
   "bike_max": 5.0,
   "hyst": 0.5
  },
  "Rules (tuned)": {
   "t_trig": 1.0,
   "bike_min": 2.2,
   "bike_max": 6.0,
   "sigma_max": 1.6,
   "stable_ms": 1500,
   "hyst": 0.5
  },
  "CUSUM": {
   "k": 1.2,
   "h": 16.0,
   "cap": 5.0,
   "h_leave": 10.0,
   "sigma_max": 1.6,
   "bike_min": 2.5,
   "bike_max": 5.8,
   "t_trig": 1.0
  },
  "Kalman-gated": {
   "gate": 3.0,
   "n_acc": 20,
   "q": 0.02,
   "bike_min": 3.0,
   "bike_max": 6.2,
   "t_trig": 1.5,
   "hyst": 1.0
  },
  "Feature-HMM": {
   "p_stay": 0.99
  },
  "Decision tree": {
   "depth": 5,
   "vote": 9
  }
 },
 "fold1": {
  "Threshold-only (tuned)": {
   "T": 1.5
  },
  "Bands-only (tuned)": {
   "t_trig": 1.0,
   "bike_min": 2.8,
   "bike_max": 5.5,
   "hyst": 0.5
  },
  "Rules (tuned)": {
   "t_trig": 1.25,
   "bike_min": 2.0,
   "bike_max": 5.0,
   "sigma_max": 1.6,
   "stable_ms": 2000,
   "hyst": 0.5
  },
  "CUSUM": {
   "k": 1.2,
   "h": 16.0,
   "cap": 5.0,
   "h_leave": 6.0,
   "sigma_max": 1.2,
   "bike_min": 2.2,
   "bike_max": 5.8,
   "t_trig": 1.5
  },
  "Kalman-gated": {
   "gate": 3.0,
   "n_acc": 20,
   "q": 0.02,
   "bike_min": 2.2,
   "bike_max": 6.2,
   "t_trig": 1.0,
   "hyst": 1.0
  },
  "Feature-HMM": {
   "p_stay": 0.99
  },
  "Decision tree": {
   "depth": 4,
   "vote": 9
  }
 },
 "fold2": {
  "Threshold-only (tuned)": {
   "T": 1.5
  },
  "Bands-only (tuned)": {
   "t_trig": 1.0,
   "bike_min": 2.8,
   "bike_max": 5.5,
   "hyst": 0.5
  },
  "Rules (tuned)": {
   "t_trig": 1.25,
   "bike_min": 2.2,
   "bike_max": 6.0,
   "sigma_max": 0.8,
   "stable_ms": 1500,
   "hyst": 1.0
  },
  "CUSUM": {
   "k": 1.2,
   "h": 16.0,
   "cap": 5.0,
   "h_leave": 10.0,
   "sigma_max": 1.2,
   "bike_min": 3.0,
   "bike_max": 5.8,
   "t_trig": 1.5
  },
  "Kalman-gated": {
   "gate": 3.0,
   "n_acc": 20,
   "q": 0.05,
   "bike_min": 2.5,
   "bike_max": 5.8,
   "t_trig": 1.0,
   "hyst": 0.5
  },
  "Feature-HMM": {
   "p_stay": 0.95
  },
  "Decision tree": {
   "depth": 4,
   "vote": 9
  }
 },
 "fold3": {
  "Threshold-only (tuned)": {
   "T": 1.5
  },
  "Bands-only (tuned)": {
   "t_trig": 1.25,
   "bike_min": 2.5,
   "bike_max": 5.5,
   "hyst": 0.5
  },
  "Rules (tuned)": {
   "t_trig": 1.0,
   "bike_min": 2.2,
   "bike_max": 5.5,
   "sigma_max": 1.2,
   "stable_ms": 2000,
   "hyst": 1.0
  },
  "CUSUM": {
   "k": 1.2,
   "h": 16.0,
   "cap": 5.0,
   "h_leave": 6.0,
   "sigma_max": 1.6,
   "bike_min": 2.5,
   "bike_max": 5.8,
   "t_trig": 1.0
  },
  "Kalman-gated": {
   "gate": 4.0,
   "n_acc": 20,
   "q": 0.02,
   "bike_min": 3.0,
   "bike_max": 5.8,
   "t_trig": 1.0,
   "hyst": 0.5
  },
  "Feature-HMM": {
   "p_stay": 0.97
  },
  "Decision tree": {
   "depth": 4,
   "vote": 9
  }
 },
 "fold4": {
  "Threshold-only (tuned)": {
   "T": 1.5
  },
  "Bands-only (tuned)": {
   "t_trig": 1.25,
   "bike_min": 2.8,
   "bike_max": 5.5,
   "hyst": 0.5
  },
  "Rules (tuned)": {
   "t_trig": 1.25,
   "bike_min": 2.2,
   "bike_max": 5.0,
   "sigma_max": 0.5,
   "stable_ms": 1500,
   "hyst": 1.0
  },
  "CUSUM": {
   "k": 0.8,
   "h": 16.0,
   "cap": 5.0,
   "h_leave": 6.0,
   "sigma_max": 0.8,
   "bike_min": 2.2,
   "bike_max": 5.8,
   "t_trig": 1.5
  },
  "Kalman-gated": {
   "gate": 3.0,
   "n_acc": 20,
   "q": 0.05,
   "bike_min": 3.0,
   "bike_max": 6.2,
   "t_trig": 1.5,
   "hyst": 0.5
  },
  "Feature-HMM": {
   "p_stay": 0.995
  },
  "Decision tree": {
   "depth": 4,
   "vote": 9
  }
 }
}
```
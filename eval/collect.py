#!/usr/bin/env python3
"""
Smart Slot - data collector (v2 recorder).

Run:   python collect.py COM12            (Linux/Mac: /dev/ttyUSB0)
Needs: pip install pyserial
Close the Arduino Serial Monitor first (only one program can use the port).

What it adds over log_serial.py:
  * saves each slot's BASELINE (needed to replay other algorithms offline)
  * event markers (when you placed / removed the object) -> latency can be measured
  * ground truth for EVERY slot per trial (non-tested slots are free negative controls)
  * suggests which slot to test next so all 3 sensors get equal coverage
  * quick feedback after each trial (delta in cm, what the board decided)
  * flags the first trial after every recalibration (the "first try shows CHECK" effect)

Each trial writes:
  trials/<scenario>_<n>.csv    same layout as before (old `log_serial.py analyze` still works)
  trials/<scenario>_<n>.json   baselines, markers, truth per slot, session info
Trial pattern (steady objects):  START -> wait ~3 s empty -> place object, press Enter (mark 1)
                                 -> hold ~8 s -> remove object, press Enter (mark 2) -> wait ~3 s -> Enter (stop)
"""
import sys, os, re, json, time, glob, threading, datetime, statistics

# ---------------------------------------------------------------- scenarios
# truth label per tested slot, accepted on-board codes (0 FREE, 1 OCC, 2 CHECK, 3 MIS),
# marks = how many Enter presses of "event" before the final stop
SCEN = {
    "idle":      dict(title="empty, untouched 5-10 min", truth="FREE",  exp="0",   marks=0, target=1,  slots="all",
                      steps="Leave ALL slots empty and untouched for 5-10 minutes. Press Enter to stop."),
    "swipe":     dict(title="hand swipe (fast)", truth="FREE",  exp="0",   marks=1, target=15, slots="one",
                      steps="Wait 3 s. Swipe your hand FAST across the slot (~0.5 s) and pull away - press Enter at the moment you swipe. Wait 3 s, Enter to stop."),
    "swipe2":    dict(title="two quick swipes", truth="FREE",  exp="0",   marks=1, target=6,  slots="one",
                      steps="Wait 3 s. Two quick swipes about 1 s apart - press Enter at the first swipe. Wait 3 s, Enter to stop."),
    "pass_slow": dict(title="slow hand pass", truth="FREE",  exp="0",   marks=1, target=6,  slots="one",
                      steps="Wait 3 s. Move your hand SLOWLY through the slot (~2 s) and away - press Enter when you start. Wait 3 s, Enter to stop."),
    "hand_hold": dict(title="hand held still ~10 s", truth="HAND",  exp="2",   marks=2, target=9,  slots="one",
                      steps="Wait 3 s. Hold your hand still above the slot (Enter = mark 1). Hold ~10 s. Pull away (Enter = mark 2). Wait 3 s, Enter to stop."),
    "low_obj":   dict(title="LOW object (short/tilted bike prop)", truth="LOW", exp="2", marks=2, target=12, slots="one",
                      steps="Wait 3 s. Place the SHORT/TILTED bike prop so it reads below bike height (Enter = mark 1). Leave ~8 s. Remove it (Enter = mark 2). Wait 3 s, Enter to stop."),
    "tall_obj":  dict(title="TALL object (human prop standing)", truth="TALL", exp="2", marks=2, target=12, slots="one",
                      steps="Wait 3 s. Stand the HUMAN prop in the slot (Enter = mark 1). Leave ~8 s. Remove it (Enter = mark 2). Wait 3 s, Enter to stop."),
    "bike":      dict(title="bike parked centred", truth="BIKE",  exp="1",   marks=2, target=15, slots="one",
                      steps="Wait 3 s. Park the bike CENTRED (Enter = mark 1). Leave ~8 s. Remove it (Enter = mark 2). Wait 3 s, Enter to stop."),
    "bike_offset": dict(title="bike off-centre / tilted", truth="BIKE",exp="1",   marks=2, target=9,  slots="one",
                      steps="Wait 3 s. Park the bike OFF-CENTRE or tilted (Enter = mark 1). Leave ~8 s. Remove it (Enter = mark 2). Wait 3 s, Enter to stop."),
    "clutter":   dict(title="OPTIONAL: pile of random stuff (ma'am test)", truth="CLUTTER", exp="2,3", marks=2, target=3, slots="one",
                      steps="Wait 3 s. Shove a pile of random stuff / a box into the slot like ma'am did (Enter = mark 1). ~8 s. Remove (Enter = mark 2). Wait 3 s, Enter to stop."),
    "two_bikes": dict(title="CONTROL: two separate bikes in neighbouring slots", truth="BIKE2", exp="1", marks=2, target=6, slots="pair",
                      steps="Type the LEFT slot of the pair. Wait 3 s. Park bike A in the left slot (Enter = mark 1), then bike B in the right slot 3-4 s LATER. Leave ~10 s. Remove both (Enter = mark 2). Wait 3 s, Enter to stop."),
    "mis":       dict(title="bike across two slots", truth="MIS",   exp="3",   marks=2, target=6,  slots="pair",
                      steps="Pick the LEFT slot of the pair. Wait 3 s. Put the bike ACROSS the line between the two slots (Enter = mark 1). ~8 s. Remove (Enter = mark 2). Wait 3 s, Enter to stop."),
}
ORDER = list(SCEN)
CODE_NAME = {0: "FREE", 1: "OCC", 2: "CHECK", 3: "MIS"}

# ---------------------------------------------------------------- serial side
class Board:
    """Background reader: keeps every CSV row + parses '# ...' status lines."""
    def __init__(self, ser):
        self.ser = ser
        self.rows = []                  # (ms, raws[], tcodes[], ncodes[])
        self.baselines = {}             # slot(1-based) -> cm
        self.base_time = {}             # slot -> wall-clock time baseline was seen
        self.cal_failed = False
        self.k = None
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.th = threading.Thread(target=self._run, daemon=True)
        self.th.start()

    def _run(self):
        while not self.stop.is_set():
            try:
                line = self.ser.readline().decode(errors="ignore").strip()
            except Exception:
                time.sleep(0.05); continue
            if not line:
                continue
            if line.startswith("#"):
                m = re.search(r"slot (\d+) baseline = ([\d.]+) cm", line)
                if m:
                    with self.lock:
                        self.baselines[int(m.group(1))] = float(m.group(2))
                        self.base_time[int(m.group(1))] = time.time()
                elif "CALIBRATION FAILED" in line:
                    self.cal_failed = True
                    print("\n  !! " + line)
                continue
            if line[0].isdigit():
                p = line.split(",")
                if len(p) < 4 or (len(p) - 1) % 3:
                    continue
                try:
                    vals = [float(x) for x in p]
                except ValueError:
                    continue
                k = (len(p) - 1) // 3
                with self.lock:
                    self.k = k
                    self.rows.append((int(vals[0]), vals[1:1 + k],
                                      [int(v) for v in vals[1 + k:1 + 2 * k]],
                                      [int(v) for v in vals[1 + 2 * k:1 + 3 * k]]))

    def n_rows(self):
        with self.lock: return len(self.rows)

    def last_ms(self):
        with self.lock: return self.rows[-1][0] if self.rows else None

    def slice(self, a):
        with self.lock: return list(self.rows[a:])

    def calibrate(self, timeout=40):
        """Send 'c' and wait until every slot reports a fresh baseline."""
        t0 = time.time()
        self.cal_failed = False
        self.ser.write(b"c")
        while time.time() - t0 < timeout:
            time.sleep(0.2)
            with self.lock:
                k = self.k or 3
                if all(self.base_time.get(s, 0) > t0 for s in range(1, k + 1)):
                    return True
        return False

# ---------------------------------------------------------------- helpers
def med5(vals):
    out, buf = [], []
    for v in vals:
        if v > 0:
            buf.append(v); buf = buf[-5:]
        out.append(statistics.median(buf) if buf else None)
    return out

def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1)))]

def counts(out_dir):
    c = {}
    for f in glob.glob(os.path.join(out_dir, "*.json")):
        try:
            m = json.load(open(f))
        except Exception:
            continue
        key = (m["scenario"], m["slot"])
        c[key] = c.get(key, 0) + 1
    return c

def suggest_slot(cnt, name, k):
    choices = [1, 2] if SCEN[name]["slots"] == "pair" and k >= 3 else list(range(1, k + 1))
    return min(choices, key=lambda s: (cnt.get((name, s), 0), s))

def status(out_dir, k):
    cnt = counts(out_dir)
    print("\n  scenario      done/target   per slot (1/2/3)")
    for n in ORDER:
        per = [cnt.get((n, s), 0) for s in range(1, k + 1)]
        tot = sum(per) + cnt.get((n, 0), 0)   # slot 0 = whole-board trials (idle)
        print(f"  {n:<12} {tot:>3}/{SCEN[n]['target']:<3}      {'/'.join(map(str, per))}")

def next_index(out_dir, name):
    pat = re.compile(rf"^{re.escape(name)}_(\d+)\.csv$")
    nums = [int(m.group(1)) for f in os.listdir(out_dir) if (m := pat.match(f))]
    return max(nums, default=0) + 1

def quick_feedback(rows, k, base, slot, marks, sc):
    """Print what the board saw. rows are the trial rows; slot is 1-based."""
    i = slot - 1
    raw = [r[1][i] for r in rows]
    filt = med5(raw)
    delta = [(base[i] - f) if f is not None else None for f in filt]
    ms = [r[0] for r in rows]
    dur = (ms[-1] - ms[0]) / 1000 if len(ms) > 1 else 0
    valid = [sum(1 for r in rows if r[1][j] > 0) / len(rows) for j in range(k)]
    print(f"  {len(rows)} rows, {dur:.1f} s ({len(rows)/max(dur,0.1):.1f} rows/s), valid echoes per slot: " +
          " ".join(f"{v*100:.0f}%" for v in valid))
    pre = [d for d, t in zip(delta, ms) if d is not None and t - ms[0] < 2500]
    if pre:
        mp = statistics.median(pre)
        flag = "  <-- baseline looks OFF, recalibrate" if abs(mp) > 0.8 else ""
        print(f"  empty-slot delta at start (should be ~0): {mp:+.2f} cm{flag}")
    t_codes = [r[2][i] for r in rows]
    n_codes = [r[3][i] for r in rows]
    exp = {int(x) for x in sc["exp"].split(",")}
    if sc["marks"] == 2 and all(m is not None for m in marks[:2]):
        a, b = marks[0] + 3000, marks[1]
        win = [(d, t, tc, nc) for d, t, tc, nc in zip(delta, ms, t_codes, n_codes) if a <= t <= b and d is not None]
        if win:
            ds = [w[0] for w in win]
            print(f"  hold window ({(b-a)/1000:.1f} s): delta median {statistics.median(ds):.2f} cm, "
                  f"10-90%: {pct(ds,.1):.2f}..{pct(ds,.9):.2f}   [bands: bike 3.0-5.8, small 1.5-3.0, tall >5.8]")
            end_t = [w[2] for w in win[-8:]]
            end_n = [w[3] for w in win[-8:]]
            tc = max(set(end_t), key=end_t.count); nc = max(set(end_n), key=end_n.count)
            ok = "OK " if tc in exp else "MISS"
            print(f"  board temporal ended in {CODE_NAME.get(tc,tc)} (expected {sc['exp']}) {ok} | "
                  f"threshold-only said {'OCC' if nc else 'FREE'}")
            first = next((t for d, t, c, _ in win if c in exp), None)
            if first is not None:
                print(f"  temporal reached an accepted state {(first-marks[0])/1000:.1f} s after you placed it")
        else:
            print("  (hold window empty - did you press the marks too fast?)")
    elif sc["marks"] == 1 and marks and marks[0] is not None:
        win = [(d, c, nc) for d, t, c, nc in zip(delta, ms, t_codes, n_codes)
               if t >= marks[0] - 500 and d is not None]
        if win:
            print(f"  peak delta during event: {max(w[0] for w in win):.2f} cm")
            left = any(c != 0 for _, c, _ in win)
            print(f"  temporal {'LEFT FREE (false alarm)' if left else 'stayed FREE (good)'} | "
                  f"threshold-only {'flipped to OCC' if any(n for *_, n in win) else 'stayed FREE'}")
    elif sc["marks"] == 0:
        left = any(c != 0 for c in t_codes); nl = sum(n_codes)
        print(f"  temporal {'LEFT FREE %d rows' % sum(1 for c in t_codes if c != 0) if left else 'stayed FREE'} | "
              f"threshold-only OCC rows: {nl}")

# ---------------------------------------------------------------- main loop
def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(1)
    port = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "trials"
    os.makedirs(out_dir, exist_ok=True)
    import serial
    ser = serial.Serial(port, 115200, timeout=0.2)
    print(f"Connected to {port}. Waiting 4 s for the board to settle...")
    time.sleep(4)
    board = Board(ser)
    temp = input("Room temperature in C (Enter to skip): ").strip()
    session = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    print("\nMake sure ALL slots are EMPTY. Recalibrating so the baselines are known...")
    input("Press Enter when the slots are empty.")
    if not board.calibrate():
        print("  !! no baselines received - check the cable / that the SmartSlot sketch is running."); sys.exit(2)
    k = board.k or 3
    since_cal = 0
    print("  baselines (cm): " + ", ".join(f"slot {s}={board.baselines[s]:.1f}" for s in sorted(board.baselines)))
    time.sleep(2)

    while True:
        cnt = counts(out_dir)
        print("\n" + "=" * 70)
        for n, name in enumerate(ORDER, 1):
            done = sum(cnt.get((name, s), 0) for s in range(0, k + 1))
            print(f"  {n:>2}. {name:<12} {done:>2}/{SCEN[name]['target']:<2}  - {SCEN[name]['title']}")
        print("   s = status table   c = recalibrate (slots empty!)   q = quit")
        ch = input("Choose: ").strip().lower()
        if ch == "q": break
        if ch == "s": status(out_dir, k); continue
        if ch == "c":
            input("Slots EMPTY? Enter to recalibrate.")
            ok = board.calibrate(); since_cal = 0
            print("  baselines (cm): " + ", ".join(f"slot {s}={board.baselines[s]:.1f}" for s in sorted(board.baselines)) if ok else "  calibration timed out")
            time.sleep(2); continue
        if not ch.isdigit() or not (1 <= int(ch) <= len(ORDER)):
            continue
        name = ORDER[int(ch) - 1]; sc = SCEN[name]

        if sc["slots"] == "all":
            slot = 0
        else:
            if sc["slots"] == "pair":      # never default: a wrong pair silently corrupts the ground truth
                slot = 0
                while slot not in range(1, k):
                    a_ = input(f"LEFT slot of the pair you will use (1-{k-1}) - TYPE it: ").strip()
                    slot = int(a_) if a_.isdigit() else 0
            else:
                sg = suggest_slot(cnt, name, k)
                ans = input(f"slot (1-{k}) [suggested {sg} - least covered]: ").strip()
                slot = int(ans) if ans.isdigit() else sg
        print(f"\n  {name}: {sc['steps']}")
        input("  Slots empty? Press Enter to START recording...")

        a = board.n_rows()
        marks = []
        print("  REC ...")
        t_start = time.time()
        for m in range(sc["marks"]):
            label = ["mark 1", "mark 2"][m] if sc["marks"] == 2 else "the event"
            input(f"    Enter at {label}: ")
            marks.append(board.last_ms())
        input("    Enter to STOP: ")
        rows = board.slice(a)
        print(f"  stopped after {time.time()-t_start:.0f} s.")
        if len(rows) < 10:
            print("  !! almost no data - trial discarded (is the board streaming?)"); continue

        base = [board.baselines.get(s + 1, float('nan')) for s in range(k)]
        quick_feedback(rows, k, base, slot if slot else 1, marks, sc)
        keep = input("  Keep this trial? [Enter = yes, d = discard]: ").strip().lower()
        if keep == "d":
            print("  discarded."); continue

        n = next_index(out_dir, name)
        base_path = os.path.join(out_dir, f"{name}_{n}")
        slot_field = slot if slot else 0
        exp = sc["exp"]
        with open(base_path + ".csv", "w") as f:
            f.write(f"# scenario={name},slot={slot_field},expect={exp}\n")
            f.write("ms," + ",".join(f"r{i+1}" for i in range(k)) + "," +
                    ",".join(f"t{i+1}" for i in range(k)) + "," +
                    ",".join(f"n{i+1}" for i in range(k)) + "\n")
            for ms, r, t, nn in rows:
                f.write(f"{ms}," + ",".join(f"{v:.1f}" for v in r) + "," +
                        ",".join(map(str, t)) + "," + ",".join(map(str, nn)) + "\n")
        truth = ["FREE"] * k
        if slot:
            truth[slot - 1] = sc["truth"]
            if sc["slots"] == "pair" and slot < k:
                truth[slot] = sc["truth"]        # mis-parked: both slots of the pair
        meta = dict(scenario=name, slot=slot_field, truth_per_slot=truth, expect=exp,
                    marks_ms=marks, baselines=base, first_after_cal=(since_cal == 0),
                    trials_since_cal=since_cal, session=session, room_temp_c=temp or None,
                    started=datetime.datetime.now().isoformat(timespec="seconds"), rows=len(rows))
        json.dump(meta, open(base_path + ".json", "w"), indent=1)
        since_cal += 1
        print(f"  saved {base_path}.csv (+.json)")
    print("Done. Data is in", out_dir)

if __name__ == "__main__":
    main()

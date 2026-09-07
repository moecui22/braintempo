#!/usr/bin/env python3
"""
braintempo - naturalistic typing-tempo tracking.

Watches how you type during ordinary work (not a typing test), reduces each
burst of typing to timing statistics, and compares today against your own
rolling baseline for the same time of day.

PRIVACY: keystroke *content* is never stored. Individual key events live only
in memory for the duration of one typing burst (seconds), are collapsed into
summary statistics, and are then discarded. The database contains no text.

Usage:
    python3 braintempo.py collect          # run the background collector
    python3 braintempo.py report           # today vs your baseline
    python3 braintempo.py report --date 2026-09-05
    python3 braintempo.py history          # last 30 days of the index
    python3 braintempo.py status           # is it collecting? how much baseline?
    python3 braintempo.py export out.csv   # per-day metrics as CSV
    python3 braintempo.py install-agent    # start automatically at login
    python3 braintempo.py uninstall-agent
"""

import argparse
import datetime as dt
import math
import os
import re
import signal
import sqlite3
import statistics
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

HOME = os.path.expanduser("~")
DATA_DIR = os.environ.get("BRAINTEMPO_DIR", os.path.join(HOME, ".braintempo"))
DB_PATH = os.path.join(DATA_DIR, "braintempo.db")
LOG_PATH = os.path.join(DATA_DIR, "collector.log")

# A "burst" is a run of typing. A gap longer than this ends it.
BURST_GAP_S = 3.0

# Bursts smaller/shorter than this are discarded: too noisy to characterise,
# and short bursts are where password and one-off entry live.
MIN_BURST_KEYS = 15
MIN_BURST_SECONDS = 2.0

# Inter-keystroke intervals above this are treated as thinking, not typing,
# and excluded from the rhythm statistics (but counted as hesitations).
PAUSE_S = 0.5
LONG_PAUSE_S = 1.0

# Never record while one of these is frontmost.
EXCLUDED_APPS = {
    "1password", "1password 7", "1password 8", "keychain access", "lastpass",
    "bitwarden", "dashlane", "loginwindow", "securityagent", "enpass",
    "keeper password manager", "nordpass", "proton pass",
}

# Time-of-day buckets. Typing tempo differs systematically across the day, so
# today's morning is only ever compared against past mornings.
BUCKETS = [
    ("night", 0, 5),
    ("morning", 5, 12),
    ("afternoon", 12, 17),
    ("evening", 17, 24),
]

# Metric key, label, direction (+1 = higher means sharper), weight in the index.
METRICS = [
    ("wpm", "Typing speed", +1, 0.30),
    ("iki_cv", "Rhythm steadiness", -1, 0.30),
    ("pause_rate", "Hesitations", -1, 0.25),
    ("bksp_rate", "Corrections", -1, 0.15),
]

BASELINE_WINDOW_DAYS = 28
MIN_BASELINE_DAYS = 5
# A day/bucket needs this much typing before it counts as a real reading.
MIN_DAY_KEYS = 400
MIN_DAY_BURSTS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS bursts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    REAL NOT NULL,
    ended_at      REAL NOT NULL,
    app           TEXT,
    n_content     INTEGER NOT NULL,
    n_backspace   INTEGER NOT NULL,
    n_nav         INTEGER NOT NULL,
    active_s      REAL NOT NULL,
    median_iki    REAL NOT NULL,
    iqr_iki       REAL NOT NULL,
    p90_iki       REAL NOT NULL,
    n_pause       INTEGER NOT NULL,
    n_long_pause  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bursts_started ON bursts(started_at);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Small statistics helpers
# ---------------------------------------------------------------------------

def quantile(sorted_vals, q):
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_vals[lo])
    frac = pos - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)


def robust_center_scale(values):
    """Median and a robust sigma (MAD-based), with sensible fallbacks."""
    vals = [v for v in values if v is not None and not math.isnan(v)]
    if not vals:
        return None, None
    med = statistics.median(vals)
    mad = statistics.median([abs(v - med) for v in vals])
    sigma = 1.4826 * mad
    if sigma <= 1e-9 and len(vals) > 1:
        try:
            sigma = statistics.stdev(vals)
        except statistics.StatisticsError:
            sigma = 0.0
    if sigma <= 1e-9:
        sigma = None
    return med, sigma


def bucket_for_hour(hour):
    for name, lo, hi in BUCKETS:
        if lo <= hour < hi:
            return name
    return "night"


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class FrontmostApp(object):
    """Cheap, cached lookup of the frontmost application's name."""

    def __init__(self, ttl=3.0):
        self.ttl = ttl
        self._name = None
        self._at = 0.0

    def get(self):
        now = time.time()
        if self._name is not None and now - self._at < self.ttl:
            return self._name
        self._at = now
        self._name = self._lookup()
        return self._name

    def _lookup(self):
        try:
            front = subprocess.check_output(
                ["lsappinfo", "front"], stderr=subprocess.DEVNULL, timeout=1.5
            ).decode("utf-8", "replace").strip()
            if not front:
                return "unknown"
            out = subprocess.check_output(
                ["lsappinfo", "info", "-only", "name", front],
                stderr=subprocess.DEVNULL, timeout=1.5,
            ).decode("utf-8", "replace")
            m = re.search(r'"LSDisplayName"\s*=\s*"([^"]*)"', out)
            if m:
                return m.group(1)
        except Exception:
            pass
        return "unknown"


class Collector(object):
    def __init__(self, conn, verbose=False):
        self.conn = conn
        self.verbose = verbose
        self.lock = threading.Lock()
        self.frontmost = FrontmostApp()
        self.stop_event = threading.Event()
        self._reset_burst()
        self.mods = set()
        self.total_bursts = 0

    # -- burst state ------------------------------------------------------
    def _reset_burst(self):
        self.b_start = None
        self.b_last = None
        self.b_app = None
        self.b_ikis = []        # intervals between consecutive counted keys
        self.b_content = 0
        self.b_backspace = 0
        self.b_nav = 0

    def _classify(self, key):
        """Return 'content', 'backspace', 'nav', or None (ignore).

        Only the *class* of the key is used. The character itself is never
        read into any stored structure.
        """
        from pynput import keyboard as kb

        # Anything with a command/control/option modifier is a shortcut,
        # not typing.
        if self.mods & {"cmd", "ctrl", "alt"}:
            return None

        if key in (kb.Key.backspace, kb.Key.delete):
            return "backspace"
        if key in (kb.Key.space, kb.Key.enter):
            return "content"
        if key in (kb.Key.left, kb.Key.right, kb.Key.up, kb.Key.down,
                   kb.Key.home, kb.Key.end, kb.Key.page_up, kb.Key.page_down,
                   kb.Key.tab):
            return "nav"
        ch = getattr(key, "char", None)
        if ch is not None and len(ch) == 1 and ch.isprintable():
            return "content"
        return None

    # -- listener callbacks ----------------------------------------------
    def on_press(self, key):
        from pynput import keyboard as kb

        name = _mod_name(key, kb)
        if name:
            self.mods.add(name)
            return

        kind = self._classify(key)
        if kind is None:
            return
        now = time.time()
        with self.lock:
            self._record(kind, now)

    def on_release(self, key):
        from pynput import keyboard as kb

        name = _mod_name(key, kb)
        if name:
            self.mods.discard(name)

    def _record(self, kind, now):
        if self.b_start is not None and now - self.b_last > BURST_GAP_S:
            self._close_burst(self.b_last)

        if self.b_start is None:
            app = self.frontmost.get()
            if app and app.strip().lower() in EXCLUDED_APPS:
                return
            self.b_start = now
            self.b_app = app
        else:
            self.b_ikis.append(now - self.b_last)

        self.b_last = now
        if kind == "content":
            self.b_content += 1
        elif kind == "backspace":
            self.b_backspace += 1
        else:
            self.b_nav += 1

    # -- burst closing ----------------------------------------------------
    def _close_burst(self, end_time):
        start, app = self.b_start, self.b_app
        ikis = self.b_ikis
        content, bksp, nav = self.b_content, self.b_backspace, self.b_nav
        self._reset_burst()

        if start is None:
            return
        duration = end_time - start
        if content < MIN_BURST_KEYS or duration < MIN_BURST_SECONDS:
            return

        typing_ikis = sorted(i for i in ikis if i <= PAUSE_S)
        if len(typing_ikis) < 5:
            return

        n_pause = sum(1 for i in ikis if i > PAUSE_S)
        n_long = sum(1 for i in ikis if i > LONG_PAUSE_S)
        # Active time excludes the thinking pauses, so speed is speed.
        active = sum(i for i in ikis if i <= PAUSE_S)
        if active <= 0.5:
            return

        row = (
            start, end_time, app,
            content, bksp, nav, active,
            quantile(typing_ikis, 0.5),
            quantile(typing_ikis, 0.75) - quantile(typing_ikis, 0.25),
            quantile(typing_ikis, 0.90),
            n_pause, n_long,
        )
        try:
            self.conn.execute(
                "INSERT INTO bursts (started_at, ended_at, app, n_content,"
                " n_backspace, n_nav, active_s, median_iki, iqr_iki, p90_iki,"
                " n_pause, n_long_pause) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", row)
            self.conn.commit()
            self.total_bursts += 1
            if self.verbose:
                wpm = (content / 5.0) / (active / 60.0)
                _log("burst  %-22s %4d keys  %5.1f wpm" % (app[:22], content, wpm))
        except Exception as exc:
            _log("db error: %s" % exc)

    def watchdog(self):
        """Close a burst once typing stops, even if no further key arrives."""
        while not self.stop_event.is_set():
            time.sleep(0.5)
            now = time.time()
            with self.lock:
                if self.b_start is not None and now - self.b_last > BURST_GAP_S:
                    self._close_burst(self.b_last)

    def flush(self):
        with self.lock:
            if self.b_start is not None:
                self._close_burst(self.b_last)


def _mod_name(key, kb):
    for base in ("cmd", "ctrl", "alt", "shift"):
        for variant in (base, base + "_l", base + "_r"):
            if key == getattr(kb.Key, variant, None):
                return base
    return None


def _log(msg):
    line = "%s  %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)


def cmd_collect(args):
    try:
        from pynput import keyboard as kb
    except ImportError:
        sys.stderr.write(
            "pynput is not installed.\n"
            "Run:  python3 -m pip install --user pynput\n")
        return 1

    conn = connect()
    collector = Collector(conn, verbose=not args.quiet)

    def shutdown(signum, frame):
        collector.stop_event.set()
        collector.flush()
        _log("stopped (%d bursts this session)" % collector.total_bursts)
        os._exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    threading.Thread(target=collector.watchdog, daemon=True).start()
    _log("collecting -> %s" % DB_PATH)
    _log("keystroke content is never stored; press Ctrl-C to stop")

    with kb.Listener(on_press=collector.on_press,
                     on_release=collector.on_release) as listener:
        listener.join()
    return 0


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def load_day_buckets(conn, since=None):
    """Aggregate bursts into (date, bucket) readings."""
    q = "SELECT started_at, app, n_content, n_backspace, n_nav, active_s," \
        " median_iki, iqr_iki, n_pause, n_long_pause FROM bursts"
    params = []
    if since is not None:
        q += " WHERE started_at >= ?"
        params.append(since)
    q += " ORDER BY started_at"

    groups = {}
    for row in conn.execute(q, params):
        started, app, content, bksp, nav, active, med, iqr, npause, nlong = row
        when = dt.datetime.fromtimestamp(started)
        key = (when.date().isoformat(), bucket_for_hour(when.hour))
        g = groups.setdefault(key, {
            "keys": 0, "bksp": 0, "nav": 0, "active": 0.0, "bursts": 0,
            "pause": 0, "long": 0, "med_w": 0.0, "cv_w": 0.0, "apps": {},
        })
        g["keys"] += content
        g["bksp"] += bksp
        g["nav"] += nav
        g["active"] += active
        g["bursts"] += 1
        g["pause"] += npause
        g["long"] += nlong
        # Burst-level rhythm stats, weighted by how much typing they describe.
        g["med_w"] += med * content
        g["cv_w"] += (iqr / med if med > 0 else 0.0) * content
        g["apps"][app or "unknown"] = g["apps"].get(app or "unknown", 0) + content

    readings = []
    for (date, bucket), g in sorted(groups.items()):
        if g["keys"] <= 0 or g["active"] <= 0:
            continue
        readings.append({
            "date": date,
            "bucket": bucket,
            "keys": g["keys"],
            "bursts": g["bursts"],
            "active_min": g["active"] / 60.0,
            "wpm": (g["keys"] / 5.0) / (g["active"] / 60.0),
            "median_iki": g["med_w"] / g["keys"],
            "iki_cv": g["cv_w"] / g["keys"],
            "pause_rate": 100.0 * g["pause"] / g["keys"],
            "long_pause_rate": 100.0 * g["long"] / g["keys"],
            "bksp_rate": 100.0 * g["bksp"] / g["keys"],
            "apps": g["apps"],
            "valid": g["keys"] >= MIN_DAY_KEYS and g["bursts"] >= MIN_DAY_BURSTS,
        })
    return readings


def _zscore(reading, key, stats):
    center, sigma, direction, _ = stats[key]
    if center is None or sigma is None:
        return None
    return max(-3.0, min(3.0, direction * (reading[key] - center) / sigma))


def _composite(reading, stats):
    """Weighted mean of the sign-aligned z-scores; higher means sharper."""
    acc = total = 0.0
    for key, _label, _dir, weight in METRICS:
        z = _zscore(reading, key, stats)
        if z is None:
            continue
        acc += z * weight
        total += weight
    return acc / total if total > 0 else None


def score_reading(target, history):
    """Compare one reading against same-bucket history. Returns dict or None."""
    tdate = dt.date.fromisoformat(target["date"])
    window = [
        r for r in history
        if r["bucket"] == target["bucket"] and r["valid"]
        and r["date"] != target["date"]
        and 0 < (tdate - dt.date.fromisoformat(r["date"])).days <= BASELINE_WINDOW_DAYS
    ]
    if len(window) < MIN_BASELINE_DAYS:
        return {"insufficient": True, "n_baseline": len(window), "window": window}

    stats, parts = {}, []
    for key, label, direction, weight in METRICS:
        center, sigma = robust_center_scale([r[key] for r in window])
        stats[key] = (center, sigma, direction, weight)
        parts.append({"key": key, "label": label, "value": target[key],
                      "baseline": center, "z": _zscore(target, key, stats),
                      "weight": weight})

    raw = _composite(target, stats)
    # Averaging four partly-independent z-scores shrinks the composite's
    # spread, so a raw value of -1.0 is NOT "one standard deviation down".
    # Re-express it in units of the composite's own day-to-day spread.
    hist = [h for h in (_composite(r, stats) for r in window) if h is not None]
    # Mean and SD, not median and MAD. Every z feeding the composite is already
    # clipped to +/-3, so outlier resistance is spent here; MAD only adds
    # estimator noise, and on a short window it runs low and inflates the index.
    if raw is None or len(hist) < 3:
        index = raw
    else:
        center2 = statistics.mean(hist)
        sigma2 = statistics.stdev(hist) if len(hist) > 1 else 0.0
        index = raw if sigma2 <= 1e-9 else max(-4.0, min(4.0, (raw - center2) / sigma2))

    return {
        "insufficient": False,
        "n_baseline": len(window),
        "parts": parts,
        "index": index,
        "raw": raw,
        "window": window,
    }


def band(index):
    if index is None:
        return "no reading"
    if index >= 1.5:
        return "well above your usual"
    if index >= 0.5:
        return "a little above your usual"
    if index > -0.5:
        return "typical for you"
    if index > -1.5:
        return "a little below your usual"
    return "well below your usual"


def app_mix_note(target, window):
    """Typing in code differs from typing in prose; flag a big context shift."""
    def share(apps):
        total = sum(apps.values()) or 1
        return {k: v / total for k, v in apps.items()}

    t = share(target["apps"])
    base = {}
    for r in window:
        for k, v in share(r["apps"]).items():
            base[k] = base.get(k, 0.0) + v / len(window)
    if not t or not base:
        return None
    top_app = max(t, key=t.get)
    delta = t[top_app] - base.get(top_app, 0.0)
    if abs(delta) >= 0.30:
        return "app mix shifted: %s was %.0f%% of typing vs %.0f%% usually" % (
            top_app, 100 * t[top_app], 100 * base.get(top_app, 0.0))
    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

BAR_W = 21


def zbar(z, scale=3.0):
    if z is None:
        return " " * BAR_W
    mid = BAR_W // 2
    pos = int(round(mid + max(-scale, min(scale, z)) / scale * mid))
    pos = max(0, min(BAR_W - 1, pos))
    cells = ["-"] * BAR_W
    cells[mid] = "|"
    cells[pos] = "#"
    return "".join(cells)


def cmd_report(args):
    conn = connect()
    readings = load_day_buckets(conn)
    if not readings:
        print("No typing recorded yet. Start the collector:\n"
              "    python3 braintempo.py collect")
        return 0

    date = args.date or dt.date.today().isoformat()
    buckets = [args.bucket] if args.bucket else [b[0] for b in BUCKETS]
    todays = [r for r in readings if r["date"] == date and r["bucket"] in buckets]
    todays = [r for r in todays if r["keys"] > 0]
    if not todays:
        print("No typing recorded for %s%s." % (
            date, " (%s)" % args.bucket if args.bucket else ""))
        return 0

    print("\nbraintempo - %s" % date)
    for target in sorted(todays, key=lambda r: [b[0] for b in BUCKETS].index(r["bucket"])):
        print("\n%s  (%d keys, %.0f min of active typing, %d bursts)" % (
            target["bucket"].upper(), target["keys"],
            target["active_min"], target["bursts"]))
        print("-" * 62)

        if not target["valid"]:
            print("  Not enough typing yet for a reading "
                  "(need >=%d keys and >=%d bursts)." % (MIN_DAY_KEYS, MIN_DAY_BURSTS))
            continue

        res = score_reading(target, readings)
        if res["insufficient"]:
            print("  Baseline still building: %d of %d %s sessions collected."
                  % (res["n_baseline"], MIN_BASELINE_DAYS, target["bucket"]))
            print("  Raw today: %.1f wpm, rhythm cv %.2f, %.1f hesitations and"
                  " %.1f corrections per 100 keys."
                  % (target["wpm"], target["iki_cv"],
                     target["pause_rate"], target["bksp_rate"]))
            continue

        for p in res["parts"]:
            unit = " wpm" if p["key"] == "wpm" else ""
            zs = "  n/a" if p["z"] is None else "%+5.2f" % p["z"]
            print("  %-18s %7.2f%-4s (usual %6.2f)  %s %s" % (
                p["label"], p["value"], unit, p["baseline"], zbar(p["z"]), zs))

        print("-" * 62)
        print("  INDEX %+.2f SD %s  %s   (baseline: %d %s sessions)" % (
            res["index"], zbar(res["index"], 4.0), band(res["index"]).upper(),
            res["n_baseline"], target["bucket"]))
        note = app_mix_note(target, res["window"])
        if note:
            print("  note: %s" % note)
    print()
    return 0


def cmd_history(args):
    conn = connect()
    readings = load_day_buckets(conn)
    bucket = args.bucket
    rows = [r for r in readings if r["valid"] and (not bucket or r["bucket"] == bucket)]
    if not rows:
        print("No valid readings yet.")
        return 0
    print("\nbraintempo history%s\n" % (" - %s" % bucket if bucket else ""))
    print("  date        bucket      wpm   cv    hes   corr   index")
    print("  " + "-" * 62)
    for r in rows[-args.n:]:
        res = score_reading(r, readings)
        idx = "  --  " if res["insufficient"] else "%+6.2f" % res["index"]
        print("  %-11s %-10s %5.1f  %4.2f  %4.1f  %4.1f  %s  %s" % (
            r["date"], r["bucket"], r["wpm"], r["iki_cv"],
            r["pause_rate"], r["bksp_rate"], idx,
            "" if res["insufficient"] else zbar(res["index"], 4.0)))
    print()
    return 0


def cmd_status(args):
    conn = connect()
    n_bursts = conn.execute("SELECT COUNT(*) FROM bursts").fetchone()[0]
    if n_bursts == 0:
        print("No data yet. Run:  python3 braintempo.py collect")
        return 0
    last = conn.execute("SELECT MAX(ended_at) FROM bursts").fetchone()[0]
    first = conn.execute("SELECT MIN(started_at) FROM bursts").fetchone()[0]
    age = time.time() - last
    readings = load_day_buckets(conn)
    valid = [r for r in readings if r["valid"]]

    print("\nbraintempo status")
    print("  database        %s" % DB_PATH)
    print("  bursts          %d" % n_bursts)
    print("  collecting since %s" % dt.datetime.fromtimestamp(first).strftime("%Y-%m-%d"))
    print("  last keystroke  %s (%s ago)" % (
        dt.datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M"),
        _human(age)))
    if age > 3600:
        print("                  ^ collector may not be running")
    print("\n  baseline readiness (need %d sessions per time of day):" % MIN_BASELINE_DAYS)
    today = dt.date.today()
    for name, _, _ in BUCKETS:
        recent = [r for r in valid if r["bucket"] == name and
                  0 <= (today - dt.date.fromisoformat(r["date"])).days <= BASELINE_WINDOW_DAYS]
        mark = "ready" if len(recent) >= MIN_BASELINE_DAYS else "building"
        print("    %-10s %2d / %d   %s" % (name, len(recent), MIN_BASELINE_DAYS, mark))
    print()
    return 0


def _human(seconds):
    if seconds < 90:
        return "%ds" % int(seconds)
    if seconds < 5400:
        return "%dm" % int(seconds / 60)
    if seconds < 172800:
        return "%.1fh" % (seconds / 3600.0)
    return "%.1f days" % (seconds / 86400.0)


def cmd_export(args):
    conn = connect()
    readings = load_day_buckets(conn)
    cols = ["date", "bucket", "keys", "bursts", "active_min", "wpm",
            "median_iki", "iki_cv", "pause_rate", "long_pause_rate",
            "bksp_rate", "valid"]
    with open(args.path, "w") as fh:
        fh.write(",".join(cols + ["index"]) + "\n")
        for r in readings:
            res = score_reading(r, readings) if r["valid"] else {"insufficient": True}
            idx = "" if res.get("insufficient") else "%.4f" % res["index"]
            vals = []
            for c in cols:
                v = r[c]
                vals.append("%.4f" % v if isinstance(v, float) else str(v))
            fh.write(",".join(vals + [idx]) + "\n")
    print("wrote %d rows to %s" % (len(readings), args.path))
    return 0


AGENT_LABEL = "com.braintempo.collector"
AGENT_PATH = os.path.join(HOME, "Library", "LaunchAgents", AGENT_LABEL + ".plist")

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>%(label)s</string>
  <key>ProgramArguments</key>
  <array>
    <string>%(python)s</string>
    <string>%(script)s</string>
    <string>collect</string>
    <string>--quiet</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>%(log)s</string>
  <key>StandardErrorPath</key><string>%(log)s</string>
</dict>
</plist>
"""


def cmd_install_agent(args):
    os.makedirs(os.path.dirname(AGENT_PATH), exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(AGENT_PATH, "w") as fh:
        fh.write(PLIST % {
            "label": AGENT_LABEL,
            "python": os.path.realpath(sys.executable),
            "script": os.path.realpath(__file__),
            "log": LOG_PATH,
        })
    subprocess.call(["launchctl", "unload", AGENT_PATH],
                    stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    rc = subprocess.call(["launchctl", "load", AGENT_PATH])
    print("installed %s (launchctl load -> %d)" % (AGENT_PATH, rc))
    print("If no data appears, grant Input Monitoring to:\n  %s"
          % os.path.realpath(sys.executable))
    return 0


def cmd_uninstall_agent(args):
    subprocess.call(["launchctl", "unload", AGENT_PATH],
                    stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    if os.path.exists(AGENT_PATH):
        os.remove(AGENT_PATH)
    print("removed %s" % AGENT_PATH)
    return 0


def main():
    p = argparse.ArgumentParser(
        prog="braintempo", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("collect", help="run the background collector")
    c.add_argument("--quiet", action="store_true", help="do not log each burst")
    c.set_defaults(func=cmd_collect)

    r = sub.add_parser("report", help="compare a day against your baseline")
    r.add_argument("--date", help="YYYY-MM-DD (default: today)")
    r.add_argument("--bucket", choices=[b[0] for b in BUCKETS])
    r.set_defaults(func=cmd_report)

    h = sub.add_parser("history", help="recent readings")
    h.add_argument("-n", type=int, default=30, help="rows to show")
    h.add_argument("--bucket", choices=[b[0] for b in BUCKETS])
    h.set_defaults(func=cmd_history)

    s = sub.add_parser("status", help="collector health and baseline readiness")
    s.set_defaults(func=cmd_status)

    e = sub.add_parser("export", help="write per-day metrics to CSV")
    e.add_argument("path")
    e.set_defaults(func=cmd_export)

    sub.add_parser("install-agent", help="start at login").set_defaults(
        func=cmd_install_agent)
    sub.add_parser("uninstall-agent", help="stop starting at login").set_defaults(
        func=cmd_uninstall_agent)

    args = p.parse_args()
    if not getattr(args, "func", None):
        p.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

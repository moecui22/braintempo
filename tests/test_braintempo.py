#!/usr/bin/env python3
"""Tests for braintempo. Standard library only; run with:

    python3 tests/test_braintempo.py

The privacy tests are the point of this file. braintempo claims it never
stores what you type; test_no_typed_content_reaches_disk types a known secret
through the real classification path and then greps the database file for it.
"""

import datetime as dt
import os
import random
import shutil
import statistics
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

TMP = tempfile.mkdtemp(prefix="braintempo-test-")
os.environ["BRAINTEMPO_DIR"] = TMP

import braintempo as bt  # noqa: E402

FAILURES = []
PASSES = []


def check(name, condition, detail=""):
    if condition:
        PASSES.append(name)
        print("  PASS  %s" % name)
    else:
        FAILURES.append((name, detail))
        print("  FAIL  %s %s" % (name, detail))


class Clock(object):
    """Controllable stand-in for time.time()."""

    def __init__(self, t0=1000000.0):
        self.t = t0

    def __call__(self):
        return self.t

    def advance(self, d):
        self.t += d
        return self.t


class FixedApp(object):
    def __init__(self, name):
        self.name = name

    def get(self):
        return self.name


def fresh(app="Notes"):
    """A collector on a clean database."""
    if os.path.exists(bt.DB_PATH):
        os.remove(bt.DB_PATH)
    conn = bt.connect()
    c = bt.Collector(conn, verbose=False)
    c.frontmost = FixedApp(app)
    return conn, c


def type_text(collector, clock, text, iki=0.18):
    """Drive the real on_press path with real pynput key objects."""
    from pynput import keyboard as kb
    for ch in text:
        clock.advance(iki)
        if ch == " ":
            collector.on_press(kb.Key.space)
        else:
            collector.on_press(kb.KeyCode.from_char(ch))


# ---------------------------------------------------------------------------

def test_no_typed_content_reaches_disk():
    """The headline privacy claim: no typed characters survive to the file."""
    secrets = [
        "hunter2correcthorsebatterystaple",
        "mypasswordisSw0rdfish",
        "4111111111111111",
    ]
    conn, c = fresh()
    clock = Clock()
    real_time = bt.time.time
    bt.time.time = clock
    try:
        for s in secrets:
            type_text(c, clock, s)
            clock.advance(10.0)   # gap ends the burst
        c.flush()
        conn.commit()
    finally:
        bt.time.time = real_time

    with open(bt.DB_PATH, "rb") as fh:
        blob = fh.read()

    leaked = [s for s in secrets if s.encode() in blob]
    check("no typed string appears in the database file",
          not leaked, "leaked: %s" % leaked)

    # Also check every substring of length 6, to catch partial storage.
    frags = set()
    for s in secrets:
        for i in range(len(s) - 5):
            frags.add(s[i:i + 6].encode())
    leaked_frags = [f for f in frags if f in blob]
    check("no 6-character fragment of typed text appears either",
          not leaked_frags, "leaked %d fragments" % len(leaked_frags))

    rows = conn.execute("SELECT COUNT(*) FROM bursts").fetchone()[0]
    check("the typing was in fact recorded (so the test is not vacuous)",
          rows == 3, "expected 3 bursts, got %d" % rows)


def test_schema_holds_no_text_column():
    conn, _ = fresh()
    cols = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(bursts)")}
    text_cols = [n for n, t in cols.items() if "CHAR" in t.upper() or "TEXT" in t.upper()]
    check("the only text column in the schema is the app name",
          text_cols == ["app"], "text columns: %s" % text_cols)


def test_password_manager_is_excluded():
    conn, c = fresh(app="1Password")
    clock = Clock()
    real_time = bt.time.time
    bt.time.time = clock
    try:
        type_text(c, clock, "a" * 60)
        c.flush()
        conn.commit()
    finally:
        bt.time.time = real_time
    n = conn.execute("SELECT COUNT(*) FROM bursts").fetchone()[0]
    check("60 keys typed in 1Password produce no row", n == 0,
          "got %d rows" % n)


def test_short_bursts_are_dropped():
    conn, c = fresh()
    clock = Clock()
    real_time = bt.time.time
    bt.time.time = clock
    try:
        type_text(c, clock, "a" * (bt.MIN_BURST_KEYS - 1))
        c.flush()
        conn.commit()
    finally:
        bt.time.time = real_time
    n = conn.execute("SELECT COUNT(*) FROM bursts").fetchone()[0]
    check("a burst below MIN_BURST_KEYS is discarded", n == 0,
          "got %d rows" % n)


def test_shortcuts_are_not_counted_as_typing():
    from pynput import keyboard as kb
    conn, c = fresh()
    clock = Clock()
    real_time = bt.time.time
    bt.time.time = clock
    try:
        c.on_press(kb.Key.cmd)
        type_text(c, clock, "s" * 40)      # 40x cmd-S, not typing
        c.on_release(kb.Key.cmd)
        c.flush()
        conn.commit()
    finally:
        bt.time.time = real_time
    n = conn.execute("SELECT COUNT(*) FROM bursts").fetchone()[0]
    check("keys pressed with cmd held are not recorded as typing", n == 0,
          "got %d rows" % n)


def test_thinking_pauses_excluded_from_speed():
    conn, c = fresh()
    t = 5000.0
    for i in range(60):
        t += 0.18
        c._record("content", t)
        if i in (20, 40):
            t += 2.0          # a long think, below BURST_GAP_S
            c._record("content", t)
    c.flush()
    conn.commit()
    row = conn.execute("SELECT n_content, active_s, n_pause FROM bursts").fetchone()
    keys, active, pauses = row
    wall = 62 * 0.18 + 2 * 2.0
    check("thinking pauses are excluded from active typing time",
          active < wall - 3.5, "active=%.2fs wall=%.2fs" % (active, wall))
    check("the two long pauses are counted as hesitations",
          pauses == 2, "n_pause=%d" % pauses)


def _synth_day(conn, date, hour=8, dw=0.0, dcv=0.0, dp=0.0, db=0.0, nb=12):
    """One day of bursts, with a day-level random effect."""
    e_w = random.gauss(0, 5.0) + dw
    e_cv = random.gauss(0, 0.045) + dcv
    e_p = random.gauss(0, 0.010) + dp
    e_b = random.gauss(0, 0.012) + db
    for i in range(nb):
        start = dt.datetime.combine(date, dt.time(hour, 0)) + dt.timedelta(minutes=15 * i)
        ts = start.timestamp()
        keys = random.randint(40, 160)
        med = max(0.08, 0.185 - e_w * 0.0012 + random.gauss(0, 0.010))
        cv = max(0.10, 0.42 + e_cv + random.gauss(0, 0.05))
        active = med * keys
        n_pause = max(0, int(keys * max(0.001, 0.030 + e_p) * random.uniform(0.7, 1.3)))
        n_bksp = max(0, int(keys * max(0.001, 0.055 + e_b) * random.uniform(0.7, 1.3)))
        conn.execute(
            "INSERT INTO bursts (started_at, ended_at, app, n_content, n_backspace,"
            " n_nav, active_s, median_iki, iqr_iki, p90_iki, n_pause, n_long_pause)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, ts + active + n_pause * 0.8, "Notes", keys, n_bksp, 4, active,
             med, med * cv, med * 1.9, n_pause, n_pause // 3))


def test_index_is_calibrated_and_directional():
    """Ordinary days must sit near 0 with SD near 1, and planted days must move."""
    random.seed(11)
    if os.path.exists(bt.DB_PATH):
        os.remove(bt.DB_PATH)
    conn = bt.connect()
    today = dt.date(2026, 9, 7)
    for d in range(30, 1, -1):
        _synth_day(conn, today - dt.timedelta(days=d))
    _synth_day(conn, today - dt.timedelta(days=1),
               dw=-11, dcv=0.13, dp=0.028, db=0.022)     # planted rough day
    _synth_day(conn, today, dw=+8, dcv=-0.07, dp=-0.014, db=-0.016)  # good day
    conn.commit()

    readings = bt.load_day_buckets(conn)
    scored = {}
    for r in readings:
        if not r["valid"]:
            continue
        res = bt.score_reading(r, readings)
        if not res["insufficient"]:
            scored[r["date"]] = res["index"]

    ordinary = [v for d, v in scored.items() if d < "2026-09-06"]
    check("at least 20 ordinary days are scored",
          len(ordinary) >= 20, "got %d" % len(ordinary))
    mean = statistics.mean(ordinary)
    sd = statistics.pstdev(ordinary)
    check("ordinary days centre near zero (|mean| < 0.6)",
          abs(mean) < 0.6, "mean=%+.2f" % mean)
    check("ordinary days have unit-ish spread (0.6 < SD < 1.6)",
          0.6 < sd < 1.6, "SD=%.2f" % sd)
    check("the planted rough day scores below every ordinary day",
          scored["2026-09-06"] < min(ordinary),
          "rough=%+.2f min ordinary=%+.2f" % (scored["2026-09-06"], min(ordinary)))
    check("the planted good day scores positive",
          scored["2026-09-07"] > 0.5, "good=%+.2f" % scored["2026-09-07"])


def test_baseline_refuses_to_score_too_early():
    random.seed(5)
    if os.path.exists(bt.DB_PATH):
        os.remove(bt.DB_PATH)
    conn = bt.connect()
    today = dt.date(2026, 9, 7)
    for d in range(3, 0, -1):        # only 3 prior days
        _synth_day(conn, today - dt.timedelta(days=d))
    _synth_day(conn, today)
    conn.commit()
    readings = bt.load_day_buckets(conn)
    target = [r for r in readings if r["date"] == today.isoformat()][0]
    res = bt.score_reading(target, readings)
    check("with 3 baseline days it declines to produce an index",
          res["insufficient"] is True, "res=%s" % res.get("index"))


def test_morning_is_not_compared_against_evening():
    random.seed(9)
    if os.path.exists(bt.DB_PATH):
        os.remove(bt.DB_PATH)
    conn = bt.connect()
    today = dt.date(2026, 9, 7)
    for d in range(20, 0, -1):
        _synth_day(conn, today - dt.timedelta(days=d), hour=20)  # evenings only
    _synth_day(conn, today, hour=8)                              # one morning
    conn.commit()
    readings = bt.load_day_buckets(conn)
    target = [r for r in readings if r["bucket"] == "morning"][0]
    res = bt.score_reading(target, readings)
    check("20 evenings do not serve as a baseline for a morning",
          res["insufficient"] is True, "res=%s" % res.get("index"))


TESTS = [
    ("PRIVACY", [
        test_no_typed_content_reaches_disk,
        test_schema_holds_no_text_column,
        test_password_manager_is_excluded,
        test_short_bursts_are_dropped,
        test_shortcuts_are_not_counted_as_typing,
    ]),
    ("MEASUREMENT", [
        test_thinking_pauses_excluded_from_speed,
    ]),
    ("SCORING", [
        test_index_is_calibrated_and_directional,
        test_baseline_refuses_to_score_too_early,
        test_morning_is_not_compared_against_evening,
    ]),
]


def main():
    try:
        import pynput  # noqa: F401
    except ImportError:
        print("pynput is required for the privacy tests: "
              "python3 -m pip install --user pynput")
        return 2
    for group, tests in TESTS:
        print("\n%s" % group)
        for t in tests:
            t()
    print("\n%d passed, %d failed" % (len(PASSES), len(FAILURES)))
    for name, detail in FAILURES:
        print("  FAILED: %s %s" % (name, detail))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())

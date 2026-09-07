# braintempo

Measures how you type during **ordinary work** — not a typing test — and compares
today against your own rolling baseline for the same time of day.

No typing test means no practice effect to correct for, which is the main reason
test-based "cognitive speed" trackers drift upward over weeks and stop meaning
anything.

## What it stores, and what it does not

**It never stores what you type.** Individual key events exist only in memory for
the length of one typing burst (seconds). They are collapsed into summary
statistics, and the keys themselves are discarded. The database has no column
that could hold text.

There is **no dictionary, no spell check, and no grammar check.** Nothing is ever
compared against a word list. The "corrections" metric is purely a count of
backspace presses — it never looks at what was deleted or what was typed.

Also skipped entirely:
- Anything typed while a password manager or Keychain is frontmost (`EXCLUDED_APPS`).
- Any burst shorter than 15 keys or 2 seconds — which is where one-off entry and
  password typing live.
- Anything typed with ⌘/⌃/⌥ held down (those are shortcuts, not typing).

Everything stays in `~/.braintempo/braintempo.db` on your machine. Nothing is
sent anywhere; the program makes no network calls.

## Verification

The privacy claim above is tested, not asserted. `tests/test_braintempo.py`
types three known secrets — including a card-shaped number — through the real
key-classification path, then greps the database file for them and for every
6-character fragment of them.

```bash
python3 tests/test_braintempo.py
```

16 checks, all passing as of 2026-09-07:

| Group | What is checked |
|---|---|
| Privacy | No typed string, and no 6-character fragment of one, reaches the database file. The schema's only text column is the app name. Typing in 1Password produces no row. Bursts under 15 keys are discarded. Keys pressed with ⌘ held are not recorded. |
| Measurement | Thinking pauses are excluded from active typing time, and counted as hesitations instead. |
| Scoring | On 30 synthetic days, ordinary days centre near 0 with SD near 1. A planted rough day scores below every ordinary day. The scorer refuses to produce an index from 3 baseline days, and refuses to use 20 evenings as a baseline for a morning. |

The privacy test also asserts that the typing was recorded at all, so it cannot
pass by silently doing nothing.

## Setup

Already installed: `pynput` (the keyboard listener).

Run the collector from Terminal:

```bash
python3 braintempo.py collect
```

The first run will prompt for **Input Monitoring** permission. Grant it to
**Terminal** in System Settings → Privacy & Security → Input Monitoring, then run
the command again. Leave it running while you work; Ctrl-C stops it.

To have it start automatically at login instead:

```bash
python3 braintempo.py install-agent
```

(`uninstall-agent` reverses this. If no data shows up after a login-start, grant
Input Monitoring to the Python binary that `install-agent` printed.)

## Daily use

```bash
python3 braintempo.py report      # today vs your baseline
python3 braintempo.py status      # is it running? how much baseline so far?
python3 braintempo.py history     # last 30 readings
python3 braintempo.py export out.csv
```

Nothing useful appears for about a week — it needs **5 sessions in the same time
of day** before it will score anything. `status` shows the countdown.

## What it measures

A *burst* is a run of typing with no gap longer than 3 seconds. Each burst is
reduced to four numbers, then bursts are pooled per day and time-of-day window
(morning 05–12, afternoon 12–17, evening 17–24, night 00–05).

| Metric | What it is | Direction |
|---|---|---|
| Typing speed | keys ÷ 5 per minute of *active* typing (thinking pauses excluded) | higher = sharper |
| Rhythm steadiness | spread of the gaps between keystrokes, relative to the typical gap | steadier = sharper |
| Hesitations | pauses over 0.5 s, per 100 keys | fewer = sharper |
| Corrections | backspace presses per 100 keys | fewer = sharper |

Rhythm steadiness and hesitations carry most of the signal. Raw speed is the
noisiest of the four, which is why it is only 30% of the index.

## How the index is computed

1. Today's morning is compared **only against past mornings** — tempo varies
   systematically across the day.
2. Baseline is the last 28 days, so slow drift in your typing is absorbed rather
   than mistaken for a good or bad day.
3. Median and MAD are used instead of mean and SD, so one unusual day does not
   distort the baseline.
4. Each metric becomes a z-score, sign-aligned so positive always means sharper,
   clipped at ±3.
5. The weighted average of those four is then **re-standardised against its own
   day-to-day spread**, so the final number really is "how unusual is today, in
   units of your own normal variation."

Read `+1.5` as *well above your usual*, `0` as *typical*, `-1.5` as *well below*.

## The honest caveat

This measures typing tempo. Typing tempo is a real and reasonably sensitive
index of **psychomotor speed and momentary alertness** — the same family of
things a reaction-time task picks up, and it does move with sleep loss, fatigue,
and time of day.

It is **not** a measure of "brain power" in any broader sense. It will not track
reasoning, memory, creativity, or mood, and a low reading is at least as likely
to mean *"I was typing in an unfamiliar app"* or *"I was composing rather than
transcribing"* as anything about your brain. The report flags the first of those
when your app mix shifts sharply.

Treat it as one noisy signal about alertness, most useful as a trend across
weeks rather than as a verdict on any single day.

## Tuning

The knobs are constants at the top of `braintempo.py`:
`BURST_GAP_S`, `MIN_BURST_KEYS`, `PAUSE_S`, `EXCLUDED_APPS`, `BUCKETS`,
`METRICS` (labels, directions, weights), `BASELINE_WINDOW_DAYS`,
`MIN_BASELINE_DAYS`.

To drop a metric entirely, set its weight to `0.0` in `METRICS`.

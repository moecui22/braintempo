# braintempo

A no-punctuation typing test that measures your speed, your correct speed and
your rhythm, then compares today against **your own baseline** for that time of
day.

Most typing tests tell you how you rank against other people. This one only ever
compares you to yourself, because the interesting question is not "am I fast" but
"am I slower than usual today, and by how much".

**[Play it here.](https://moecui22.github.io/braintempo/)**

## The test

Type the words as they scroll. No punctuation, no capitals — the format that
measures letter-typing speed without punctuation pauses getting in the way.
15, 30 or 60 seconds. A daily challenge gives everyone the same word list for
that date.

While you type it tracks a combo, awards XP, levels you up, and keeps a
day streak. There are twelve achievements. None of that changes the
measurement; it exists so that you actually come back tomorrow, which is the
only way a baseline ever gets built.

## What it measures

| Measure | What it is | Direction |
|---|---|---|
| Net WPM | Correct characters ÷ 5, per minute | higher is sharper |
| Raw WPM | Every character you typed ÷ 5, per minute | context for the above |
| Accuracy | Correct keystrokes as a share of all keystrokes | higher is sharper |
| Rhythm steadiness | Spread of the gaps between keystrokes, relative to the typical gap | steadier is sharper |
| Hesitations | Pauses over 0.5 s, per 100 keys | fewer is sharper |

Rhythm and hesitations carry most of the signal. Raw speed is the noisiest of
them, which is why it is weighted at 30% rather than everything.

There is no spell check and no grammar check anywhere in this. Correctness is
decided character by character against the word on screen, and nothing is
compared against a dictionary.

## How today gets scored

1. Today is compared **only against the same time of day**. Typing tempo moves
   across the day, so a morning run is never scored against your evenings.
2. The baseline is your last 30 runs in that window, from earlier days. Runs from
   today never score themselves.
3. Median and MAD are used instead of mean and standard deviation, so one strange
   day cannot bend the baseline.
4. Each measure becomes a z-score, sign-aligned so positive always means sharper,
   clipped at ±3.
5. The weighted average of those four is then rescaled against **its own**
   day-to-day spread. Averaging four partly-independent z-scores shrinks the
   composite, so without this step every reading is understated.

Read `+1.5` as a sharp day, `0` as typical, `-1.5` as a slow one. Nothing is
scored until you have 5 runs from earlier days in that time window, which takes
about a week.

A run needs at least 60 characters and 15 measurable keystroke gaps to count.
Anything shorter is shown but kept out of your baseline, bests and achievements —
too little typing cannot describe a rhythm.

## Where the data lives

In your browser's `localStorage`, on your machine. No account, no server, no
analytics, no network requests of any kind. Export to CSV or JSON at any time,
and erase everything with one button.

Clearing your browser data clears your history with it, so export if you care
about it.

## The honest caveat

This measures typing tempo, which is a real and reasonably sensitive index of
**psychomotor speed and momentary alertness** — the family of things a
reaction-time task picks up. It does move with sleep loss, fatigue and time of
day.

It is not a measure of "brain power" in any broader sense. It will not track
reasoning, memory, creativity or mood. A low reading is at least as likely to
mean you were distracted, or typing on an unfamiliar keyboard, as anything about
your brain.

One thing to watch specifically: a typing *test* has a practice curve that
naturalistic typing does not. Your first fortnight will trend upward because you
are learning the format, not because you are getting sharper. The 30-run rolling
baseline absorbs that drift rather than reading it as good days.

Treat it as one noisy signal about alertness, read across weeks rather than as a
verdict on any single day.

## Also in here: the background collector

`braintempo.py` is a different way to get the same measurements: it watches your
typing during ordinary work instead of asking you to take a test. That removes
the practice curve entirely, at the cost of needing Input Monitoring permission.

It never stores what you type. Key events live in memory for one burst, become
timing statistics, and are discarded. `tests/test_braintempo.py` types known
secrets through the real classification path and greps the database for them.

```bash
python3 -m pip install --user pynput
python3 braintempo.py collect     # from Terminal, then grant Input Monitoring
python3 braintempo.py report
python3 tests/test_braintempo.py  # 16 checks
```

The two share the scoring method but not a database. Use whichever you will
actually keep up.

## Layout

| Path | What it is |
|---|---|
| `docs/index.html` | The whole web app. One file, no dependencies, no build step. |
| `braintempo.py` | The background collector and its CLI reports. |
| `tests/test_braintempo.py` | Privacy, measurement and scoring tests for the collector. |

# ⌨️ braintempo

**A typing test that only ever compares you to yourself.**

Most typing tests rank you against strangers. This one answers the more useful
question: *am I slower than usual today, and by how much?* 🧠

### 👉 [**Play it here**](https://moecui22.github.io/braintempo/)

---

## 🎮 How it works

Type the words as they scroll. No punctuation, no capitals. 15, 30 or 60 seconds.

Along the way: a combo counter, XP, levels, a day streak, 12 achievements, and a
daily challenge that gives everyone the same words. None of it touches the
measurement — it just makes you come back tomorrow, which is the only way a
baseline ever gets built. 🔥

## 📊 What it measures

| | Measure | Direction |
|---|---|---|
| ⚡ | **Net WPM** — correct characters ÷ 5, per minute | higher is sharper |
| 🎯 | **Accuracy** — correct keystrokes as a share of all | higher is sharper |
| 🎵 | **Rhythm steadiness** — spread of your keystroke gaps | steadier is sharper |
| ⏸️ | **Hesitations** — pauses over 0.5 s, per 100 keys | fewer is sharper |

Rhythm and hesitations carry most of the signal. Raw speed is the noisiest, so
it is weighted at 30% rather than everything.

No spell check, no grammar check, no dictionary anywhere. Correctness is decided
character by character against the word on screen.

## 🧮 How today gets scored

1. 🕐 Compared **only against the same time of day** — your morning is never
   scored against your evenings.
2. 📅 Baseline is your last 30 runs in that window, from **earlier days**. Today
   never scores itself.
3. 🛡️ Median and MAD, not mean and SD, so one strange day cannot bend it.
4. ➕ Each measure becomes a z-score, sign-aligned so positive always means
   sharper, then rescaled against its own day-to-day spread.

Read **`+1.5`** as a sharp day, **`0`** as typical, **`-1.5`** as a slow one.

⏳ Nothing is scored until you have 5 runs from earlier days — about a week.
Runs under 60 characters do not count.

## 🔒 Your data

Stays in your browser's `localStorage`. No account, no server, no analytics,
**zero network requests**. Export to CSV or JSON, or erase it all, any time.

Clearing your browser data clears your history with it.

## ⚠️ The honest caveat

Typing tempo is a real index of **psychomotor speed and momentary alertness** —
it does move with sleep loss, fatigue and time of day.

It is **not** a measure of "brain power". It will not track reasoning, memory or
creativity. A low reading is at least as likely to mean you were distracted or
on an unfamiliar keyboard.

One thing to watch: a typing *test* has a practice curve. Your first fortnight
will trend upward because you are learning the format, not because you are
getting sharper. The rolling 30-run baseline absorbs that drift.

📈 Read it across weeks, not as a verdict on any single day.

## 🐍 Bonus: the background collector

`braintempo.py` measures the same things from your **ordinary typing** instead of
a test — no practice curve, at the cost of an Input Monitoring permission.

It never stores what you type. Keystrokes live in memory for one burst, become
timing statistics, and are discarded. The tests prove it: they type known secrets
through the real code path, then grep the database for them.

```bash
python3 -m pip install --user pynput
python3 braintempo.py collect     # run from Terminal, then grant Input Monitoring
python3 braintempo.py report
python3 tests/test_braintempo.py  # 16 checks ✅
```

## 🎨 Design

Follows the **Linear** design language via
[voltagent/awesome-design-md](https://github.com/voltagent/awesome-design-md).
`DESIGN.md` is that file plus a note on three forced deviations.

Chart colours were run through a colour-blindness and contrast validator before
use — worst-case separation 25.7 under simulated protanopia, well clear. 🌈

## 📁 Layout

| Path | What it is |
|---|---|
| `docs/index.html` | The whole web app. One file, no dependencies, no build. |
| `braintempo.py` | The background collector and its CLI reports. |
| `tests/test_braintempo.py` | Privacy, measurement and scoring tests. |
| `DESIGN.md` | The design system the interface follows. |

MIT

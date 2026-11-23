# Game rules (`quiz.py`)

Pure Python; the only I/O is `Bank.load`. Every time-dependent call takes `now`.

## Constants

| Name | Value | Meaning |
|---|---|---|
| `ROUND_LENGTH` | 10 | Questions per non-endless round; also the batch size for endless |
| `TIME_LIMIT` | 20.0 s | Per question (timed rounds) |
| `GRACE` | 1.5 s | Slack before a timed answer counts as late (so late = elapsed > 21.5 s) |
| `MIN_HUMAN` | 0.4 s | Faster answers get no speed bonus |
| `ENDLESS_STRIKES` | 3 | Misses that end an endless run |
| `ENDLESS_MAX` | 300 | Hard stop for an endless run |
| `SEEN_MAX` | 800 | Recently-seen ids used for picking |
| `BASE_POINTS` | `{1: 100, 2: 125, 3: 150}` | By difficulty |
| `SPEED_MAX` | 50 | Speed bonus ceiling (reached at `MIN_HUMAN`, see below) |
| `STREAK_STEP` / `STREAK_CAP` | 10 / 50 | Streak bonus |
| `PLAN` | `[1,1,2,1,2,2,3,2,3,3]` | Target difficulty per slot |
| `MODES` | quick, category, region, daily, endless | |

## `Bank`

- `pool(mode, cat, region)`: `category` -> questions with that `cat` (unknown -> `ValueError`);
  `region` -> questions with that `cont` (unknown -> `ValueError`); every other mode -> all
  questions. Phrasebook questions (`cont: null`) never appear in region rounds.
- `meta()`: `source`, `count`, categories with their counts (empty ones hidden), and regions
  with `count >= 3 * ROUND_LENGTH` (30). Category and region pools are rebuilt by list scan on
  every new round (no index).

## Picking: `select(pool, n, rng, seen, avoid_ids, avoid_keys, start, mix_categories)`

1. Category cycle: the pool's categories, shuffled, repeated to length n (or all `None` if not
   mixing). This gives a spread of categories in quick/daily/region/endless rounds.
2. Three tiers, worked out once: **unseen** (not in the browser's seen list), **old** (the
   seen questions seen longest ago: the older half, at least n), then **any**.
3. For each slot: target difficulty `plan_for(start + slot)` (PLAN for slots 0-9; then 2 until
   slot 14; afterwards 3 except every third slot is 2). Candidates exclude used ids and any
   question whose group key `k` is already used. Within the first tier that has a match, try
   (category + difficulty), (difficulty only), (category only), (anything), and pick randomly.
4. If no candidate is left at all, the slot (and the rest) is skipped: `select` can return
   fewer than n.

Seen order: the list is oldest first, newest last (`seen_rank` higher = more recent).

## Daily flight

`daily_questions(bank, date)` = `select(bank.questions, 10, Random("wayfarer-daily-<date>"))`:
the same ten for everyone on that date, independent of seen lists. Only the question set is
shared: each `Round` shuffles option order with its own RNG, so A/B/C/D positions differ
between players. A browser can play the daily flight more than once; the board keeps each
name's best (the page only remembers the last daily score locally).

## `Round`

Created with `mode, cat, region, timed, seen, date, rng, now`:
- unknown mode -> `RoundError(400, "Unknown mode.")`; bad cat/region ->
  `RoundError(400, "Unknown category." / "Unknown region.")`; empty pick ->
  `RoundError(400, "No questions for that.")`.
- `cat` kept only for category mode, `region` only for region mode, `date` only for daily.
- category rounds use `mix_categories=False`; endless starts with 10 and grows.
- `total` = number of questions (None for endless).
- `board`: `daily:<date>`, `cat:<id>`, `region:<continent>`, or the mode (`quick`/`endless`).
  `label` from `board_label()` ("Quick round", "Daily flight 2026-11-22", "Category: Capitals",
  "Region: Asia", "Endless").

### `current(now)`

Raises 409 "This round is over." if done. For question `n = len(answers)`: on endless,
`_ensure(n)` appends another batch of 10 (avoiding all used ids, and group keys of the last 60
questions; if none are left, a second try only avoids the last 40 ids, i.e. repeats allowed)
up to `ENDLESS_MAX`. First serve of `n` shuffles option order (`orders[n]`, a permutation of
indices into `[a, *d]`) and stamps `served_at[n] = now`. Serving again returns the same order
and does **not** restart the clock. Returns `n, id, q, options, cat, cat_name, diff, timed,
limit, remaining` (None if untimed), `results` (right/wrong of the answered questions, for
the page's dots after a reload; always `[]` in endless) plus `status()`. No `a`, `d`, `src`,
`path` or `x`.

### `answer(n, choice, now)`

- 409 if done, if `n` isn't the current index, or if `n` was never served.
- `choice` must be None or an int 0-3 (bools rejected) -> else 400 "Pick one of the four answers."
- `right = order.index(0)`; `late = timed and elapsed > TIME_LIMIT + GRACE`;
  `correct = choice is not None and choice == right and not late`.
- Correct: streak +1, best_streak updated. Wrong/late/timeout: streak 0; endless: strikes +1.
- Points from `points()` added to score; the answer record keeps id, cat, correct, points,
  timeout (choice None **or** late), elapsed.
- Done: endless when strikes reach 3 or `_ensure(n+1)` can't extend (300 cap); otherwise when
  every question is answered.
- Returns `n, correct, late, timeout (choice is None only), answer (index), answer_text,
  points {base, speed, streak, total}, source {title, path}, extra` plus `status()`.

### `status()` / `summary()`

`status`: score, streak, best_streak, strikes, answered, total, done.
`summary`: id, mode, board, label, timed, date, score, right, answered, accuracy (rounded %),
best_streak, strikes, per-category `{cat, name, right, total}` in first-seen order, done,
`can_submit` (done, not submitted, answered > 0) and `share` text. Summary works mid-round too.

## Scoring: `points(correct, diff, elapsed, streak_after)`

- Wrong: all zeros.
- base = 100/125/150 by difficulty.
- speed = `round(50 * (20 - elapsed) / (20 - 0.4))` only if `0.4 <= elapsed < 20`, else 0:
  50 at 0.4 s, falling linearly to 0 at 20 s. Applies to untimed rounds too (fast answers
  still score more).
- streak bonus = `min(50, 10 * (streak_after - 1))`: 0 for the first right answer, 10 for the
  second in a row, ..., capped at 50 from the sixth.
- Example: diff 1, 10 s, first in streak = 100 + 26 + 0 = 126 (a test checks this).

## Share text

`"Wayfarer · <label>\n<line>\n<marks>"`, marks are green/red squares for the first 10 answers
(first 50 in endless). Endless line: "N right before three strikes · S pts · best streak B"
when it ended on strikes; "N/M right · S pts · best streak B" when it stopped at the 300 cap
(or is still going).

## `Rounds` (live round store)

`add` prunes idle rounds then evicts the oldest (insertion/LRU order) until under `cap`.
`get` prunes, raises `RoundError(404, "That round has expired. Start a new one.")` if
missing, moves the round to the end (LRU) and sets its `touched` to now, so every API call on
a round (including the end screen's `summary` and `submit`) keeps it alive. `prune` drops
rounds idle (by `touched`) longer than `ttl`.
No locking: all callers are `async` handlers without awaits in between, so the single event
loop serialises them.

# Gotchas and how to extend

## Gotchas and quirks (as the code stands)

Rounds and scoring:
- **Everything about a round is in memory.** A container restart (or an update) ends every
  round in progress; the page then gets 404 "That round has expired" and goes home.
- **Single process only.** `Rounds` has no lock and relies on the async handlers not
  awaiting mid-update. Don't add uvicorn `--workers` (rounds would live in one worker and
  the leaderboard file would be written by several).
- **Speed bonus** is 50 at `MIN_HUMAN` (0.4 s), linear down to 0 at 20 s; nothing under
  0.4 s (too fast to have read the question).
- **Speed bonus also applies to untimed rounds** (under 20 s). The toggle text says so.
- **Late vs timeout** differ between places: the `answer` response's `timeout` is only
  "choice was null"; the stored answer record's `timeout` is null **or** late. Both count as
  a miss.
- **Daily flight is replayable** and options are shuffled per round; only the question set is
  shared. The board keeps each name's best.
- **Endless share text** says "before three strikes" only when it ended on strikes; at the
  300 cap it says "N/300 right".
- Endless batches avoid group keys only of the last 60 questions, so a long run can bring
  back the reverse of an early question (e.g. both capital directions).
- `Rounds.get` refreshes `Round.touched`, so any call keeps a round alive (`ROUND_TTL` is an
  idle timeout). A round is still lost on a container restart.
- Category and region pools are rebuilt by scanning all questions per new round, and
  `select` rescans per slot; fine on a PC, **not measured on the R6S**.

Bank:
- `select` can return fewer than 10 questions for a tiny pool; `total` then reflects that.
  The current bank has every offered pool >= 30.
- Regions need >= 30 questions to be offered; Antarctica (2) isn't. Phrasebook questions
  have no continent, so they never appear in region rounds.
- Question ids include the sorted distractors for `population`, `drive` and `voltage`
  (`build.py:596`), so for those kinds a change of distractors gives a new id (CLAUDE.md and
  bank/README.md say so).
- `build.py` must stay importable without libzim: the tests import `DISPUTED` from it.
- The ZIM name `wikivoyage_en_all_nopic_2026-09.zim` is hard-coded in `bank/README.md` and
  `.vscode/tasks.json`.
- Facts are a snapshot (2026-09-13); currencies, codes and borders change.

Page:
- **Resume is per tab**: a reload picks the round up (`sessionStorage`), a new tab doesn't;
  "Leave this round" (and the logo, mid-round) abandons it. Going to AirplaneMode with the
  house button and back in the same tab resumes it. A reload during the verdict skips to the
  next question.
- The client timer fires `choose(null)` at its own 0; the server has 1.5 s grace on top.
- On any non-404 answer error the page calls `next()`, re-serving the same question.
- `markSeen` happens when a question is served, not answered.
- The boot error says "Pull down to retry" (pull-to-refresh), there is no retry button.

Repo:
- `.vscode/tasks.json` is strict JSON: backslashes in strings must be doubled
  (`.venv\\Scripts\\python`), as the Windows test task now does.
- `bank/__pycache__/` exists locally (git-ignored).

## How to extend

### A new kind of question (inside an existing category)
1. Add a generator in `bank/build.py` (or extend one), calling `self.add(kind, cat, q, a, d,
   src_rec, fame, cont, key, extra)`. Pick a group key `k` that ties it to related questions
   (e.g. `cap:<country>`) so they don't share a round.
2. If it's an "odd one out" kind (several equally valid ids for the same text), add it to the
   sorted-distractors list in `finish()` and to the answer-in-question exceptions in `sane()`.
3. Add a `CAPS` entry if it can produce many questions (most famous first).
4. Never offer `DISPUTED` names; keep `SKIP_KEYS` in mind.
5. Run `python bank/build.py generate`, read the counts and dropped numbers, spot-check, run
   tests, commit the bank and the code together. Quality over quantity: drop a kind rather
   than ship noisy questions.

### A new category
- Add it to `CATEGORIES` in `build.py` (id, name, blurb). The app reads categories from the
  bank, so no app change is needed, **except** the page's icon list (`catIcon` in
  `static/index.html` falls back to the "places" pin) and a new `<symbol id="c-<id>">`.
- Test `test_bank_sanity` requires each category to have >= 100 questions.
- A new category id is a new leaderboard board `cat:<id>` automatically.

### A new mode
- `quiz.MODES`, `Round.__init__` (pool and picking), `Round.board`, `board_label`,
  `app.valid_board`, and possibly `api/leaderboards`. Add a home pass in `home()` and handle
  it in `hudHtml` and `end()`'s "Play again". Add a test to `test_bots_play_every_mode_to_the_end`.

### Change scoring or timing
- Constants at the top of `quiz.py`. Update `test_points` (exact values), the About sheet
  text and the README "How to play".
- Keep the clock on the server (`clock()` passed in); never trust client timing.

### A one-line explanation per answer from the shared model (CLAUDE.md "Ideas")
- Would make it `needs: llm: optional` with `x` as fallback. Not started. The platform rule
  (decision 0017) is that `uses_llm` apps are hidden on the lite profile, so keep Wayfarer
  working without a model if this is ever done.

### New env var
- Read it at the top of `app.py`, document it in `app.yaml` `env:` and the README table,
  and set it in the platform `compose.yaml` if the box needs a non-default.

### Before shipping
- Tests pass; play the page in the platform dev stack at phone size (including the guide
  link if the guide is running); retake `docs/screenshots/` if the UI changed; mark anything
  not verified on real phones/hardware as *untested*; ask before pushing a `v*` tag.

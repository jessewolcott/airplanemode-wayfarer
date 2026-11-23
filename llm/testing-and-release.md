# Testing, dev setups, build, CI and release

## Tests (`tests/test_wayfarer.py`)

A plain script, not pytest: `.venv\Scripts\python tests\test_wayfarer.py` (or
`python tests/test_wayfarer.py`). Needs `fastapi` + `uvicorn` from `requirements.txt` plus
`httpx` for `TestClient` (CI pins `httpx==0.28.1`). Python 3.10+. Runs every `test_*`
function in definition order, prints `ok`/`FAIL`, exits 1 on any failure.

Setup: sets `DATA_DIR` to a fresh temp dir **before** importing `app`, so the real bank is
loaded and the leaderboard goes to a temp `scores.json`. Replaces `app.clock` with a manual
`Clock` (starts at 1000, `tick(s)`). One shared `TestClient`. `right_index(q)` is a "bot that
knows the bank": it finds the answer by looking the served id up in `BANK.by_id`.
`tiny_bank()` builds a 72-question bank (3 cats x 2 continents x 3 difficulties x 4, pairs
sharing a group key) for exact picking tests.

| Test | Checks |
|---|---|
| `test_bank_file_is_small_and_has_a_source` | File < 2,000,000 bytes; `source.zim` ends `.zim`, date format, licence CC BY-SA 4.0 |
| `test_bank_sanity` | Required keys, unique ids, known cat, diff 1-3, cont known or None, 4 distinct trimmed non-empty options, answer not in distractors, no markup, >= 3000 questions, >= 6 categories each >= 100 |
| `test_no_answer_spelt_out_in_country_questions` | For 6 kinds the answer isn't a substring of the question |
| `test_disputed_places_stay_out` | No `DISPUTED` name as an option, no "(country)" anywhere, no `cap:Israel` / `place:Jerusalem` keys |
| `test_category_and_region_filters` | Pools filter correctly; unknown cat/region raise `ValueError` |
| `test_no_repeats_in_a_round` | No repeated id or group key (tiny bank and real bank) |
| `test_mixed_rounds_spread_categories_and_follow_the_ramp` | >= 6 categories and difficulties exactly `PLAN` (seed 5) |
| `test_daily_is_stable_and_changes_by_date` | Same date same ids, next date differs, seen list ignored |
| `test_unseen_questions_come_first` | Unseen first; all seen -> picks from the oldest half |
| `test_round_uses_seen_list_from_the_browser` | Second capitals round avoids the first's ids |
| `test_points` | Exact point values incl. the 0 s, `MIN_HUMAN` (50), just under 20 s (0), monotonic speed, slow and streak cases |
| `test_round_scoring_and_streaks` | 9/10 right with a miss at 5: best streak 5, score sums |
| `test_health_meta_and_page` | `/health`, `/api/meta`, relative-URL rules on the page, icon served |
| `test_questions_go_out_without_answers` | No answer keys in `next`; re-serve keeps order and clock |
| `test_resume_after_a_reload` | Re-asking `next` after an answer: same question and options, clock not restarted, `results` [True], no answer keys; endless sends `results: []` |
| `test_answer_validation` | 409 before serving / wrong n / twice; 400 bad choice; 422 non-int; 404 unknown round; 400 bad mode/cat/region |
| `test_timer_is_enforced_on_the_server` | Late (> 21.5 s) = wrong, 0 points; untimed slow = right, no speed; null = timeout |
| `test_full_round_summary_and_leaderboard` | Submit before done 409; perfect round summary; blank name 400; name cleaned to <= 16; board shows server score; second submit 409; `next` after done 409; bad board 404; home boards order quick/daily/endless |
| `test_finished_round_stays_alive_while_its_end_screen_is_used` | Summary and submit refresh the idle timer; a really idle round still 404s |
| `test_meta_reports_a_failed_leaderboard_write` | A failed write turns `leaderboard_saved` false in `/api/meta` |
| `test_endless_ends_after_three_strikes` | 23 right + 3 wrong = 26 answers, done, no repeats, share says "23 right before three strikes" |
| `test_rounds_expire_and_are_capped` | Cap evicts oldest; TTL expiry -> 404 |
| `test_bots_play_every_mode_to_the_end` | 3 seeds x every mode, category and offered region, random skill/timing/timeouts: always finishes, score = sum of points, 10 answers (non-endless), no repeats |
| `test_endless_long_run_never_crashes` | Perfect endless run stops at exactly `ENDLESS_MAX` (300); share text says 300/300, not "three strikes" |
| `test_store_persists_and_keeps_best_per_name` | Ranks, case-insensitive best-per-name, reload from disk, 50-row cap, 14 daily boards kept, empty name `ValueError` |
| `test_store_without_a_volume_or_with_a_bad_file` | In-memory mode; damaged file -> empty, then rewritten |
| `test_clean_name` | Whitespace, length, RTL override, None |

Shared state: the API tests share one app, `rounds` and leaderboard. Order mostly doesn't
matter, but they are run as a sequence; keep new tests self-contained.

Not covered: the page's JavaScript (timer, rendering, local storage, guide link), and
`bank/extract.py` / `build.py` beyond the properties of their output. Check the page by hand.

## Dev setups

1. **Standalone** (`compose.yaml`, project `wayfarer-dev`): `docker compose up -d --build`
   -> `http://localhost:8773` (`PORT` overrides), named volume `wayfarer-data`, `mem_limit: 64m`.
   No admin service and no guide, so `../api/apps` fails and no guide link shows.
   VS Code tasks: "Standalone: up", "Standalone: down", "Tests: Wayfarer" (default test task).
2. **Platform dev stack** (the real test bed): from `../airplanemode`,
   `docker compose -f compose.yaml -f compose.dev.yaml up -d --build` builds this repo as
   `airplanemode-wayfarer:dev`; open `http://localhost:8080/wayfarer/`. With the guide (and
   its ZIM) running, the "Read it in the Travel Guide" link appears.

Port 8000 is reserved by Windows on Jesse's machine, hence 8773.

## Docker image

`Dockerfile`: `python:3.12-slim`, install `requirements.txt`, `COPY . .`, create user
`wayfarer` (uid 10001) and `/data` owned by it, `USER wayfarer`, `VOLUME /data`,
`EXPOSE 8000`, `uvicorn app:app --host 0.0.0.0 --port 8000`.

`.dockerignore` keeps out: `.git`, `.github`, `.vscode`, `.venv`, `.cache`, `.scratch`,
`tests`, `**/__pycache__`, `**/*.md`, `compose.yaml`, `docs`, `bank/*.py`, `llm`. So the image
holds `app.py`, `quiz.py`, `store.py`, `static/`, `bank/questions.json.gz`, `app.yaml`,
`requirements.txt`, `Dockerfile`, `.gitattributes`, `.gitignore`, `.dockerignore`. (`**/*.md`
rather than `*.md`: Docker matches `*.md` against the context root only, which let
`bank/README.md` in.)

## CI (`.github/workflows/image.yml`)

On push to `main`, tags `v*`, and pull requests: checkout, Python 3.12, `pip install -r
requirements.txt httpx==0.28.1`, run the tests, then QEMU + buildx and build
`linux/arm64,linux/amd64`. Pushes (not on PRs) to `ghcr.io/<owner>/airplanemode-wayfarer`
with tags `main` (branch) and `X.Y.Z` (semver from the tag). Actions: checkout@v7,
setup-python@v7, setup-qemu@v4, setup-buildx@v4, login@v4, metadata@v6, build-push@v7.

## Releasing

1. Tests pass; check the page in the platform dev stack at phone size; retake
   `docs/screenshots/` if the UI changed.
2. If the bank changed: regenerate per question-bank.md and commit the `.gz` with the code.
3. **Ask Jesse**, then push `vX.Y.Z`. CI publishes `ghcr.io/jessewolcott/airplanemode-wayfarer:X.Y.Z`.
4. In `../airplanemode`: bump the pinned tag in `compose.yaml` (never `latest`); CI there
   checks image pin + Caddy route + `portal/apps.json` entry. Update the status in both
   CLAUDE.md files.

Released: 0.1.0 (tag `v0.1.0`; commits "Wayfarer 0.1.0", "Fix sideways scroll on desktop",
"Add screenshots and mark 0.1.0 as released"). Pinned in the platform's `compose.yaml`.

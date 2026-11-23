# HTTP API (`app.py`)

FastAPI with `docs_url`, `redoc_url` and `openapi_url` all off. Routes are rooted at `/`
(Caddy strips `/wayfarer`). No authentication, no state-changing header check and no rate
limiting: anyone on the hotspot can create rounds and submit finished ones (decision 0016:
the hotspot password is the access control).

`quiz.RoundError` is turned into `{"detail": message}` with its status by an exception
handler. `HTTPException` gives the same `detail` shape. Pydantic validation failures give 422
with FastAPI's list-shaped `detail`; the page shows "Something went wrong." for non-string
details.

## Endpoints

### `GET /health`
`{"ok": true, "questions": 5398}`.

### `GET /api/meta`
`bank.meta()` plus `daily` (today, `YYYY-MM-DD`, box local time), `round_length` (10),
`time_limit` (20.0), `strikes` (3), `leaderboard_saved` (`store.persistent`, read at request
time; a property, so it turns false once a failed write has switched the store to memory).

```json
{"source": {...}, "count": 5398,
 "categories": [{"id": "places", "name": "Where in the world", "blurb": "...", "count": 1242}, ...],
 "regions": [{"id": "Africa", "count": 609}, ...],
 "daily": "2026-11-22", "round_length": 10, "time_limit": 20.0, "strikes": 3, "leaderboard_saved": true}
```

### `POST /api/rounds`
Body (`NewRound`): `mode` (default `"quick"`), `cat` (<= 40 chars), `region` (<= 40 chars),
`timed` (default true), `seen` (list of strings, at most `SEEN_MAX * 2` = 1600 items; more ->
422). Each seen id is cut to 16 chars; `Round` keeps the last 800.
Response: `{id, mode, board, label, timed, total, limit, strikes_max}` (`total` null for endless).
Errors: 400 unknown mode / category / region / no questions.

### `POST /api/rounds/{rid}/next`
The current question (see quiz-logic.md `current`). First call starts its clock; repeat calls
return the same question and the time left, plus `results` (see quiz-logic.md). This is
also how the page resumes a round after a reload. 404 expired/unknown round; 409 "This round
is over." when done (the page treats 409 as "go to the end screen").

### `POST /api/rounds/{rid}/answer`
Body (`Answer`): `n` (int), `choice` (int 0-3 or null = ran out of time). Returns the verdict
(see quiz-logic.md `answer`). 409 wrong `n`, not yet served, already answered or round over;
400 choice out of range; 422 choice not an int.

### `GET /api/rounds/{rid}`
The summary (score, right, answered, accuracy, best_streak, strikes, categories, done,
can_submit, share, ...). Works for unfinished rounds too.

### `POST /api/rounds/{rid}/submit`
Body (`Submit`): `name` (<= 64 chars before cleaning; longer -> 422). Checks in this order:
409 "Finish the round first." (not done), 409 "This round is already on the board.",
409 "Nothing to put on the board." (0 answered), then `clean_name` (16 chars max) -> 400
"Type a name for the board." if empty. Then `store.add(board, name, score, right, answered,
best_streak)` with the **server's** score, sets `submitted`, and returns
`{board, label, rank, name, rows}` (rows = top 10 of that board). `rank` can be None if the
name fell off the 50-row board (or its board was pruned).

### `GET /api/leaderboard?board=quick`
Top 10 rows of one board. `valid_board` accepts `quick`, `endless`, `daily:<any 10 chars>`,
`cat:<known category id>`, `region:<known continent>`; anything else -> 404 "No such board."
Response `{board, label, rows}`; rows are `{name, score, right, answered, streak, at, rank}`.

### `GET /api/leaderboards`
The home screen's three boards, top 5 each, in this order: `quick`, `daily:<today>`, `endless`.
`{"boards": [{board, label, rows}, ...]}`.

### Page and static
- `GET /` -> `static/index.html` with `Cache-Control: no-cache`.
- `/static/*` -> `StaticFiles(static/)` (the page uses `static/icon.svg`).

## What must never be in a `next` response

The test `test_questions_go_out_without_answers` asserts that none of `"a"`, `"d"`,
`"answer"`, `"src"`, `"path"`, `"x"` appear as JSON keys in a served question. The `id` is
sent (the browser needs it for its seen list); a client could look ids up in the public bank
file, but that is accepted: scoring and timing are still server-side.

## Things the API does not do

- Resume is per browser tab only (the page keeps the round in `sessionStorage`); a round
  can't be moved to another tab or device, and a container restart loses it.
- No per-player identity: names are free text, best score per case-insensitive name.
- No admin endpoints (no way to clear or edit the leaderboard except editing
  `/data/scores.json` while the app is stopped).

# Architecture

## The pieces

```
 offline, on a dev machine                         on the box
 ------------------------                          ----------
 Wikivoyage ZIM ──extract.py──> .cache/facts.jsonl.gz
                                      │
                               build.py generate
                                      │
                                      v
                     bank/questions.json.gz  ── baked into the image ──> quiz.Bank (in memory)
                                                                             │
 phone browser ── Caddy /wayfarer/* (prefix stripped) ──> uvicorn app:app ──┤ quiz.Rounds (in memory)
                                                                             │
                                                                   store.Store ──> /data/scores.json
```

- **Bank**: loaded once at import time (`app.py:42`, `quiz.Bank.load(BANK_PATH)`). Holds all
  questions, `by_id`, `cat_names`, `continents` and `source`. Loading raises `BankError` on
  duplicate ids, which stops the app from starting.
- **Rounds**: `quiz.Rounds(ttl, cap)`, an `OrderedDict` of live `quiz.Round` objects keyed by a
  random `secrets.token_urlsafe(12)` id. Forgotten after `ROUND_TTL` idle seconds (default
  3 h) and capped at `MAX_ROUNDS` (default 500, oldest evicted). Nothing about a round is
  persisted; a restart ends every round in progress.
- **Store**: the leaderboard, one JSON file `DATA_DIR/scores.json` (see leaderboard-store.md).
- **Clock**: `app.clock = time.monotonic`; every round call passes `clock()` in. Tests replace
  `app.clock` with a fake.
- **Daily date**: `app.today()` is the container's local date (`datetime.now().astimezone()`),
  so `TZ` on the container decides when the daily flight changes.

## Request flow for one round

1. Page boots: `GET api/meta` (categories, regions, source, today's date, constants) and,
   in the background, `GET ../api/apps` + `GET ../guide/health` to find the Travel Guide.
2. Player picks a mode: `POST api/rounds` with `{mode, cat, region, timed, seen}`. The server
   picks the questions now (except endless, which extends itself 10 at a time).
3. `POST api/rounds/{id}/next` returns question `n` with options in a per-round random order
   and starts that question's clock (only the first time it's served).
4. `POST api/rounds/{id}/answer` with `{n, choice}` (0-3 or null). Server checks timing,
   updates score/streak/strikes and returns the right index, answer text, points breakdown,
   source article and optional extra note.
5. Repeat 3-4 until `done`. Then `GET api/rounds/{id}` for the summary and optionally
   `POST api/rounds/{id}/submit` with a name to go on that round's board.

All routes are rooted at `/` because Caddy's `handle_path /wayfarer/*` strips the prefix; the
page uses only relative URLs.

## How it sits in the platform

From `../airplanemode` (checked against the platform repo, not this one):
- `compose.yaml`: service `wayfarer`, image `ghcr.io/jessewolcott/airplanemode-wayfarer:0.1.0`,
  `DATA_DIR=/data`, `TZ=${TZ:-UTC}`, volume `wayfarer-data:/data`,
  `mem_limit: ${WAYFARER_MEM:-64m}`, `restart: unless-stopped`. No `profiles`, so it runs on
  every hardware profile including GL.iNet lite.
- `compose.dev.yaml`: builds `../airplanemode-wayfarer` as `airplanemode-wayfarer:dev`.
- `Caddyfile`: `redir /wayfarer /wayfarer/` and `handle_path /wayfarer/* { reverse_proxy wayfarer:8000 }`.
- `portal/apps.json`: id `wayfarer`, path `/wayfarer/`, category `Games`, `uses_llm: false`,
  icon `/icons/wayfarer.svg`.
- Wayfarer calls two things outside itself, both from the **browser**, both optional:
  `../api/apps` (admin service menu) and the guide's `health` endpoint. The backend makes no
  outbound calls at all.

## Repo layout

```
app.py  quiz.py  store.py          backend
static/index.html  static/icon.svg frontend
bank/questions.json.gz             data (committed, shipped)
bank/build.py  bank/extract.py     generator (not shipped: .dockerignore drops bank/*.py)
bank/README.md                     generator docs
tests/test_wayfarer.py
Dockerfile  requirements.txt  compose.yaml  app.yaml
docs/icon.svg  docs/screenshots/{home,question,answer}.png
.cache/ (git-ignored: facts.jsonl.gz)   .scratch/ (git-ignored: ad-hoc scripts)
```

## Configuration (env vars actually read)

| Var | Default | Read in | Notes |
|---|---|---|---|
| `DATA_DIR` | `/data` | `app.py:40` | Empty or unwritable -> leaderboard in memory only |
| `BANK_PATH` | `bank/questions.json.gz` next to `app.py` | `app.py:39` | `.gz` suffix -> gzip, else plain JSON |
| `ROUND_TTL` | `10800` (3 h) | `app.py:44` | Idle seconds before a round is forgotten (any call on it refreshes it) |
| `MAX_ROUNDS` | `500` | `app.py:44` | Live rounds kept (LRU eviction) |
| `TZ` | container default (UTC) | via `today()` | When the daily flight changes |

## Image and memory

`python:3.12-slim`, `fastapi==0.115.6`, `uvicorn==0.34.0`, runs as uid 10001 (`wayfarer`),
`VOLUME /data` (created and chowned in the image so a new named volume inherits the owner),
port 8000, single uvicorn process (no `--workers`). CLAUDE.md records ~40 MB RssAnon with the
bank loaded and 40 rounds live, on amd64; the limit is 64m. arm64 (R6S) memory and the speed
of picking a round (several list scans over ~5k questions per slot) are **not measured**.

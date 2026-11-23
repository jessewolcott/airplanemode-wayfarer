# Wayfarer: how it's built (LLM / engineer reference)

Checked-in notes, written from the code (release 0.1.0 plus the fixes after it, 2026-11-22).
`.dockerignore` lists `llm`, so none of this reaches the image. If the code and these notes
disagree, the code wins; fix the notes.

## Overview

Wayfarer is an AirplaneMode app: single-player, phone-first travel trivia that runs fully
offline on the travel router. Its ~5,400 four-option questions (5,398 in the 0.1.0 bank) are
**generated ahead of time** from a Wikivoyage ZIM (the same Kiwix file the Travel Guide
serves) by `bank/build.py` + `bank/extract.py`, and committed as `bank/questions.json.gz`
(~0.31 MB gzipped, ~1.42 MB raw JSON). The image ships that file; the app never needs the
ZIM, the guide or the shared model (`needs: llm: none`).

The backend is FastAPI in three small modules: `quiz.py` (the bank, question picking, rounds,
scoring; pure, the clock is passed in), `store.py` (the shared leaderboard, one JSON file in
`DATA_DIR`) and `app.py` (HTTP routes, ~160 lines). **The server holds the answers**: a
question goes out as text plus four shuffled options, the browser sends back an index, and the
server scores it with its own clock. Leaderboard entries only come from rounds the server
played to the end, once each. Rounds live in memory (lost on restart); only scores persist.

Modes: Quick round (10 mixed questions on a difficulty ramp), Daily flight (the same 10
questions for everyone on the box that day), Endless (until three misses, capped at 300),
Category and Region (continent) rounds. The whole UI is one file, `static/index.html`
(~620 lines of HTML/CSS/vanilla JS). When the Travel Guide is installed, each answer links to
its source article there.

## Files in this folder

| File | What it covers |
|---|---|
| [architecture.md](architecture.md) | The pieces, request flow, platform wiring, repo layout, config, memory |
| [question-bank.md](question-bank.md) | The bank file format and how `bank/extract.py` + `bank/build.py` make it; regenerating |
| [quiz-logic.md](quiz-logic.md) | `quiz.py` in depth: picking, the difficulty ramp, seen lists, daily, rounds, timing, scoring |
| [api.md](api.md) | `app.py`: every endpoint, request/response shapes, validation and errors |
| [leaderboard-store.md](leaderboard-store.md) | `store.py`: boards, best-per-name, caps, pruning, persistence, name cleaning |
| [game-page.md](game-page.md) | `static/index.html`: screens, timer, local storage, Travel Guide link, keyboard |
| [testing-and-release.md](testing-and-release.md) | Tests, dev setups, Docker image, CI, releasing |
| [gotchas-and-extending.md](gotchas-and-extending.md) | Non-obvious behaviour, doc mismatches, and how to add things safely |

## Source map

| Path | Role |
|---|---|
| `app.py` | FastAPI app: routes, request models, round store, page |
| `quiz.py` | Game rules: `Bank`, `select`, `daily_questions`, `points`, `Round`, `Rounds` |
| `store.py` | Leaderboard (`Store`, `clean_name`) |
| `static/index.html` | The whole frontend |
| `static/icon.svg` | Favicon (served at `static/icon.svg`) |
| `bank/questions.json.gz` | The generated, committed question bank (do not hand-edit) |
| `bank/extract.py` | Stage 1: ZIM -> `.cache/facts.jsonl.gz` (needs libzim + lxml) |
| `bank/build.py` | Stage 2 (and CLI for both): facts -> `bank/questions.json.gz` |
| `bank/README.md` | How the bank is made and regenerated |
| `tests/test_wayfarer.py` | Bank, picking, scoring, API and store tests (plain script) |
| `Dockerfile`, `requirements.txt`, `.dockerignore` | Image |
| `compose.yaml` | Standalone dev on port 8773 |
| `app.yaml` | AirplaneMode app manifest |
| `.github/workflows/image.yml` | CI: tests, then multi-arch image to GHCR |
| `.vscode/tasks.json` | Standalone up/down, tests, "Bank: regenerate from the guide's ZIM" |
| `CLAUDE.md`, `README.md` | Rules and the user-facing description |
| `../airplanemode/docs/decisions/0016-games-and-couch.md` | The design decision (shared with Gate Change, Passport, The Couch) |

## The rules that must not break (from `CLAUDE.md`)

1. **The server holds the answers.** `next` sends question text and shuffled options only;
   `answer` takes an index and scores with the server's clock (timed: late = wrong; under
   `MIN_HUMAN` s = no speed bonus). Leaderboard entries only from finished rounds, once each.
   Tests check no answer field leaks.
2. The bank is generated, never hand-edited: change `build.py`, regenerate, check counts and a
   sample, run the tests, commit both.
3. Keep `DISPUTED` places out (not asked, not offered). Drop a kind of question rather than
   ship noisy ones.
4. Question ids are hashes of kind + text + answer (+ the sorted distractors for `population`,
   `drive` and `voltage`), so browsers' "seen" lists survive rebuilds.
5. Page: relative URLs only (`api/...`); the guide link is `../guide/z/<zim>/<path>`, shown
   only when `../api/apps` lists `guide`. No external assets (the CC licence link is just a link).
6. Leaderboard: `DATA_DIR/scores.json`, written atomically, capped (50 per board, 14 daily
   boards). Works in memory with no volume.
7. Memory limit 64m (~40 MB RssAnon measured on amd64). Not measured on the R6S.
8. Ask Jesse before pushing a `v*` release tag.

Nothing here has been verified on real phones or on router hardware.

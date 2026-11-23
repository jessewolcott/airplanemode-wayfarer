# Wayfarer (AirplaneMode app): notes for Claude

Single-player travel trivia. Questions are generated offline from a Wikivoyage ZIM by
`bank/build.py` (+ `bank/extract.py`) into `bank/questions.json.gz`, which is committed and
shipped in the image. `quiz.py` is the game (bank, picking, rounds, scoring; pure, the clock is
passed in), `store.py` the leaderboard, `app.py` the FastAPI app, `static/index.html` the page.

Part of AirplaneMode: the platform repo is `../airplanemode`. Read its `CLAUDE.md` for the
environment notes and `docs/app-contract.md` for the rules. The ZIM comes from the Travel Guide's
volume (`../airplanemode-guide`), but the app itself never needs the guide.

## Commands

```powershell
docker compose up -d --build                  # standalone -> http://localhost:8773
.venv\Scripts\python tests\test_wayfarer.py   # tests (CI runs them before building)
python bank\build.py generate                 # rebuild the bank from .cache\facts.jsonl.gz
```

Regenerating from a ZIM (stage 1 needs libzim, Python 3.11+, so Docker): see `bank/README.md`
or the VS Code task "Bank: regenerate from the guide's ZIM". In Git Bash set `MSYS_NO_PATHCONV=1`.

## Rules

- **The server holds the answers.** `next` sends the question text and shuffled options only;
  `answer` takes an index, scores it with the server's clock (timed rounds: late = wrong; under
  `MIN_HUMAN` s = no speed bonus). Leaderboard entries come only from finished rounds, once each.
  Tests check that no answer field leaks.
- The bank is generated, never hand-edited. Change `build.py`, regenerate, check the printed
  counts and a sample, run the tests (they include a bank sanity check), commit both.
- Keep `DISPUTED` places out (not asked, not offered). Quality over quantity: drop a kind of
  question rather than ship noisy ones.
- Question ids are hashes of kind + text + answer (plus the sorted wrong answers for the "odd
  one out" kinds `population`, `drive` and `voltage`, whose text alone isn't unique): keep it
  that way so browsers' "seen" lists survive rebuilds.
- Page: relative URLs only (`api/...`; the guide link is `../guide/z/<zim>/<path>` and only shown
  when `../api/apps` lists `guide`). No external assets; the CC licence link is just a link.
- Leaderboard: `DATA_DIR/scores.json`, written atomically, capped (50 per board, 14 daily boards).
  Works in memory with no volume.
- Memory limit 64m (measured ~40 MB RssAnon with the bank loaded and 40 rounds live, on amd64).

## Ideas, not done

- The shared model could write a one-line explanation for each answer (`needs: llm: optional`
  then, with the current `x` note as the fallback). Kept simple for now: `llm: none`.
- More kinds: dishes from "Eat" sections, UNESCO sites, "Other destinations" lists, if they can
  be extracted cleanly.

## Not verified yet

- Memory and speed on the R6S (arm64); picking a round is ~5k-question list scans, fast on a PC.
- Real phones. The page was played in a phone-sized desktop browser through the platform's dev
  stack (Caddy), including the Travel Guide link.

## Releasing

Push a `vX.Y.Z` tag. CI tests, then publishes `ghcr.io/jessewolcott/airplanemode-wayfarer:X.Y.Z`
(arm64 + amd64). **Ask Jesse before pushing a release tag.** Released: 0.1.0 (pinned in the platform's `compose.yaml`).

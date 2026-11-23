# The page (`static/index.html`)

One file, ~620 lines: CSS (lines ~10-208), an inline SVG sprite (`<symbol>` icons, category
icons and the two verdict stamps), and one `<script>` of vanilla JS. Dark only
(`color-scheme: dark`), travel look (boarding-pass "pass" buttons, paper question card,
postmark, stamps). No external assets or fonts (system font stacks). Built for phones:
`--col: min(100%, 560px)`, 44 px+ tap targets, safe-area insets, `prefers-reduced-motion`
turns off animations.

## State and helpers

- `S = {meta, round, q, last, timer, guide, busy, results}`: in memory. `S.round` (the
  `POST api/rounds` response) is also kept in `sessionStorage` `wayfarer.round` (helper
  `live.get/set/clear`): set in `start()`, cleared in `home()`. On boot, if it's there, the
  page sets `S.round` and calls `next()` instead of `home()`: the server re-serves the current
  question with its clock still running and `results` for the dots; 409 -> end screen;
  404 (expired, restarted) -> toast and home. A reload during an answer's verdict skips to
  the next question. Per tab only.
- `api(path, body, method)`: GET without a body, POST with JSON; `cache: "no-store"`; on
  non-2xx throws an `Error` whose message is the string `detail`, "Something went wrong."
  for non-string details (422), or "The box didn't answer." with no JSON; `e.status` is set.
- `esc()` HTML-escapes everything inserted into `innerHTML` (question text, options, names,
  labels, source titles).
- `store.get/set` wrap `localStorage` with the `wayfarer.` prefix, JSON-encoded, failures
  swallowed.

## localStorage keys

| Key | Content |
|---|---|
| `wayfarer.seen` | Up to 800 question ids, oldest first; an id is moved to the end when served (`markSeen` in `next()`) |
| `wayfarer.timed` | Timer toggle (default true) |
| `wayfarer.daily` | `{date, score}` of the last finished daily round (shown on the Daily pass) |
| `wayfarer.name` | Last name posted to a board (prefills the form, highlights "me" rows) |
| `wayfarer.tab` | Selected home leaderboard tab |

## Screens

**Boot**: `findGuide()` (not awaited) and `GET api/meta` -> resume the saved round (above)
or `home()`. If meta fails:
"Couldn't reach the game on this box. Pull down to retry."

**Home** ("Departures"): Quick round (hero pass, "Gate Q10"), Daily flight (shows today's
score and a tick if `wayfarer.daily.date` is today; still playable again), Endless ("Lives 3"),
category chips (from `meta.categories`, blurb as tooltip), region chips (from `meta.regions`
with counts), "Cabin settings" timer toggle, and a leaderboard box with tabs for the three
boards from `api/leaderboards` (tab labels: "Quick", "Daily", "Endless").

**Question**: HUD (mode label or "Daily flight", `NN/10` or `Q n` for endless, streak with a
flame that gets "hot" at >= 2, score), endless shows three plane "lives" (greyed per strike),
others show 10 progress dots (right/wrong from `S.results`, current one white). Timer bar if
timed. Paper card with category icon + name and difficulty dots (filled = `diff`). Four
option buttons labelled A-D. "Leave this round" asks `confirm()` and just goes home (the
round is abandoned on the server, never submitted).

**Timer**: `startTimer(remaining, limit)` uses `requestAnimationFrame` and
`performance.now()`, starting from the server's `remaining` (so a re-served question keeps
its time). Turns red under 5 s. At 0 it calls `choose(null)` (a timeout). The server allows
another 1.5 s of grace for network lag; the client never sends a late real choice itself, but
a slow network can still make a sent answer arrive late (server marks it `late`).

**Answer** (`choose(i)`): guards double answers with `S.q.answered`, disables buttons,
marks the pending pick, posts the answer. On error: toast; 404 -> home; otherwise `next()`
(which re-serves the same question, clock still running). On success: right option blue with
a check (and a "pop" if correct), wrong pick striped orange with a cross and "shake", others
dimmed; HUD redrawn from the response; verdict card with a rotating title ("Cleared for
landing!", ...) or "Out of time" / "Not this time", points breakdown (or "Strike n of 3" in
endless / "No points, streak reset"), "The answer: X" when wrong, the `extra` note,
"From the Wikivoyage article <title>" and the guide link. Button "Next question" or
"See your boarding pass".

**End** ("Arrivals"): `GET api/rounds/{id}`; stores `wayfarer.daily` for daily rounds;
score card with label, verdict by accuracy (>= 90 "Seasoned globetrotter", >= 70 "Frequent
flyer", >= 40 "Weekend wanderer", else "First-time flyer"), right/accuracy/best streak,
per-category bars, name form (`maxlength=16`) if `can_submit`, the board for this round
(`api/leaderboard?board=`), share textarea + Copy (Clipboard API on secure contexts, else
`execCommand("copy")`, else "Selected: use your phone's Copy." The box is plain HTTP, so the
fallback path is the normal one there), "Play again" (same mode/cat/region) and "Back to
departures".

**Sheets**: About (question count, source, snapshot date and ZIM file name, CC BY-SA 4.0
licence link, scoring summary) and Leaderboards (Quick / Daily / Endless tabs; notes that
category and region boards are only shown at the end of such a round). Escape or a tap on
the backdrop closes.

**Keyboard**: A-D or 1-4 answers the current question (not while a sheet is open or an input
has focus).

## The Travel Guide link

`findGuide()`:
1. `GET ../api/apps` (the admin service's menu, which omits hidden apps). If no app with
   `id === "guide"`, no link is ever shown (e.g. GL.iNet lite, where the guide is a model app
   and absent, or standalone dev).
2. `base = "../" + guide.path` without leading slashes (normally `../guide/`).
3. `GET <base>health`; pick the zim whose `name` or `id` matches `/wikivoyage/i`, else the first.
4. Link: `<base>z/<zim id>/<path segments, each URI-encoded>`; if no zim was found,
   `<base>?q=<title>` (the guide's page reads `?q=` and runs a search). Opens in a new tab.

The guide's `zim id` is its file stem, so the link works for whatever Wikivoyage ZIM the guide
has, but article paths come from the bank's snapshot: an article renamed since 2026-09 may
404 in the guide. Not checked.

## Rules the tests enforce on this file

`test_health_meta_and_page`: contains `"api/rounds`, never `"/api/` or `` `/api/ ``, and no
`src=` or `<link href=` that starts with `http:`, `https:`, `//` or `/x`. Plain `<a href>` to
the CC licence is allowed. `/static/icon.svg` must serve 200.

## Not verified

Played only in a phone-sized desktop browser through the platform dev stack (CLAUDE.md,
README). Not on real phones: timer smoothness, copy fallback, safe areas and keyboard
behaviour on iOS/Android are untested.

# Leaderboard storage (`store.py`)

One small JSON file, `DATA_DIR/scores.json`, on a named volume on the box (`wayfarer-data`).
The box writes to eMMC/SD, so the design is: one file, capped, written atomically, only on a
leaderboard submit. Same pattern as Dark Light's `store.py` (per the module docstring).

## Constants

| Name | Value | Meaning |
|---|---|---|
| `PER_BOARD` | 50 | Rows kept per board |
| `DAILY_KEEP` | 14 | Newest `daily:*` boards kept |
| `MAX_BOARDS` | 60 | Total boards kept (others evicted) |
| `NAME_MAX` | 16 | Characters in a cleaned name |

## File shape

```json
{"boards": {"quick": [{"name": "Ann", "score": 1460, "right": 10, "answered": 10, "streak": 10, "at": 1795000000.1}],
            "daily:2026-11-22": [...], "endless": [...], "cat:capitals": [...], "region:Asia": [...]}}
```

`at` is `time.time()` rounded to 0.1 s (wall clock, unlike rounds which use monotonic time).
Rows are kept sorted by score descending, then earlier `at` first. `top()` adds `rank`.

## `Store(folder)`

- `usable_dir(folder)`: empty/None -> None. Otherwise `mkdir -p`, write and delete a
  `.write-test` probe; any `OSError` -> None.
- No usable dir -> `path = None`, `persistent` (a property: `path is not None`) is False:
  everything works in memory until a
  restart. The page then says scores are kept "until it restarts" (`leaderboard_saved`).
- Existing file is read once at start. Parse error, `OSError`, or wrong shape -> start empty
  silently; the next write replaces the damaged file (a test covers this).
- `threading.Lock` guards `boards` (the handlers are async and don't actually run in threads,
  but the lock is harmless).

## `add(board, name, score, right, answered, best_streak, now=None)`

1. `clean_name(name)`; empty -> `ValueError("name")` (app.py checks first and returns 400).
2. Under the lock: find an existing row with the same name **case-insensitively**
   (`casefold`). None -> append. Exists -> replaced only if the new score is strictly higher
   (so the stored name's capitalisation changes to the new one on replacement).
3. Sort, truncate to 50, `_prune()`, `_save()`.
4. Return the 1-based rank of that name on the board, or None if it isn't there.

`_prune()`: drops all but the newest 14 `daily:` boards (by string sort of the date), then
while more than 60 boards exist, drops the board whose newest row is oldest.

`_save()`: writes `scores.tmp` next to the file, then `os.replace` onto `scores.json`. On
`OSError` (disk full, read-only) it sets `self.path = None` and carries on in memory for the
rest of the process lifetime; `persistent` then reads False, so `/api/meta` reports
`leaderboard_saved: false` (a test covers this).

The write is synchronous inside an async handler; the file is small (<= 60 boards x 50 rows),
so it blocks the event loop only briefly.

## `clean_name(name)`

Non-strings -> `""`. NFC-normalise, collapse whitespace runs to one space, remove every
Unicode "C*" category character (controls, format chars like U+202E RTL override, unassigned,
private use), strip, cut to 16, strip again. Tests: `"  a\tb\n c "` -> `"a b c"`, 40 x's -> 16,
RTL override removed, `None` -> `""`.

## Who can write

Only `POST /api/rounds/{id}/submit`, only for a round the server played to `done`, once per
round, with the server's own score. A client can still play many rounds and submit under any
name (no identity), and could script perfect play by looking ids up in the public bank; that
is accepted for a hotspot game.

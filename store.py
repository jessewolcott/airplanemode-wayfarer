"""The shared leaderboard: one small JSON file in DATA_DIR (a named volume on the box).

The box stores to eMMC or SD cards, so this stays small and writes rarely: one file, capped,
written atomically. With no DATA_DIR, or one that isn't writable, everything still works and
just lives in memory (same pattern as darklight's store.py).

Boards: "quick", "endless", "daily:YYYY-MM-DD", "cat:<id>", "region:<continent>". Each keeps
each name's best score only, at most PER_BOARD rows; only the newest DAILY_KEEP daily boards
are kept.
"""
import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path

PER_BOARD = 50
DAILY_KEEP = 14
MAX_BOARDS = 60
NAME_MAX = 16


def clean_name(name):
    if not isinstance(name, str):
        return ""
    name = re.sub(r"\s+", " ", unicodedata.normalize("NFC", name))
    name = "".join(c for c in name if unicodedata.category(c)[0] != "C").strip()
    return name[:NAME_MAX].strip()


def usable_dir(path):
    if not path:
        return None
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        probe = Path(path) / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return Path(path)
    except OSError:
        return None


class Store:
    def __init__(self, folder):
        folder = usable_dir(folder)
        self.path = folder / "scores.json" if folder else None
        self.persistent = self.path is not None
        self.lock = threading.Lock()
        self.boards = {}
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("boards"), dict):
                    self.boards = data["boards"]
            except (OSError, ValueError):
                pass   # a damaged file shouldn't stop the game; it's rebuilt on the next write

    def _save(self):
        if not self.path:
            return
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"boards": self.boards}, separators=(",", ":"), ensure_ascii=False),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            self.path = None   # read-only or full: carry on in memory

    def _prune(self):
        daily = sorted(b for b in self.boards if b.startswith("daily:"))
        for b in daily[:-DAILY_KEEP]:
            del self.boards[b]
        while len(self.boards) > MAX_BOARDS:
            # drop the board with the oldest newest entry
            oldest = min(self.boards, key=lambda b: max((r["at"] for r in self.boards[b]), default=0))
            del self.boards[oldest]

    def add(self, board, name, score, right, answered, best_streak, now=None):
        """Record a finished round. Returns the name's 1-based rank on the board (or None)."""
        name = clean_name(name)
        if not name:
            raise ValueError("name")
        now = now or time.time()
        row = {"name": name, "score": int(score), "right": int(right), "answered": int(answered),
               "streak": int(best_streak), "at": round(now, 1)}
        with self.lock:
            rows = self.boards.setdefault(board, [])
            mine = next((r for r in rows if r["name"].casefold() == name.casefold()), None)
            if mine is None:
                rows.append(row)
            elif row["score"] > mine["score"]:
                rows[rows.index(mine)] = row
            rows.sort(key=lambda r: (-r["score"], r["at"]))
            del rows[PER_BOARD:]
            self._prune()
            self._save()
            for i, r in enumerate(self.boards.get(board, [])):
                if r["name"].casefold() == name.casefold():
                    return i + 1
            return None

    def top(self, board, limit=10):
        with self.lock:
            return [dict(r, rank=i + 1) for i, r in enumerate(self.boards.get(board, [])[:limit])]

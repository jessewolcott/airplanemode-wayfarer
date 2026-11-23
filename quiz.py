"""Wayfarer's game rules: the question bank, picking questions, rounds and scoring.

Pure Python, no I/O except loading the bank, and the clock is always passed in, so the tests can
drive everything. The client never sees an answer before it has answered: questions go out as
text plus four shuffled options, the client sends back the index it chose, and the score is
worked out here.
"""
import gzip
import json
import random
import secrets
from collections import Counter, OrderedDict
from pathlib import Path

ROUND_LENGTH = 10
TIME_LIMIT = 20.0          # seconds per question
GRACE = 1.5                # network + tap slack before a timed answer counts as too late
MIN_HUMAN = 0.4            # answers faster than this earn no speed bonus (nobody reads that fast)
ENDLESS_STRIKES = 3
ENDLESS_MAX = 300          # an endless run stops here (it's a game, not a soak test)
SEEN_MAX = 800             # how many "recently seen" ids a client may send

BASE_POINTS = {1: 100, 2: 125, 3: 150}
SPEED_MAX = 50
STREAK_STEP = 10
STREAK_CAP = 50

# difficulty for each slot of a 10-question round: a gentle ramp
PLAN = [1, 1, 2, 1, 2, 2, 3, 2, 3, 3]
MODES = ("quick", "category", "region", "daily", "endless")


class BankError(Exception):
    pass


class Bank:
    def __init__(self, data):
        self.source = data["source"]
        self.categories = data["categories"]
        self.cat_names = {c["id"]: c["name"] for c in self.categories}
        self.continents = data["continents"]
        self.questions = data["questions"]
        self.by_id = {q["id"]: q for q in self.questions}
        if len(self.by_id) != len(self.questions):
            raise BankError("duplicate question ids")

    @classmethod
    def load(cls, path):
        path = Path(path)
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as f:
            return cls(json.load(f))

    def pool(self, mode, cat=None, region=None):
        if mode == "category":
            if cat not in self.cat_names:
                raise ValueError("unknown category")
            return [q for q in self.questions if q["cat"] == cat]
        if mode == "region":
            if region not in self.continents:
                raise ValueError("unknown region")
            return [q for q in self.questions if q.get("cont") == region]
        return self.questions

    def meta(self):
        cats = Counter(q["cat"] for q in self.questions)
        conts = Counter(q.get("cont") for q in self.questions)
        return {
            "source": self.source,
            "count": len(self.questions),
            "categories": [{**c, "count": cats[c["id"]]} for c in self.categories if cats[c["id"]]],
            # a continent needs enough questions for a few rounds to be offered
            "regions": [{"id": c, "count": conts[c]} for c in self.continents if conts[c] >= 3 * ROUND_LENGTH],
        }


# ---------------------------------------------------------------- picking questions
def plan_for(i):
    """Target difficulty of the i-th question (0-based) of any round."""
    if i < len(PLAN):
        return PLAN[i]
    return 2 if i < 15 else 3 if i % 3 else 2


def select(pool, n, rng, seen=(), avoid_ids=(), avoid_keys=(), start=0, mix_categories=True):
    """Pick n questions from pool.

    - never a question in avoid_ids (already in this round) or one whose group key `k` is in
      avoid_keys (no "capital of Japan" next to "Tokyo is the capital of which country?");
    - questions the browser hasn't seen first; when they run out, the ones it saw longest ago;
    - aims for the round's difficulty ramp and, when mixing, a spread of categories;
    - relaxes category, then difficulty, before giving up on a slot.
    """
    seen = list(seen)[-SEEN_MAX:]
    seen_rank = {qid: i for i, qid in enumerate(seen)}     # higher = seen more recently
    avoid_ids = set(avoid_ids)
    keys = set(avoid_keys)
    cats = sorted({q["cat"] for q in pool})
    rng.shuffle(cats)
    cat_cycle = [cats[i % len(cats)] for i in range(n)] if (mix_categories and cats) else [None] * n
    # tiers, worked out once: never seen here, then the half of the pool seen longest ago, then any
    unseen_ids = {q["id"] for q in pool if q["id"] not in seen_rank}
    by_age = sorted((q for q in pool if q["id"] in seen_rank), key=lambda q: seen_rank[q["id"]])
    old_ids = {q["id"] for q in by_age[: max(n, len(by_age) // 2)]}
    out = []
    for slot in range(n):
        want_diff = plan_for(start + slot)
        want_cat = cat_cycle[slot]
        free = [q for q in pool if q["id"] not in avoid_ids and q["k"] not in keys]
        if not free:
            break
        tiers = [[q for q in free if q["id"] in unseen_ids], [q for q in free if q["id"] in old_ids], free]
        choice = None
        for tier in tiers:
            for need_cat, need_diff in ((True, True), (False, True), (True, False), (False, False)):
                c = [q for q in tier
                     if (not need_cat or want_cat is None or q["cat"] == want_cat)
                     and (not need_diff or q["diff"] == want_diff)]
                if c:
                    choice = rng.choice(c)
                    break
            if choice:
                break
        if not choice:
            break
        out.append(choice)
        avoid_ids.add(choice["id"])
        keys.add(choice["k"])
    return out


def daily_questions(bank, date):
    """The same ROUND_LENGTH questions for everyone on the box on `date` (YYYY-MM-DD)."""
    rng = random.Random(f"wayfarer-daily-{date}")
    return select(bank.questions, ROUND_LENGTH, rng)


# ---------------------------------------------------------------- scoring
def points(correct, diff, elapsed, streak_after):
    """Points for one answer. streak_after is the streak including this answer."""
    if not correct:
        return {"base": 0, "speed": 0, "streak": 0, "total": 0}
    base = BASE_POINTS.get(diff, 100)
    speed = 0
    if MIN_HUMAN <= elapsed < TIME_LIMIT:
        # full SPEED_MAX at MIN_HUMAN (the fastest answer that counts), down to 0 at TIME_LIMIT
        speed = round(SPEED_MAX * (TIME_LIMIT - elapsed) / (TIME_LIMIT - MIN_HUMAN))
    bonus = min(STREAK_CAP, STREAK_STEP * max(0, streak_after - 1))
    return {"base": base, "speed": speed, "streak": bonus, "total": base + speed + bonus}


# ---------------------------------------------------------------- rounds
class RoundError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Round:
    def __init__(self, bank, mode, *, cat=None, region=None, timed=True, seen=(), date=None, rng=None, now=0.0):
        if mode not in MODES:
            raise RoundError(400, "Unknown mode.")
        self.id = secrets.token_urlsafe(12)
        self.bank = bank
        self.mode = mode
        self.cat = cat if mode == "category" else None
        self.region = region if mode == "region" else None
        self.timed = bool(timed)
        self.date = date if mode == "daily" else None
        self.rng = rng or random.Random(secrets.randbits(64))
        self.seen = [s for s in seen if isinstance(s, str)][-SEEN_MAX:]
        self.created = self.touched = now
        try:
            self.pool = bank.pool(mode, self.cat, self.region)
        except ValueError as e:
            raise RoundError(400, str(e).capitalize() + ".")
        if mode == "daily":
            self.questions = daily_questions(bank, date)
        elif mode == "endless":
            self.questions = select(self.pool, ROUND_LENGTH, self.rng, self.seen)
        else:
            self.questions = select(self.pool, ROUND_LENGTH, self.rng, self.seen,
                                    mix_categories=mode != "category")
        if not self.questions:
            raise RoundError(400, "No questions for that.")
        self.total = None if mode == "endless" else len(self.questions)
        self.orders = {}           # question index -> option order (indices into [answer, *d])
        self.served_at = {}        # question index -> when it was first sent
        self.answers = []          # one dict per answered question
        self.score = 0
        self.streak = 0
        self.best_streak = 0
        self.strikes = 0
        self.done = False
        self.submitted = False

    # -------- labels
    @property
    def board(self):
        if self.mode == "daily":
            return f"daily:{self.date}"
        if self.mode == "category":
            return f"cat:{self.cat}"
        if self.mode == "region":
            return f"region:{self.region}"
        return self.mode

    @property
    def label(self):
        return board_label(self.board, self.bank)

    # -------- play
    def _ensure(self, n):
        if self.mode == "endless" and n >= len(self.questions) and len(self.questions) < ENDLESS_MAX:
            more = select(self.pool, ROUND_LENGTH, self.rng, self.seen,
                          avoid_ids=[q["id"] for q in self.questions],
                          avoid_keys=[q["k"] for q in self.questions[-60:]],
                          start=len(self.questions))
            if not more:   # every question used: allow repeats of the early ones
                more = select(self.pool, ROUND_LENGTH, self.rng, self.seen,
                              avoid_ids=[q["id"] for q in self.questions[-40:]], start=len(self.questions))
            self.questions.extend(more)
        return n < len(self.questions)

    def current(self, now):
        """The question to show (sending it starts its clock; asking again doesn't restart it)."""
        if self.done:
            raise RoundError(409, "This round is over.")
        n = len(self.answers)
        if not self._ensure(n):
            self.done = True
            raise RoundError(409, "This round is over.")
        q = self.questions[n]
        if n not in self.orders:
            order = [0, 1, 2, 3]
            self.rng.shuffle(order)
            self.orders[n] = order
            self.served_at[n] = now
        self.touched = now
        opts = [q["a"], *q["d"]]
        elapsed = max(0.0, now - self.served_at[n])
        return {
            "n": n, "id": q["id"], "q": q["q"], "options": [opts[i] for i in self.orders[n]],
            "cat": q["cat"], "cat_name": self.bank.cat_names.get(q["cat"], q["cat"]), "diff": q["diff"],
            "timed": self.timed, "limit": TIME_LIMIT,
            "remaining": max(0.0, TIME_LIMIT - elapsed) if self.timed else None,
            # right/wrong of the questions already answered: lets a reloaded page redraw its dots
            "results": [a["correct"] for a in self.answers[-ROUND_LENGTH:]] if self.mode != "endless" else [],
            **self.status(),
        }

    def answer(self, n, choice, now):
        if self.done:
            raise RoundError(409, "This round is over.")
        if n != len(self.answers) or n not in self.orders:
            raise RoundError(409, "That isn't the current question.")
        if choice is not None and (not isinstance(choice, int) or isinstance(choice, bool) or not 0 <= choice <= 3):
            raise RoundError(400, "Pick one of the four answers.")
        q = self.questions[n]
        order = self.orders[n]
        right = order.index(0)
        elapsed = max(0.0, now - self.served_at[n])
        late = self.timed and elapsed > TIME_LIMIT + GRACE
        correct = choice is not None and choice == right and not late
        if correct:
            self.streak += 1
            self.best_streak = max(self.best_streak, self.streak)
        else:
            self.streak = 0
            if self.mode == "endless":
                self.strikes += 1
        pts = points(correct, q["diff"], elapsed, self.streak)
        self.score += pts["total"]
        self.answers.append({"id": q["id"], "cat": q["cat"], "correct": correct, "points": pts["total"],
                             "timeout": choice is None or late, "elapsed": round(elapsed, 2)})
        self.touched = now
        if self.mode == "endless":
            self.done = self.strikes >= ENDLESS_STRIKES or not self._ensure(n + 1)
        else:
            self.done = len(self.answers) >= len(self.questions)
        return {
            "n": n, "correct": correct, "late": late, "timeout": choice is None,
            "answer": right, "answer_text": q["a"], "points": pts,
            "source": {"title": q["src"], "path": q["path"]}, "extra": q.get("x", ""),
            **self.status(),
        }

    def status(self):
        return {"score": self.score, "streak": self.streak, "best_streak": self.best_streak,
                "strikes": self.strikes, "answered": len(self.answers), "total": self.total,
                "done": self.done}

    def summary(self):
        right = sum(1 for a in self.answers if a["correct"])
        per = OrderedDict()
        for a in self.answers:
            c = per.setdefault(a["cat"], {"cat": a["cat"], "name": self.bank.cat_names.get(a["cat"], a["cat"]),
                                          "right": 0, "total": 0})
            c["total"] += 1
            c["right"] += a["correct"]
        n = len(self.answers)
        return {
            "id": self.id, "mode": self.mode, "board": self.board, "label": self.label, "timed": self.timed,
            "date": self.date, "score": self.score, "right": right, "answered": n,
            "accuracy": round(100 * right / n) if n else 0, "best_streak": self.best_streak,
            "strikes": self.strikes, "categories": list(per.values()), "done": self.done,
            "can_submit": self.done and not self.submitted and n > 0,
            "share": share_text(self, right, n),
        }


def board_label(board, bank):
    kind, _, arg = board.partition(":")
    if kind == "daily":
        return f"Daily flight {arg}"
    if kind == "cat":
        return f"Category: {bank.cat_names.get(arg, arg)}"
    if kind == "region":
        return f"Region: {arg}"
    return {"quick": "Quick round", "endless": "Endless"}.get(kind, kind)


def share_text(rnd, right, n):
    marks = "".join("🟩" if a["correct"] else "🟥" for a in rnd.answers[:ROUND_LENGTH if rnd.mode != "endless" else 50])
    head = f"Wayfarer · {rnd.label}"
    if rnd.mode == "endless" and rnd.strikes >= ENDLESS_STRIKES:
        line = f"{right} right before three strikes · {rnd.score} pts · best streak {rnd.best_streak}"
    elif rnd.mode == "endless":   # stopped at ENDLESS_MAX (or still going)
        line = f"{right}/{n} right · {rnd.score} pts · best streak {rnd.best_streak}"
    else:
        line = f"{right}/{n} · {rnd.score} pts · best streak {rnd.best_streak}"
    return f"{head}\n{line}\n{marks}"


class Rounds:
    """Live rounds, in memory: forgotten after `ttl` idle seconds, at most `cap` of them."""

    def __init__(self, ttl=3 * 3600, cap=500):
        self.ttl = ttl
        self.cap = cap
        self.items = OrderedDict()

    def add(self, rnd, now):
        self.prune(now)
        while len(self.items) >= self.cap:
            self.items.popitem(last=False)
        self.items[rnd.id] = rnd
        return rnd

    def get(self, rid, now):
        self.prune(now)
        rnd = self.items.get(rid)
        if rnd is None:
            raise RoundError(404, "That round has expired. Start a new one.")
        self.items.move_to_end(rid)
        rnd.touched = now      # any call keeps it alive (the end screen's summary and submit too)
        return rnd

    def prune(self, now):
        for rid in [r for r, v in self.items.items() if now - v.touched > self.ttl]:
            del self.items[rid]

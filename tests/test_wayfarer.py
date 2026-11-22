"""Wayfarer tests: the question bank, picking, scoring, the API and the leaderboard.

Run: python tests/test_wayfarer.py      (plain asserts; Python 3.10+)
"""
import gzip
import json
import logging
import os
import random
import re
import sys
import tempfile
import traceback
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.update(DATA_DIR=tempfile.mkdtemp(prefix="wayfarer-test-"))
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import app as wf  # noqa: E402
import quiz  # noqa: E402
from store import Store, clean_name  # noqa: E402

BANK = wf.bank
MARKUP = re.compile(r"[\[\]{}<>|=]|''|&[a-z]+;|https?:")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def tick(self, s):
        self.t += s


clock = Clock()
wf.clock = clock
c = TestClient(wf.app)
logging.getLogger("httpx").setLevel(logging.WARNING)


def ok(r, code=200):
    assert r.status_code == code, (r.status_code, r.text)
    return r.json()


def right_index(q):
    """What a bot that knows the bank would answer."""
    return q["options"].index(BANK.by_id[q["id"]]["a"])


def new_round(**kw):
    body = {"mode": "quick", "timed": True, "seen": []}
    body.update(kw)
    return ok(c.post("/api/rounds", json=body))


def play(rid, choose, gap=2.0):
    """Play a round to the end. choose(q) -> index or None. Returns the answers."""
    out = []
    for _ in range(quiz.ENDLESS_MAX + 5):
        r = c.post(f"/api/rounds/{rid}/next")
        if r.status_code == 409:
            break
        q = ok(r)
        clock.tick(gap)
        res = ok(c.post(f"/api/rounds/{rid}/answer", json={"n": q["n"], "choice": choose(q)}))
        out.append((q, res))
        if res["done"]:
            break
    return out


def tiny_bank():
    """A small hand-made bank for exact selection tests."""
    qs = []
    for cat in ("a", "b", "c"):
        for cont in ("Asia", "Europe"):
            for diff in (1, 2, 3):
                for i in range(4):
                    n = len(qs)
                    qs.append({"id": f"q{n:03d}", "cat": cat, "kind": cat, "q": f"Question {n}?", "a": f"A{n}",
                               "d": [f"B{n}", f"C{n}", f"D{n}"], "diff": diff, "cont": cont, "k": f"k{n // 2}",
                               "src": "X", "path": "X"})
    return quiz.Bank({"source": {"zim": "t.zim", "date": "2026-01-01"}, "version": 1,
                      "categories": [{"id": x, "name": x.upper(), "blurb": ""} for x in "abc"],
                      "continents": ["Asia", "Europe"], "questions": qs})


# ---------------------------------------------------------------- the bank
def test_bank_file_is_small_and_has_a_source():
    path = ROOT / "bank" / "questions.json.gz"
    assert path.stat().st_size < 2_000_000, path.stat().st_size
    src = BANK.source
    assert src["zim"].endswith(".zim") and re.fullmatch(r"\d{4}-\d\d-\d\d", src["date"]), src
    assert src["license"] == "CC BY-SA 4.0"


def test_bank_sanity():
    ids, kinds = set(), Counter()
    cats = {c["id"] for c in BANK.categories}
    for q in BANK.questions:
        for key in ("id", "cat", "kind", "q", "a", "d", "diff", "k", "src", "path"):
            assert key in q, (key, q)
        assert q["id"] not in ids, q["id"]
        ids.add(q["id"])
        assert q["cat"] in cats, q
        assert q["diff"] in (1, 2, 3), q
        assert q["cont"] is None or q["cont"] in BANK.continents, q
        opts = [q["a"], *q["d"]]
        assert len(opts) == 4, q
        assert all(isinstance(o, str) and o.strip() == o and o for o in opts), q
        assert len({o.casefold() for o in opts}) == 4, q          # four distinct options
        assert q["a"] not in q["d"], q                              # the answer is among them once
        assert q["q"].strip() and q["src"].strip() and q["path"].strip(), q
        for s in opts + [q["q"]]:
            assert not MARKUP.search(s), (s, q)                     # no wiki markup or HTML
        kinds[q["kind"]] += 1
    assert len(BANK.questions) >= 3000, len(BANK.questions)
    per_cat = Counter(q["cat"] for q in BANK.questions)
    assert len(per_cat) >= 6 and min(per_cat.values()) >= 100, per_cat


def test_no_answer_spelt_out_in_country_questions():
    for q in BANK.questions:
        if q["kind"] in ("city_country", "park_country", "city_region", "capital", "currency", "airport_city"):
            assert q["a"].casefold() not in q["q"].casefold(), q


def test_disputed_places_stay_out():
    sys.path.insert(0, str(ROOT / "bank"))
    from build import DISPUTED   # the generator's list (build.py needs nothing extra to import)
    for q in BANK.questions:
        for s in [q["a"], *q["d"]]:
            assert s not in DISPUTED, q
        assert "(country)" not in q["q"] + q["a"] + "".join(q["d"]), q
    assert not any(q["k"] in ("cap:Israel", "place:Jerusalem") for q in BANK.questions)


# ---------------------------------------------------------------- picking
def test_category_and_region_filters():
    b = tiny_bank()
    rng = random.Random(1)
    for cat in "abc":
        got = quiz.select(b.pool("category", cat=cat), 10, rng, mix_categories=False)
        assert len(got) == 10 and all(q["cat"] == cat for q in got)
    got = quiz.select(b.pool("region", region="Asia"), 10, rng)
    assert len(got) == 10 and all(q["cont"] == "Asia" for q in got)
    for mode, kw in (("category", {"cat": "zzz"}), ("region", {"region": "Mars"})):
        try:
            b.pool(mode, **kw)
            raise AssertionError("should refuse")
        except ValueError:
            pass
    # the real bank too
    for cat in BANK.cat_names:
        assert all(q["cat"] == cat for q in BANK.pool("category", cat=cat))
    for cont in BANK.continents:
        assert all(q["cont"] == cont for q in BANK.pool("region", region=cont))


def test_no_repeats_in_a_round():
    b = tiny_bank()
    for seed in range(30):
        got = quiz.select(b.questions, 10, random.Random(seed))
        assert len({q["id"] for q in got}) == len(got) == 10
        assert len({q["k"] for q in got}) == 10          # no two questions from one group
    for seed in range(10):
        got = quiz.select(BANK.questions, 10, random.Random(seed))
        assert len({q["k"] for q in got}) == 10


def test_mixed_rounds_spread_categories_and_follow_the_ramp():
    got = quiz.select(BANK.questions, 10, random.Random(5))
    assert len({q["cat"] for q in got}) >= 6, [q["cat"] for q in got]
    assert [q["diff"] for q in got] == quiz.PLAN


def test_daily_is_stable_and_changes_by_date():
    a = [q["id"] for q in quiz.daily_questions(BANK, "2026-11-22")]
    b = [q["id"] for q in quiz.daily_questions(BANK, "2026-11-22")]
    d = [q["id"] for q in quiz.daily_questions(BANK, "2026-11-23")]
    assert a == b and len(a) == quiz.ROUND_LENGTH and a != d
    # seen lists don't change the daily flight
    r1 = quiz.Round(BANK, "daily", date="2026-11-22", seen=a)
    assert [q["id"] for q in r1.questions] == a


def test_unseen_questions_come_first():
    b = tiny_bank()
    pool = b.questions
    keep = {q["id"] for q in quiz.select(pool, 10, random.Random(3))}
    seen = [q["id"] for q in pool if q["id"] not in keep]
    for seed in range(10):
        got = quiz.select(pool, 10, random.Random(seed), seen=seen)
        assert {q["id"] for q in got} == keep, seed
    # everything seen: still a full round, from the ones seen longest ago
    order = [q["id"] for q in pool]
    random.Random(4).shuffle(order)
    got = quiz.select(pool, 10, random.Random(0), seen=order)
    assert len(got) == 10
    newest = set(order[len(order) // 2:])
    assert not ({q["id"] for q in got} & newest), "should prefer the oldest-seen half"


def test_round_uses_seen_list_from_the_browser():
    first = quiz.Round(BANK, "category", cat="capitals", rng=random.Random(1))
    seen = [q["id"] for q in first.questions]
    second = quiz.Round(BANK, "category", cat="capitals", seen=seen, rng=random.Random(1))
    assert not set(seen) & {q["id"] for q in second.questions}


# ---------------------------------------------------------------- scoring
def test_points():
    assert quiz.points(False, 3, 1.0, 0) == {"base": 0, "speed": 0, "streak": 0, "total": 0}
    p = quiz.points(True, 1, 10.0, 1)
    assert p == {"base": 100, "speed": 25, "streak": 0, "total": 125}, p
    assert quiz.points(True, 3, 0.0, 1)["speed"] == 0          # inhumanly fast: no speed bonus
    assert quiz.points(True, 2, quiz.MIN_HUMAN, 1)["speed"] == 49
    assert quiz.points(True, 1, 25.0, 1)["speed"] == 0         # untimed and slow: answer points only
    assert quiz.points(True, 1, 5.0, 3)["streak"] == 20
    assert quiz.points(True, 1, 5.0, 50)["streak"] == quiz.STREAK_CAP


def test_round_scoring_and_streaks():
    r = quiz.Round(BANK, "quick", rng=random.Random(2), now=0)
    t = 0.0
    expected = 0
    for i in range(quiz.ROUND_LENGTH):
        q = r.current(t)
        right = q["options"].index(BANK.by_id[q["id"]]["a"])
        t += 5
        choice = right if i != 4 else (right + 1) % 4
        res = r.answer(q["n"], choice, t)
        assert res["correct"] == (i != 4)
        expected += res["points"]["total"]
    assert r.done and r.score == expected
    assert r.best_streak == 5 and r.summary()["right"] == 9


# ---------------------------------------------------------------- API
def test_health_meta_and_page():
    assert ok(c.get("/health"))["ok"] is True
    m = ok(c.get("/api/meta"))
    assert m["count"] == len(BANK.questions) and m["source"]["date"]
    assert {x["id"] for x in m["categories"]} <= set(BANK.cat_names) and m["regions"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\d", m["daily"])
    html = c.get("/").text
    assert '"api/rounds' in html and '"/api/' not in html and "`/api/" not in html
    # assets are local and relative (links to the licence are allowed: they're just links)
    assert not re.search(r'(src|<link[^>]*href)="(https?:|//|/[^/])', html)
    assert c.get("/static/icon.svg").status_code == 200


def test_questions_go_out_without_answers():
    rd = new_round()
    q = ok(c.post(f"/api/rounds/{rd['id']}/next"))
    text = json.dumps(q)
    assert set(q) >= {"n", "q", "options", "cat"} and len(q["options"]) == 4
    for leak in ('"a"', '"d"', '"answer"', '"src"', '"path"', '"x"'):
        assert leak not in text, leak
    # asking again (refresh) gives the same question, same order, clock still running
    clock.tick(3)
    again = ok(c.post(f"/api/rounds/{rd['id']}/next"))
    assert again["options"] == q["options"] and again["n"] == q["n"]
    assert abs(again["remaining"] - (quiz.TIME_LIMIT - 3)) < 0.01


def test_answer_validation():
    rd = new_round()
    rid = rd["id"]
    ok(c.post(f"/api/rounds/{rid}/answer", json={"n": 0, "choice": 0}), 409)    # not served yet
    q = ok(c.post(f"/api/rounds/{rid}/next"))
    ok(c.post(f"/api/rounds/{rid}/answer", json={"n": 1, "choice": 0}), 409)    # not the current one
    ok(c.post(f"/api/rounds/{rid}/answer", json={"n": 0, "choice": 7}), 400)
    ok(c.post(f"/api/rounds/{rid}/answer", json={"n": 0, "choice": -1}), 400)
    ok(c.post(f"/api/rounds/{rid}/answer", json={"n": 0, "choice": "x"}), 422)
    clock.tick(2)
    res = ok(c.post(f"/api/rounds/{rid}/answer", json={"n": 0, "choice": right_index(q)}))
    assert res["correct"] and res["answer"] == right_index(q) and res["source"]["title"]
    ok(c.post(f"/api/rounds/{rid}/answer", json={"n": 0, "choice": right_index(q)}), 409)  # twice
    ok(c.post("/api/rounds/nope/next"), 404)
    ok(c.post("/api/rounds", json={"mode": "speedrun"}), 400)
    ok(c.post("/api/rounds", json={"mode": "category", "cat": "nope"}), 400)
    ok(c.post("/api/rounds", json={"mode": "region", "region": "Atlantis"}), 400)


def test_timer_is_enforced_on_the_server():
    rd = new_round(timed=True)
    q = ok(c.post(f"/api/rounds/{rd['id']}/next"))
    clock.tick(quiz.TIME_LIMIT + quiz.GRACE + 0.5)
    res = ok(c.post(f"/api/rounds/{rd['id']}/answer", json={"n": q["n"], "choice": right_index(q)}))
    assert not res["correct"] and res["late"] and res["points"]["total"] == 0
    # untimed: slow is fine, just no speed bonus
    rd = new_round(timed=False)
    q = ok(c.post(f"/api/rounds/{rd['id']}/next"))
    assert q["remaining"] is None
    clock.tick(60)
    res = ok(c.post(f"/api/rounds/{rd['id']}/answer", json={"n": q["n"], "choice": right_index(q)}))
    assert res["correct"] and res["points"]["speed"] == 0 and res["points"]["base"] >= 100
    # a timeout (no choice) is a miss
    rd = new_round()
    q = ok(c.post(f"/api/rounds/{rd['id']}/next"))
    clock.tick(quiz.TIME_LIMIT)
    res = ok(c.post(f"/api/rounds/{rd['id']}/answer", json={"n": q["n"], "choice": None}))
    assert not res["correct"] and res["timeout"]


def test_full_round_summary_and_leaderboard():
    rd = new_round()
    rid = rd["id"]
    ok(c.post(f"/api/rounds/{rid}/submit", json={"name": "Ann"}), 409)            # not finished
    answers = play(rid, right_index)
    assert len(answers) == quiz.ROUND_LENGTH and answers[-1][1]["done"]
    s = ok(c.get(f"/api/rounds/{rid}"))
    assert s["right"] == 10 and s["accuracy"] == 100 and s["best_streak"] == 10 and s["can_submit"]
    assert sum(x["total"] for x in s["categories"]) == 10
    assert "10/10" in s["share"] and "Wayfarer" in s["share"]
    ok(c.post(f"/api/rounds/{rid}/submit", json={"name": "   "}), 400)
    r = ok(c.post(f"/api/rounds/{rid}/submit", json={"name": "  Ann\u0000  the   Explorer of Everywhere "}))
    assert r["name"] == clean_name("Ann the Explorer of Everywhere") and len(r["name"]) <= 16
    assert r["rows"][0]["score"] == s["score"]                                       # the server's score
    ok(c.post(f"/api/rounds/{rid}/submit", json={"name": "Ann"}), 409)              # once per round
    ok(c.post(f"/api/rounds/{rid}/next"), 409)
    board = ok(c.get("/api/leaderboard", params={"board": "quick"}))
    assert any(row["score"] == s["score"] for row in board["rows"])
    ok(c.get("/api/leaderboard", params={"board": "nonsense"}), 404)
    lb = ok(c.get("/api/leaderboards"))
    assert [b["board"].split(":")[0] for b in lb["boards"]] == ["quick", "daily", "endless"]


def test_endless_ends_after_three_strikes():
    rd = new_round(mode="endless")
    assert rd["total"] is None
    n = [0]

    def choose(q):
        n[0] += 1
        return right_index(q) if n[0] <= 23 else (right_index(q) + 1) % 4
    answers = play(rd["id"], choose)
    assert len(answers) == 26, len(answers)                  # 23 right, then three strikes
    assert answers[-1][1]["strikes"] == 3 and answers[-1][1]["done"]
    ids = [q["id"] for q, _ in answers]
    assert len(set(ids)) == len(ids)


def test_rounds_expire_and_are_capped():
    rs = quiz.Rounds(ttl=10, cap=3)
    made = [rs.add(quiz.Round(BANK, "quick", rng=random.Random(i), now=0), 0) for i in range(4)]
    try:
        rs.get(made[0].id, 1)
        raise AssertionError("the oldest should be gone")
    except quiz.RoundError as e:
        assert e.status == 404
    assert rs.get(made[3].id, 5) is made[3]
    try:
        rs.get(made[3].id, 100)
        raise AssertionError("should have expired")
    except quiz.RoundError:
        pass


def test_bots_play_every_mode_to_the_end():
    """Many rounds with random answers and timings, fixed seeds: they always finish."""
    rng = random.Random(1234)
    modes = [("quick", {}), ("daily", {}), ("endless", {})]
    modes += [("category", {"cat": c}) for c in BANK.cat_names]
    modes += [("region", {"region": r["id"]}) for r in BANK.meta()["regions"]]
    for seed in range(3):
        for mode, kw in modes:
            rd = new_round(mode=mode, timed=bool(seed % 2), seen=[q["id"] for q in rng.sample(BANK.questions, 200)], **kw)
            skill = rng.random()

            def choose(q):
                if rng.random() < 0.05:
                    return None
                return right_index(q) if rng.random() < skill else rng.randrange(4)
            answers = play(rd["id"], choose, gap=rng.uniform(0.2, 8))
            assert answers and answers[-1][1]["done"], (mode, kw)
            s = ok(c.get(f"/api/rounds/{rd['id']}"))
            assert s["done"] and s["answered"] == len(answers)
            assert s["score"] == sum(r["points"]["total"] for _q, r in answers)
            if mode != "endless":
                assert len(answers) == quiz.ROUND_LENGTH
            ids = [q["id"] for q, _ in answers]
            assert len(ids) == len(set(ids))


def test_endless_long_run_never_crashes():
    r = quiz.Round(BANK, "endless", rng=random.Random(9), now=0)
    t = 0
    while not r.done:
        q = r.current(t)
        t += 3
        r.answer(q["n"], q["options"].index(BANK.by_id[q["id"]]["a"]), t)
    assert len(r.answers) == quiz.ENDLESS_MAX


# ---------------------------------------------------------------- leaderboard store
def test_store_persists_and_keeps_best_per_name():
    d = tempfile.mkdtemp(prefix="wayfarer-store-")
    s = Store(d)
    assert s.persistent
    assert s.add("quick", "Ann", 900, 8, 10, 5, now=1) == 1
    assert s.add("quick", "Bob", 1200, 10, 10, 10, now=2) == 1
    assert s.add("quick", "ann", 500, 4, 10, 2, now=3) == 2      # worse: the best stays
    s2 = Store(d)                                                 # read back from disk
    rows = s2.top("quick")
    assert [(r["name"], r["score"], r["rank"]) for r in rows] == [("Bob", 1200, 1), ("Ann", 900, 2)]
    for i in range(80):
        s2.add("quick", f"P{i}", i, 1, 10, 1, now=10 + i)
    assert len(Store(d).boards["quick"]) == 50
    for day in range(20):
        s2.add(f"daily:2026-01-{day + 1:02d}", "Ann", day, 1, 10, 1, now=100 + day)
    daily = sorted(b for b in Store(d).boards if b.startswith("daily:"))
    assert len(daily) == 14 and daily[-1] == "daily:2026-01-20"
    try:
        s2.add("quick", " \t ", 1, 1, 1, 1)
        raise AssertionError("empty names are refused")
    except ValueError:
        pass


def test_store_without_a_volume_or_with_a_bad_file():
    s = Store("")
    assert not s.persistent
    s.add("quick", "Ann", 10, 1, 1, 1)
    assert s.top("quick")[0]["name"] == "Ann"
    d = tempfile.mkdtemp(prefix="wayfarer-store-")
    Path(d, "scores.json").write_text("{not json", encoding="utf-8")
    s = Store(d)
    assert s.top("quick") == []
    s.add("quick", "Ann", 10, 1, 1, 1)
    assert json.loads(Path(d, "scores.json").read_text(encoding="utf-8"))["boards"]["quick"][0]["name"] == "Ann"


def test_clean_name():
    assert clean_name("  a\tb\n c ") == "a b c"
    assert clean_name("x" * 40) == "x" * 16
    assert clean_name("‮evil") == "evil"
    assert clean_name(None) == ""


if __name__ == "__main__":
    tests = [(n, f) for n, f in list(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception:
            failed += 1
            print(f"FAIL  {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)

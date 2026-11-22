"""Build Wayfarer's question bank from a Wikivoyage ZIM.  Not imported by the app.

    # 1. extract facts (needs libzim + lxml, Python 3.11+: run it in python:3.12-slim)
    python bank/build.py extract /data/zim/wikivoyage_en_all_nopic_2026-09.zim
    # 2. generate questions from the facts (plain Python 3.10+, no dependencies)
    python bank/build.py generate
    # or both at once
    python bank/build.py all /data/zim/wikivoyage_en_all_nopic_2026-09.zim

Stage 1 writes .cache/facts.jsonl.gz (git-ignored); stage 2 writes bank/questions.json.gz.
See bank/README.md for the container command and what each category is made from.

Everything here is deterministic: the same ZIM gives the same bank (random.Random(SEED)), and a
question's id is a hash of what it asks, so the browser's "recently seen" list survives a rebuild
for the questions that didn't change.
"""
import gzip
import hashlib
import json
import math
import random
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import unquote

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FACTS = ROOT / ".cache" / "facts.jsonl.gz"
OUT = HERE / "questions.json.gz"
SEED = 20261122

CONTINENTS = ["Africa", "Antarctica", "Asia", "Europe", "North America", "Oceania", "South America"]

CATEGORIES = [
    # id, name, blurb
    ("places", "Where in the world", "Which country is it in?"),
    ("regions", "Regions", "Which part of the country?"),
    ("capitals", "Capitals", "Capital cities, both ways round."),
    ("money", "Money", "What's in your wallet?"),
    ("airports", "Airport codes", "Read the luggage tag."),
    ("phrases", "Phrasebook", "Hello, thank you, goodbye..."),
    ("practical", "Practicalities", "Power, roads, clocks and phones."),
    ("population", "Crowds", "Which place is biggest?"),
]

# How many questions each kind may contribute at most (most famous first).
CAPS = {"city_country": 1100, "park_country": 150, "city_region": 700, "airport_city": 450,
        "airport_name": 250, "phrase_say": 550, "phrase_lang": 300, "population": 260}

US_STATES = set("""Alabama Alaska Arizona Arkansas California Colorado Connecticut Delaware Florida
Georgia Hawaii Idaho Illinois Indiana Iowa Kansas Kentucky Louisiana Maine Maryland Massachusetts
Michigan Minnesota Mississippi Missouri Montana Nebraska Nevada New_Hampshire New_Jersey New_Mexico
New_York North_Carolina North_Dakota Ohio Oklahoma Oregon Pennsylvania Rhode_Island South_Carolina
South_Dakota Tennessee Texas Utah Vermont Virginia Washington West_Virginia Wisconsin
Wyoming""".replace("_", " ").split()) | {"New Hampshire", "New Jersey", "New Mexico", "New York",
                                         "North Carolina", "North Dakota", "Rhode Island",
                                         "South Carolina", "South Dakota", "West Virginia"}

PHRASES = {   # normalised English <dt> -> how the question says it
    "hello": "Hello", "thank you": "Thank you", "please": "Please", "yes": "Yes", "no": "No",
    "goodbye": "Goodbye", "good morning": "Good morning", "good night": "Good night",
    "excuse me": "Excuse me", "i'm sorry": "I'm sorry", "how are you": "How are you?",
    "you're welcome": "You're welcome", "help": "Help!", "cheers": "Cheers!",
    "i don't understand": "I don't understand", "where is the toilet": "Where is the toilet?",
}
EMERGENCY_POOL = ["112", "911", "999", "000", "110", "119", "100", "117", "113", "122", "190",
                  "193", "102", "103", "101", "995", "997", "115", "118", "199"]

# Places whose status is disputed or that few countries recognise: left out of the game entirely
# (not asked about, not offered as answers). A quiz isn't the place to take sides. Israel's
# capital is left out for the same reason.
DISPUTED = {"Abkhazia", "South Ossetia", "Transnistria", "Northern Cyprus", "Somaliland", "Western Sahara",
            "Liberland", "Nagorno-Karabakh", "Palestinian territories"}
SKIP_KEYS = {"cap:Israel", "place:Jerusalem"}

MARKUP = re.compile(r"[\[\]{}<>|=]|''|&[a-z]+;|https?:")


# ---------------------------------------------------------------- helpers
def base(title):
    """'Victoria (state)' -> 'Victoria'."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", title).strip()


def fold(s):
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c) and (c.isalnum() or c == " ")).strip()


def is_latin(s):
    letters = [c for c in s if c.isalpha()]
    return bool(letters) and all("LATIN" in unicodedata.name(c, "") for c in letters)


def qid(*parts):
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:10]


class Bank:
    def __init__(self, facts):
        self.recs = facts["recs"]
        self.meta = facts["meta"]
        self.inlinks = facts["inlinks"]
        self.by_title = {r["t"]: r for r in self.recs}
        self.by_path = {r["p"]: r for r in self.recs}
        self.rng = random.Random(SEED)
        self.out = []
        self.countries = {r["t"] for r in self.recs if "Country articles" in r["c"]
                          and r.get("b") and r["b"][0] in CONTINENTS}
        self.qb = {r["t"]: r for r in self.recs if r.get("q") and r.get("b") and r["b"][0] in CONTINENTS
                   and r["t"] not in ("European Union",) and r["t"] not in DISPUTED and "/" not in r["t"]}
        self.countries -= DISPUTED

    # fame: how many articles link here, plus a little for article quality
    def fame(self, rec):
        if rec is None:
            return 0.0
        status = 0
        for c in rec["c"]:
            if c.startswith("Star"):
                status = max(status, 3)
            elif c.startswith("Guide"):
                status = max(status, 2)
            elif c.startswith("Usable"):
                status = max(status, 1)
        return math.log1p(self.inlinks.get(rec["p"], 0)) + 0.4 * status + 0.25 * math.log1p(rec["n"] / 1000)

    def country_of(self, rec):
        for t in rec.get("b", [])[:-1]:
            if t in self.countries:
                return t
        for t in rec.get("b", [])[:-1]:
            if t in self.qb:
                return t
        return None

    def area_of(self, title):
        """(continent, sub-continent) for a country-ish article title."""
        r = self.by_title.get(title)
        b = (r or {}).get("b") or []
        return (b[0] if b else None, b[1] if len(b) > 2 else None)

    def add(self, kind, cat, q, answer, distractors, src, fame, cont, key, extra=""):
        if key in SKIP_KEYS:
            return
        # "Georgia (country)" -> "Georgia" wherever a country's title is shown
        show = lambda s: base(s) if s in self.qb else s
        answer, distractors = show(answer), [show(d) for d in distractors]
        for t in self.qb:
            if "(" in t and t in q:
                q = q.replace(t, base(t))
        self.out.append({"kind": kind, "cat": cat, "q": q, "a": answer, "d": list(distractors),
                         "src": src["t"], "path": src["p"], "fame": fame, "cont": cont, "k": key,
                         "x": extra})

    def pick(self, pool, n, exclude, prefer=None, then=None):
        """n distinct distractors (strings): from `prefer` first, then `then`, then `pool`,
        skipping anything that folds like one of `exclude`."""
        bad = {fold(e) for e in exclude}
        out, seen = [], set(bad)
        for group in [g for g in (prefer, then, pool) if g]:
            g = [x for x in dict.fromkeys(group) if fold(x) not in seen]
            self.rng.shuffle(g)
            for x in g:
                if len(out) == n:
                    break
                if fold(x) not in seen:
                    out.append(x)
                    seen.add(fold(x))
        return out if len(out) == n else None

    # ------------------------------------------------------------ places + regions
    def places(self):
        cities = []
        for r in self.recs:
            if not r.get("b") or r["b"][0] not in CONTINENTS or "/" in r["t"] or "(" in r["t"]:
                continue
            kind = "city" if "City articles" in r["c"] else "park" if "Park articles" in r["c"] else None
            if not kind:
                continue
            country = self.country_of(r)
            if not country or country == r["t"]:
                continue
            cities.append((self.fame(r), kind, r, country))
        cities.sort(key=lambda x: (-x[0], x[2]["t"]))
        by_sub = defaultdict(set)
        by_cont = defaultdict(set)
        for c in self.qb:
            cont, sub = self.area_of(c)
            by_cont[cont].add(c)
            if sub:
                by_sub[sub].add(c)
        used = Counter()
        for fame, kind, r, country in cities:
            k = f"{kind}_country"
            if used[k] >= CAPS[k] or used[(k, country)] >= 25:
                continue
            cont, sub = self.area_of(country)
            # real countries first (same sub-continent, then continent); territories only to fill up
            d = self.pick(sorted(by_cont[cont]), 3, [country, *r["b"]],
                          sorted(x for x in by_sub.get(sub, ()) if x in self.countries),
                          sorted(x for x in by_cont[cont] if x in self.countries))
            if not d:
                continue
            what = "country" if all(x in self.countries for x in [country, *d]) else "country or territory"
            q = f"Which {what} is {r['t']} in?" if kind == "city" else f"{r['t']} is in which {what}?"
            self.add(k, "places", q, country, d, r, fame, cont, "place:" + r["t"])
            used[k] += 1
            used[(k, country)] += 1

        # regions: the first level under the country (a US state for the United States)
        regions = defaultdict(set)
        city_region = []
        for fame, kind, r, country in cities:
            if kind != "city":
                continue
            b = r["b"]
            i = b.index(country)
            inner = b[i + 1:-1]
            if country == "United States":
                region = next((x for x in inner if base(x) in US_STATES), None)
            else:
                region = inner[0] if inner else None
            if not region:
                continue
            regions[country].add(region)
            city_region.append((fame, r, country, region))
        used = Counter()
        for fame, r, country, region in city_region:
            sib = sorted(regions[country])
            if len(sib) < 4 or used["r"] >= CAPS["city_region"] or used[country] >= 40:
                continue
            shown = {x: base(x) for x in sib}
            if len(set(shown.values())) != len(shown):
                shown = {x: x for x in sib}
            d = self.pick([shown[x] for x in sib], 3, [shown[region]])
            if not d:
                continue
            word = "state" if country == "United States" else "region"
            the = "the " if country.startswith(("United", "Czech", "Dominican", "Netherlands", "Philippines")) else ""
            q = f"Which {word} of {the}{country} is {r['t']} in?"
            self.add("city_region", "regions", q, shown[region], d, r, fame, self.area_of(country)[0],
                     "place:" + r["t"])
            used["r"] += 1
            used[country] += 1

    # ------------------------------------------------------------ quickbar facts
    def quickbar_facts(self):
        facts = {}
        for t, r in self.qb.items():
            q = r["q"]
            f = {}
            cap = (q.get("Capital") or [""])[0]
            if re.fullmatch(r"[^\W\d_][\w .'’-]*", cap) and len(cap) <= 30 and " and " not in cap:
                f["capital"] = cap
            cur = (q.get("Currency") or [""])[0]
            m = re.fullmatch(r"([^()]{3,40}) \(([A-Z]{3})\)", cur)
            if m and not re.search(r"([a-z]{3,})|[a-z][A-Z]", m.group(1)):
                name = m.group(1).strip()
                f["currency"] = name[:1].upper() + name[1:]
                f["currency_raw"] = name
                f["currency_code"] = m.group(2)
            pop = (q.get("Population") or [""])[0]
            m = re.match(r"([\d.]+) (thousand|million|billion)", pop)
            if m:
                f["population"] = float(m.group(1)) * {"thousand": 1e3, "million": 1e6, "billion": 1e9}[m.group(2)]
                f["population_text"] = pop.split(" (")[0]
            el = (q.get("Electricity") or [""])[0]
            volts = {int(v) for v in re.findall(r"(\d{3}) volt", el)}
            if volts and (all(v <= 130 for v in volts) or all(v >= 200 for v in volts)):
                f["low_voltage"] = all(v <= 130 for v in volts)
            cc = (q.get("Country code") or [""])[0]
            if re.fullmatch(r"\+\d{1,4}", cc):
                f["calling"] = cc
            tz = (q.get("Time zone") or [""])[0]
            offs = re.findall(r"UTC([+−\-±])(\d\d):(\d\d)", tz)
            if len(offs) == 1 and " to " not in tz:
                sign, h, mi = offs[0]
                sign = "−" if sign in "−-" else "+" if sign == "+" else "±"
                f["utc"] = f"UTC{sign if h != '00' or mi != '00' else '±'}{int(h)}" + (f":{mi}" if mi != "00" else "")
            em = (q.get("Emergencies") or [""])[0]
            nums = re.findall(r"(?<![\d+-])(\d{2,4})(?![\d-])", em)
            if nums:
                f["emergency"] = nums
            ds = (q.get("Driving side") or [""])[0]
            if ds in ("left", "right"):
                f["drive"] = ds
            facts[t] = f
        return facts

    def country_questions(self):
        facts = self.quickbar_facts()
        names = sorted(facts)
        cont_of = {c: self.area_of(c)[0] for c in names}
        sub_of = {c: self.area_of(c)[1] for c in names}

        def same(c, key, pred=lambda o: True):
            """(same sub-continent, same continent) lists of other countries having `key`."""
            sub = [o for o in names if o != c and key in facts[o] and sub_of[c] and sub_of[o] == sub_of[c] and pred(o)]
            cont = [o for o in names if o != c and key in facts[o] and cont_of[o] == cont_of[c] and pred(o)]
            return sub, cont

        capitals = Counter(f.get("capital") for f in facts.values())
        currencies = Counter(f.get("currency") for f in facts.values())
        callings = Counter(f.get("calling") for f in facts.values())
        for c in names:
            f, r = facts[c], self.qb[c]
            fame, cont = self.fame(r), cont_of[c]
            the = "the " if c.startswith(("United ", "Czech Republic", "Dominican Republic", "Netherlands",
                                          "Philippines", "Bahamas", "Gambia", "Maldives", "Comoros",
                                          "Central African", "Democratic Republic", "Republic of",
                                          "Solomon", "Marshall", "Cayman", "Seychelles", "Falkland",
                                          "British", "U.S.", "Cook Islands", "Faroe", "Turks",
                                          "Federated", "Palestinian", "Vatican", "Cocos", "Pitcairn")) else ""
            # capitals, both ways (skip city-states and "Kuwait City"-style giveaways)
            cap = f.get("capital")
            if cap and capitals[cap] == 1 and fold(base(c)) not in fold(cap) and fold(cap) not in fold(c):
                sub, con = same(c, "capital")
                d = self.pick([facts[o]["capital"] for o in con], 3, [cap], [facts[o]["capital"] for o in sub])
                if d:
                    self.add("capital", "capitals", f"What is the capital of {the}{c}?", cap, d, r, fame, cont,
                             "cap:" + c)
                d = self.pick(con, 3, [c], sub)
                if d:
                    self.add("capital_of", "capitals", f"{cap} is the capital of which country?", c, d, r,
                             fame - 0.3, cont, "cap:" + c)
            cur = f.get("currency")
            if cur:
                sub, con = same(c, "currency")
                d = self.pick([facts[o]["currency"] for o in names if "currency" in facts[o]], 3, [cur],
                              [facts[o]["currency"] for o in sub], [facts[o]["currency"] for o in con])
                if d and fold(base(c)) not in fold(cur):
                    self.add("currency", "money", f"Which currency do you pay with in {the}{c}?", cur, d, r,
                             fame, cont, "cur:" + c, f"Code: {f['currency_code']}")
                if currencies[cur] == 1 and fold(base(c))[:5] not in fold(cur):
                    d = self.pick(con, 3, [c], sub)
                    if d:
                        self.add("currency_of", "money", f"The {f['currency_raw']} ({f['currency_code']}) is the money of which country?",
                                 c, d, r, fame - 0.5, cont, "cur:" + c)
            cc = f.get("calling")
            if cc and callings[cc] == 1:
                sub, con = same(c, "calling")
                d = self.pick(con, 3, [c], sub)
                if d:
                    self.add("calling_of", "practical", f"Whose international calling code is {cc}?", c, d, r,
                             fame - 0.8, cont, "call:" + c)
                d = self.pick([facts[o]["calling"] for o in con], 3, [cc], [facts[o]["calling"] for o in sub])
                if d:
                    self.add("calling", "practical", f"You're phoning {the}{c} from abroad. Which code do you dial?",
                             cc, d, r, fame - 0.4, cont, "call:" + c)
            if "drive" in f:
                side = f["drive"]
                sub, con = same(c, "drive", lambda o: facts[o]["drive"] != side)
                if len(con) >= 3 and (side == "left" or sum(1 for o in names if cont_of[o] == cont and facts[o].get("drive") == "left") >= 4):
                    d = self.pick(con, 3, [c], sub)
                    if d:
                        self.add("drive", "practical", f"In which of these do people drive on the {side}?", c, d, r,
                                 fame - 0.3, cont, "drive:" + c)
            if "low_voltage" in f:
                low = f["low_voltage"]
                sub, con = same(c, "low_voltage", lambda o: facts[o]["low_voltage"] != low)
                if low and len(con) >= 3:
                    d = self.pick(con, 3, [c], sub)
                    if d:
                        self.add("voltage", "practical",
                                 "Which of these runs on 100–127 volt mains power (bring a converter from 230 V countries)?",
                                 c, d, r, fame - 0.5, cont, "volt:" + c)
            if "utc" in f:
                sub, con = same(c, "utc")
                pool = [facts[o]["utc"] for o in con]
                d = self.pick(pool, 3, [f["utc"]], [facts[o]["utc"] for o in sub])
                if d:
                    self.add("utc", "practical", f"What is standard time in {the}{c}?", f["utc"], d, r,
                             fame - 0.8, cont, "utc:" + c)
            if "emergency" in f:
                mine = f["emergency"]
                ans = mine[0]
                pool = [x for x in EMERGENCY_POOL if x not in mine]
                d = self.pick(pool, 3, [ans])
                if d and len(ans) >= 3:
                    self.add("emergency", "practical", f"Which of these is an emergency number in {the}{c}?", ans, d, r,
                             fame - 0.4, cont, "sos:" + c)

        # population: four places from one continent, clearly apart (each at least 1.5x the next)
        made = 0
        tries = 0
        pops = [c for c in names if "population" in facts[c] and facts[c]["population"] > 0]
        seen = set()
        while made < CAPS["population"] and tries < 20000:
            tries += 1
            cont = self.rng.choice(CONTINENTS[:1] + CONTINENTS[2:])
            group = [c for c in pops if cont_of[c] == cont]
            if len(group) < 6:
                continue
            four = self.rng.sample(group, 4)
            vals = sorted(facts[c]["population"] for c in four)
            if any(b < a * 1.5 for a, b in zip(vals, vals[1:])):
                continue
            biggest = self.rng.random() < 0.6
            target = max(four, key=lambda c: facts[c]["population"]) if biggest else \
                min(four, key=lambda c: facts[c]["population"])
            sig = (tuple(sorted(four)), biggest)
            if sig in seen:
                continue
            seen.add(sig)
            fame = sum(self.fame(self.qb[c]) for c in four) / 4
            q = "Which of these has the most people?" if biggest else "Which of these has the fewest people?"
            extra = "; ".join(f"{c}: {facts[c]['population_text']}"
                              for c in sorted(four, key=lambda c: -facts[c]["population"]))
            self.add("population", "population", q, target, [c for c in four if c != target], self.qb[target],
                     fame, cont, "pop:" + "|".join(sorted(four)), extra)
            made += 1

    # ------------------------------------------------------------ airports
    def airports(self):
        links, names = defaultdict(Counter), defaultdict(Counter)
        for r in self.recs:
            for code, link, name, _h2 in r.get("i", []):
                if link:
                    links[code][unquote(link)] += 1
                if name:
                    names[code][name] += 1
        rows = []
        for code, ls in links.items():
            (top, n), *rest = ls.most_common()
            if rest and rest[0][1] * 3 > n:
                continue    # the code points at two places: ambiguous
            tgt = self.by_path.get(top)
            if not tgt or not tgt.get("b") or tgt["b"][0] not in CONTINENTS:
                continue
            airport = city = None
            if "Airport articles" in tgt["c"]:
                airport = tgt
                parent = self.by_title.get(tgt["b"][-2]) if len(tgt["b"]) > 1 else None
                if parent and "City articles" in parent["c"]:
                    city = parent
            elif "City articles" in tgt["c"]:
                city = tgt
            aname = airport["t"] if airport else None
            if not aname and names[code]:
                nm, cnt = names[code].most_common(1)[0]
                if (cnt >= 1 and len(nm) <= 45 and re.search(r"Airport|Aeropuerto|Aéroport|Aeroporto|Flughafen|Aerodrome|Airfield", nm)
                        and not re.search(r"[()\d:;,]|IATA", nm)):
                    aname = nm
            if not city and not aname:
                continue
            country = self.country_of(city or airport or tgt)
            if not country:
                continue
            rows.append({"code": code, "city": city, "airport": aname, "country": country,
                         "rec": airport or city or tgt, "mentions": n})
        cities_by_country = defaultdict(set)
        airports_by_cont = defaultdict(set)
        city_count = Counter(x["city"]["t"] for x in rows if x["city"])
        for x in rows:
            cont, sub = self.area_of(x["country"])
            x["cont"], x["sub"] = cont, sub
            if x["city"] and "(" not in x["city"]["t"]:
                cities_by_country[x["country"]].add(x["city"]["t"])
            if x["airport"]:
                airports_by_cont[cont].add(x["airport"])
        cities_by_cont = defaultdict(set)
        for x in rows:
            if x["city"] and "(" not in x["city"]["t"]:
                cities_by_cont[x["cont"]].add(x["city"]["t"])
        rows.sort(key=lambda x: (-(self.fame(x["city"] or x["rec"]) + 0.3 * math.log1p(x["mentions"])), x["code"]))
        used = Counter()
        for x in rows:
            fame = self.fame(x["city"] or x["rec"]) + 0.3 * math.log1p(x["mentions"])
            c = x["city"]
            if c and "(" not in c["t"] and "/" not in c["t"] and city_count[c["t"]] == 1 \
                    and used["city"] < CAPS["airport_city"]:
                d = self.pick(sorted(cities_by_cont[x["cont"]]), 3, [c["t"]],
                              prefer=sorted(cities_by_country[x["country"]]))
                if d:
                    self.add("airport_city", "airports", f"Your bag tag says {x['code']}. Which city are you flying to?",
                             c["t"], d, c, fame, x["cont"], "iata:" + x["code"],
                             f"{x['code']} is {x['airport']}." if x["airport"] else "")
                    used["city"] += 1
                    continue
            if x["airport"] and used["airport"] < CAPS["airport_name"]:
                pool = sorted(airports_by_cont[x["cont"]])
                d = self.pick(pool, 3, [x["airport"]], [a for a in pool if fold(a)[:1] == x["code"][0].lower()])
                if d:
                    self.add("airport_name", "airports", f"Which airport has the code {x['code']}?", x["airport"], d,
                             x["rec"], fame, x["cont"], "iata:" + x["code"])
                    used["airport"] += 1

    # ------------------------------------------------------------ phrasebooks
    @staticmethod
    def clean_phrase(lead, ital):
        s = lead.strip()
        s = re.split(r"\s\(|\(\s*$", s)[0].strip()
        s = s.rstrip(" .,!;:¡¿。！、").strip()
        if not s or s == ".":
            s = ital.strip().rstrip(" .,!").strip()
            ital = ""
            if re.search(r"[A-Z]{2}", s) and "-" in s:
                return None   # that's the pronunciation guide ("CHEE-yerrz"), not the word
        if not s or len(s) > 28 or re.search(r"[/:;=\d…]|\.\.\.|\bor\b|\(|\)|\[", s):
            return None
        if not is_latin(s):
            if any("LATIN" in unicodedata.name(ch, "") for ch in s) or ". " in s:
                return None   # mixed scripts: a messy line
            rom = ital.strip().rstrip(" .,!").strip()
            if rom and is_latin(rom) and len(rom) <= 28 and not re.search(r"[/:;=\d()]", rom):
                return f"{s} ({rom})"
            return None   # no romanisation: unreadable for most players, and it would stand out
        if ". " in s:
            return None
        return s[:1].upper() + s[1:]

    def phrases(self):
        books = {}
        for r in self.recs:
            if "ph" not in r or not any(c.split()[0] in ("Usable", "Guide", "Star") for c in r["c"]):
                continue
            lang = r["t"][: -len(" phrasebook")]
            got = {}
            for _h, en, lead, ital, _tail in r["ph"]:
                raw = en.strip()
                key = re.sub(r"[^a-z' ]", "", re.sub(r"\([^)]*\)", "", raw.lower().replace("’", "'"))).strip()
                if key not in PHRASES:
                    continue
                plain = "(" not in raw
                word = self.clean_phrase(lead, ital)
                if not word or fold(word.split(" (")[0]) == fold(PHRASES[key]):
                    continue
                if key not in got or (plain and not got[key][1]):
                    got[key] = (word, plain)
            if len(got) >= 4:
                books[lang] = (r, {k: v[0] for k, v in got.items()})
        langs = sorted(books)
        rows = []
        for lang in langs:
            r, got = books[lang]
            for key, word in got.items():
                rows.append((self.fame(r) - (0 if key in ("hello", "thank you", "yes", "no", "goodbye", "please") else 0.6),
                             lang, key, word))
        rows.sort(key=lambda x: (-x[0], x[1], x[2]))
        used = Counter()
        for fame, lang, key, word in rows:
            r, got = books[lang]
            latin = is_latin(word.split(" (")[0])
            phrase = PHRASES[key]
            # everyone else's word for the same phrase, same script family, not the same word
            others = [o for o in langs if o != lang and key in books[o][1]
                      and is_latin(books[o][1][key].split(" (")[0]) == latin]
            if len(others) < 3:
                continue
            same_word = {o for o in langs if key in books[o][1] and fold(books[o][1][key]) == fold(word)}
            if used["say"] < CAPS["phrase_say"] and used[("say", lang)] < 6:
                d = self.pick([books[o][1][key] for o in others if o not in same_word], 3, [word])
                if d:
                    self.add("phrase_say", "phrases", f"How do you say “{phrase}” in {lang}?", word, d, r, fame,
                             None, f"phr:{lang}:{key}")
                    used["say"] += 1
                    used[("say", lang)] += 1
                    continue
            if used["lang"] < CAPS["phrase_lang"] and used[("lang", lang)] < 3:
                d = self.pick([o for o in others if o not in same_word], 3, [lang])
                if d:
                    self.add("phrase_lang", "phrases", f"“{word}” means “{phrase.rstrip('!?')}” in which language?", lang, d,
                             r, fame - 0.3, None, f"phr:{lang}:{key}")
                    used["lang"] += 1
                    used[("lang", lang)] += 1

    # ------------------------------------------------------------ finish
    def sane(self, x):
        opts = [x["a"]] + x["d"]
        if len(opts) != 4 or any(not o or not o.strip() for o in opts) or not x["q"].strip():
            return False
        if len({fold(o) for o in opts}) != 4:
            return False
        for s in opts + [x["q"]]:
            if MARKUP.search(s) or len(s) > 140:
                return False
        if x["kind"] not in ("phrase_lang", "capital_of", "currency_of", "calling_of", "airport_name",
                             "population", "drive", "voltage") and fold(x["a"]) in fold(x["q"]):
            return False   # the answer is spelt out in the question
        if x["kind"] in ("capital_of", "currency_of", "calling_of", "phrase_lang", "airport_name") \
                and fold(x["a"]) and fold(x["a"]) in fold(x["q"]):
            return False
        return True

    def finish(self):
        rows, ids = [], set()
        dropped = Counter()
        for x in self.out:
            if not self.sane(x):
                dropped[x["kind"]] += 1
                continue
            i = qid(x["kind"], x["q"], x["a"], *(sorted(x["d"]) if x["kind"] in ("population", "drive", "voltage") else []))
            if i in ids:
                dropped["duplicate"] += 1
                continue
            ids.add(i)
            x["id"] = i
            rows.append(x)
        # difficulty: thirds by fame within each kind (1 = well-known, 3 = obscure)
        by_kind = defaultdict(list)
        for x in rows:
            by_kind[x["kind"]].append(x)
        for xs in by_kind.values():
            xs.sort(key=lambda x: -x["fame"])
            for n, x in enumerate(xs):
                x["diff"] = 1 + min(2, 3 * n // max(1, len(xs)))
        rows.sort(key=lambda x: x["id"])
        questions = [{"id": x["id"], "cat": x["cat"], "kind": x["kind"], "q": x["q"], "a": x["a"], "d": x["d"],
                      "diff": x["diff"], "cont": x["cont"], "k": x["k"], "src": x["src"], "path": x["path"],
                      **({"x": x["x"]} if x["x"] else {})} for x in rows]
        return questions, dropped


def load_facts(path):
    recs, meta, inlinks = [], {}, {}
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if "_meta" in r:
                meta, inlinks = r["_meta"], r["_inlinks"]
            else:
                recs.append(r)
    return {"recs": recs, "meta": meta, "inlinks": inlinks}


def generate(facts_path=FACTS, out_path=OUT):
    b = Bank(load_facts(facts_path))
    b.places()
    b.country_questions()
    b.airports()
    b.phrases()
    questions, dropped = b.finish()
    m = b.meta
    bank = {
        "version": 1,
        "source": {"zim": m.get("File", ""), "name": m.get("Name", ""), "date": m.get("Date", ""),
                   "title": m.get("Title", "Wikivoyage"), "site": m.get("Source", "en.wikivoyage.org"),
                   "license": "CC BY-SA 4.0"},
        "categories": [{"id": i, "name": n, "blurb": bl} for i, n, bl in CATEGORIES],
        "continents": CONTINENTS,
        "questions": questions,
    }
    data = json.dumps(bank, ensure_ascii=False, separators=(",", ":"), sort_keys=False).encode("utf-8")
    with open(out_path, "wb") as f:
        # mtime=0: the same bank gives the same bytes (no noise in git)
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0, compresslevel=9, filename="") as gz:
            gz.write(data)
    kinds = Counter(q["kind"] for q in questions)
    cats = Counter(q["cat"] for q in questions)
    print(f"{len(questions)} questions, {len(data) / 1e6:.2f} MB raw, {Path(out_path).stat().st_size / 1e6:.2f} MB gzipped")
    print("by category:", dict(sorted(cats.items())))
    print("by kind:", dict(sorted(kinds.items())))
    print("dropped by the sanity pass:", dict(dropped))


def main(argv):
    if len(argv) >= 2 and argv[0] in ("extract", "all"):
        sys.path.insert(0, str(HERE))
        import extract   # needs libzim + lxml
        FACTS.parent.mkdir(exist_ok=True)
        extract.main(argv[1], str(FACTS))
        if argv[0] == "extract":
            return
    elif not argv or argv[0] != "generate":
        print(__doc__)
        sys.exit(2)
    generate()


if __name__ == "__main__":
    main(sys.argv[1:])

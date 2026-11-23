# The question bank

## File format (`bank/questions.json.gz`)

Gzipped UTF-8 JSON, written compact (`separators=(",", ":")`, `ensure_ascii=False`) with
gzip `mtime=0`, `filename=""`, level 9, so the same input gives byte-identical output.

```json
{
  "version": 1,
  "source": {"zim": "wikivoyage_en_all_nopic_2026-09.zim", "name": "wikivoyage_en_all",
             "date": "2026-09-13", "title": "Wikivoyage", "site": "en.wikivoyage.org",
             "license": "CC BY-SA 4.0"},
  "categories": [{"id": "places", "name": "Where in the world", "blurb": "Which country is it in?"}, ...],
  "continents": ["Africa", "Antarctica", "Asia", "Europe", "North America", "Oceania", "South America"],
  "questions": [
    {"id": "001b9c582d", "cat": "places", "kind": "city_country",
     "q": "Which country is Sopron in?", "a": "Hungary", "d": ["Poland", "Switzerland", "Slovenia"],
     "diff": 2, "cont": "Europe", "k": "place:Sopron", "src": "Sopron", "path": "Sopron"}
  ]
}
```

Question fields:

| Field | Meaning |
|---|---|
| `id` | First 10 hex chars of SHA-1 of `kind|q|a` (plus sorted distractors for `population`, `drive`, `voltage`) |
| `cat` | Category id (one of the 8 below) |
| `kind` | Generator kind (finer than category) |
| `q`, `a`, `d` | Question text, the right answer, exactly three distractors |
| `diff` | 1-3, by "fame" thirds within the kind |
| `cont` | Continent, or `null` (all phrasebook questions) |
| `k` | Group key: questions sharing a `k` never appear in the same round (e.g. `cap:Japan` for both capital directions) |
| `src`, `path` | Wikivoyage article title and ZIM path (for the "From the article" line and the guide link) |
| `x` | Optional extra note shown after answering (currency code, airport name, population list); present on 545 questions |

The app (`quiz.Bank`) uses `source`, `categories`, `continents`, `questions`; it ignores
`version` and `kind` (only tests look at `kind`).

## Counts in the 0.1.0 bank (checked by loading the file)

- 5,398 questions; difficulty split 1803 / 1800 / 1795.
- By category: places 1242, practical 1086, phrases 826, airports 700, regions 691,
  capitals 426, population 260, money 167.
- By kind: city_country 1092, city_region 691, phrase_say 546, airport_city 450,
  phrase_lang 280, population 260, airport_name 250, drive 222, emergency 219, capital 213,
  capital_of 213, calling 212, calling_of 212, utc 189, park_country 150, currency 134,
  currency_of 33, voltage 32.
- By continent: Europe 1420, Asia 1111, North America 819, Africa 609, Oceania 349,
  South America 262, Antarctica 2, none 826 (phrasebook). Antarctica is under the
  `3 * ROUND_LENGTH` threshold, so it isn't offered as a region.

## Two stages

### Stage 1: `bank/extract.py` (ZIM -> facts)

Needs `libzim` (Python 3.11+) and `lxml`, so it runs in `python:3.12-slim`. Iterates every
non-redirect `text/html` entry that has `"wgCategories"` in its page source and writes one
gzipped JSON line per article to `.cache/facts.jsonl.gz` (git-ignored; README says ~45 s):

| Key | Content |
|---|---|
| `t`, `p` | title, path |
| `n` | `wgWMEPageLength` (or raw HTML length) |
| `c` | categories filtered by `KEEP_CATS` (articles/cities/airports/phrasebook/status...) |
| `b` | geo breadcrumbs (`ext-geocrumbs-breadcrumbs`), continent first, the article last |
| `q` | `#quickbar` rows: `{label: [text, wiki-link or None]}` |
| `i` | `{{IATA}}` hits: `[code, linked article, listing name, h2 section]` |
| `ph` | phrasebook `<dt>English</dt><dd>...</dd>` pairs (outside tables only) |

The last line is `{"_meta": {...ZIM metadata, "File": basename}, "_inlinks": {path: count}}`
(in-links count each linking article once per target).

### Stage 2: `bank/build.py generate` (facts -> bank)

Plain Python 3.10+, no dependencies. `random.Random(SEED)` with `SEED = 20261122`. Order of
generation matters for determinism: `places()`, `country_questions()`, `airports()`,
`phrases()`, then `finish()`.

Key sets built in `Bank.__init__`:
- `countries`: titles in "Country articles" whose breadcrumb starts with a continent, minus `DISPUTED`.
- `qb`: any article with a quickbar and continent breadcrumb (countries and territories),
  excluding "European Union", `DISPUTED` and titles with `/`.

`fame(rec)` = `log1p(inlinks) + 0.4 * status (Usable 1 / Guide 2 / Star 3) + 0.25 * log1p(length/1000)`.
Used to sort (famous first, so per-kind `CAPS` keep the best-known) and later for difficulty.

What each generator does (details beyond `bank/README.md`'s table):
- **places**: city/park articles (no `/` or `(` in title) -> "Which country is X in?" /
  "X is in which country?". Country = first breadcrumb in `countries`, else in `qb`.
  Max 25 per country per kind. Distractors: real countries in the same sub-continent, then
  same continent, then territories. Wording becomes "country or territory" if any option
  is not in `countries`.
- **regions**: first breadcrumb under the country (US: first breadcrumb whose base is a US
  state). Needs >= 4 sibling regions; max 40 per country. "Which state/region of [the] C is X in?"
- **capitals/money/practical** (quickbar): capital (both directions, only if unique and not a
  giveaway like "Kuwait City"), currency (only clean `Name (ISO)`; reverse only if unique),
  calling code (both directions, only if unique), driving side (odd one out), voltage (only
  the 100-127 V direction), single-offset time zone, emergency number (distractors from a
  fixed `EMERGENCY_POOL` minus the country's own numbers; answer must be >= 3 digits).
- **population**: four places from one continent (Antarctica skipped, continent needs >= 6
  with population), each >= 1.5x the next; 60% "most people", 40% "fewest". Extra note
  lists all four populations. Up to 260, up to 20,000 tries.
- **airports**: code -> most-linked target; skipped if the runner-up has > 1/3 of the links
  (ambiguous). `airport_city` ("Your bag tag says XXX...") when the city is unambiguous;
  otherwise `airport_name` from an airport article title or a clean listing name.
- **phrases**: Usable/Guide/Star phrasebooks with >= 4 of the 16 phrases in `PHRASES`.
  Non-Latin words need a Latin romanisation (shown as `word (romanisation)`), else dropped.
  `phrase_say` up to 6 per language; otherwise `phrase_lang` up to 3 per language.
  Distractors from languages in the same script family, excluding languages whose word is
  the same.

`add()` rewrites shown country titles: "Georgia (country)" -> "Georgia" in answers,
distractors and question text. `SKIP_KEYS = {"cap:Israel", "place:Jerusalem"}`.

### `finish()`: sanity pass, ids, difficulty

Drops a question unless: exactly 4 non-empty options, 4 distinct after `fold()` (lower case,
accents and punctuation stripped), no `MARKUP` match (`[]{}<>|=`, `''`, `&entity;`, `http(s):`),
nothing over 140 chars, and the answer not spelt out in the question (kind-specific rule).
Duplicate ids are dropped. Difficulty: within each kind, sort by fame descending and split
into thirds (1 = famous). Questions are sorted by id. `generate()` prints totals, MB raw and
gzipped, counts by category and kind, and what the sanity pass dropped.

## Regenerating

Follow `bank/README.md` (or the VS Code task "Bank: regenerate from the guide's ZIM"):

```sh
# Git Bash: export MSYS_NO_PATHCONV=1 first
docker run --rm -v airplanemode_guide-data:/data:ro -v "H:/Github/airplanemode/airplanemode-wayfarer:/src" -w /src \
  python:3.12-slim sh -c "pip install -q libzim==4.1.0 lxml==6.1.3 && python bank/build.py all /data/zim/wikivoyage_en_all_nopic_2026-09.zim"
python bank/build.py generate      # stage 2 only, while tuning (needs .cache/facts.jsonl.gz)
```

The ZIM file name is hard-coded in both the README command and the VS Code task; update both
when the guide downloads a newer ZIM. Then: read the printed counts, spot-check a sample,
run the tests (they include bank sanity checks and the < 2 MB size limit), commit
`bank/questions.json.gz` together with the `build.py` change, update the README's
"Snapshot" line, release. Decision 0016: "regenerate it (and release) when the guide's ZIM
is updated".

`build.py` with no args, or an unknown first arg, prints its docstring and exits 2.
`tests` import `DISPUTED` from `bank/build.py`, so that module must stay importable without
libzim (it only imports `extract` inside `main()` for `extract`/`all`).

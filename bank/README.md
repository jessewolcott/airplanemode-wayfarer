# The question bank

`questions.json.gz` is generated from a Wikivoyage ZIM (the same Kiwix file the Travel Guide
serves) by `build.py`, and committed. The app reads only this file: no ZIM, no volume, no
dependency on the guide being installed. The source ZIM's file name and date are recorded in the
bank (`source`) and shown in the app's About sheet.

## Regenerating (when a new Wikivoyage ZIM comes out)

1. Get the new ZIM. On the dev machine the guide's volume already has one:
   `airplanemode_guide-data:/data/zim/wikivoyage_en_all_nopic_<date>.zim`
   (or download it from <https://download.kiwix.org/zim/wikivoyage/>).
2. Run both stages in a container (libzim needs Python 3.11+):

   ```sh
   # Git Bash: export MSYS_NO_PATHCONV=1 first
   docker run --rm -v airplanemode_guide-data:/data:ro -v "H:/Github/airplanemode/airplanemode-wayfarer:/src" -w /src \
     python:3.12-slim sh -c "pip install -q libzim==4.1.0 lxml==6.1.3 && python bank/build.py all /data/zim/wikivoyage_en_all_nopic_2026-09.zim"
   ```

   Stage 1 (`extract`, ~45 s) writes `.cache/facts.jsonl.gz` (git-ignored). Stage 2
   (`generate`, a few seconds, plain Python 3.10+) writes `questions.json.gz`; you can rerun it
   on its own while tuning: `python bank/build.py generate`.
3. Read the printed counts (per category, per kind, and what the sanity pass dropped), spot-check
   a sample, run `python tests/test_wayfarer.py`, commit the new bank, release.

The build is deterministic: the same ZIM gives byte-identical output (fixed seed, gzip mtime 0).
A question's id is a hash of its kind, text and answer (and, for the "odd one out" kinds
`population`, `drive` and `voltage`, whose text is the same for many questions, its sorted wrong
answers too), so the browser's "recently seen" list keeps working across rebuilds for the
questions that didn't change.

## What the questions are made from

Everything comes from parts of the rendered HTML that are reliably structured:

| Category | Source in the HTML | Kinds |
|---|---|---|
| Where in the world | breadcrumbs (`ext-geocrumbs-breadcrumbs`) of city and park articles | which country is X in |
| Regions | the breadcrumb just under the country (a US state for the US) | which region/state of C is X in |
| Capitals | country quickbar (`#quickbar`): Capital | capital of C; X is the capital of which country |
| Money | quickbar: Currency, only clean `name (ISO)` values | currency of C; the X (ISO) is the money of which country |
| Airport codes | the `{{IATA}}` template (`<b>KIX</b><sup>…IATA</sup>`): what the code links to, airport articles, listing names | bag tag XXX: which city; which airport has code XXX |
| Phrasebook | `<dt>English</dt><dd>translation</dd>` in Usable/Guide/Star phrasebooks | how do you say P in L; "word" means P in which language |
| Practicalities | quickbar: Driving side, Electricity (voltage class), Time zone (single offset only), Emergencies, Country code | odd one out (drives on the left/right, 100–127 V); standard time; emergency number; calling code both ways |
| Crowds | quickbar: Population | which of four has the most/fewest people (each ≥1.5× the next) |

Distractors come from the same sub-continent first (the breadcrumb under the continent), then
the same continent; real countries before territories. Phrase distractors are the same phrase in
other languages of the same script family; a language whose word is the same isn't offered.

Difficulty (1–3) is the third the question falls in, within its kind, by how well known the
subject is: links to its article from other articles (log), plus a little for article status
(Usable/Guide/Star) and length.

Left out on purpose:
- places whose status is disputed or little recognised (`DISPUTED` in `build.py`), and Israel's
  capital: a quiz isn't the place to take sides;
- titles with a disambiguator ("Victoria (British Columbia)") and districts ("London/Soho");
- dishes from "Eat" sections, "Cities" lists and plug types: too free-form to check;
- continent questions: Wikivoyage's continents are travel regions (the Caucasus is under
  Europe, the Middle East under Asia), which would make "right" answers look wrong.

The sanity pass drops any question without exactly four distinct non-empty options, with wiki
markup or HTML left in, or that spells the answer out in the question, and removes duplicates.

Wikivoyage text is available under CC BY-SA 4.0; the questions are adapted from it, so the
bank is too.

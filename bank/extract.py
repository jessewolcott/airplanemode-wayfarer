"""Stage 1 of the question bank: pull structured facts out of a Wikivoyage ZIM.

Needs libzim (Python 3.11+) and lxml, so it runs in a python:3.12-slim container; see
bank/README.md. Writes one JSON object per article (gzipped JSON lines) with only the bits the
generator uses, plus a final {"_meta": ...} record with the ZIM's name/date and in-link counts.
Not imported by the app.
"""
import gzip
import html
import json
import re
import sys
import time
from collections import Counter

import libzim.reader
from lxml import html as lhtml

CATS_RE = re.compile(r'"wgCategories":(\[[^\]]*\])')
LEN_RE = re.compile(r'"wgWMEPageLength":(\d+)')
CRUMBS_RE = re.compile(r'<span class="ext-geocrumbs-breadcrumbs">(.*?)</span></div>', re.S)
CRUMB_RE = re.compile(r'<bdi>(?:<a href="([^"]*)"[^>]*>)?([^<]*)(?:</a>)?</bdi>')
LINK_RE = re.compile(r'<a rel="mw:WikiLink" href="([^"#?]+)')
IATA_RE = re.compile(
    r'<b[^>]*>(?:<a [^>]*href="([^"#]*)[^"]*"[^>]*>)?([A-Z]{3})(?:</a>)?</b>\s*<sup[^>]*>\s*<small>\s*'
    r'<a [^>]*href="IATA"')
H2_RE = re.compile(r'<h2 id="([^"]*)"')
LISTING_NAME_RE = re.compile(r'class="fn org listing-name">(.*?)</span>(?:<span[^>]*>[^<]*</span>)?\s*\(\s*$', re.S)
TAG_RE = re.compile(r"<[^>]+>")

KEEP_CATS = re.compile(r"(articles|cities|UNESCO|Destinations? of the month|Phrasebook|airports?)$|^(Star|Guide|Usable|Outline)", re.I)


def text(s):
    return " ".join(html.unescape(TAG_RE.sub(" ", s)).split())


def quickbar(raw):
    i = raw.find('id="quickbar"')
    if i < 0:
        return None
    j = raw.find("</table>", i)
    frag = lhtml.fragment_fromstring(raw[raw.rfind("<div", 0, i):j + 8] + "</div>")
    out = {}
    for tr in frag.iter("tr"):
        tds = tr.findall("td")
        if len(tds) != 2 or "info" not in (tds[0].get("class") or ""):
            continue
        label = " ".join(tds[0].text_content().split())
        val = " ".join(tds[1].text_content().split())
        a = tds[1].find(".//a")
        out[label] = [val, a.get("href") if a is not None and a.get("rel") == "mw:WikiLink" else None]
    return out


def iata(raw):
    """[code, linked airport article or None, listing name or None, h2 section]."""
    out = []
    heads = [(m.start(), m.group(1)) for m in H2_RE.finditer(raw)]
    for m in IATA_RE.finditer(raw):
        before = raw[max(0, m.start() - 1500):m.start()]
        # drop the "<span class="nickname ...">" opener and see if a listing name ends right before "("
        before = re.sub(r'<span class="nickname[^"]*">\s*$', "", before)
        ln = LISTING_NAME_RE.search(before)
        name = text(ln.group(1)) if ln else None
        h2 = ""
        for pos, h in heads:
            if pos < m.start():
                h2 = h
        out.append([m.group(2), m.group(1) or None, name, h2])
    return out


def phrases(raw):
    """Phrasebook <dt>English</dt><dd>translation</dd> pairs: [h3, english, lead text, first <i>]."""
    doc = lhtml.document_fromstring(raw)
    out = []
    head = ""
    for el in doc.iter("h2", "h3", "dt"):
        if el.tag in ("h2", "h3"):
            head = " ".join(el.text_content().split())
            continue
        if any(a.tag == "table" for a in el.iterancestors()):
            continue   # sidebars ("Common signs") are reversed: foreign word in <dt>
        dd = el.getnext()
        if dd is None or dd.tag != "dd":
            continue
        en = " ".join(el.text_content().split())
        lead = " ".join((dd.text or "").split())
        first_i = dd.find("i")
        it = " ".join(first_i.text_content().split()) if first_i is not None else ""
        tail = " ".join((first_i.tail or "").split()) if first_i is not None else ""
        out.append([head, en, lead, it, tail[:40]])
    return out


def main(zim_path, out_path):
    a = libzim.reader.Archive(zim_path)
    meta = {k: bytes(a.get_metadata(k)).decode("utf-8", "replace")
            for k in ("Name", "Title", "Date", "Source", "Language", "Flavour") if k in a.metadata_keys}
    import os
    meta["File"] = os.path.basename(zim_path)
    inlinks = Counter()
    n = 0
    t0 = time.time()
    with gzip.open(out_path, "wt", encoding="utf-8") as out:
        for i in range(a.entry_count):
            e = a.get_entry_by_id(i)
            if e.is_redirect:
                continue
            item = e.get_item()
            if not item.mimetype.startswith("text/html"):
                continue
            raw = bytes(item.content).decode("utf-8", "replace")
            cm = CATS_RE.search(raw)
            if not cm:
                continue
            cats = [c for c in json.loads(cm.group(1)) if KEEP_CATS.search(c)]
            lm = LEN_RE.search(raw)
            rec = {"t": e.title, "p": e.path, "n": int(lm.group(1)) if lm else len(raw), "c": cats}
            cr = CRUMBS_RE.search(raw)
            if cr:
                rec["b"] = [html.unescape(t) for _h, t in CRUMB_RE.findall(cr.group(1))]
            qb = quickbar(raw)
            if qb:
                rec["q"] = qb
            ia = iata(raw)
            if ia:
                rec["i"] = ia
            if e.title.endswith(" phrasebook"):
                rec["ph"] = phrases(raw)
            for target in set(LINK_RE.findall(raw)):
                if target != e.path:
                    inlinks[target] += 1
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            if n % 5000 == 0:
                print(f"{n} articles, {time.time() - t0:.0f} s", file=sys.stderr)
        out.write(json.dumps({"_meta": meta, "_inlinks": inlinks}, ensure_ascii=False) + "\n")
    print(f"done: {n} articles in {time.time() - t0:.0f} s -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

"""Ranking by local citations and the BibTeX of the selection.

The local citation score (LCS) of a paper is the number of papers in the final pool that cite it; the local cited
references (LCR) are the number of papers in the pool it cites (Garfield's HistCite; van Eck & Waltman's
CitNetExplorer). Papers are ranked by LCS per year since publication, so a recent paper cited 4 times in 2 years
ranks with an old one cited 20 times in 10 years; ties are broken by LCS, then LCR, then global citations."""
import re
import unicodedata

STOP_KEY = {"a", "an", "the", "on", "of", "in", "for", "to", "and", "with", "towards", "toward", "from", "by"}


def local_citations(pool, current_year):
    """Sets lcs, lcr and lcs_per_year on every record of the pool (citations via OpenAlex reference lists)."""
    by_id = {r["openalex_id"]: r for r in pool if r.get("openalex_id")}
    for r in pool:
        r["lcs"], r["lcr"] = 0, 0
    for r in pool:
        refs = {w for w in (r.get("openalex") or {}).get("referenced_works", []) if w in by_id and w != r["openalex_id"]}
        r["lcr"] = len(refs)
        for w in refs:
            by_id[w]["lcs"] += 1
    for r in pool:
        age = max(1, current_year - (r.get("year") or current_year) + 1)
        r["lcs_per_year"] = r["lcs"] / age


def isolated(pool, network):
    """Record ids of the papers in pool that neither cite nor are cited by any other paper of network (e.g. all
    papers that passed the eligibility checks, older ones included). Needs a reference list in OpenAlex; papers
    without one are never counted as isolated."""
    ids = {r["openalex_id"] for r in network if r.get("openalex_id")}
    cited = set()
    for r in network:
        cited |= {w for w in (r.get("openalex") or {}).get("referenced_works", []) if w != r.get("openalex_id")}
    out = set()
    for r in pool:
        refs = (r.get("openalex") or {}).get("referenced_works") or []
        if not refs:
            continue
        cites_one = any(w in ids and w != r["openalex_id"] for w in refs)
        if not cites_one and r["openalex_id"] not in cited:
            out.add(r["record_id"])
    return out


def ranked(pool):
    return sorted(pool, key=lambda r: (-r["lcs_per_year"], -r["lcs"], -r["lcr"], -(r.get("cited_by_count") or 0),
                                       r["record_id"]))


def allocate(n, weights):
    """Split n over the strata in proportion to the weights (largest remainder)."""
    total = sum(weights.values())
    raw = {s: n * w / total for s, w in weights.items()}
    quota = {s: int(v) for s, v in raw.items()}
    for s in sorted(raw, key=lambda s: raw[s] - quota[s], reverse=True)[: n - sum(quota.values())]:
        quota[s] += 1
    return quota


def select(ordered, n, allocation="proportional"):
    """Top n of the ranked pool. 'global': plainly the first n. 'proportional' / 'equal': stratified by the search
    that found a paper (papers only in the bib files form their own stratum); each stratum gets a share of n in
    proportion to its size in the pool (or an equal share) and fills it with its highest-ranked papers; a paper
    found by several searches is taken once. Returns (papers in rank order, quota per stratum)."""
    if allocation == "global":
        return ordered[:n], {}
    strata = {}
    for r in ordered:
        for s in r["queries"] or ["only in the bib files"]:
            strata.setdefault(s, []).append(r)
    quota = allocate(n, {s: (len(v) if allocation == "proportional" else 1) for s, v in strata.items()})
    chosen, seen = [], set()
    for s in sorted(quota, key=quota.get, reverse=True):
        need = quota[s]
        for r in strata[s]:
            if need == 0:
                break
            if r["record_id"] not in seen:
                chosen.append(r); seen.add(r["record_id"]); need -= 1
    for r in ordered:                                   # a stratum ran short: fill with the best remaining
        if len(chosen) >= n:
            break
        if r["record_id"] not in seen:
            chosen.append(r); seen.add(r["record_id"])
    return sorted(chosen, key=lambda r: r["rank"]), quota


# ------------------------------------------------------------------------------------------ BibTeX

def _ascii(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()


def make_key(rec, taken):
    first = re.split(r"\s+and\s+", rec.get("authors", ""), maxsplit=1)[0]
    surname = first.split(",")[0] if "," in first else (first.split() or ["Anon"])[-1]
    surname = re.sub(r"[^A-Za-z]", "", _ascii(surname)).capitalize() or "Anon"
    words = [w for w in re.findall(r"[A-Za-z0-9]+", _ascii(rec.get("title", ""))) if w.lower() not in STOP_KEY]
    base = f"{surname}{rec.get('year') or ''}{words[0].capitalize() if words else 'Untitled'}"
    key, n = base, 1
    while key in taken:
        n += 1
        key = f"{base}{chr(ord('a') + n - 1)}"
    taken.add(key)
    return key


def bibtex_entry(rec, key, note):
    f = rec.get("raw_fields") or {}
    etype = {"journal": "article", "conference": "inproceedings", "book": "incollection"}.get(rec["venue_kind"], "misc")
    venue = f.get("booktitle") or f.get("journal") or ""
    fields = [("title", f.get("title", rec["title"])), ("author", f.get("author", rec["authors"])),
              ("year", f.get("year", rec.get("year") or "")),
              ("journal" if etype == "article" else "booktitle", f.get("journal") if etype == "article" else venue)]
    fields += [(k, f.get(k, "")) for k in ("volume", "number", "pages", "publisher", "series", "isbn", "issn")]
    fields += [("doi", rec.get("doi", "")), ("url", f"https://doi.org/{rec['doi']}" if rec.get("doi") else f.get("url", "")),
               ("abstract", f.get("abstract", "")), ("keywords", f.get("keywords", "")), ("annote", note)]
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields if v)
    return f"@{etype}{{{key},\n{body}\n}}\n"

"""Reading the raw Scopus / ACM DL exports and the Zotero library, normalising fields, deduplication."""
import re
import unicodedata
from pathlib import Path


# ---------------------------------------------------------------- BibTeX parsing

def _balance(value):
    """Drop unmatched braces (some exported abstracts contain set notation with a stray brace)."""
    out, depth = [], 0
    for c in value:
        if c == "}" and depth == 0:
            continue
        depth += (c == "{") - (c == "}")
        out.append(c)
    s = "".join(out)
    while depth:
        i = s.rindex("{")
        s, depth = s[:i] + s[i + 1:], depth - 1
    return s


def parse_bib(text):
    """Line-oriented parser: every entry starts with '@type{' and every field with 'name = ' at the start
    of a line (true for Scopus, ACM DL and Zotero/Better BibTeX exports). Robust against unbalanced
    braces and multi-line values. Returns [{'type', 'key', 'fields': {name: value}}]."""
    entries = []
    for chunk in re.split(r"(?m)^(?=@\w+\s*\{)", text):
        m = re.match(r"@(\w+)\s*\{\s*([^,\n]*),", chunk)
        if not m or m.group(1).lower() in ("comment", "preamble", "string"):
            continue
        tail = re.search(r"\}\s*(?:%[^\n]*\s*)*\Z", chunk)     # '% ...' comment lines after the entry
        if tail:
            chunk = chunk[:tail.start() + 1]
        body = chunk[m.end():]
        starts = list(re.finditer(r"(?m)^[ \t]*([\w\-]+)[ \t]*=[ \t]*", body))
        fields = {}
        for n, s in enumerate(starts):
            seg = body[s.end(): starts[n + 1].start() if n + 1 < len(starts) else len(body)].rstrip()
            if n + 1 == len(starts) and seg.endswith("}"):
                seg = seg[:-1].rstrip()                       # closing brace of the entry
            seg = seg.rstrip(",").rstrip()
            if (seg.startswith("{") and seg.endswith("}")) or (seg.startswith('"') and seg.endswith('"')):
                seg = seg[1:-1]
            fields.setdefault(s.group(1).lower(), _balance(seg))
        entries.append({"type": m.group(1).lower(), "key": m.group(2).strip(), "fields": fields})
    return entries


def read_bib(path):
    return parse_bib(Path(path).read_text(encoding="utf-8", errors="replace"))


# ---------------------------------------------------------------- normalisation

def clean(value):
    """Plain text for display/CSV: no braces, LaTeX accents, HTML tags or repeated whitespace."""
    v = re.sub(r"<[^>]+>", "", value or "")
    v = re.sub(r"\\['`^\"~=.uvHc]\{?(\w)\}?", r"\1", v)
    v = re.sub(r"\\([&%$#_])", r"\1", v)
    v = v.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", v).strip()


def norm_doi(doi):
    d = (doi or "").strip().lower()
    d = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", d)
    return d.rstrip(".")


def norm_title(title):
    t = re.sub(r"<[^>]+>", "", title or "")
    t = re.sub(r"\\[a-zA-Z]+|\\.|[{}$^_]", "", t)
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", t.lower())


def first_author_surname(authors):
    first = re.split(r"\s+and\s+", clean(authors), maxsplit=1)[0].strip()
    if not first:
        return ""
    surname = first.split(",")[0] if "," in first else first.split()[-1]
    surname = unicodedata.normalize("NFKD", surname).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z]", "", surname.lower())


def to_year(value):
    m = re.search(r"(1[89]\d\d|20\d\d)", value or "")
    return int(m.group(1)) if m else None


def page_count(fields):
    """Number of pages from ACM 'numpages' or a 'first - last' range; None if unknown/implausible."""
    num = re.sub(r"\D", "", fields.get("numpages", ""))
    if num:
        n = int(num)
        return n if 0 < n <= 500 else None
    m = re.match(r"^\D*(\d+)\s*[-\u2013]+\s*\D*(\d+)\s*$", fields.get("pages", "").strip())
    if m:
        first, last = int(m.group(1)), int(m.group(2))
        n = last - first + 1
        return n if 0 < n <= 500 else None
    return None


def scopus_cited_by(note):
    m = re.search(r"Cited by:\s*(\d+)", note or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------- records

def make_record(entry, query, database):
    """One raw hit (a search result or a bib entry) as a flat dict. Venue fields of non-Scopus sources go into
    the acm_* slots (booktitle, journal, series, number)."""
    f = entry["fields"]
    doi = norm_doi(f.get("doi", ""))
    dois = {doi} | {norm_doi(d) for d in f.get("alt_doi", "").split(";")}
    database_is_scopus = database == "Scopus"
    return {
        "query": query,
        "database": database,
        "source_key": entry["key"],
        "entry_type": entry["type"],
        "title": clean(f.get("title", "")),
        "abstract": clean(f.get("abstract", "")),
        "authors": clean(f.get("author", "")),
        "year": to_year(f.get("year", "")),
        "doi": doi,
        "dois": {d for d in dois if d},
        "url": f.get("url", ""),
        "keywords": clean(f.get("keywords", "") or f.get("author_keywords", "")),
        # venue information (kept separately per source; merged later)
        "scopus_type": f.get("type", "") if database_is_scopus else "",
        "scopus_source": clean(f.get("journal", "")) if database_is_scopus else "",
        "acm_booktitle": clean(f.get("booktitle", "")) if not database_is_scopus else "",
        "acm_journal": clean(f.get("journal", "")) if not database_is_scopus else "",
        "acm_series": clean(f.get("series", "")) if database == "ACM" else "",
        "acm_number": clean(f.get("number", "")) if database == "ACM" else "",
        "issn": clean(f.get("issn", "")),
        "pages_n": page_count(f),
        "scopus_cited_by": scopus_cited_by(f.get("note", "")) if database == "Scopus" else None,
        "raw_fields": dict(f),                       # original BibTeX fields, for writing the selection
    }


def load_exports(searches_dir, queries):
    """All raw hits of the queries in scope. Returns (records, per-query/per-database counts)."""
    records, counts = [], {}
    for q in queries:
        qdir = Path(searches_dir) / q
        if not qdir.is_dir():
            raise FileNotFoundError(f"search folder not found: {qdir}")
        for path in sorted(qdir.glob("*.bib")):
            name = path.name.lower()
            database = "Scopus" if name.startswith("scopus") else "ACM" if name.startswith("acm") else None
            if database is None:
                continue
            entries = read_bib(path)
            counts[(q, database)] = len(entries)
            records.extend(make_record(e, q, database) for e in entries)
    return records, counts


def load_bib_sources(sources):
    """Further bib files that join the pool on equal terms, e.g. the Zotero collection.
    sources: [(path, name)]. Returns (hits, {name: count})."""
    hits, counts = [], {}
    for path, name in sources:
        entries = read_bib(path)
        counts[name] = len(entries)
        hits.extend(make_record(e, "", name) for e in entries)
    return hits, counts


# ---------------------------------------------------------------- deduplication

class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def same_work(a, b):
    """Title-based duplicate test for two hits with the same normalised title."""
    if a["dois"] and b["dois"] and a["dois"] & b["dois"]:
        return True
    ya, yb = a["year"], b["year"]
    close = ya is not None and yb is not None and abs(ya - yb) <= 1
    sa, sb = first_author_surname(a["authors"]), first_author_surname(b["authors"])
    same_author = sa == sb
    if len(norm_title(a["title"])) < 25:           # short generic titles: require exact year and author
        return ya == yb and same_author
    if not (a["dois"] and b["dois"]):               # DOI missing on one side (the author check keeps a
        return close and (same_author or not sa or not sb)   # DOI-less hit from bridging two works)
    return ya == yb and same_author                 # different DOIs: e.g. proceedings paper and SEN reprint


def deduplicate(hits):
    """Merge hits of the same work across databases and queries. Returns merged records.
    Duplicates: shared DOI; or same normalised title and (year within 1 if a DOI is missing on one side,
    or same year and first-author surname if both have different DOIs)."""
    uf = _UnionFind(len(hits))
    by_doi, by_title = {}, {}
    for i, h in enumerate(hits):
        for d in h["dois"]:
            if d in by_doi:
                uf.union(i, by_doi[d])
            else:
                by_doi[d] = i
        t = norm_title(h["title"])
        if t:
            by_title.setdefault(t, []).append(i)
    for idxs in by_title.values():
        for x in range(len(idxs)):
            for y in range(x + 1, len(idxs)):
                if same_work(hits[idxs[x]], hits[idxs[y]]):
                    uf.union(idxs[x], idxs[y])
    groups = {}
    for i in range(len(hits)):
        groups.setdefault(uf.find(i), []).append(hits[i])
    return [_merge(g) for g in groups.values()]


def _merge(group):
    """Scopus hit as base (it has document type and citation count), then ACM, then other bib files; gaps filled
    from the others."""
    prio = {"Scopus": 0, "ACM": 1}
    group = sorted(group, key=lambda h: (prio.get(h["database"], 2), h["query"]))
    rec = dict(group[0])
    rec["raw_fields"] = dict(group[0].get("raw_fields") or {})
    for h in group[1:]:
        for k, v in h.items():
            if k in ("query", "database", "source_key", "dois", "raw_fields"):
                continue
            if not rec.get(k) and v:
                rec[k] = v
        for k, v in (h.get("raw_fields") or {}).items():
            rec["raw_fields"].setdefault(k, v)
    rec["dois"] = set().union(*(h["dois"] for h in group))
    rec["doi"] = next((h["doi"] for h in group if h["doi"]), "") or (sorted(rec["dois"])[0] if rec["dois"] else "")
    rec["queries"] = sorted({h["query"] for h in group if h["query"]})
    rec["databases"] = sorted({h["database"] for h in group})
    rec["source_keys"] = sorted({f'{h["database"]}:{h["source_key"]}' for h in group})
    rec["abstract"] = max((h["abstract"] for h in group), key=len)
    rec.pop("query"), rec.pop("database"), rec.pop("source_key")
    return rec

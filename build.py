"""Builds the searchable paper list from the study selection (selection/): papers.js (data of index.html: every
record of the searches with what the selection steps look at, so the page can apply them with other settings),
papers.csv and papers.bib (the final pool) and records.csv (all records with status, step and reason).

Records cited in the paper are marked; they come from cited_in_paper.csv, which `--paper <dir of SanitySurvey.tex>`
rewrites from the paper's sources.

Keywords are those of the bib records (author keywords, Zotero tags) plus the OpenAlex keywords of the paper,
found by DOI or, without DOI, by title. OpenAlex answers are kept in openalex_keywords.json, so a rebuild asks
only for what is missing. Abstracts come from the pool, and extra_abstracts.csv fills in some that no open API
has.

    python build.py                    # after selection/run.py, same Python packages
    python build.py --paper <dir of SanitySurvey.tex>
"""
import csv
import html
import json
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
POOL = HERE / "selection" / "out"
CACHE = HERE / "openalex_keywords.json"
sys.path.insert(0, str(HERE / "selection"))
from lib.bibio import clean, norm_doi, norm_title, read_bib  # noqa: E402


def openalex(params):
    req = urllib.request.Request("https://api.openalex.org/works?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": "sanity-survey-paper-list/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["results"]


def keywords(work):
    return [k["display_name"] for k in work.get("keywords") or []]


def fetch_keywords(cache, dois, titles):
    """Adds the missing DOIs (50 per request) and titles to the cache. Without an API key OpenAlex allows
    about 100 title searches a day; past that the run stops fetching and the rest stays missing."""
    dois = sorted(d for d in dois if d not in cache["doi"])
    titles = [t for t in titles if norm_title(t) not in cache["title"]]
    try:
        for i in range(0, len(dois), 50):
            batch = dois[i:i + 50]
            found = {norm_doi(w["doi"]): keywords(w)
                     for w in openalex({"filter": "doi:" + "|".join(batch), "select": "doi,keywords", "per-page": 50})}
            cache["doi"].update({d: found.get(d, []) for d in batch})
        for t in titles:
            query = re.sub(r"[^\w\s]", " ", t).lower()     # lower case: AND, OR and NOT are search operators
            try:
                works = openalex({"filter": "title.search:" + query, "select": "title,keywords", "per-page": 5})
            except urllib.error.HTTPError as e:
                if e.code != 400:
                    raise
                print("OpenAlex rejected the title search for:", t)
                continue
            cache["title"][norm_title(t)] = next(
                (keywords(w) for w in works if norm_title(w["title"] or "") == norm_title(t)), [])
    except urllib.error.HTTPError as e:
        if e.code != 429:
            raise
        print("OpenAlex: daily limit reached, run build.py again tomorrow for the missing keywords")


def authors(value):
    """'Last, First and Last2, First2' -> 'First Last, First2 Last2'."""
    names = []
    for name in re.split(r"\s+and\s+", value):
        last, _, first = name.partition(",")
        names.append(f"{first.strip()} {last.strip()}".strip())
    return ", ".join(names)


def without_copyright(abstract):
    i = abstract.rfind("©")
    return abstract[:i].rstrip() if i > len(abstract) / 2 else abstract


def cited_in_paper(paper_dir):
    """Writes cited_in_paper.csv: the references cited in the paper (the \\cite commands of the .tex files that
    SanitySurvey.tex inputs), with DOI and title from the paper's bib files."""
    paper_dir = Path(paper_dir)
    main_tex = (paper_dir / "SanitySurvey.tex").read_text(encoding="utf-8")
    keys, todo, seen = [], [paper_dir / "SanitySurvey.tex"], set()
    while todo:
        tex = todo.pop(0)
        if tex in seen or not tex.exists():
            continue
        seen.add(tex)
        text = re.sub(r"(?<!\\)%.*", "", tex.read_text(encoding="utf-8"))          # without comments
        todo += [paper_dir / (m if m.endswith(".tex") else m + ".tex") for m in re.findall(r"\\input\{([^}]+)\}", text)]
        for group in re.findall(r"\\(?:no)?cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^}]*)\}", text):
            keys += [k.strip() for k in group.split(",") if k.strip() and k.strip() not in keys]
    bibs = re.search(r"\\bibliography\{([^}]+)\}", main_tex).group(1).split(",")
    entries = {e["key"]: e for b in bibs for e in read_bib(paper_dir / (b.strip() + ".bib"))}
    with open(HERE / "cited_in_paper.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["doi", "title", "year"])
        for k in keys:
            e = entries.get(k, {"fields": {}})["fields"]
            w.writerow([norm_doi(e.get("doi", "")), clean(e.get("title", "")), e.get("year", "")])
    print(f"cited_in_paper.csv: {len(keys)} cited keys, {sum(k in entries for k in keys)} found in the bib files")


# ------------------------------------------------------------------ the selection steps, for index.html

FLAG_STEPS = ["not_a_paper", "not_english", "not_peer_reviewed", "retracted", "off_topic", "superseded_versions",
              "surveys", "isolated"]
RANK_ORDER = {"A*": 4, "A": 3, "B": 2, "C": 1}


def core_order(rank):
    """As rules.venue_below: 4 (A*) to 1 (C), 0 for national/regional ranks, None for unranked or unknown venues."""
    rank = (rank or "").strip()
    if rank in RANK_ORDER:
        return RANK_ORDER[rank]
    if rank.startswith(("National", "Regional")):
        return 0
    if rank.startswith("Australasian"):
        return RANK_ORDER.get(rank.split()[-1], 0)
    return None


def selection_attributes():
    """What the steps of run.py look at, for every record (same ids as records.csv), so that index.html can apply
    them with other settings: the yes/no steps as the reason of the step (if it applies), the thresholds as values.
    The steps that compare records with each other (superseded version, foundational, no citation link) are
    computed against the records of the paper's run. Needs the packages of selection/run.py."""
    from collections import Counter
    import yaml
    import run
    from lib import bibio, notions, rank, rules, venues

    sel = HERE / "selection"
    cfg = yaml.safe_load((sel / "config.yaml").read_text(encoding="utf-8"))
    path = lambda p: (sel / p).resolve()  # noqa: E731
    cy, f = int(cfg["current_year"]), cfg["filters"]
    hits, _ = bibio.load_exports(path(cfg["inputs"]["searches_dir"]), list(cfg["queries"]))
    extra, _ = bibio.load_bib_sources([(path(s["path"]), s["name"]) for s in cfg["inputs"].get("bib_files", [])])
    records = bibio.deduplicate(hits + extra)
    records.sort(key=lambda r: (bibio.norm_title(r["title"]), r["year"] or 0, r["doi"]))
    for i, r in enumerate(records, 1):
        r.update(record_id=f"R{i:05d}", status="kept", step="", reason="", rank="")
    run.enrich_records(records, cfg, path(cfg.get("cache_dir", "cache")),
                       venues.VenueRanks(path(cfg.get("data_dir", "data"))), offline=True)

    ot = f.get("off_topic", {})
    domain_rx = re.compile("|".join(f"(?:{p})" for p in ot.get("domain_terms", [r"$^"])), re.IGNORECASE)
    notion_rx = notions.compile_patterns(cfg["notions"])
    surname = lambda r: bibio.first_author_surname(r["authors"])  # noqa: E731
    refs = lambda r: (r["openalex"] or {}).get("referenced_works") or []  # noqa: E731
    attrs = {r["record_id"]: {
        "pages": r["pages_n"], "core": core_order(r["venue_rank"]), "pct": r["citation_percentile"], "cited": 0,
        "notions": notions.notions_of(r, notion_rx),
        "flags": {k: why for k, why in [
            ("not_a_paper", rules.not_a_paper(r)), ("not_english", rules.not_english(r)),
            ("not_peer_reviewed", rules.not_peer_reviewed(r)), ("retracted", rules.retracted(r)),
            ("off_topic", rules.off_topic(r, domain_rx, set(ot.get("keep_fields", [])), set(ot.get("keep_subfields", [])))),
            ("surveys", rules.survey(r))] if why},
    } for r in records}

    # the paper's run, as in run.py; on the way, the steps that compare records are computed for all records
    steps = run.make_steps(cfg, cy, records)
    scope = {label for key, label, *_ in steps if key in ("published_before", "venue_rank", "citation_impact")}
    year = int(f.get("published_before", {}).get("year", 2015))
    alive, paper_step = list(records), {}
    for key, label, _, check in steps:
        if not f.get(key, {}).get("apply", True):
            continue
        if key == "superseded_versions":
            ids = {r["record_id"] for r in alive}
            everyone = rules.superseded_versions(records, surname)
            reasons = check(alive)
            for r in records:
                why = reasons.get(r["record_id"]) if r["record_id"] in ids else everyone.get(r["record_id"])
                if why:
                    attrs[r["record_id"]]["flags"][key] = why
        elif key == "foundational":
            network = [r for r in records if r["status"] in ("kept", "kept apart")]
            count = Counter(w for r in network if (r["year"] or 0) >= year for w in set(refs(r)))
            for r in records:
                if r["openalex_id"]:
                    attrs[r["record_id"]]["cited"] = count.get(r["openalex_id"], 0)
            reasons = check(alive)
        elif key == "isolated":
            network = [r for r in records if r["status"] in ("kept", "kept apart") or r["step"] in scope]
            for rid in rank.isolated(records, network):
                attrs[rid]["flags"][key] = "neither cites nor is cited by any other paper that passed the eligibility steps"
            reasons = check(alive)
        else:
            reasons = check(alive)
        for r in alive:
            if r["record_id"] in reasons:
                r.update(status="kept apart" if key in run.KEPT_APART else "removed", step=label,
                         reason=reasons[r["record_id"]])
                paper_step[r["record_id"]] = key
        alive = [r for r in alive if r["record_id"] not in reasons]

    settings = {"apply": {key: f.get(key, {}).get("apply", True) for key, *_ in steps},
                "max_pages": int(f.get("short_papers", {}).get("max_pages", 4)), "year": year,
                "min_rank": f.get("venue_rank", {}).get("min_rank", "B"),
                "min_percentile": float(f.get("citation_impact", {}).get("min_percentile", 0.5)),
                "grace_years": int(f.get("citation_impact", {}).get("grace_years", 2)),
                "min_citations": int(f.get("foundational", {}).get("min_citations", 10)), "current_year": cy}
    years = {r["record_id"]: r["year"] for r in records}
    wrong = [rid for rid, a in attrs.items() if first_step(a, years[rid], settings) != paper_step.get(rid)]
    print(f"selection: {len(records)} records; with the paper's settings the page's rules give the same step as "
          f"run.py for {len(records) - len(wrong)} of them" + (f", NOT for {wrong[:10]}" if wrong else ""))
    if wrong:
        sys.exit("the rules of index.html do not reproduce run.py")
    return attrs, settings, [key for key, *_ in steps]


def first_step(a, y, s):
    """The step that removes the record or keeps it apart with settings s (None: final pool). index.html applies
    exactly these rules (function firstStep)."""
    for key in s["apply"]:
        if not s["apply"][key]:
            continue
        if key in FLAG_STEPS:
            hit = key in a["flags"]
        elif key == "short_papers":
            hit = a["pages"] is not None and a["pages"] <= s["max_pages"]
        elif key == "foundational":
            hit = a["cited"] >= s["min_citations"] and (y or 9999) < s["year"]
        elif key == "published_before":
            hit = y is not None and y < s["year"]
        elif key == "venue_rank":
            hit = a["core"] is not None and a["core"] < RANK_ORDER[s["min_rank"]]
        elif key == "citation_impact":
            hit = (a["pct"] is not None and not (y is not None and y > s["current_year"] - s["grace_years"])
                   and a["pct"] < s["min_percentile"])
        else:                                            # related_topics
            hit = not a["notions"]
        if hit:
            return key
    return None


def unique(words):
    seen, out = set(), []
    for w in (w.strip() for w in words):
        if w and w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return out


def main():
    with open(POOL / "pool_ranked.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    with open(HERE / "extra_abstracts.csv", encoding="utf-8") as f:
        extra = {r["doi"]: r for r in csv.DictReader(f)}
    bib = {re.search(r"(R\d+)\s*$", e["fields"]["annote"]).group(1): e for e in read_bib(POOL / "filter_results.bib")}

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {"doi": {}, "title": {}}
    fetch_keywords(cache, {norm_doi(r["doi"]) for r in rows if r["doi"]}, [r["title"] for r in rows if not r["doi"]])
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")

    papers = []
    for r in rows:
        entry, doi = bib[r["record_id"]], norm_doi(r["doi"])
        oa = cache["doi"].get(doi, []) if doi else cache["title"].get(norm_title(r["title"]), [])
        abstract, source = r["abstract"], ""
        if not abstract and doi in extra:
            abstract, source = extra[doi]["abstract"], extra[doi]["source"]
        papers.append({
            "rank": int(r["rank"]),
            "key": entry["key"],
            "title": r["title"],
            "authors": authors(r["authors"]),
            "year": int(r["year"]),
            "venue": re.sub(r"\s*\(including subseries.*\)$", "", r["venue_name"]),
            "doi": doi,
            "url": f"https://doi.org/{doi}" if doi else r["url"],
            "notions": r["notions"].split("; "),
            "keywords": unique(re.split(r"[;,]", clean(entry["fields"].get("keywords", ""))) + oa),
            "abstract": without_copyright(abstract),
            "abstract_source": source,
        })

    # every other record of the searches, and for all records what the selection steps look at
    attrs, settings, steps = selection_attributes()
    pool_ids = {r["record_id"] for r in rows}
    with open(POOL / "records.csv", encoding="utf-8") as f:
        records = list(csv.DictReader(f))
    others = []
    for r in records:
        if r["record_id"] in pool_ids:
            continue
        doi = norm_doi(r["doi"])
        others.append({
            "id": r["record_id"],
            "title": html.unescape(r["title"]),
            "authors": authors(html.unescape(r["authors"])),
            "year": int(r["year"]) if r["year"] else None,
            "venue": re.sub(r"\s*\(including subseries.*\)$", "", r["venue_name"]),
            "doi": doi,
            "url": f"https://doi.org/{doi}" if doi else r["url"],
            "abstract": without_copyright(r["abstract"]),
        })
    out = [{"id": r["record_id"], **p} for r, p in zip(rows, papers)] + others
    for p in out:
        a = attrs[p["id"]]
        p["notions"] = a.pop("notions")
        p["sel"] = a
    # records cited in the paper: by DOI, else by title (and year, if a conference and a journal version share it)
    with open(HERE / "cited_in_paper.csv", encoding="utf-8") as f:
        for c in csv.DictReader(f):
            match = [p for p in out if c["doi"] and p["doi"] == c["doi"]] or \
                    [p for p in out if c["title"] and norm_title(p["title"]) == norm_title(c["title"])]
            match = [p for p in match if str(p["year"]) == c["year"]] or match
            for p in match:
                p["cited"] = 1
    with open(HERE / "papers.js", "w", encoding="utf-8") as f:
        f.write("window.STEPS = " + json.dumps(steps) + ";\n")
        f.write("window.SETTINGS = " + json.dumps(settings) + ";\n")
        f.write("window.PAPERS = [\n" + ",\n".join(json.dumps(p, ensure_ascii=False) for p in out) + "\n];\n")
    with open(HERE / "papers.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(papers[0]))
        w.writeheader()
        for p in papers:
            w.writerow({**p, "notions": "; ".join(p["notions"]), "keywords": "; ".join(p["keywords"])})
    shutil.copyfile(POOL / "filter_results.bib", HERE / "papers.bib")
    shutil.copyfile(POOL / "records.csv", HERE / "records.csv")

    print(f"{len(papers)} papers, {sum(bool(p['keywords']) for p in papers)} with keywords, "
          f"{sum(bool(p['abstract']) for p in papers)} with abstract; {len(others)} other records; "
          f"{sum('cited' in p for p in out)} records cited in the paper")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--paper":
        cited_in_paper(sys.argv[2])
    main()

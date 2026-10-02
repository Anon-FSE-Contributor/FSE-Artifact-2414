#!/usr/bin/env python3
"""Study selection for the sanity survey (see README.md):
all bib files together -> deduplicate -> filter steps (waterfall) -> rank by local citations -> top 100/200/300.

    python run.py              # uses config.yaml next to this file
    python run.py --offline    # only cached OpenAlex/Crossref data (no network)
"""
import argparse
import datetime
import random
import re
from collections import Counter
from pathlib import Path

import yaml

from lib import bibio, enrich, notions, rank, report, rules, sankey, venues, waterfall

HERE = Path(__file__).resolve().parent
KEPT_APART = {"surveys", "foundational", "related_topics"}   # steps that move papers to their own category
COLUMNS = ["record_id", "rank", "title", "year", "authors", "venue_name", "venue_kind", "venue_rank",
           "lcs", "lcr", "lcs_per_year", "cited_by_count", "citation_percentile", "pages_n", "notions", "queries",
           "databases", "doi", "url", "status", "step", "reason", "abstract"]


def log(msg):
    print(msg, flush=True)


# ------------------------------------------------------------------------------------------ metadata

def venue_kind(rec, xref):
    if xref and xref.get("event_acronym"):
        return "conference"
    st, et = rec.get("scopus_type"), rec.get("entry_type")
    if st == "Conference paper" or et == "inproceedings":
        return "conference"
    if et == "article" and rec.get("acm_journal", "").startswith("Proc. ACM"):
        return "conference"                                   # PACMPL / PACMSE issues are conferences
    if st in ("Article", "Review") or et == "article":
        return "journal"
    if st == "Book chapter" or et in ("inbook", "incollection"):
        return "book"
    ct = (xref or {}).get("type", "")
    return {"proceedings-article": "conference", "journal-article": "journal", "book-chapter": "book"}.get(ct, "unknown")


def resolve_venue(rec, xref, ranks):
    oa = rec.get("openalex") or {}
    kind = venue_kind(rec, xref)
    containers = (xref or {}).get("containers") or []
    titles = [t for t in [rec.get("scopus_source"), rec.get("acm_booktitle"), rec.get("acm_journal"),
                          (xref or {}).get("event_name"), *containers, oa.get("source_name")] if t]
    issns = [i for i in (rec.get("issn") or "").replace(",", " ").split() if i]
    issns += ((xref or {}).get("issns") or []) + (oa.get("source_issns") or [])
    rank_, how = "", ""
    if kind in ("conference", "book", "unknown"):
        rank_, _, how = ranks.conference(venues.acronym_candidates(rec, xref), titles)
    if not rank_ and kind in ("journal", "book", "unknown"):
        rank_, how = ranks.journal(issns, titles)
    rec.update(venue_kind=kind, venue_name=titles[0] if titles else "", venue_rank=rank_, venue_rank_how=how)


def enrich_records(records, cfg, cache_dir, ranks, offline):
    net = cfg.get("network", {})
    http = enrich.Http(mailto=net.get("mailto", ""), pause=float(net.get("pause_seconds", 0.2)), offline=offline)
    dois = {d for r in records for d in r["dois"]}
    works_cache = enrich.Cache(cache_dir / "openalex_works.json")
    log(f"  OpenAlex: {len(dois)} DOIs ({sum(d in works_cache for d in dois)} cached)")
    works = enrich.openalex_works_by_doi(dois, http, works_cache, log)
    log("  Crossref (conference names for the CORE ranks)")
    xrefs = enrich.crossref_meta({r["doi"] for r in records if r["doi"]}, http,
                                 enrich.Cache(cache_dir / "crossref.json"), log)
    missing = {r["doi"] for r in records if r["doi"] and not r["abstract"]}
    log(f"  OpenAlex abstracts for {len(missing)} records without one")
    abstracts = enrich.openalex_abstracts(missing, http, enrich.Cache(cache_dir / "openalex_abstracts.json"))
    for r in records:
        if not r["abstract"] and abstracts.get(r["doi"]):
            r["abstract"] = abstracts[r["doi"]]
        oa = next((works[d] for d in [r["doi"], *sorted(r["dois"])] if d and works.get(d)), None)
        r["openalex"] = oa
        oa = oa or {}
        r["openalex_id"] = oa.get("id", "")
        r["citation_percentile"] = oa.get("citation_percentile")
        r["cited_by_count"] = oa.get("cited_by_count") if oa.get("cited_by_count") is not None else r["scopus_cited_by"]
        if r["pages_n"] is None and str(oa.get("first_page") or "").isdigit() and str(oa.get("last_page") or "").isdigit():
            n = int(oa["last_page"]) - int(oa["first_page"]) + 1
            r["pages_n"] = n if 0 < n <= 500 else None
        resolve_venue(r, xrefs.get(r["doi"]), ranks)


# ------------------------------------------------------------------------------------------ steps

def make_steps(cfg, current_year, records):
    """(config key, waterfall label, short label, check) in waterfall order. A check maps the surviving records
    to {record_id: reason}. records: all records; their status tells which passed the eligibility steps."""
    f = cfg["filters"]
    single = lambda check: (lambda alive: {r["record_id"]: why for r in alive for why in [check(r)] if why})
    max_pages = int(f.get("short_papers", {}).get("max_pages", 4))
    year = int(f.get("published_before", {}).get("year", 2015))
    min_rank = f.get("venue_rank", {}).get("min_rank", "B")
    ci = f.get("citation_impact", {})
    pct, grace = float(ci.get("min_percentile", 0.5)), int(ci.get("grace_years", 2))
    scope_labels = {f"Published before {year}", f"Venue ranked below CORE {min_rank}",
                    f"Citation impact in the bottom {pct:.0%} of its year and field"}
    notion_rx = notions.compile_patterns(cfg["notions"])
    ot = f.get("off_topic", {})
    domain_rx = re.compile("|".join(f"(?:{p})" for p in ot.get("domain_terms", [r"$^"])), re.IGNORECASE)

    def eligible_literature():
        """Every paper that passed the eligibility steps: still in the pool, kept apart as a survey, or removed
        only by the scope steps (date, venue, citations). Links to these count for the citation-link step."""
        return [r for r in records if r["status"] in ("kept", "kept apart") or r["step"] in scope_labels]

    min_found = int(f.get("foundational", {}).get("min_citations", 10))

    def foundational(alive):
        """Older than the cutoff but still cited by at least min_found eligible papers published from the cutoff
        year on: kept apart as foundational work."""
        network = eligible_literature()
        old_ids = {r["openalex_id"]: r for r in alive if r["openalex_id"] and (r["year"] or 9999) < year}
        count = Counter()
        for r in network:
            if (r["year"] or 0) >= year:
                for w in set((r["openalex"] or {}).get("referenced_works", [])):
                    if w in old_ids:
                        count[w] += 1
        out = {}
        for w, n in count.items():
            if n >= min_found:
                old_ids[w]["foundational"] = n
                out[old_ids[w]["record_id"]] = f"foundational: published {old_ids[w]['year']}, cited by {n} eligible papers since {year}"
        return out

    return [   # (config key, label for the table, short label for the figure, check)
        ("not_a_paper", "Not a paper (proceedings volumes, front matter, keynotes, artifacts, encyclopedia entries)",
         "Not a paper", single(rules.not_a_paper)),
        ("not_english", "Not in English", "Not in English", single(rules.not_english)),
        ("not_peer_reviewed", "Not peer reviewed (theses, reports, preprints)", "Not peer reviewed",
         single(rules.not_peer_reviewed)),
        ("retracted", "Retracted", "Retracted", single(rules.retracted)),
        ("off_topic", "Outside the subject area (non-computing field and no domain term)", "Off-topic",
         single(lambda r: rules.off_topic(r, domain_rx, set(ot.get("keep_fields", [])),
                                          set(ot.get("keep_subfields", []))))),
        ("short_papers", f"Short papers (≤ {max_pages} pages)", f"≤ {max_pages} pages",
         single(lambda r: rules.short_paper(r, max_pages))),
        ("superseded_versions", "Superseded by an extended version", "Superseded version",
         lambda alive: rules.superseded_versions(alive, lambda r: bibio.first_author_surname(r["authors"]))),
        ("surveys", "Surveys and reviews (kept apart for related work)", "Surveys and reviews",
         single(rules.survey)),
        ("foundational", f"Foundational: published before {year}, cited by ≥ {min_found} eligible papers since "
                         f"{year} (kept apart)", "Foundational", foundational),
        ("published_before", f"Published before {year}", f"Before {year}",
         single(lambda r: rules.published_before(r, year))),
        ("venue_rank", f"Venue ranked below CORE {min_rank}", f"Venue below CORE {min_rank}",
         single(lambda r: rules.venue_below(r, min_rank))),
        ("citation_impact", f"Citation impact in the bottom {pct:.0%} of its year and field", "Low citation impact",
         single(lambda r: rules.low_citation_impact(r, pct, grace, current_year))),
        ("isolated", "No citation link to any other eligible paper (older ones included)", "No citation link",
         lambda alive: {rid: "neither cites nor is cited by any other paper that passed the eligibility steps"
                        for rid in rank.isolated(alive, eligible_literature())}),
        ("related_topics", "Not about a sanity notion (kept apart as related topics)", "Related topics",
         lambda alive: {r["record_id"]: "mentions none of the six sanity notions (translation, testing, smells, "
                                        "good practices, ...)" for r in alive if not notions.notions_of(r, notion_rx)}),
    ]


# ------------------------------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(HERE / "config.yaml"))
    ap.add_argument("--offline", action="store_true", help="use cached API responses only")
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve()
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    path = lambda p: (cfg_path.parent / p).resolve()
    out_dir, cache_dir = path(cfg.get("output_dir", "out")), path(cfg.get("cache_dir", "cache"))
    out_dir.mkdir(parents=True, exist_ok=True)
    offline = args.offline or cfg.get("network", {}).get("offline", False)
    current_year = int(cfg.get("current_year", datetime.date.today().year))

    # 1. all bib files together ---------------------------------------------------------------
    queries = list(cfg["queries"])
    hits, search_counts = bibio.load_exports(path(cfg["inputs"]["searches_dir"]), queries)
    extra, extra_counts = bibio.load_bib_sources([(path(s["path"]), s["name"]) for s in cfg["inputs"].get("bib_files", [])])
    all_hits = hits + extra
    log(f"1. Records identified: {len(all_hits)} ({len(hits)} from the searches, "
        + ", ".join(f"{v} from {k}" for k, v in extra_counts.items()) + ")")

    # 2. deduplicate --------------------------------------------------------------------------
    records = bibio.deduplicate(all_hits)
    records.sort(key=lambda r: (bibio.norm_title(r["title"]), r["year"] or 0, r["doi"]))
    for i, r in enumerate(records, 1):
        r.update(record_id=f"R{i:05d}", status="kept", step="", reason="", rank="")
    log(f"2. After removing duplicates: {len(records)}")

    # 3. metadata -----------------------------------------------------------------------------
    log("3. Metadata from OpenAlex and Crossref" + (" (offline)" if offline else ""))
    enrich_records(records, cfg, cache_dir, venues.VenueRanks(path(cfg.get("data_dir", "data"))), offline)

    # 4. filter steps (waterfall) ------------------------------------------------------------
    log("4. Filter steps")
    rows = [("Records identified", 0, len(all_hits), "total", "Records identified", "identified"),
            ("Duplicates", len(all_hits) - len(records), len(records), "step", "Duplicates", "duplicates")]
    alive, removed_by_step = list(records), {}
    for key, label, short, check in make_steps(cfg, current_year, records):
        if not cfg["filters"].get(key, {}).get("apply", True):
            continue
        reasons = check(alive)
        apart = key in KEPT_APART
        for r in alive:
            if r["record_id"] in reasons:
                r.update(status="kept apart" if apart else "removed", step=label, reason=reasons[r["record_id"]])
        removed_by_step[label] = [r for r in alive if r["record_id"] in reasons]
        alive = [r for r in alive if r["record_id"] not in reasons]
        rows.append((label, len(reasons), len(alive), "apart" if apart else "step", short, key))
        log(f"   {label}: -{len(reasons)} -> {len(alive)}")
    rows.append(("Final pool", 0, len(alive), "total", "Final pool", "final"))

    # 5. rank by local citations, tag the sanity notions, write the final pool and the selections ----
    rank.local_citations(alive, current_year)
    ordered = rank.ranked(alive)
    for pos, r in enumerate(ordered, 1):
        r["rank"] = pos
    notion_counts = notions.tag(ordered, cfg["notions"])
    today = datetime.date.today().isoformat()

    def note(r):
        return (f"rank {r['rank']}; cited by {r['lcs']} papers of the pool ({r['lcs_per_year']:.2f} per year); "
                f"cites {r['lcr']} papers of the pool; notions: {', '.join(r['notions']) or 'none'}; {r['record_id']}")

    def write_bib(file, papers, header):
        taken = set()
        report.write_text(out_dir / file, "\n".join([header] + [rank.bibtex_entry(r, rank.make_key(r, taken), note(r))
                                                                 for r in papers]))

    write_bib("filter_results.bib", ordered,
              f"% Final pool after all filter steps: {len(ordered)} papers about the six sanity notions, ranked by "
              f"local citations per year (run.py, {today}). See README.md.\n")
    for file, prefix, what in (("related_topics", "Not about a sanity notion", "related topics (no sanity notion)"),
                               ("surveys", "Surveys and reviews", "surveys and reviews"),
                               ("foundational", "Foundational", "foundational papers")):
        group = sorted((r for r in records if r["status"] == "kept apart" and r["step"].startswith(prefix)),
                       key=lambda r: (-(r.get("foundational") or 0), -(r.get("cited_by_count") or 0)))
        taken = set()
        report.write_text(out_dir / f"{file}.bib", "\n".join(
            [f"% Kept apart: {len(group)} {what}, most cited first (run.py, {today}). See README.md.\n"]
            + [rank.bibtex_entry(r, rank.make_key(r, taken), f"{r['reason']}; {r['record_id']}") for r in group]))
        report.write_csv(out_dir / f"{file}.csv", group, COLUMNS)
    sizes = [int(n) for n in cfg.get("selection", {}).get("sizes", [100, 200, 300])]
    allocation = cfg.get("selection", {}).get("allocation", "proportional")
    selections, notes = [], []
    for n in sizes:
        top, quota = rank.select(ordered, n, allocation)
        write_bib(f"selected_{n}.bib", top, f"% Top {len(top)} of {len(ordered)} papers by local citations per year, "
                                            f"allocation '{allocation}' (run.py, {today}). See README.md.\n")
        report.write_csv(out_dir / f"selected_{n}.csv", top, COLUMNS)
        selections.append((n, len(top)))
        years = Counter(("before 2000" if y < 2000 else f"{y // 5 * 5}–{y // 5 * 5 + 4}") if y else "unknown"
                        for y in (r["year"] for r in top))
        found = Counter(q for r in top for q in (r["queries"] or ["only in the bib files"]))
        notes.append(f"**Top {n}** by period: " + ", ".join(f"{k}: {v}" for k, v in sorted(years.items()))
                     + "; by search (a paper can come from several): "
                     + ", ".join(f"{k}: {v}" for k, v in sorted(found.items()))
                     + (" ; shares per search: " + ", ".join(f"{k} {v}" for k, v in sorted(quota.items())) if quota else ""))

    # 6. outputs ------------------------------------------------------------------------------
    report.write_csv(out_dir / "records.csv", records, COLUMNS)
    report.write_csv(out_dir / "pool_ranked.csv", ordered, COLUMNS)
    report.write_csv(out_dir / "removed.csv", [r for r in records if r["status"] == "removed"], COLUMNS)
    audit_n, rng = int(cfg.get("audit", {}).get("sample_size", 30)), random.Random(cfg.get("audit", {}).get("random_seed", 1))
    audit = [dict(r, audit_wrongly_removed="") for label, rs in removed_by_step.items()
             for r in sorted(rs if len(rs) <= audit_n else rng.sample(rs, audit_n), key=lambda x: x["record_id"])]
    report.write_csv(out_dir / "audit_sample.csv", audit, ["audit_wrongly_removed", "step", "reason"] + COLUMNS[:6])
    waterfall.table_csv(out_dir / "waterfall.csv", rows)
    n_other = sum(extra_counts.values())
    source_line = (f"Sources: Scopus and ACM DL, {len(queries)} searches ({len(hits)} records: "
                   + ", ".join(f"{q} {search_counts.get((q, 'Scopus'), 0)}+{search_counts.get((q, 'ACM'), 0)}" for q in queries)
                   + f"); other sources ({n_other} records: " + ", ".join(f"{k} {v}" for k, v in extra_counts.items()) + ").")
    report.write_text(out_dir / "waterfall.md", f"# Waterfall ({today})\n\n{source_line}\n\n"
                      + waterfall.table_md(rows) + f"\n\nThe final pool is `filter_results.bib`. Selections from it: "
                      + ", ".join(f"`selected_{n}.bib`" for n, _ in selections) + ".\n\n" + "\n\n".join(notes))
    waterfall.figure([out_dir / "waterfall.pdf", out_dir / "waterfall.png"], rows)
    sources = [("Scopus", sum(v for (q, db), v in search_counts.items() if db == "Scopus")),
               ("ACM DL", sum(v for (q, db), v in search_counts.items() if db == "ACM")),
               ("Other sources", n_other)]
    sankey.figure([out_dir / "sankey.pdf", out_dir / "sankey.png"], sources, rows)
    none = sum(not r["notions"] for r in ordered)
    notions.write_csv(out_dir / "notions.csv", notion_counts, len(ordered), none)
    notions.figure([out_dir / "notions.pdf", out_dir / "notions.png"], notion_counts)
    log(f"5. Final pool {len(alive)} (filter_results.bib); notions: "
        + ", ".join(f"{k} {v}" for k, v in notion_counts.items()) + f", none {none}")
    log(f"   wrote filter_results.bib, " + ", ".join(f"selected_{n}.bib" for n in sizes)
        + f", waterfall, sankey and notions figures to {out_dir}")


if __name__ == "__main__":
    main()

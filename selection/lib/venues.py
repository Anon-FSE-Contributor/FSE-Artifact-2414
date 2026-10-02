"""Venue ranks: CORE 2023 (conferences), CORE/ERA 2010 (journals, as used by Croft et al.) and, optionally,
a SCImago export for the venue h-index. A record's venue is resolved by (1) the conference acronym from
Crossref's event record, the ACM 'series' field, a trailing '(ACRONYM)' in the venue title or the issue of an
ACM PACM journal (e.g. 'OOPSLA1'); (2) the ISSN for journals; (3) similarity of the venue title.
Anything that cannot be resolved stays 'unknown' and is never used to exclude a record."""
import csv
import re
from pathlib import Path

STOP = {"proceedings", "proc", "of", "the", "on", "and", "in", "for", "ieee", "acm", "ieeeacm", "acmieee",
        "sigsoft", "sigplan", "international", "annual", "joint", "conference", "symposium", "a", "an",
        "at", "to", "with", "held", "part", "volume", "vol"}
ORDINALS = re.compile(r"\b(\d+(st|nd|rd|th)|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
                      r"eleventh|twelfth|thirteenth|fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|"
                      r"nineteenth|twentieth|twenty\w*|thirty\w*|fortieth|forty\w*|fiftieth|fifty\w*)\b")


def title_tokens(s):
    s = (s or "").lower().replace("&", " and ")
    s = re.sub(r"\([^)]*\)", " ", s)                 # parenthetical acronyms / notes
    s = ORDINALS.sub(" ", s)
    s = re.sub(r"[^a-z ]", " ", s)
    return {t for t in s.split() if t not in STOP and len(t) > 1}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def norm_acronym(a):
    """'ICSE '22' -> 'ICSE', 'CAV 2019' -> 'CAV', 'ESEC/FSE '21' -> 'ESEC/FSE'."""
    a = re.sub(r"['’]\s*\d{2,4}\b|\b(19|20)\d{2}\b", "", a or "")
    a = re.sub(r"[^A-Za-z0-9/+\-]", " ", a).strip().upper()
    return re.sub(r"\s+", " ", a).split(" ")[0] if a else ""


def norm_issn(i):
    i = re.sub(r"[^0-9Xx]", "", i or "").upper()
    return f"{i[:4]}-{i[4:]}" if len(i) == 8 else ""


class VenueRanks:
    def __init__(self, data_dir):
        data_dir = Path(data_dir)
        self.conf = []                                # (tokens, acronym, rank, title)
        conf_file = next(iter(sorted(data_dir.glob("core_conferences*.csv"))), None)
        if conf_file:
            for row in csv.reader(conf_file.open(encoding="utf-8", errors="replace")):
                if len(row) >= 5 and row[0].isdigit():
                    self.conf.append((title_tokens(row[1]), row[2].strip().upper(), row[4].strip(), row[1]))
        self.jnl_by_issn, self.jnl_by_title = {}, {}
        jnl_file = next(iter(sorted(data_dir.glob("core_journals*.csv"))), None)
        if jnl_file:
            for row in csv.DictReader(jnl_file.open(encoding="utf-8", errors="replace")):
                for k in ("ISSN1", "ISSN2", "ISSN3", "ISSN4"):
                    if norm_issn(row.get(k)):
                        self.jnl_by_issn[norm_issn(row[k])] = row["rank"].strip()
                self.jnl_by_title[" ".join(sorted(title_tokens(row["title"])))] = row["rank"].strip()
        self.sjr_by_issn, self.sjr_by_title = {}, {}
        sjr_file = next(iter(sorted(data_dir.glob("scimago*.csv"))), None)
        if sjr_file:                                  # optional manual download from scimagojr.com
            text = sjr_file.read_text(encoding="utf-8", errors="replace")
            for row in csv.DictReader(text.splitlines(), delimiter=";"):
                h = re.sub(r"\D", "", row.get("H index", "") or "")
                if not h:
                    continue
                for i in (row.get("Issn") or "").split(","):
                    if norm_issn(i):
                        self.sjr_by_issn[norm_issn(i)] = int(h)
                self.sjr_by_title[" ".join(sorted(title_tokens(row.get("Title", ""))))] = int(h)
        self.has_sjr = bool(self.sjr_by_issn)

    # -------------------------------------------------------------- conferences
    def conference(self, acronyms, titles):
        """(rank, matched CORE title, how) or ('', '', '')."""
        title_toks = [title_tokens(t) for t in titles if t]
        for acr in acronyms:
            parts = [acr] + ([p for p in acr.split("/") if p] if "/" in acr else [])
            for a in parts:
                cands = [c for c in self.conf if c[1] == a]
                if len(cands) == 1:
                    return cands[0][2], cands[0][3], f"acronym {a}"
                if len(cands) > 1 and title_toks:
                    best = max(cands, key=lambda c: max(jaccard(c[0], t) for t in title_toks))
                    if max(jaccard(best[0], t) for t in title_toks) >= 0.3:
                        return best[2], best[3], f"acronym {a} + title"
        best, score = None, 0.0
        for c in self.conf:
            for t in title_toks:
                s = jaccard(c[0], t)
                if s > score:
                    best, score = c, s
        if best and score >= 0.8:
            return best[2], best[3], f"title ({score:.2f})"
        return "", "", ""

    # -------------------------------------------------------------- journals
    def journal(self, issns, titles):
        for i in issns:
            if norm_issn(i) in self.jnl_by_issn:
                return self.jnl_by_issn[norm_issn(i)], "issn"
        for t in titles:
            key = " ".join(sorted(title_tokens(t)))
            if key and key in self.jnl_by_title:
                return self.jnl_by_title[key], "title"
        return "", ""

    def sjr_h_index(self, issns, titles):
        for i in issns:
            if norm_issn(i) in self.sjr_by_issn:
                return self.sjr_by_issn[norm_issn(i)]
        for t in titles:
            key = " ".join(sorted(title_tokens(t)))
            if key and key in self.sjr_by_title:
                return self.sjr_by_title[key]
        return None


def rank_class(rank, high=("A*", "A"), low_prefixes=("B", "C", "Australasian", "National", "Regional")):
    """'high' | 'low' | 'unknown' for a CORE rank string."""
    r = (rank or "").strip()
    if r in high:
        return "high"
    if any(r == p or r.startswith(p) for p in low_prefixes):
        return "low"
    return "unknown"


def acronym_candidates(rec, crossref):
    """Conference acronyms for a record, most reliable first."""
    out = []
    if crossref and crossref.get("event_acronym"):
        out.append(norm_acronym(crossref["event_acronym"]))
    if rec.get("acm_series"):
        out.append(norm_acronym(rec["acm_series"]))
    if rec.get("acm_journal", "").startswith("Proc. ACM") and re.search(r"[A-Za-z]", rec.get("acm_number", "")):
        out.append(norm_acronym(re.sub(r"\d+$", "", rec["acm_number"])))
    for t in (rec.get("scopus_source"), rec.get("acm_booktitle"), crossref and crossref.get("event_name")):
        m = re.search(r"\(([A-Za-z][A-Za-z0-9/+\-]{1,15})(?:\s*['’]?\d{2,4})?\)\s*$", t or "")
        if m:
            out.append(norm_acronym(m.group(1)))
    return [a for i, a in enumerate(out) if a and a not in out[:i]]

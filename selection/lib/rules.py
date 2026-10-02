"""The filter steps of the waterfall. Each check returns a reason string ('' = passes). run.py applies the steps
in the configured order; a record is removed by the first step it fails."""
import re

FRONT_MATTER = re.compile(
    r"^\s*(proceedings\b|preface\b|foreword\b|front\s?matter|back\s?matter|table of contents|contents\b|"
    r"author index|index of authors|message from|welcome (message|to|from)|editorial\b|guest editorial|"
    r"editor'?s'? (note|introduction|message)|keynote|invited (talk|paper|speaker|lecture)|tutorial\b|panel\b|"
    r"workshop (summary|report|overview|organi[sz]ation)|report on\b|summary of the\b|"
    r"introduction to the special|special issue\b|erratum|corrigendum|correction to\b|"
    r"(program|programme|organi[sz]ing|steering) committee|committees?\s*$|organi[sz]ation\s*$|reviewers\s*$|"
    r"sponsors?\b|session details|chairs'? (message|welcome)|conference (overview|summary))"
    r"|(:|\s[\-–—]|\()\s*(research )?artifacts?( for [^:]*)?\)?\s*$",   # artifact-track records
    re.IGNORECASE)
NON_ENGLISH = re.compile(r"\b(la|le|les|des|du|une|pour|und|der|die|das|für|mit|zur|para|con|los|las|el|"
                         r"em|com|uma|dos|della|per|di|avec|sur|dans)\b", re.IGNORECASE)
NON_PAPER_OA_TYPES = {"paratext", "editorial", "erratum", "letter", "retraction", "peer-review",
                      "supplementary-materials", "dataset", "libguides", "reference-entry", "grant", "standard"}
NOT_PEER_REVIEWED_OA_TYPES = {"preprint", "dissertation", "report"}
TRUSTED_TYPES = {"Article", "Conference paper", "Review", "Book chapter"}
PAPER_ENTRY_TYPES = {"inproceedings", "article", "inbook", "incollection", "conference"}

SECONDARY = re.compile(
    r"\b(systematic (literature )?review|systematic mapping|mapping study|literature review|literature survey|"
    r"tertiary study|scoping review|meta-analysis|state[- ]of[- ](the[- ])?art (review|survey|overview)|"
    r"review of (the )?(literature|research|approaches|techniques|methods|tools))\b"
    r"|(^|:\s*)(an? )?((comprehensive|systematic|short|brief|critical) )?(survey|review|overview)\b"
    r"(?!\s*(and|stage|process|meeting|board|step|phase|based|-based))"
    r"|\b(an? )((comprehensive|systematic|short|brief|critical) )?(survey|review|overview) (of|on)\b",
    re.IGNORECASE)
QUESTIONNAIRE = re.compile(r"(practitioner|industr\w* survey|questionnaire|interview|online survey|survey-based|"
                           r"survey (of|among|with) (developers|engineers|practitioners|professionals|experts))",
                           re.IGNORECASE)

RANK_ORDER = {"A*": 4, "A": 3, "B": 2, "C": 1}


# ------------------------------------------------------------------ eligibility

def not_a_paper(rec):
    oa, title = rec.get("openalex") or {}, rec.get("title", "")
    words = title.split()
    tool_name = len(words) >= 5 and re.fullmatch(r"[A-Z0-9\-]{4,}:?", words[0]) is not None
    if rec.get("entry_type") == "proceedings":
        return "whole proceedings volume"
    if rec.get("entry_type") == "book" and rec.get("scopus_type") != "Book chapter":
        return "whole book"
    if (FRONT_MATTER.match(title) and not tool_name) or \
            (FRONT_MATTER.search(title) and re.search(r"artifacts?\)?\s*$", title, re.I)):
        return "front matter, editorial, keynote, tutorial, panel, workshop report or artifact"
    if oa.get("type") in NON_PAPER_OA_TYPES:
        return f"not a paper (OpenAlex type '{oa['type']}')"
    return ""


def not_english(rec):
    lang = (rec.get("openalex") or {}).get("language")
    # Scopus hits passed LANGUAGE(english); OpenAlex's guess alone is unreliable (it calls "Verification of EB3
    # specifications using CADP" Latvian), so the title must look non-English too
    if lang not in (None, "en") and "Scopus" not in rec.get("databases", []) and \
            len(NON_ENGLISH.findall(rec.get("title", ""))) >= 2:
        return f"not in English (OpenAlex language '{lang}')"
    return ""


def not_peer_reviewed(rec):
    oa, et = rec.get("openalex") or {}, rec.get("entry_type")
    if et in ("phdthesis", "mastersthesis", "techreport", "unpublished", "misc", "online"):
        return f"not peer reviewed (BibTeX type '{et}': thesis, report, preprint or web page)"
    if oa.get("type") in NOT_PEER_REVIEWED_OA_TYPES and rec.get("scopus_type") not in TRUSTED_TYPES \
            and et not in PAPER_ENTRY_TYPES:
        return f"not peer reviewed (OpenAlex type '{oa['type']}')"
    return ""


def retracted(rec):
    oa = rec.get("openalex") or {}
    if oa.get("is_retracted") or re.match(r"^\s*retract(ed|ion)\b", rec.get("title", ""), re.IGNORECASE):
        return "retracted"
    return ""


def off_topic(rec, domain_rx, allowed_fields, allowed_subfields, min_abstract=200):
    """Outside the survey's subject area: its title, abstract and keywords contain none of the domain terms AND
    either OpenAlex files it outside computing (field and subfield not allowed) or, when OpenAlex has no topic
    for it, it has a real abstract (min_abstract characters) to judge by. Two signals must agree, because
    OpenAlex alone misfiles many relevant papers (e.g. an autoformalization paper under Cultural Studies).
    Papers with neither an OpenAlex topic nor an abstract are kept."""
    oa = rec.get("openalex") or {}
    field, subfield = oa.get("field"), oa.get("subfield")
    if field in allowed_fields or subfield in allowed_subfields:
        return ""
    if not field and len(rec.get("abstract", "")) < min_abstract:
        return ""
    text = " ".join([rec.get("title", ""), rec.get("abstract", ""), rec.get("keywords", "")])
    if domain_rx.search(text):
        return ""
    where = f"OpenAlex: {field} / {subfield}" if field else "no OpenAlex topic"
    return f"outside the subject area ({where}; no domain term)"


def short_paper(rec, max_pages):
    n = rec.get("pages_n")
    return f"short paper ({n} pages, at most {max_pages})" if n is not None and n <= max_pages else ""


def survey(rec):
    """Literature surveys / reviews: set aside (related work, snowballing), not ranked with primary studies."""
    title = rec.get("title", "")
    m = SECONDARY.search(title)
    if m and not QUESTIONNAIRE.search(title):
        return "survey or review (title: '" + m.group(0).strip(": ") + "')"
    return ""


# ------------------------------------------------------------------ extended versions

def _title_tokens(t):
    return {w for w in re.findall(r"[a-z0-9]+", (t or "").lower()) if len(w) > 2} - \
           {"the", "and", "for", "with", "from", "towards", "toward", "using", "based", "via", "into", "its"}


def _completeness(rec):
    """Higher = more complete version: journal before conference/chapter, then later year, then more pages."""
    return (rec.get("venue_kind") == "journal", rec.get("year") or 0, rec.get("pages_n") or 0)


def superseded_versions(records, surname, min_similarity=0.75, max_years=5):
    """Pairs of records by the same first author with near-identical titles (token Jaccard >= min_similarity)
    within max_years: the less complete one is superseded (e.g. a conference paper and its journal extension).
    Returns {record_id: reason}."""
    by_author = {}
    for r in records:
        s = surname(r)
        if s:
            by_author.setdefault(s, []).append(r)
    out = {}
    for group in by_author.values():
        if len(group) < 2:
            continue
        toks = [_title_tokens(r["title"]) for r in group]
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if not toks[i] or not toks[j] or a["year"] is None or b["year"] is None:
                    continue
                if abs(a["year"] - b["year"]) > max_years:
                    continue
                sim = len(toks[i] & toks[j]) / len(toks[i] | toks[j])
                if sim < min_similarity:
                    continue
                keep, drop = (a, b) if _completeness(a) >= _completeness(b) else (b, a)
                if drop["record_id"] not in out:
                    out[drop["record_id"]] = (f"superseded by the more complete version {keep['record_id']} "
                                              f"({keep['year']}, {keep.get('venue_kind')}): {keep['title'][:70]}")
    return out


# ------------------------------------------------------------------ scope and impact

def published_before(rec, year):
    y = rec.get("year")
    return f"published {y}, before {year}" if y is not None and y < year else ""


def venue_below(rec, min_rank):
    """CORE rank below min_rank (A*, A, B or C). National/regional ranks count as below C. Unknown or unranked
    venues are kept: there is nothing to judge them by."""
    rank = (rec.get("venue_rank") or "").strip()
    if not rank:
        return ""
    order = RANK_ORDER.get(rank)
    if order is None:
        if rank.startswith(("National", "Regional")):
            order = 0
        elif rank.startswith("Australasian"):
            order = RANK_ORDER.get(rank.split()[-1], 0)
        else:
            return ""                                   # Unranked, TBR, Journal Published, ...
    return f"venue ranked {rank} in CORE (below {min_rank})" if order < RANK_ORDER[min_rank] else ""


def low_citation_impact(rec, min_percentile, grace_years, current_year):
    """OpenAlex citation percentile among works of the same type, year and subfield below min_percentile.
    Papers from the last grace_years years are kept (they have not had time to be cited); unknown is kept."""
    year, pct = rec.get("year"), rec.get("citation_percentile")
    if pct is None or (year is not None and year > current_year - grace_years):
        return ""
    return (f"citation impact in the bottom {min_percentile:.0%} of its year and field (percentile {pct:.2f})"
            if pct < min_percentile else "")

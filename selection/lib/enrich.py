"""Metadata from OpenAlex (citations, normalised citation percentile, pages, type, retraction, references,
venue h-index) and Crossref (conference event acronym, container titles). All responses are cached in
cache/*.json, so a re-run is offline and reproducible; delete the cache files to refresh."""
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import requests

from .bibio import norm_doi

OPENALEX = "https://api.openalex.org"
CROSSREF = "https://api.crossref.org"
WORK_FIELDS = ("id,doi,title,publication_year,type,language,is_retracted,cited_by_count,fwci,"
               "citation_normalized_percentile,biblio,primary_location,referenced_works,primary_topic")


class Cache(dict):
    def __init__(self, path):
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            self.update(json.loads(self.path.read_text(encoding="utf-8")))

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self, ensure_ascii=False, indent=0), encoding="utf-8")
        tmp.replace(self.path)


class Http:
    def __init__(self, mailto="", pause=0.2, offline=False):
        self.mailto, self.pause, self.offline = mailto, pause, offline
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "LangSurvey-study-selection/1.0"

    def get_json(self, url, params=None):
        if self.offline:
            raise RuntimeError("offline mode: cache miss for " + url)
        params = dict(params or {})
        if self.mailto:
            params["mailto"] = self.mailto
        for attempt in range(7):
            time.sleep(self.pause)
            try:
                r = self.session.get(url, params=params, timeout=60)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                return None
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"giving up on {url} after repeated errors")


def _batches(items, size):
    items = list(items)
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _short(openalex_id):
    return (openalex_id or "").rsplit("/", 1)[-1]


def _compact_work(w):
    loc = w.get("primary_location") or {}
    src = loc.get("source") or {}
    pct = w.get("citation_normalized_percentile") or {}
    bib = w.get("biblio") or {}
    topic = w.get("primary_topic") or {}
    return {
        "topic": topic.get("display_name"),
        "subfield": (topic.get("subfield") or {}).get("display_name"),
        "field": (topic.get("field") or {}).get("display_name"),
        "domain": (topic.get("domain") or {}).get("display_name"),
        "id": _short(w.get("id")),
        "doi": norm_doi(w.get("doi") or ""),
        "title": w.get("title") or "",
        "year": w.get("publication_year"),
        "type": w.get("type"),
        "language": w.get("language"),
        "is_retracted": bool(w.get("is_retracted")),
        "cited_by_count": w.get("cited_by_count"),
        "fwci": w.get("fwci"),
        "citation_percentile": pct.get("value"),
        "first_page": bib.get("first_page"),
        "last_page": bib.get("last_page"),
        "source_id": _short(src.get("id")),
        "source_name": src.get("display_name") or loc.get("raw_source_name") or "",
        "source_type": src.get("type"),
        "source_issns": src.get("issn") or [],
        "referenced_works": [_short(x) for x in w.get("referenced_works") or []],
    }


def openalex_works_by_doi(dois, http, cache, log=print):
    """{doi: compact work or None}; one request per 50 DOIs."""
    dois = sorted({norm_doi(d) for d in dois if d})
    # refetch entries cached before the topic fields were added ('field' key missing); misses stay misses
    todo = [d for d in dois if d not in cache or (cache[d] is not None and "field" not in cache[d])]
    plain = [d for d in todo if not re.search(r"[|,<>\s\"]", d)]
    odd = [d for d in todo if d not in plain]
    for n, batch in enumerate(_batches(plain, 50), 1):
        data = http.get_json(f"{OPENALEX}/works", {"filter": "doi:" + "|".join(batch),
                                                   "select": WORK_FIELDS, "per-page": 100})
        found = {}
        for w in (data or {}).get("results", []):
            cw = _compact_work(w)
            found[cw["doi"]] = cw
        for d in batch:
            cache[d] = found.get(d)
        if n % 10 == 0:
            cache.save()
            log(f"    OpenAlex works: {min(n * 50, len(plain))}/{len(plain)}")
    for d in odd:
        w = http.get_json(f"{OPENALEX}/works/https://doi.org/{quote(d, safe='')}", {"select": WORK_FIELDS})
        cache[d] = _compact_work(w) if w else None
    cache.save()
    return {d: cache.get(d) for d in dois}


def openalex_abstracts(dois, http, cache):
    """{doi: abstract text or ''} rebuilt from OpenAlex's inverted index, for records that came without one."""
    dois = sorted({norm_doi(d) for d in dois if d and not re.search(r"[|,<>\s\"]", d)})
    for batch in _batches([d for d in dois if d not in cache], 50):
        data = http.get_json(f"{OPENALEX}/works", {"filter": "doi:" + "|".join(batch),
                                                   "select": "doi,abstract_inverted_index", "per-page": 100})
        found = {}
        for w in (data or {}).get("results", []):
            inv = w.get("abstract_inverted_index") or {}
            words = sorted((pos, word) for word, positions in inv.items() for pos in positions)
            found[norm_doi(w.get("doi") or "")] = " ".join(word for _, word in words)
        for d in batch:
            cache[d] = found.get(d, "")
    cache.save()
    return {d: cache.get(d, "") for d in dois}


def openalex_works_by_id(ids, http, cache):
    """{W-id: {'id','doi','title','year','type'}} for reference lists (e.g. of anchor surveys)."""
    ids = sorted({i for i in ids if i})
    for batch in _batches([i for i in ids if i not in cache], 50):
        data = http.get_json(f"{OPENALEX}/works", {"filter": "openalex:" + "|".join(batch),
                                                   "select": "id,doi,title,publication_year,type",
                                                   "per-page": 100})
        found = {_short(w["id"]): w for w in (data or {}).get("results", [])}
        for i in batch:
            w = found.get(i)
            cache[i] = {"id": i, "doi": norm_doi(w.get("doi") or ""), "title": w.get("title") or "",
                        "year": w.get("publication_year"), "type": w.get("type")} if w else None
    cache.save()
    return {i: cache.get(i) for i in ids}


def openalex_sources(source_ids, http, cache):
    """{S-id: {'name','type','issns','h_index'}} (h-index as computed by OpenAlex)."""
    ids = sorted({s for s in source_ids if s})
    for batch in _batches([s for s in ids if s not in cache], 50):
        data = http.get_json(f"{OPENALEX}/sources", {"filter": "openalex:" + "|".join(batch),
                                                     "select": "id,display_name,type,issn,summary_stats",
                                                     "per-page": 100})
        found = {_short(s["id"]): s for s in (data or {}).get("results", [])}
        for s in batch:
            src = found.get(s)
            cache[s] = {"name": src.get("display_name"), "type": src.get("type"),
                        "issns": src.get("issn") or [],
                        "h_index": (src.get("summary_stats") or {}).get("h_index")} if src else None
    cache.save()
    return {s: cache.get(s) for s in ids}


def crossref_meta(dois, http, cache, log=print):
    """{doi: {'event_acronym','event_name','containers','type','issns'} or None}; 40 DOIs per request."""
    dois = sorted({norm_doi(d) for d in dois if d})
    todo = [d for d in dois if d not in cache]
    plain = [d for d in todo if not re.search(r"[,<>\s\"]", d)]
    odd = [d for d in todo if d not in plain]

    def compact(item):
        ev = item.get("event") or {}
        return {"event_acronym": ev.get("acronym") or "", "event_name": ev.get("name") or "",
                "containers": item.get("container-title") or [], "type": item.get("type") or "",
                "issns": item.get("ISSN") or []}

    for n, batch in enumerate(_batches(plain, 40), 1):
        data = http.get_json(f"{CROSSREF}/works", {"filter": ",".join("doi:" + d for d in batch),
                                                   "select": "DOI,event,container-title,type,ISSN",
                                                   "rows": 100})
        found = {norm_doi(i["DOI"]): compact(i) for i in (data or {}).get("message", {}).get("items", [])}
        for d in batch:
            cache[d] = found.get(d)
        if n % 10 == 0:
            cache.save()
            log(f"    Crossref: {min(n * 40, len(plain))}/{len(plain)}")
    for d in odd:
        data = http.get_json(f"{CROSSREF}/works/{quote(d, safe='')}")
        cache[d] = compact(data["message"]) if data and "message" in data else None
    cache.save()
    return {d: cache.get(d) for d in dois}

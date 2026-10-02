# Sanity in Software Requirements: Quo Vadis?

Replication package of the paper *Sanity in Software Requirements: Quo Vadis? A Survey of Approaches and Tools for
Requirement Sanity Analysis*.

`index.html` is a search page over all 8,028 records of the literature search and the 671 papers of the final pool:
**https://anon-fse-contributor.github.io/FSE-Artifact-2414/** (or open it locally in a browser).

| File | Content |
|---|---|
| `index.html`, `papers.js` | Search page and its data |
| `papers.csv`, `papers.bib` | The 671 papers of the final pool |
| `records.csv` | All 8,028 records with status, selection step and reason |
| `cited_in_paper.csv` | The references cited in the paper |
| `build.py`, `openalex_keywords.json`, `extra_abstracts.csv` | Script and inputs that generate the files above |
| `selection/` | The study selection: `run.py`, its settings (`config.yaml`), CORE rank lists (`data/`) and cached OpenAlex/Crossref responses (`cache/`) |
| `searches/` | Inputs of the selection: the Scopus and ACM DL exports of the six searches, the Zotero collection and the draft's bib |

## Run it

Needs Python 3 with `pyyaml`, `requests` and `matplotlib`.

```bash
python selection/run.py --offline   # study selection from the cached API data -> selection/out/
python build.py                     # papers.js, papers.csv, papers.bib and records.csv from selection/out/
```

Without `--offline`, `run.py` asks OpenAlex and Crossref for records missing from `selection/cache/`. With the cache
as published, both scripts reproduce the files of this repository (up to the run date in the header of `papers.bib`).

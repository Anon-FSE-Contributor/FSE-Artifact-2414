"""Small helpers for the output files."""
import csv
from pathlib import Path


def _fmt(v):
    if isinstance(v, (list, tuple, set)):
        return "; ".join(str(x) for x in (sorted(v) if isinstance(v, set) else v))
    if isinstance(v, float):
        return f"{v:.4f}"
    return "" if v is None else v


def write_csv(path, rows, columns):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(columns)
        for r in rows:
            w.writerow([_fmt(r.get(c)) for c in columns])


def write_text(path, text):
    Path(path).write_text(text.rstrip() + "\n", encoding="utf-8")

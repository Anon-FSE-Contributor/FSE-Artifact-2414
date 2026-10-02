"""Which sanity notions (Table 1 of the paper) a paper addresses, by keywords in its title, abstract and author
keywords, and the bar chart of the counts. A paper can address several notions."""
import csv
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from . import style  # noqa: E402
from .colors import INK, INK_2, REMAINING, tint  # noqa: E402

BAR_H = 0.62                             # thickness of a bar, as a share of the space per notion
EDGE = 0.012                             # the bars start with a strip in the full colour, as the nodes of the Sankey
GUTTER = 0.12                            # inches of white on the left, between the Sankey and the longest name


def compile_patterns(patterns):
    return {name: re.compile(rx, re.IGNORECASE) for name, rx in patterns.items()}


def notions_of(rec, compiled):
    text = " ".join([rec.get("title", ""), rec.get("abstract", ""), rec.get("keywords", "")])
    return [name for name, rx in compiled.items() if rx.search(text)]


def tag(pool, patterns):
    """Sets rec['notions'] on every paper; returns {notion: number of papers}."""
    compiled = compile_patterns(patterns)
    counts = {name: 0 for name in patterns}
    for r in pool:
        r["notions"] = notions_of(r, compiled)
        for n in r["notions"]:
            counts[n] += 1
    return counts


def write_csv(path, counts, total, none):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["notion", "papers"])
        for name, n in counts.items():
            w.writerow([name, n])
        w.writerow(["none of the six", none])
        w.writerow(["papers in the final pool", total])


def figure(paths, counts, width_in=None, height_in=None):
    """Horizontal bars of the papers per notion, largest first: the notion on the left, the count at the end of the
    bar, no axis. Bars in the colour of the final pool in the Sankey; subfigure size and the paper's font."""
    style.apply()
    width_in, height_in = width_in or style.NOTIONS_WIDTH_IN, height_in or style.HEIGHT_IN
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    names, values = [k for k, _ in ranked], [v for _, v in ranked]
    n, top = len(values), max(values, default=1) or 1
    fig, ax = plt.subplots(figsize=(width_in, height_in))
    ys = range(n - 1, -1, -1)
    ax.barh(ys, values, height=BAR_H, color=tint(REMAINING), edgecolor="none", zorder=2)
    ax.barh(ys, [min(v, top * EDGE) for v in values], height=BAR_H, color=REMAINING, edgecolor="none", zorder=3)
    for y, v in zip(ys, values):
        ax.text(v + top * 0.03, y, f"{v:,}", ha="left", va="center", color=INK_2)
    ax.set_yticks(list(ys))
    ax.set_yticklabels(names, color=INK)
    ax.set_ylim(-0.5, n - 0.5)
    ax.set_xlim(0, top * 1.22)
    ax.set_xticks([])
    ax.tick_params(axis="y", length=0, pad=3)
    for side in ("top", "right", "bottom", "left"):
        ax.spines[side].set_visible(False)
    fig.tight_layout(pad=0.05, rect=(GUTTER / width_in, 0, 1, 1))
    for p in paths:
        fig.savefig(p)
    plt.close(fig)

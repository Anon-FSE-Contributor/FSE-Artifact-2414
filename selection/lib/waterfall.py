"""The waterfall: how many records each step removes, as a Markdown/CSV table and a figure for the paper.
Rows are (label, removed, remaining, kind, short label); kind is 'total' or 'step'."""
import csv

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

from .colors import CATEGORY, GRID, INK, INK_2, REMAINING, REMOVED, of_step, tint  # noqa: E402

CATEGORY_NAMES = {"surveys": "Surveys", "foundational": "Foundational", "related_topics": "Related topics"}
HEIGHT_IN = 2.6                                     # not in the paper: full text width, room for one row per step


def table_md(rows):
    lines = ["| Step | Removed | Remaining |", "|---|---:|---:|"]
    for label, removed, remaining, kind, *_ in rows:
        lines.append(f"| **{label}** | | **{remaining:,}** |" if kind == "total"
                     else f"| {label} | {removed:,}{' (kept apart)' if kind == 'apart' else ''} | {remaining:,} |")
    return "\n".join(lines)


def table_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["step", "removed", "remaining"])
        for label, removed, remaining, kind, *_ in rows:
            w.writerow([label, "" if kind == "total" else removed, remaining])


def figure(paths, rows, width_in=None, height_in=None):
    """Horizontal waterfall from the identified records to the final pool, with the steps grouped as in the Sankey:
    totals are bars from 0; each step is a bar spanning the records it takes out, coloured by what happens to them.
    Tinted bars with a solid outline; standard size and the paper's font."""
    from . import sankey, style                     # local import: sankey imports this module's colours only
    style.apply()
    width_in, height_in = width_in or style.TEXT_WIDTH_IN, height_in or HEIGHT_IN
    ident = next(r for r in rows if r[3] == "total")
    items = [("Records identified", 0, ident[2], "total", "")]
    items += [(label, removed, after, "step", key) for label, removed, after, key in sankey.stages(rows)]
    items += [("Final pool", 0, items[-1][2], "total", "")]
    fig, ax = plt.subplots(figsize=(width_in, height_in))
    xmax = max(r[2] for r in items)
    for i, (label, removed, remaining, kind, key) in enumerate(items):
        y = len(items) - 1 - i
        color = REMAINING if kind == "total" else of_step(key)
        left, width = (0, remaining) if kind == "total" else (remaining, removed)
        ax.barh(y, width, left=left, height=0.62, color=tint(color), edgecolor=color, linewidth=0.8, zorder=2)
        ax.text(left + width + xmax * 0.012, y, f"{remaining:,}" if kind == "total" else f"\u2212{removed:,}",
                va="center", ha="left", color=INK if kind == "total" else INK_2,
                fontweight="bold" if kind == "total" else "normal")
    ax.set_yticks(range(len(items)))
    ax.set_yticklabels([r[0] for r in reversed(items)], color=INK)
    ax.set_ylim(-0.6, len(items) - 0.4)
    ax.set_xlim(0, xmax * 1.13)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.tick_params(axis="x", colors=INK_2, length=0)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.6, zorder=0)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    present = [k for k in CATEGORY if any(it[4] == k for it in items)]
    handles = [plt.Rectangle((0, 0), 1, 1, facecolor=tint(c), edgecolor=c, linewidth=0.8)
               for c in [REMAINING, REMOVED] + [CATEGORY[k] for k in present]]
    ax.legend(handles, ["Remaining", "Removed"] + [CATEGORY_NAMES[k] for k in present], frameon=False,
              loc="lower right", handlelength=1.0, handleheight=0.8, labelspacing=0.25, borderaxespad=0.1)
    fig.tight_layout(pad=0.3)
    for p in paths:
        fig.savefig(p)
    plt.close(fig)

"""Sankey diagram of the selection for the paper: sources -> deduplication -> filter steps -> final pool.

Drawn in inches at print size, to be included at 100%. The records flow left to right along the top through one
node per step, with the number of records left above each node. At each step the records it takes out branch off
the bottom of the flow and curve down to their own node one step further right; these nodes form a staircase, the
first step lowest, each labelled on its right. The final pool ends at the top right. The flow thickness and the step
spacing are fitted so that the drawing fills the subfigure. Flows are tinted, nodes are slim bars in the full colour
of what passes through them."""
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.path import Path  # noqa: E402
from matplotlib.patches import PathPatch, Rectangle  # noqa: E402

from . import style  # noqa: E402
from .colors import CATEGORY, INK, INK_2, REMAINING as FLOW, of_step, tint  # noqa: E402

# One branch per group of steps; the four tiny eligibility steps share one branch.
STAGES = [(["duplicates"], None),
          (["not_a_paper", "not_english", "not_peer_reviewed", "retracted"], "Not a paper or preprint"),
          (["off_topic"], None), (["short_papers"], None), (["superseded_versions"], None), (["surveys"], None),
          (["foundational"], None), (["published_before"], None), (["venue_rank"], None), (["citation_impact"], None),
          (["isolated"], None), (["related_topics"], None)]

# Sizes in inches.
NODE_W, LABEL_GAP = 0.016, 0.03        # width of the node bars; space between a node and its label
MERGE = 0.16                           # from the source nodes to the first node of the flow
GAP, PAD = 0.02, 0.0                   # from the flow to the nodes below it; between two rows of labels
SOURCE_GAP, COUNT_DY = 0.05, 0.018     # between the sources; from the flow to the counts above it
MIN_NODE = 0.012                       # nodes of tiny branches are drawn at least this tall
MAX_FLOW = 0.6                         # the flow is at most this share of the height at its start
COUNT_SPACE = 0.04                     # counts closer than this are set in two alternating rows
MARGIN = 0.005                         # around the drawing
ROW = 1.08                             # pitch of the rows of labels, in multiples of the font size
EDGE = 0.85                            # tint of the hairline around each band


def stages(rows):
    """[(label, removed, remaining after, key)] for the Sankey; rows are the waterfall rows
    (label, removed, remaining, kind, short label, key). key decides the colour of the branch."""
    by_key = {r[5]: r for r in rows if len(r) > 5}
    out = []
    for keys, label in STAGES:
        present = [k for k in keys if k in by_key]
        if present:
            out.append((label or by_key[present[0]][4], sum(by_key[k][1] for k in present), by_key[present[-1]][2],
                        present[0]))
    return out


def _band(ax, x0, lo0, hi0, x1, lo1, hi1, color):
    """Band from lo0..hi0 at x0 to lo1..hi1 at x1 along S-shaped edges. Opaque, with a hairline in a deeper tint of
    its own colour, which keeps the thinnest branches visible and parts a branch from the flow it leaves."""
    xm, c4 = (x0 + x1) / 2, Path.CURVE4
    verts = [(x0, hi0), (xm, hi0), (xm, hi1), (x1, hi1), (x1, lo1), (xm, lo1), (xm, lo0), (x0, lo0), (x0, hi0)]
    codes = [Path.MOVETO, c4, c4, c4, Path.LINETO, c4, c4, c4, Path.CLOSEPOLY]
    ax.add_patch(PathPatch(Path(verts, codes), facecolor=tint(color), edgecolor=tint(color, EDGE), linewidth=0.4,
                           zorder=1))


def _node(ax, x, lo, hi, color=FLOW):
    grow = max(0.0, MIN_NODE - (hi - lo)) / 2
    ax.add_patch(Rectangle((x, lo - grow), NODE_W, hi - lo + 2 * grow, facecolor=color, edgecolor="none", zorder=3))


def _measure(texts):
    """(width, height) in inches of each (text, font weight) in the current style."""
    fig = plt.figure()
    renderer = fig.canvas.get_renderer()
    sizes = []
    for s, weight in texts:
        box = fig.text(0, 0, s, fontweight=weight, linespacing=1.1).get_window_extent(renderer)
        sizes.append((box.width / fig.dpi, box.height / fig.dpi))
    plt.close(fig)
    return sizes


def _layout(sources, st, flow_in, label_h, source_h):
    """Vertical layout for a flow flow_in inches thick at its start; y grows upwards and the flow's top is at 0.
    Returns the records per inch, the records at each node of the flow, the centre of each branch node (by step,
    from 1), the centres of the sources and the lowest point of the drawing."""
    total = sum(c for _, c in sources)
    s, n = flow_in / total, len(st)
    rem = [total] + [after for _, _, after, _ in st]
    slot = [0.0] + [max(removed * s, label_h) for _, removed, _, _ in st]
    y = [0.0] * (n + 1)
    for k in range(n, 0, -1):                             # from the top right down the staircase
        y[k] = -rem[k] * s - GAP - slot[k] / 2            # below the flow at its column
        # The label of step k reaches under the next node and its label, so it has to stay below both. A tall node
        # sits in its own column, so only its label counts, and the next node may come down beside its upper half.
        if k == n:                                        # and below the label of the final pool
            y[k] = min(y[k], -rem[n] * s / 2 - label_h - PAD)
        else:                                             # and below the next node and its label
            y[k] = min(y[k], y[k + 1] - (label_h + slot[k + 1]) / 2 - PAD)
    lowest = min([y[k] - slot[k] / 2 for k in range(1, n + 1)] + [-rem[0] * s])
    centres, top = [], 0.0
    for _, count in sources:                              # the sources hang from the top of the flow
        h = max(count * s, source_h)
        centres.append(top - h / 2)
        top -= h + SOURCE_GAP
    return dict(s=s, rem=rem, y=y, source_centre=centres, lowest=min(lowest, top + SOURCE_GAP))


def figure(paths, sources, rows, width_in=None, height_in=None):
    """sources: [(name, count)]; rows: waterfall rows. Draws the figure at exactly width_in x height_in (default:
    the subfigure size in style) in the paper's font, 1 data unit = 1 inch."""
    style.apply()
    width_in, height_in = width_in or style.SANKEY_WIDTH_IN, height_in or style.HEIGHT_IN
    st = stages(rows)
    n = len(st)
    total = sum(c for _, c in sources)
    labels = [(f"{label} ({removed:,})", "normal") for label, removed, _, _ in st]
    final_label = (f"Final pool ({st[-1][2]:,})", "bold")
    source_labels = [(f"{name}\n{count:,}", "normal") for name, count in sources]
    sizes = _measure(labels + [final_label] + source_labels + [(f"{total:,}", "normal")])
    label_w = [0.0] + [w for w, _ in sizes[:n]]
    label_w[n] = max(label_w[n], sizes[n][0])             # the final pool is labelled in the last column too
    label_h = count_h = ROW * style.FONT_SIZE / 72        # one row of labels
    source_w = max(w for w, _ in sizes[n + 1:-1])
    source_h = max(h for _, h in sizes[n + 1:-1])
    count_w = max(_measure([(f"{c:,}", "normal") for c in [total] + [a for _, _, a, _ in st]]))[0]
    inner_w, inner_h = width_in - 2 * MARGIN, height_in - 2 * MARGIN

    # fit the step spacing to the width: the label of step k starts at column k and must end inside the figure
    left = NODE_W + LABEL_GAP + source_w
    room = inner_w - left - MERGE - NODE_W - LABEL_GAP
    dx = min((room - label_w[k]) / k for k in range(1, n + 1))
    xs = [MERGE + i * dx for i in range(n + 1)]
    count_rows = 1 if dx >= count_w + COUNT_SPACE else 2
    counts_top = COUNT_DY + count_rows * count_h

    def height(flow_in):
        return counts_top - _layout(sources, st, flow_in, label_h, source_h)["lowest"]

    lo, hi = 0.05, MAX_FLOW * inner_h                     # fit the flow thickness to the height
    if height(hi) <= inner_h:
        lo = hi
    while hi - lo > 1e-4:
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if height(mid) <= inner_h else (lo, mid)
    g = _layout(sources, st, lo, label_h, source_h)
    s, rem, y = g["s"], g["rem"], g["y"]

    fig = plt.figure(figsize=(width_in, height_in))
    ax = fig.add_axes([0, 0, 1, 1])
    y0 = g["lowest"] - (height_in - (counts_top - g["lowest"])) / 2
    ax.set_xlim(-left - MARGIN, width_in - left - MARGIN)
    ax.set_ylim(y0, y0 + height_in)
    ax.axis("off")
    eps = 0.005                                           # bands reach just under the nodes

    y_in = 0.0                                            # the sources merge into the first node
    for (name, count), c in zip(sources, g["source_centre"]):
        h = count * s
        _band(ax, -eps, c - h / 2, c + h / 2, xs[0] + eps, y_in - h, y_in, FLOW)
        _node(ax, -NODE_W, c - h / 2, c + h / 2)
        ax.text(-NODE_W - LABEL_GAP, c, f"{name}\n{count:,}", ha="right", va="center", color=INK, linespacing=1.1)
        y_in -= h

    for i in range(n + 1):                                # the nodes of the flow, with the records left
        _node(ax, xs[i], -rem[i] * s, 0.0)
        if i < n:                                         # the final pool's count is in its label
            ax.text(xs[i] + NODE_W / 2, COUNT_DY + (i % count_rows) * count_h, f"{rem[i]:,}", ha="center",
                    va="bottom", color=INK_2)
    for k in range(1, n + 1):                             # the flow on to the next node, and the branch
        label, removed, after, key = st[k - 1]
        x0, x1 = xs[k - 1] + NODE_W - eps, xs[k] + eps
        _band(ax, x0, -after * s, 0.0, x1, -after * s, 0.0, FLOW)
        h = removed * s
        _band(ax, x0, -rem[k - 1] * s, -after * s, x1, y[k] - h / 2, y[k] + h / 2, of_step(key))
        _node(ax, xs[k], y[k] - h / 2, y[k] + h / 2, of_step(key))
        ax.text(xs[k] + NODE_W + LABEL_GAP, y[k], f"{label} ({removed:,})", ha="left", va="center",
                color=INK if key in CATEGORY else INK_2)
    ax.text(xs[n] + NODE_W + LABEL_GAP, -rem[n] * s / 2, final_label[0], ha="left", va="center", color=INK,
            fontweight="bold")
    for p in paths:
        fig.savefig(p)
    plt.close(fig)

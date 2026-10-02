"""Colours of all figures: the Sankey, the notions chart, the waterfall, and Paper/assets/figs/fig_prisma.tex, which
repeats the hex codes. Four hues of Paul Tol's colour-blind-safe schemes for scientific figures (SRON, 2021): blue
for the records that go on and the final pool, and one accent for each category kept apart. Checked with the dataviz
validator over all pairs of the four: worst colour-vision-deficiency separation dE 8.3 (target >= 8), worst
normal-vision separation dE 20.0 (floor 15). Records a step removes are neutral gray, so the eye follows the flow
and the three categories.

Areas (flows, bars) are filled with a tint of their colour, the look of the colour at opacity FILL on the white page,
and nodes use the full colour. The tints are opaque, so shapes that touch show no seams. Every flow and bar is
labelled, so no figure relies on colour alone."""

REMAINING = "#4477AA"                  # blue: records that go on to the next step, the final pool
REMOVED = "#A5A5A5"                    # gray: removed by a step
CATEGORY = {                           # kept apart in their own category
    "surveys": "#DDAA33",              # gold
    "foundational": "#AA3377",         # wine
    "related_topics": "#228833",       # green
}
FILL = 0.55
INK, INK_2, GRID = "#1a1a1a", "#555555", "#e3e3e3"


def of_step(key):
    """Colour of what a step takes out of the flow."""
    return CATEGORY.get(key, REMOVED)


def tint(color, amount=FILL):
    """color mixed with white: as it looks at opacity `amount` on white paper, but opaque."""
    rgb = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return tuple(1 - amount + amount * v for v in rgb)

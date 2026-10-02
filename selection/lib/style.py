"""One style for all figures: the paper's font (Linux Libertine, as used by acmart) at one size, and the sizes of the
two subfigures of the selection figure in sec/003_methodology.tex, so that the figures are included at 100% and print
1:1. If the subfigure widths there change, change SANKEY_SHARE and NOTIONS_SHARE too."""
import subprocess
from pathlib import Path

import matplotlib
from matplotlib import font_manager

TEXT_WIDTH_IN = 395.8225 / 72.27        # \textwidth of acmsmall (395.8225 pt)
SANKEY_SHARE, NOTIONS_SHARE = 0.7, 0.3  # subfigure widths in sec/003_methodology.tex, as shares of \textwidth
SANKEY_WIDTH_IN = SANKEY_SHARE * TEXT_WIDTH_IN
NOTIONS_WIDTH_IN = NOTIONS_SHARE * TEXT_WIDTH_IN
HEIGHT_IN = 1.6                         # height of both subfigures; the Sankey needs this much for its labels at 7 pt
FONT_SIZE = 7                           # all text in the figures
FONT_FILES = ["LinLibertine_R.otf", "LinLibertine_RB.otf", "LinLibertine_RI.otf"]


def _find(name):
    try:
        out = subprocess.run(["kpsewhich", name], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    if not out:
        hits = sorted(Path("/usr/local/texlive").glob(f"*/texmf-dist/fonts/opentype/public/libertine/{name}"))
        out = str(hits[-1]) if hits else ""
    return out


def apply():
    """Register Linux Libertine (from the TeX installation) and set the rcParams; falls back to DejaVu Serif."""
    family = "DejaVu Serif"
    for name in FONT_FILES:
        path = _find(name)
        if path:
            font_manager.fontManager.addfont(path)
            family = font_manager.FontProperties(fname=path).get_name()
    matplotlib.rcParams.update({
        "font.family": family, "font.size": FONT_SIZE, "axes.labelsize": FONT_SIZE, "xtick.labelsize": FONT_SIZE,
        "ytick.labelsize": FONT_SIZE, "legend.fontsize": FONT_SIZE, "axes.titlesize": FONT_SIZE,
        "pdf.fonttype": 42, "savefig.dpi": 300,
    })
    return family

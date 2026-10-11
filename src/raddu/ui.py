"""Terminal styling helpers: colour handling, banners, tables, progress bars.

Everything degrades cleanly when stdout is not a TTY or when NO_COLOR is set.
"""

from __future__ import annotations

import math
import os
import shutil
import sys
import unicodedata
from typing import Iterable, Sequence


def _color_enabled(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("RADDU_FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


# Brand colour: a warm amber (24-bit), used for the logo and all chrome.
BRAND_RGB = (217, 119, 87)

# The raddu mark: a pocket radio. The display area is animated during
# playback by Radio below; this is its idle form.
LOGO: tuple[str, ...] = (
    "              ╱",
    "            ╱",
    "╭────────────────╮",
    "│ ┌────────────┐ │",
    "│ │ FM  ▯      │ │",
    "│ │ ▁▂▃▄▅▆▇█▇▆▅ │",
    "│ └────────────┘ │",
    "│ (o) ▒▒▒▒▒▒▒▒▒▒ │",
    "│        ░░░░░   │",
    "╰────────────────╯",
)

BARS = "▁▂▃▄▅▆▇█"


def display_width(text: str) -> int:
    """Approximate terminal columns, skipping zero-width combining marks."""
    return sum(0 if unicodedata.combining(ch) else 1 for ch in text)


class Palette:
    """ANSI codes, neutralised automatically when colour is off."""

    CODES = {
        "reset": "\033[0m",
        "bold": "\033[1m",
        "dim": "\033[2m",
        "red": "\033[31m",
        "green": "\033[32m",
        "yellow": "\033[33m",
        "blue": "\033[34m",
        "magenta": "\033[35m",
        "cyan": "\033[36m",
        "grey": "\033[90m",
        "brand": f"\033[38;2;{BRAND_RGB[0]};{BRAND_RGB[1]};{BRAND_RGB[2]}m",
    }

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, *styles: str) -> str:
        if not self.enabled or not styles:
            return text
        prefix = "".join(self.CODES[s] for s in styles if s in self.CODES)
        return f"{prefix}{text}{self.CODES['reset']}" if prefix else text


def palette(stream=None) -> Palette:
    return Palette(_color_enabled(stream if stream is not None else sys.stdout))


def term_width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except OSError:
        return default


def banner(pm: Palette, version: str, subtitle: str = "terminal Quran player") -> None:
    """Print the radio mark with the version set beside it."""
    text = [pm(f"raddu v{version}", "bold", "brand"), pm(subtitle, "brand")]
    top = max((len(LOGO) - len(text)) // 2, 0)
    for i, row in enumerate(LOGO):
        k = i - top
        suffix = "   " + text[k] if 0 <= k < len(text) else ""
        print((pm(row, "brand") + suffix).rstrip())


def card(pm: Palette, rows: Sequence[tuple[str, str]], *, width: int | None = None) -> None:
    """Print a framed key/value card, sized to its content."""
    limit = width or min(term_width() - 4, 74)
    inner = max(
        [display_width(k) + display_width(v) + 4 for k, v in rows] + [28]
    )
    inner = min(inner, limit - 4)

    def line(text: str = "") -> None:
        pad = max(inner - display_width(text), 0)
        print(pm("│ ", "brand") + text + " " * pad + pm(" │", "brand"))

    print(pm("╭" + "─" * (inner + 2) + "╮", "brand"))
    for key, value in rows:
        if not key:
            line(value)
            continue
        label = pm(f"{key:<8}", "brand", "bold")
        room = inner - 8
        v = value if display_width(value) <= room else value[:room - 1] + "…"
        print(pm("│ ", "brand") + label + v + " " * max(inner - 8 - display_width(v), 0)
              + pm(" │", "brand"))
    print(pm("╰" + "─" * (inner + 2) + "╯", "brand"))


class Radio:
    """The raddu mark, alive during playback.

    Replaces the old progress bar entirely: the tuning cursor sweeps left to
    right, the display shows a live spectrum, and the speaker grille fills as
    bytes arrive. One object instead of a logo plus a separate meter.

    Column budgets are exact, so the block never reflows while it animates:

        outer case   18 cols  = "│ " + 14 + " │"
        display      12 cols  = " FM " + 9   (inside the inner box)
        spectrum     12 cols  = " " + 10 + " "
        grille       14 cols  = "(o) " + 10
        readout      14 cols  = "   " + 11
    """

    DISPLAY_CELLS = 7
    SPEC_CELLS = 10
    GRILLE_CELLS = 10
    ROWS = len(LOGO)

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stderr
        self.total = 0
        self.done = 0
        self.phase = 0
        self.started = False
        self.live = hasattr(self.stream, "isatty") and self.stream.isatty()

    # -- progress hooks (match net.stream's callback signature) ---------
    def start(self, total: int) -> None:
        self.total = total or 0
        self.done = 0
        self.phase = 0
        self.started = True
        self._paint(up=0)
        if not self.live:
            # not a terminal: one settled frame, no animation
            self.started = False

    def advance(self, chunk: int) -> None:
        self.done += chunk
        self.phase += 1
        self._paint(up=self.ROWS - 1)

    def stop(self) -> None:
        if self.started:
            self.stream.write("\r\033[K")
            self.stream.flush()
            self.started = False

    # -- rendering -------------------------------------------------------
    @staticmethod
    def _case(content: str, width: int = 14) -> str:
        return "│ " + content.ljust(width) + " │"

    def _display(self) -> str:
        """The FM line: a cursor sweeping left to right and back."""
        span = self.DISPLAY_CELLS - 1
        p = self.phase % (span * 2)
        pos = p if p <= span else span * 2 - p      # triangle wave 0..span..0
        cells = [" "] * self.DISPLAY_CELLS
        cells[pos] = "▯"
        return (" FM  " + "".join(cells)).ljust(12)

    def _spectrum(self) -> str:
        """A scrolling waveform whose height follows how much audio arrived."""
        frac = (self.done / self.total) if self.total else 0.0
        energy = 0.22 + 0.78 * frac if self.total else 0.16
        bars = []
        for i in range(self.SPEC_CELLS):
            a = math.sin(self.phase * 0.42 + i * 0.9)
            b = math.sin(self.phase * 0.17 + i * 0.37)
            v = (a * b + 1) / 2 * energy
            bars.append(BARS[min(len(BARS) - 1, int(v * len(BARS)))])
        return (" " + "".join(bars) + " ")

    def _grille(self) -> str:
        """The speaker grille doubles as the progress bar."""
        frac = (self.done / self.total) if self.total else 0.0
        filled = int(round(frac * self.GRILLE_CELLS))
        return "▓" * filled + "░" * (self.GRILLE_CELLS - filled)

    def _readout(self) -> str:
        if not self.total:
            return "connecting"
        pct = min(self.done / self.total * 100, 100)
        mb = self.done / 1_048_576
        return f"{pct:5.1f}% {mb:5.1f}M"

    def _lines(self) -> list[str]:
        box = "│" + self._display() + "│"
        wave = "│" + self._spectrum() + "│"
        return [
            LOGO[0],
            LOGO[1],
            LOGO[2],
            self._case("┌────────────┐"),
            self._case(box),
            self._case(wave),
            self._case("└────────────┘"),
            self._case("(o) " + self._grille()),
            self._case(" " + self._readout()),
            LOGO[9],
        ]

    def _paint(self, up: int = 0) -> None:
        if not self.live:
            return
        out = [f"\033[{up}A"] if up else []
        for row in self._lines():
            out.append("\r\033[K" + row + "\n")
        self.stream.write("".join(out))
        self.stream.flush()


def rule(pm: Palette, width: int | None = None) -> None:
    print(pm("─" * min(width or term_width(), 78), "brand"))


def table(
    pm: Palette,
    headers: Sequence[str],
    rows: Iterable[Sequence[str]],
    *,
    aligns: Sequence[str] | None = None,
    indent: str = "  ",
) -> None:
    """Render a simple aligned table.  Column 0 is treated as a header/code."""
    rows = [list(map(str, r)) for r in rows]
    if not rows:
        return
    ncols = len(headers)
    aligns = list(aligns or ["l"] * ncols)
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r[:ncols]):
            widths[i] = max(widths[i], len(cell))

    def fmt(cells, styler):
        out = []
        for i, cell in enumerate(cells[:ncols]):
            pad = widths[i] - len(cell)
            out.append((" " * pad + cell) if aligns[i] == "r" else (cell + " " * pad))
        return indent + styler("  ".join(out).rstrip())

    print(fmt(headers, lambda s: pm(s, "brand", "bold")))
    for r in rows:
        print(fmt(r, lambda s: s))


def human_size(n: int) -> str:
    step = 1024.0
    val = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if val < step:
            return f"{val:.0f} {unit}" if unit in ("B", "KB") else f"{val:.1f} {unit}"
        val /= step
    return f"{val:.1f} TB"


def human_time(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
"""Terminal styling helpers: colour handling, banners, tables, progress bars.

Everything degrades cleanly when stdout is not a TTY or when NO_COLOR is set.
"""

from __future__ import annotations

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

# The raddu mark: a pocket radio.
LOGO: tuple[str, ...] = (
    "              ╱",
    "            ╱",
    "╭────────────────╮",
    "│ ┌────────────┐ │",
    "│ │ FM  ▮      │ │",
    "│ └────────────┘ │",
    "│ (o)     ░░░░░░ │",
    "│         ░░░░░░ │",
    "╰────────────────╯",
)

# Block-letter wordmark, drawn large so the name reads as a wordmark rather
# than as a line of running text.
WORDMARK: tuple[str, ...] = (
    "███████╗  ██████╗  ██████╗  ██████╗  ██╗   ██╗",
    "╚══██╔══╝ ██╔══██╗██╔═══██╗██╔══██╗ ╚██╗ ██╔╝",
    "   ██║    ██████╔╝██║   ██║██████╔╝  ╚████╔╝ ",
    "   ██║    ██╔══██╗██║   ██║██╔══██╗   ╚██╔╝  ",
    "   ██║    ██████╔╝╚██████╔╝██║  ██║   ██║   ",
    "   ╚═╝    ╚═════╝  ╚═════╝ ╚═╝  ╚═╝   ╚═╝   ",
)


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
    """Print the radio mark beside the block wordmark."""
    rows = max(len(LOGO), len(WORDMARK))
    pad = " " * display_width(LOGO[0])
    top = (len(LOGO) - len(WORDMARK)) // 2 if len(LOGO) > len(WORDMARK) else 0
    for i in range(rows):
        left = LOGO[i] if i < len(LOGO) else pad
        right = WORDMARK[i - top] if top <= i < top + len(WORDMARK) else ""
        print((pm(left, "brand") + "  " + pm(right, "brand", "bold")).rstrip())
    print(pm(f"  رَدْدُ   ·   {subtitle}   ·   v{version}", "brand"))


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


class ProgressBar:
    """Single-line progress bar written to stderr."""

    def __init__(self, label: str, stream=None, width: int = 28) -> None:
        self.stream = stream or sys.stderr
        self.label = label
        self.width = width
        self.total = 0
        self.done = 0
        self._last = 0.0

    def start(self, total: int) -> None:
        self.total = total or 0
        self.done = 0
        self._render(force=True)

    def advance(self, chunk: int) -> None:
        self.done += chunk
        self._render()

    def stop(self) -> None:
        if self._last:
            self.stream.write("\r" + " " * (self.width + 34) + "\r")
            self.stream.flush()
            self._last = 0.0

    def _render(self, force: bool = False) -> None:
        if not self.total:
            return
        frac = min(self.done / self.total, 1.0)
        filled = int(self.width * frac)
        bar = "█" * filled + "░" * (self.width - filled)
        mb = self.done / 1_048_576
        tot = self.total / 1_048_576
        line = (f"  {self.label:<18} {bar} {frac * 100:5.1f}%  "
                f"{mb:6.1f}/{tot:.1f} MB")
        self.stream.write("\r" + line[: term_width() - 1])
        self.stream.flush()
        self._last = 1.0


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
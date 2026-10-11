"""Terminal styling helpers: colour handling, banners, tables, progress bars.

Everything degrades cleanly when stdout is not a TTY or when NO_COLOR is set.
"""

from __future__ import annotations

import os
import shutil
import sys
from typing import Iterable, Sequence


def _color_enabled(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("RADDU_FORCE_COLOR"):
        return True
    return hasattr(stream, "isatty") and stream.isatty()


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
    rule = "─" * min(term_width(), 64)
    print(pm(rule, "grey"))
    print(f" {pm('raddu', 'bold', 'cyan')} {pm('v' + version, 'grey')}"
          f"  {pm('·', 'grey')}  {pm(subtitle, 'grey')}")
    print(pm(rule, "grey"))


def rule(pm: Palette, width: int | None = None) -> None:
    print(pm("─" * min(width or term_width(), 78), "grey"))


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

    print(fmt(headers, lambda s: pm(s, "grey", "bold")))
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
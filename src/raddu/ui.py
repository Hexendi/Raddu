"""Terminal styling helpers: colour handling, banners, tables, progress bars.

Everything degrades cleanly when stdout is not a TTY or when NO_COLOR is set.
"""

from __future__ import annotations

import math
import os
import shutil
import sys
import threading
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

# The raddu mark: a pocket radio, shown as a static badge in the banner.
# Playback is reported by the Visualizer below, not by animating this.
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


class Visualizer:
    """A music-player style spectrum, drawn live during playback.

    Mirrored bars scrolling right to left around a centre line, the way a
    phone music app looks. The speaker grille of the old meter is gone; this
    is the progress display.

    The bars are driven by elapsed frames and by how much audio has arrived,
    not by the loudness of the sound. raddu pipes the MP3 straight to the
    player and never sees the samples, so there is no signal to sample -
    an FFT would mean decoding the stream a second time.
    """

    LEVELS = 8
    HEIGHT = 4              # rows of bars above (and below) the centre line
    FPS = 14

    def __init__(self, title: str = "", stream=None) -> None:
        self.stream = stream or sys.stderr
        self.title = title
        self.total = 0
        self.done = 0
        self.phase = 0
        self.started = False
        self.live = hasattr(self.stream, "isatty") and self.stream.isatty()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- progress hooks (match net.stream's callback signature) ---------
    def start(self, total: int) -> None:
        with self._lock:
            self.total = total or 0
            self.done = 0
        self.phase = 0
        self.started = True
        self._paint(up=0)
        if not self.live:
            # not a terminal: one settled frame, no animation
            self.started = False
            return
        # The animation runs on its own clock. Driving it from the network
        # callback instead made the frame rate a function of how fast the
        # server happened to be: a 550 KB file produced thirteen frames and
        # the display looked frozen.
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        interval = 1.0 / self.FPS
        while not self._stop.wait(interval):
            self.phase += 1
            try:
                self._paint(up=self.rows())
            except (ValueError, OSError):     # stream closed under us
                break

    def advance(self, chunk: int) -> None:
        with self._lock:
            self.done += chunk

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        if self.started:
            self.stream.write("\r\033[K")
            self.stream.flush()
            self.started = False

    # -- geometry --------------------------------------------------------
    @property
    def width(self) -> int:
        return max(24, min(52, term_width() - 8))

    def rows(self) -> int:
        return self.HEIGHT * 2 + 2      # mirrored bars, centre rule, readout

    # -- data ------------------------------------------------------------
    def _levels(self) -> list[int]:
        """Per-column bar heights, scrolling with the stream."""
        frac = (self.done / self.total) if self.total else 0.0
        gain = 0.45 + 0.55 * frac if self.total else 0.30
        cols = []
        for i in range(self.width):
            # sample behind the head so the bars trail the download
            t = self.phase * 0.5 - i * 0.42
            a = math.sin(t * 0.9) + 0.6 * math.sin(t * 0.31 + 1.7)
            b = math.sin(t * 0.21 + i * 0.5)
            v = (a * b + 1.6) / 3.2
            v = max(0.0, min(1.0, v))
            v = v ** 2.4 * gain          # push toward zero so bars read as bars
            cols.append(min(self.LEVELS, int(v * self.LEVELS + 0.5)))
        return cols

    # -- rendering -------------------------------------------------------
    @staticmethod
    def _cell(level: int, row: int, height: int) -> str:
        """One character of one column, mirrored around the centre line."""
        dist = abs(row)
        if dist > level:
            return " "
        if level <= 1:
            return BARS[0]
        return BARS[min(len(BARS) - 1, level - dist)]

    def _lines(self) -> list[str]:
        cols = self._levels()
        top = self.HEIGHT
        out = []
        for r in range(-top, 0):
            out.append("  " + "".join(self._cell(l, r, top) for l in cols))
        out.append("  " + "─" * len(cols))
        for r in range(1, self.HEIGHT + 1):      # mirror of the rows above
            out.append("  " + "".join(self._cell(l, r, self.HEIGHT) for l in cols))

        if self.total:
            pct = min(self.done / self.total * 100, 100)
            right = f"{pct:5.1f}%  {human_size(self.done)}"
        else:
            right = "connecting"
        left = self.title
        gap = max(len(cols) + 4 - len(left) - len(right), 1)
        out.append(f"  {left}{' ' * gap}{right}")
        return out

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
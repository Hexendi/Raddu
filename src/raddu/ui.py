"""Terminal styling helpers: colour handling, banners, tables, progress bars.

Everything degrades cleanly when stdout is not a TTY or when NO_COLOR is set.
"""

from __future__ import annotations

import math
import os
import shutil
import sys
import threading
import time
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
    "│ │ FM  ▮      │ │",
    "│ └────────────┘ │",
    "│ (o)     ░░░░░░ │",
    "│         ░░░░░░ │",
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

    Bars are tinted along an orange ramp instead of being left white: the
    taller the bar the lighter the tint, and the whole ramp drifts across
    the display over time, so the colour never sits in one place.
    """

    LEVELS = 8
    HEIGHT = 4              # rows of bars above (and below) the centre line
    FPS = 14

    # Deep orange up to a pale amber, as 24-bit RGB.
    RAMP = (
        (146, 58, 12), (176, 72, 18), (203, 86, 26), (217, 105, 45),
        (233, 132, 72), (245, 162, 104), (252, 191, 146), (255, 222, 196),
    )

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
    def _tint(self, index: int, level: int) -> str:
        """Pick a ramp entry for a column and emit its escape sequence.

        Height chooses the tint, and the ramp is offset by both the column
        position and the frame counter, so the colours travel across the
        display instead of staying pinned to each bar.
        """
        if not self.live:
            return ""
        r, g, b = self.RAMP[
            (level + index // 4 + self.phase // 3) % len(self.RAMP)
        ]
        return f"\033[38;2;{r};{g};{b}m"

    def _cell(self, index: int, level: int, row: int) -> str:
        """One character of one column, mirrored around the centre line."""
        dist = abs(row)
        if dist > level:
            return self._tint(index, level) + " "
        if level <= 1:
            ch = BARS[0]
        else:
            ch = BARS[min(len(BARS) - 1, level - dist)]
        return self._tint(index, level) + ch

    def _row(self, cols: list[int], row: int) -> str:
        return "  " + "".join(
            self._cell(i, l, row) for i, l in enumerate(cols)
        ) + "\033[0m"

    def _lines(self) -> list[str]:
        cols = self._levels()
        out = []
        for r in range(-self.HEIGHT, 0):
            out.append(self._row(cols, r))
        rule = "─" * len(cols)
        out.append("  " + (self._tint(0, 1) + rule + "\033[0m" if self.live else rule))
        for r in range(1, self.HEIGHT + 1):      # mirror of the rows above
            out.append(self._row(cols, r))

        if self.total:
            pct = min(self.done / self.total * 100, 100)
            right = f"{pct:5.1f}%  {human_size(self.done)}"
        else:
            right = "connecting"
        left = self.title
        gap = max(len(cols) + 4 - len(left) - len(right), 1)
        line = f"  {left}{' ' * gap}{right}"
        return out + [self._tint(0, 2) + line + "\033[0m" if self.live else line]

    def _paint(self, up: int = 0) -> None:
        if not self.live:
            return
        out = [f"\033[{up}A"] if up else []
        for row in self._lines():
            out.append("\r\033[K" + row + "\n")
        self.stream.write("".join(out))
        self.stream.flush()


class ProgressBar:
    """Single-line download progress bar, used only at download time.

    Playback keeps the animated :class:`Visualizer`; installing surahs
    (``-d`` / ``--download``) shows this plain bar instead of the voice
    art so the percentage and byte counts stay readable.
    """

    def __init__(self, label: str = "", stream=None, width: int = 28) -> None:
        self.stream = stream or sys.stderr
        self.label = label
        self.width = width
        self.total = 0
        self.done = 0
        self.started = False
        self.live = hasattr(self.stream, "isatty") and self.stream.isatty()
        self._last = 0.0

    # -- progress hooks (match net.stream's callback signature) ---------
    def start(self, total: int) -> None:
        self.total = total or 0
        self.done = 0
        self.started = True
        self._render(force=True)

    def advance(self, chunk: int) -> None:
        self.done += chunk
        self._render()

    def stop(self) -> None:
        if self.started and self.live:
            try:
                self.stream.write("\r\033[K")
                self.stream.flush()
            except (ValueError, OSError):
                pass
        self.started = False
        self._last = 0.0

    def _render(self, force: bool = False) -> None:
        if not self.live or not self.started:
            return
        now = time.monotonic()
        if not force and now - self._last < 0.08:
            return
        self._last = now
        if not self.total:
            line = f"  {self.label} connecting…"
            try:
                self.stream.write("\r\033[K" + line[: term_width() - 1])
                self.stream.flush()
            except (ValueError, OSError):
                pass
            return
        frac = min(self.done / self.total, 1.0)
        room = max(term_width() - 42 - len(self.label), 10)
        bar_w = max(10, min(self.width, room))
        filled = int(bar_w * frac)
        bar = "█" * filled + "░" * (bar_w - filled)
        line = (f"  {self.label} {bar} {frac * 100:5.1f}%  "
                f"{human_size(self.done)}/{human_size(self.total)}")
        try:
            self.stream.write("\r\033[K" + line[: term_width() - 1])
            self.stream.flush()
        except (ValueError, OSError):
            pass


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
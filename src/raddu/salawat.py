"""Salawat reminder: send blessings on the Prophet ﷺ every few minutes.

A stand-alone mode, separate from the reciters: ``raddu --salawat 10``
plays a short Arabic clip every ten minutes until stopped, in the
foreground or in the background (``-b``).

The default clip is short and simple: a real voice saying
"صلي على محمد ﷺ" (about four seconds), from the Internet Archive. It is
fetched once and cached. Any other clip
(for example "اللهم صل وسلم على نبينا محمد" in a voice you like) can be
used instead with ``raddu --set salawat_file=PATH``.
"""

from __future__ import annotations

import pathlib

from . import config
from .interlude import OFF_WORDS, ClipError, Timer, fetch_clip

DEFAULT_MINUTES = 10.0
CLIP_URL = ("https://archive.org/download/100-.-salawat-repeated-100-times-1/"
            "%D9%86%D8%BA%D9%85%D8%A9%20%20%D8%B5%D9%84%D9%8A%20%D8%B9%D9%84%D9%89%20%D9%85%D8%AD%D9%85%D8%AF%20%EF%B7%BA.mp3")
CLIP_NAME = "salawat-salli-ala-muhammad.mp3"
LABEL = "ﷺ  صلي على محمد"


class SalawatError(Exception):
    """Bad interval, or no clip to play."""


def parse_minutes(value: str | float | None) -> float | None:
    """``"10"`` -> 10.0; an off word -> None; anything else is an error."""
    if value is None:
        return None
    text = str(value).strip().lower()
    for suffix in ("min", "m"):
        text = text.removesuffix(suffix)
    if text in OFF_WORDS or text == "":
        return None
    try:
        minutes = float(text)
    except ValueError:
        raise SalawatError(
            f"salawat interval must be a number of minutes or 'off', got {value!r}"
        ) from None
    if minutes <= 0:
        raise SalawatError("salawat interval must be more than 0 minutes")
    return minutes


def default_minutes() -> float:
    """Interval used by a bare ``--salawat``: the saved one, else 10."""
    try:
        return parse_minutes(config.load().get("salawat")) or DEFAULT_MINUTES
    except SalawatError:
        return DEFAULT_MINUTES


def clip_path(timeout: float = 30.0) -> pathlib.Path:
    try:
        return fetch_clip(CLIP_URL, CLIP_NAME, "salawat_file", timeout)
    except ClipError as exc:
        raise SalawatError(str(exc)) from exc


class Reminder(Timer):
    """Plays the salawat clip every ``minutes``, counted after each clip."""

    name = "salawat"

    def __init__(self, spec, clip, minutes: float, on_event=None) -> None:
        super().__init__(spec, clip, on_event)
        self.interval = minutes * 60.0

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.fire(LABEL)

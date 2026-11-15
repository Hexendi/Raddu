"""Salawat reminder: send blessings on the Prophet ﷺ every few minutes.

A stand-alone mode, separate from the reciters: ``raddu --salawat 10``
plays a short Arabic clip every ten minutes until stopped, in the
foreground or in the background (``-b``).

The default clip is a real recording, not generated audio: Surah
Al-Ahzab 33:56 - "Indeed Allah and His angels send blessings upon the
Prophet. O you who believe, send blessings upon him and greet him with
peace" - recited by Mishary Rashid al-Afasy, served by everyayah.com. It
is fetched once and cached. Any other clip (for example
"اللهم صل وسلم على نبينا محمد" in a voice you like) can be used instead
with ``raddu --set salawat_file=PATH``.
"""

from __future__ import annotations

import pathlib

from . import config
from .interlude import OFF_WORDS, ClipError, Timer, fetch_clip

DEFAULT_MINUTES = 10.0
CLIP_URL = "https://everyayah.com/data/Alafasy_128kbps/033056.mp3"
CLIP_NAME = "salawat-alafasy-033056.mp3"
LABEL = "ﷺ  اللهم صل وسلم على نبينا محمد"


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

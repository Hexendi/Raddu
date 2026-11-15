"""Adhan at prayer time: a stand-alone mode, separate from the reciters.

``raddu --adhan`` waits for each of the five prayers at the configured
location (Nouakchott unless changed with ``--set location=...``) and
plays the adhan when the time comes, then waits for the next one, until
stopped. It can run in the foreground or in the background (``-b``).

The default recording is the adhan of Sabah Fakhri (1985), published in
the public domain on Wikimedia Commons. It is fetched once and cached;
``raddu --set adhan_file=PATH`` uses any other recording instead.
"""

from __future__ import annotations

import datetime as dt
import pathlib
from typing import Callable

from . import prayer
from .interlude import ClipError, Timer, fetch_clip

CLIP_URL = ("https://upload.wikimedia.org/wikipedia/commons/2/27/"
            "Call_to_prayer_by_Sabah_Fakhry.mp3")
CLIP_NAME = "adhan-sabah-fakhri.mp3"

# If the computer was asleep and wakes up shortly after a prayer time, the
# adhan is still called; much later than this it is skipped, not replayed.
GRACE = dt.timedelta(minutes=3)
# Longest single sleep, so clock changes and suspend are noticed quickly.
TICK = 20.0


class AdhanError(Exception):
    """No clip to play."""


def clip_path(timeout: float = 30.0) -> pathlib.Path:
    try:
        return fetch_clip(CLIP_URL, CLIP_NAME, "adhan_file", timeout)
    except ClipError as exc:
        raise AdhanError(str(exc)) from exc


def label(name: str) -> str:
    en, ar = prayer.NAMES[name]
    return f"الله أكبر  ·  {en} {ar}"


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


class Watch(Timer):
    """Plays the adhan at each prayer time until stopped."""

    name = "adhan"

    def __init__(self, spec, clip, loc: prayer.Location | None = None, on_event=None,
                 clock: Callable[[], dt.datetime] = _now) -> None:
        super().__init__(spec, clip, on_event)
        # None: follow the saved location, re-read every cycle so that
        # "raddu --set location=..." applies to a watcher already running
        self.loc = loc
        self.clock = clock
        # prayers before start-up are never called retroactively
        self.last = clock()

    def next_prayer(self) -> tuple[str, dt.datetime]:
        return prayer.upcoming(self.loc or prayer.location(), self.last)

    def _run(self) -> None:
        while not self._stop.is_set():
            name, when = self.next_prayer()
            now = self.clock()
            if now < when:
                wait = (when - now).total_seconds()
                if self._stop.wait(min(wait, TICK)):
                    return
                continue
            self.last = when
            if now - when <= GRACE:
                self.fire(label(name))

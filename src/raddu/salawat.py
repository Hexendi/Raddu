"""Salawat reminder: send blessings on the Prophet ﷺ every few minutes.

While a recitation plays, a background timer fires every N minutes. When
it does, the recitation is paused (the player process is stopped with
SIGSTOP, so nothing is skipped), a short Arabic clip is played, and the
recitation resumes exactly where it was.

The default clip is a real recording, not generated audio: Surah
Al-Ahzab 33:56 - "Indeed Allah and His angels send blessings upon the
Prophet. O you who believe, send blessings upon him and greet him with
peace" - recited by Mishary Rashid al-Afasy, served by everyayah.com. It
is fetched once and cached, so the reminder also works offline after the
first run. Any other clip (for example "اللهم صل وسلم على نبينا محمد"
in a voice you like) can be used instead with
``raddu --set salawat_file=PATH``.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import threading

from . import config, net
from .player import PlayerSpec

DEFAULT_MINUTES = 10.0
CLIP_URL = "https://everyayah.com/data/Alafasy_128kbps/033056.mp3"
CLIP_NAME = "alafasy-033056.mp3"
LABEL = "ﷺ  اللهم صل وسلم على نبينا محمد"

OFF_WORDS = ("off", "no", "none", "clear", "0", "false")

_CAN_PAUSE = hasattr(signal, "SIGSTOP") and hasattr(signal, "SIGCONT")


class SalawatError(Exception):
    """The reminder could not be set up (bad interval, no clip)."""


def parse_minutes(value: str | float | None) -> float | None:
    """``"10"`` -> 10.0; an off word -> None; anything else is an error."""
    if value is None:
        return None
    text = str(value).strip().lower().removesuffix("m").removesuffix("min")
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


def configured_minutes() -> float | None:
    """The interval saved with ``--set salawat=N``, if any."""
    try:
        return parse_minutes(config.load().get("salawat"))
    except SalawatError:
        return None


def cache_path() -> pathlib.Path:
    return config.data_dir() / "salawat" / CLIP_NAME


def clip_path(timeout: float = 30.0) -> pathlib.Path:
    """Return a playable clip, downloading the default one on first use."""
    custom = config.load().get("salawat_file")
    if custom:
        p = pathlib.Path(custom).expanduser()
        if not p.is_file():
            raise SalawatError(f"salawat_file not found: {p}")
        return p

    dest = cache_path()
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    try:
        with open(tmp, "wb") as fh:
            net.download(CLIP_URL, fh, timeout=timeout)
        tmp.replace(dest)
    except (net.NetworkError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        if isinstance(exc, net.NetworkError) and exc.offline:
            raise SalawatError(
                "the salawat clip is not downloaded yet and there is no "
                "internet connection"
            ) from exc
        raise SalawatError(f"could not fetch the salawat clip: {exc}") from exc
    return dest


def _signal(proc, sig) -> bool:
    if proc is None or proc.poll() is not None:
        return False
    try:
        os.kill(proc.pid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def pause(proc) -> bool:
    return _CAN_PAUSE and _signal(proc, signal.SIGSTOP)


def resume(proc) -> bool:
    return _CAN_PAUSE and _signal(proc, signal.SIGCONT)


class Reminder:
    """Background timer that interleaves the clip with the recitation."""

    def __init__(self, spec: PlayerSpec, clip: pathlib.Path, minutes: float) -> None:
        self.spec = spec
        self.clip = clip
        self.interval = minutes * 60.0
        self.session = None          # the player.Session currently playing
        self.viz = None              # its visualizer, to relabel during the clip
        self.played = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._clip_proc: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._run, name="salawat", daemon=True)

    def attach(self, session, viz=None) -> None:
        self.session, self.viz = session, viz

    def start(self) -> "Reminder":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            proc = self._clip_proc
        if proc is not None and proc.poll() is None:
            proc.kill()
        self._thread.join(timeout=3)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self.play_once()

    def play_once(self) -> None:
        session = self.session
        target = getattr(session, "proc", None)
        paused = pause(target)
        viz = self.viz
        title = getattr(viz, "title", None)
        if viz is not None:
            viz.title = LABEL
        try:
            self._play_clip()
            self.played += 1
        finally:
            if viz is not None and title is not None:
                viz.title = title
            if paused:
                resume(target)

    def _play_clip(self) -> None:
        if self.spec.stdin_ok:
            with open(self.clip, "rb") as fh:
                proc = subprocess.Popen([self.spec.binary, *self.spec.args],
                                        stdin=fh, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL)
        else:
            argv = [self.spec.binary,
                    *[a.format(file=str(self.clip)) for a in self.spec.args]]
            proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        with self._lock:
            self._clip_proc = proc
        try:
            while proc.poll() is None:
                if self._stop.wait(0.1):
                    proc.kill()
                    break
            proc.wait()
        finally:
            with self._lock:
                self._clip_proc = None

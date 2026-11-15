"""Shared machinery for the timed clips: the salawat reminder and the adhan.

Both are stand-alone modes, independent of the reciters: a background
thread waits until something is due, then plays a short clip through the
same audio player raddu uses for recitations. A module-wide lock makes
sure two clips never play over each other (an adhan that falls due while
a salawat clip is playing waits for it to finish).

Clips are fetched once and cached in the data dir, so after the first run
everything here works without a connection.
"""

from __future__ import annotations

import pathlib
import subprocess
import threading
from typing import Callable

from . import config, net
from .player import PlayerSpec

OFF_WORDS = ("off", "no", "none", "clear", "0", "false")

# Only one clip plays at a time, whichever timer it comes from.
_AUDIO = threading.Lock()


class ClipError(Exception):
    """A clip could not be found or fetched."""


def fetch_clip(url: str, cache_name: str, custom_key: str,
               timeout: float = 30.0) -> pathlib.Path:
    """Return a playable clip: the user's own file if configured under
    ``custom_key``, otherwise ``url`` downloaded once into the cache."""
    custom = config.load().get(custom_key)
    if custom:
        p = pathlib.Path(custom).expanduser()
        if not p.is_file():
            raise ClipError(f"{custom_key} not found: {p}")
        return p

    dest = config.data_dir() / "clips" / cache_name
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    try:
        with open(tmp, "wb") as fh:
            net.download(url, fh, timeout=timeout)
        tmp.replace(dest)
    except (net.NetworkError, OSError) as exc:
        tmp.unlink(missing_ok=True)
        if isinstance(exc, net.NetworkError) and exc.offline:
            raise ClipError(
                f"{cache_name} is not downloaded yet and there is no "
                "internet connection"
            ) from exc
        raise ClipError(f"could not fetch {cache_name}: {exc}") from exc
    return dest


class Timer:
    """A background thread that plays ``clip`` whenever ``_run`` says so.

    Subclasses implement ``_run`` and call ``self.fire(label)`` when a clip
    is due. ``on_event(label, playing)`` is called around each clip so the
    caller can show what is happening.
    """

    name = "timer"

    def __init__(self, spec: PlayerSpec, clip: pathlib.Path,
                 on_event: Callable[[str, bool], None] | None = None) -> None:
        self.spec = spec
        self.clip = clip
        self.on_event = on_event
        self.played = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._clip_proc: subprocess.Popen | None = None
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)

    def start(self) -> "Timer":
        self._thread.start()
        return self

    def stop(self) -> None:
        # set under the lock, so a clip being started right now either sees
        # the flag or is registered in time for us to kill it
        with self._lock:
            self._stop.set()
            proc = self._clip_proc
        if proc is not None and proc.poll() is None:
            proc.kill()
        self._thread.join(timeout=3)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _run(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def fire(self, label: str) -> None:
        with _AUDIO:
            if self._stop.is_set():
                return
            if self.on_event:
                self.on_event(label, True)
            try:
                self._play_clip()
                self.played += 1
            finally:
                if self.on_event:
                    self.on_event(label, False)

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
            if self._stop.is_set():
                proc.kill()
        try:
            while proc.poll() is None:
                if self._stop.wait(0.1):
                    proc.kill()
                    break
            proc.wait()
        finally:
            with self._lock:
                self._clip_proc = None

"""Audio player detection and playback.

raddu does not implement decoding: it pipes the MP3 into whatever real
player you already have installed (mpv, ffplay, mpg123, vlc, ...).  Players
that cannot read from a pipe get a temporary file instead.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from . import net


@dataclass(frozen=True)
class PlayerSpec:
    name: str
    binary: str
    args: tuple[str, ...]
    stdin_ok: bool = True
    note: str = ""


# Ordered by preference: best gapless/CPU-light behaviour first.
KNOWN_PLAYERS: tuple[PlayerSpec, ...] = (
    PlayerSpec("mpv", "mpv", ("--no-cache", "--no-terminal", "--really-quiet", "-"),
               note="lightweight, gapless"),
    PlayerSpec("ffplay", "ffplay", ("-nodisp", "-autoexit", "-loglevel", "error", "-i", "pipe:0"),
               note="ships with ffmpeg"),
    PlayerSpec("mpg123", "mpg123", ("-q", "-"), note="minimal mp3 player"),
    PlayerSpec("cvlc", "cvlc", ("--play-and-exit", "--intf", "dummy", "-"),
               note="vlc cli"),
    PlayerSpec("vlc", "vlc", ("--play-and-exit", "--intf", "dummy", "-"),
               note="vlc"),
    PlayerSpec("mplayer", "mplayer", ("-really-quiet", "-"), note="mplayer"),
    PlayerSpec("afplay", "afplay", ("{file}",), stdin_ok=False, note="macOS"),
    PlayerSpec("paplay", "paplay", ("{file}",), stdin_ok=False, note="PulseAudio"),
    PlayerSpec("mpg321", "mpg321", ("-q", "-"), note="minimal mp3 player"),
)


class PlayerError(Exception):
    """No usable player, or the player failed."""


def detect(preferred: str | None = None) -> PlayerSpec:
    """Return a usable player spec, honouring an explicit preference."""
    if preferred:
        key = preferred.strip().lower()
        for p in KNOWN_PLAYERS:
            if p.name == key:
                if not shutil.which(p.binary):
                    raise PlayerError(
                        f"player {p.name!r} selected but {p.binary!r} is not on PATH."
                    )
                return p
        raise PlayerError(
            f"unknown player {preferred!r}. available: "
            + ", ".join(p.name for p in KNOWN_PLAYERS)
        )
    for p in KNOWN_PLAYERS:
        if shutil.which(p.binary):
            return p
    raise PlayerError(
        "no audio player found on PATH.\n"
        "       install one of: " + ", ".join(p.name for p in KNOWN_PLAYERS) + "\n"
        "       for example:  sudo apt install mpv     (recommended)\n"
        "                     sudo apt install mpg123  (lightest)"
    )


def available() -> list[tuple[PlayerSpec, str | None]]:
    out = []
    for p in KNOWN_PLAYERS:
        path = shutil.which(p.binary)
        out.append((p, path))
    return out


class Session:
    """One running player process, fed from the network."""

    def __init__(self, spec: PlayerSpec, on_total=None, on_chunk=None) -> None:
        self.spec = spec
        self._on_total = on_total
        self._on_chunk = on_chunk
        self._tmp: tempfile.TemporaryDirectory | None = None
        self._stdin = None
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _drop_stdin(self) -> None:
        """Close the player's input pipe without flushing it.

        ``BufferedWriter.close()`` flushes first, which blocks indefinitely
        once the player has stopped reading - exactly what happens on
        Ctrl-C.  ``detach()`` drops the buffer and hands back the raw stream,
        which closes the fd straight away.

        The handle is claimed *before* any work.  A signal can land in the
        middle of this function, and the resulting KeyboardInterrupt unwinds
        into ``close()``, which would otherwise call in here a second time
        and close an already-closed fd - the object then fails to finalise
        with EBADF on the way out.
        """
        fh, self._stdin = self._stdin, None
        if fh is None:
            return
        try:
            raw = fh.detach()
        except (OSError, ValueError, AttributeError):
            return
        try:
            raw.close()
        except BaseException:
            try:                      # interrupted: the fd still has to go
                raw.close()
            except Exception:
                pass
            raise

    def play(self, url: str, *, timeout: float = 30.0) -> int:
        if self.spec.stdin_ok:
            self.proc = subprocess.Popen(
                [self.spec.binary, *self.spec.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._stdin = self.proc.stdin
            try:
                written = net.stream(
                    url, self._stdin, timeout=timeout,
                    on_total=self._on_total, on_chunk=self._on_chunk,
                )
            except BrokenPipeError:
                # player exited early (user pressed q) - that is a clean stop
                return 0
            finally:
                self._drop_stdin()
            rc = self.proc.wait()
            self.proc = None
            if rc not in (0, None):
                raise PlayerError(f"{self.spec.binary} exited with status {rc}.")
            return written

        # players that need a real file
        self._tmp = tempfile.TemporaryDirectory(prefix="raddu-")
        dest = pathlib.Path(self._tmp.name) / "surah.mp3"
        with open(dest, "wb") as fh:
            written = net.stream(
                url, fh, timeout=timeout,
                on_total=self._on_total, on_chunk=self._on_chunk,
            )
        argv = [self.spec.binary, *[a.format(file=str(dest)) for a in self.spec.args]]
        self.proc = subprocess.run(argv, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        return written

    def stop(self) -> None:
        self._drop_stdin()
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
        self.proc = None

    def close(self) -> None:
        self.stop()
        if self._tmp:
            self._tmp.cleanup()
            self._tmp = None


def has_audio_device() -> bool:
    """Heuristic check so we can fail clearly on headless boxes."""
    if sys.platform.startswith("linux"):
        if os.environ.get("PULSE_SERVER") or os.environ.get("PIPEWIRE_RUNTIME"):
            return True
        return bool(shutil.which("pactl") or pathlib.Path("/dev/snd").exists())
    return True
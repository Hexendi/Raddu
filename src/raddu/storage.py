"""Offline storage management for raddu voices."""

from __future__ import annotations

import os
import pathlib
import shutil
from typing import TYPE_CHECKING

from . import config

if TYPE_CHECKING:
    from .catalog import Track


def data_dir() -> pathlib.Path:
    base = os.environ.get("XDG_DATA_HOME") or pathlib.Path.home() / ".local" / "share"
    return pathlib.Path(base) / config.APP


def offline_dir() -> pathlib.Path:
    custom = config.load().get("storage") or config.load().get("offline_dir")
    if custom:
        return pathlib.Path(custom).expanduser()
    return data_dir() / "voices"


def track_path(track: Track) -> pathlib.Path:
    """Return the canonical offline file path for a track."""
    return offline_dir() / track.reciter.code / track.style.code / f"{track.surah.filename}.mp3"


def get_offline_file(track: Track) -> pathlib.Path | None:
    """Return existing local path if downloaded and non-empty, or None."""
    primary = track_path(track)
    try:
        if primary.is_file() and primary.stat().st_size > 0:
            return primary
        # Backwards compatibility / alternate flat filename
        alt = offline_dir() / track.reciter.code / track.style.code / track.filename
        if alt.is_file() and alt.stat().st_size > 0:
            return alt
    except OSError:
        pass
    return None


def is_offline(track: Track) -> bool:
    return get_offline_file(track) is not None


def list_installed() -> list[dict]:
    """Scan offline storage and report installed reciters and surahs."""
    base = offline_dir()
    if not base.is_dir():
        return []
    results = []
    try:
        reciter_dirs = sorted(p for p in base.iterdir() if p.is_dir())
    except OSError:
        return []

    for rdir in reciter_dirs:
        rec_code = rdir.name
        try:
            style_dirs = sorted(p for p in rdir.iterdir() if p.is_dir())
        except OSError:
            continue
        for sdir in style_dirs:
            style_code = sdir.name
            try:
                files = [f for f in sdir.glob("*.mp3") if f.is_file() and f.stat().st_size > 0]
            except OSError:
                continue
            if not files:
                continue
            total_size = sum(f.stat().st_size for f in files)
            surah_numbers = set()
            for f in files:
                num_str = f.stem.split("-")[0]
                if num_str.isdigit():
                    surah_numbers.add(int(num_str))
            results.append({
                "reciter_code": rec_code,
                "style_code": style_code,
                "count": len(files),
                "surahs": sorted(surah_numbers),
                "total_bytes": total_size,
                "path": sdir,
            })
    return results


def voice_status(reciter_code: str, style_code: str | None = None) -> tuple[int, int]:
    """Return (downloaded_count, total_bytes) for a specific reciter / style."""
    base = offline_dir() / reciter_code
    if not base.is_dir():
        return 0, 0
    total_files = 0
    total_bytes = 0
    styles = [base / style_code] if style_code else [p for p in base.iterdir() if p.is_dir()]
    for sdir in styles:
        if not sdir.is_dir():
            continue
        try:
            files = [f for f in sdir.glob("*.mp3") if f.is_file() and f.stat().st_size > 0]
            total_files += len(files)
            total_bytes += sum(f.stat().st_size for f in files)
        except OSError:
            pass
    return total_files, total_bytes


def remove_voice(reciter_code: str, style_code: str | None = None) -> tuple[int, int]:
    """Delete offline files for a reciter. Return (deleted_count, freed_bytes)."""
    base = offline_dir() / reciter_code
    if not base.is_dir():
        return 0, 0
    if style_code:
        target = base / style_code
        if not target.is_dir():
            return 0, 0
        files = [f for f in target.glob("*.mp3") if f.is_file()]
        count = len(files)
        size = sum(f.stat().st_size for f in files)
        shutil.rmtree(target, ignore_errors=True)
        try:
            if not any(base.iterdir()):
                base.rmdir()
        except OSError:
            pass
        return count, size
    else:
        files = list(base.glob("**/*.mp3"))
        count = len(files)
        size = sum(f.stat().st_size for f in files)
        shutil.rmtree(base, ignore_errors=True)
        return count, size

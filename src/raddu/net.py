"""HTTP streaming and probing for surah audio.

The CDN serves one MP3 per surah, so we stream it straight into the audio
player's stdin.  No temporary file, no copy of the Quran on disk.
"""

from __future__ import annotations

import importlib.util
import urllib.error
import urllib.request
from typing import Callable, Protocol

USER_AGENT = "raddu/1.0 (+https://github.com/; terminal quran player)"

CHUNK = 64 * 1024

# urllib only speaks http/https proxies.  A socks5://*_PROXY in the
# environment would otherwise fail with a confusing tunnel error, so we fall
# back to a direct connection and tell the user about it once.
_HAS_SOCKS = importlib.util.find_spec("socks") is not None
_BYPASSED_SOCKS = False


def proxy_note() -> str | None:
    return ("bypassed socks5 proxy from the environment "
            "(urllib cannot use it; install PySocks to use one)") if _BYPASSED_SOCKS else None


def _opener():
    global _BYPASSED_SOCKS
    proxies = urllib.request.getproxies()
    socks = [v for v in proxies.values() if v.lower().startswith("socks")]
    if socks and not _HAS_SOCKS:
        _BYPASSED_SOCKS = True
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


class AudioSink(Protocol):
    def write(self, data: bytes) -> int: ...
    def flush(self) -> None: ...


class NetworkError(Exception):
    """User-facing network failure with a readable message."""

    def __init__(self, message: str, *, offline: bool = False) -> None:
        super().__init__(message)
        self.offline = offline


def _open(url: str, timeout: float, *, probe: bool = False):
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if probe:
        headers["Range"] = "bytes=0-1"
    req = urllib.request.Request(url, headers=headers)
    try:
        return _opener().open(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise NetworkError(
                "access denied by the audio server (HTTP 403): "
                "this recording is not publicly streamable; "
                "run 'raddu --check' to see which voices are reachable."
            ) from exc
        if exc.code == 404:
            raise NetworkError(
                "audio not found on the server (HTTP 404): "
                "the file may be missing upstream - try another style "
                "with --style."
            ) from exc
        raise NetworkError(f"audio server error (HTTP {exc.code})") from exc
    except urllib.error.URLError as exc:
        # Any failure to reach the server (DNS, refused, unreachable, ...)
        # is reported as a plain offline message. The raw URL and the
        # low-level errno ("Temporary failure in name resolution", ...)
        # are deliberately hidden: the user only needs to know they are
        # offline.
        raise NetworkError("no internet connection", offline=True) from exc
    except (TimeoutError, OSError) as exc:
        raise NetworkError("no internet connection", offline=True) from exc


def probe(url: str, timeout: float = 20.0) -> int:
    """Return the byte size the CDN reports, without downloading the body."""
    resp = _open(url, timeout, probe=True)
    try:
        cr = resp.headers.get("Content-Range", "")
        if "/" in cr and not cr.endswith("/?"):
            tail = cr.rsplit("/", 1)[1]
            if tail.isdigit():
                return int(tail)
        cl = resp.headers.get("Content-Length")
        return int(cl) if cl and cl.isdigit() else 0
    finally:
        resp.close()


def stream(
    url: str,
    sink: AudioSink,
    *,
    timeout: float = 30.0,
    on_total: Callable[[int], None] | None = None,
    on_chunk: Callable[[int], None] | None = None,
    on_close: Callable[[], None] | None = None,
) -> int:
    """Stream ``url`` into ``sink``; return the number of bytes written."""
    resp = _open(url, timeout)
    written = 0
    try:
        total = int(resp.headers.get("Content-Length") or 0)
        if on_total:
            on_total(total)
        while True:
            try:
                chunk = resp.read(CHUNK)
            except (TimeoutError, OSError) as exc:
                raise NetworkError(
                    "connection lost while streaming", offline=True
                ) from exc
            if not chunk:
                break
            sink.write(chunk)
            written += len(chunk)
            if on_chunk:
                on_chunk(len(chunk))
        try:
            sink.flush()
        except (BrokenPipeError, ValueError):
            pass
    finally:
        resp.close()
        if on_close:
            on_close()
    return written


def download(url: str, dest, *, timeout: float = 60.0,
             on_total: Callable[[int], None] | None = None,
             on_chunk: Callable[[int], None] | None = None) -> int:
    """Stream ``url`` to an open binary file object at ``dest``."""
    return stream(url, dest, timeout=timeout, on_total=on_total, on_chunk=on_chunk)
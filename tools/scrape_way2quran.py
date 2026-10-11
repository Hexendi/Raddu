#!/usr/bin/env python3
"""Scrape Way2Quran reciter pages into tools/cache/.

Way2Quran is the only source found so far that tags reciters with a
nationality, which is what makes it possible to add genuinely Mauritanian
voices and say so without guessing from a name.

It also exposes something mp3quran.net does not: a stable, documented
filename per surah. Their numbering is inconsistently zero-padded
(001.mp3 exists but 002.mp3 does not; 2.mp3 exists but 1.mp3 does not),
so the exact filename is scraped rather than constructed.

Usage:
    python3 tools/scrape_way2quran.py                 # from RECITERS below
    python3 tools/scrape_way2quran.py <slug> [...]    # extra slugs
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "tools" / "cache"
UA = "raddu-catalog-sync/1.0 (+https://example.invalid)"

BASE = "https://way2quran.com/en/reciters"
MEDIA = "https://media.way2quran.com"

# Slugs of the reciters listed under nationality=MR (Mauritanian).
MAURITANIAN = [
    "mohamed-salem-ould-zine",
    "mohamed-laghzaf-weld-mohamed-sidi",
    "al-nagy-wild-bilal",
    "sheikhna-ould-al-hajj",
    "mohamed-ould-el-salik",
    "alragel-ould-othman",
    "al-hassan-wld-hbyb-al-lh",
    "muhammad-al-rady-wld-amlyh",
    "mohammed-yahya-al-shanqeeti",
]

LINK = re.compile(r"https://media\.way2quran\.com/([^/\"']+)/([^/\"']+)/([0-9]+)\.mp3")


def fetch(url: str, timeout: float = 60.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def scrape(slug: str) -> list[dict]:
    """Return one record per (reciter, riwāyah) found on the page."""
    html = fetch(f"{BASE}/{slug}")
    found: dict[tuple[str, str], dict[int, str]] = {}
    for rec, riw, num in LINK.findall(html):
        rec_map = found.setdefault((rec, riw), {})
        rec_map.setdefault(int(num), num)   # surah number -> exact filename

    nationality = re.search(r"nationality=([A-Z]{2})", html)
    out = []
    for (rec, riw), files in sorted(found.items()):
        surahs = sorted(files)
        out.append({
            "slug": slug,
            "server": f"{MEDIA}/{rec}/{riw}/",
            "riwaya_slug": riw,
            "nationality": nationality.group(1) if nationality else None,
            "surah_count": len(surahs),
            "missing": sorted(set(range(1, 115)) - set(surahs)),
            # exact filename per surah; zero-padding is not uniform upstream
            "files": {str(n): f"{fn}.mp3" for n, fn in files.items()},
        })
    return out


def main() -> int:
    slugs = sys.argv[1:] or MAURITANIAN
    CACHE.mkdir(parents=True, exist_ok=True)
    summary = []
    for slug in slugs:
        try:
            for rec in scrape(slug):
                dest = CACHE / f"way2quran_{rec['slug']}_{rec['riwaya_slug']}.json"
                dest.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n")
                summary.append(rec)
                print(f"  {rec['slug']:<38} {rec['riwaya_slug']:<16} "
                      f"{rec['nationality'] or '--'}  {rec['surah_count']:>3}/114  {dest.name}")
        except Exception as exc:                      # noqa: BLE001
            print(f"  {slug:<38} FAILED: {exc}")
    print(f"\n{len(summary)} recitation(s) cached")
    return 0


if __name__ == "__main__":
    sys.exit(main())
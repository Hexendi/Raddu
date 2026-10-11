#!/usr/bin/env python3
"""Regenerate src/raddu/data/{surahs,reciters}.json from the cached upstream dumps.

Upstream sources (downloaded into tools/cache/ by --refresh):
  * https://api.alquran.cloud/v1/surah          -> surah names, ayah counts, revelation type
  * https://mp3quran.net/api/v3/suwar            -> Arabic surah names
  * https://mp3quran.net/api/v3/reciters?language=ar
                                                 -> reciter + moshaf (riwaya) CDN servers

Run:  python3 tools/sync_catalog.py
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CACHE = ROOT / "tools" / "cache"
OUT = ROOT / "src" / "raddu" / "data"

UA = "raddu-catalog-sync/1.0 (+https://example.invalid)"

SOURCES = {
    "alqurancloud_surah.json": "https://api.alquran.cloud/v1/surah",
    "mp3quran_suwar.json": "https://mp3quran.net/api/v3/suwar",
    "mp3quran_reciters_ar.json": "https://mp3quran.net/api/v3/reciters?language=ar",
}

# --------------------------------------------------------------------------
# Curated reciter selection.
#
# Each entry:  code -> {reciter_id, default moshaf_id, styles{name: moshaf_id}}
# The list is deliberately capped at 10 well-known reciters; every moshaf id
# below was resolved against the live mp3quran API dump.
# --------------------------------------------------------------------------
RECITERS: dict[str, dict] = {
    "sud": {
        "reciter_id": 54,
        "default": 54,
        "styles": {54: "hafs"},
        "name_en": "Abdur-Rahman as-Sudais",
        "name_ar": "عبدالرحمن السديس",
        "codes": ["sud", "sudais", "sudais84", "as-sudais", "abdulrahman", "abdulrahman-sudais"],
    },
    "hus": {
        "reciter_id": 118,
        "default": 118,
        "styles": {118: "hafs", 119: "mujawwad", 270: "qalun", 120: "warsh", 269: "doori"},
        "name_en": "Mahmoud Khalil al-Husary",
        "name_ar": "محمود خليل الحصري",
        "codes": ["hus", "husary", "alhussary", "al-husary", "hussary", "husarymujawwad", "husm"],
    },
    "abs": {
        "reciter_id": 51,
        "default": 51,
        "styles": {51: "mujawwad", 53: "hafs", 52: "warsh"},
        "name_en": "Abdul Basit Abdus-Samad",
        "name_ar": "عبدالباسط عبدالصمد",
        "codes": ["abs", "abdulbasit", "basit", "abdulbasitabdusamad", "abdulbasit-abdulsamad", "absm"],
    },
    "shr": {
        "reciter_id": 31,
        "default": 31,
        "styles": {31: "hafs"},
        "name_en": "Saud ash-Shuraim",
        "name_ar": "سعود الشريم",
        "codes": ["shr", "shuraim", "shuraym", "saud", "ash-shuraim", "shuraimhafs"],
    },
    "aft": {
        "reciter_id": 123,
        "default": 123,
        "styles": {123: "hafs", 124: "kisaai"},
        "name_en": "Mishary Rashid al-Afasy",
        "name_ar": "مشاري العفاسي",
        "codes": ["aft", "afasy", "alafasy", "mishary", "alafasyhafs", "alafasy128"],
    },
    "sha": {
        "reciter_id": 4,
        "default": 4,
        "styles": {4: "hafs"},
        "name_en": "Abu Bakr ash-Shatri",
        "name_ar": "أبو بكر الشاطري",
        "codes": ["sha", "shatri", "shatree", "abubakr", "abubakr-shatri", "abubakralshatri"],
    },
    "min": {
        "reciter_id": 112,
        "default": 113,
        "styles": {113: "mujawwad", 112: "hafs", 10924: "archive"},
        "name_en": "Muhammad Siddiq al-Minshawi",
        "name_ar": "محمد صديق المنشاوي",
        "codes": ["min", "mins", "minshawi", "alminshawi", "minshawi-mujawwad", "minm"],
    },
    "mui": {
        "reciter_id": 102,
        "default": 133,
        "styles": {133: "mujawwad", 102: "hafs"},
        "name_en": "Maher al-Muaiqly",
        "name_ar": "ماهر المعيقلي",
        "codes": ["mui", "muaiqly", "maher", "mahermuaiqly", "muaiqly-mujawwad", "muim"],
    },
    "ajm": {
        "reciter_id": 5,
        "default": 5,
        "styles": {5: "hafs"},
        "name_en": "Ahmad ibn Ali al-Ajmi",
        "name_ar": "أحمد بن علي العجمي",
        "codes": ["ajm", "ajmi", "alajmi", "ahmad-ajmi", "ahmadajmi"],
    },
    "hud": {
        "reciter_id": 74,
        "default": 74,
        "styles": {74: "hafs", 75: "qalun", 305: "shubah"},
        "name_en": "Ali Abdur-Rahman al-Hudhaify",
        "name_ar": "علي بن عبدالرحمن الحذيفي",
        "codes": ["hud", "hudhaify", "alhudhaify", "ali-hudhaify", "huthaify"],
    },
}

# Style-code -> human label (Arabic + English).
STYLE_LABEL = {
    "hafs": ("حفص عن عاصم", "Hafs an Asim"),
    "warsh": ("ورش عن نافع", "Warsh an Nafi"),
    "qalun": ("قالون عن نافع", "Qalun an Nafi"),
    "doori": ("الدوري عن أبي عمرو", "ad-Doori from Abu Amr"),
    "shubah": ("شعبة عن عاصم", "Shu'bah from Asim"),
    "kisaai": ("الدوري عن الكسائي", "ad-Doori from al-Kisa'i"),
    "mujawwad": ("مجوّد", "Mujawwad"),
    "archive": ("تسجيل قديم", "Archival recording"),
}

# Extra aliases for surahs where the upstream English romanisation is awkward.
SURAH_ALIASES: dict[int, list[str]] = {
    1: ["fatiha", "fatihah", "opening", "alfatiha"],
    2: ["baqara", "baqarah", "cow", "albaqara"],
    3: ["imraan", "imran", "al-imran", "alimran", "familyofimran", "aalimran"],
    4: ["nisaa", "nisa", "women", "alnisaa"],
    5: ["maaida", "maidah", "table", "almaaida"],
    6: ["anam", "an6am", "cattle", "alanam", "anaaam"],
    7: ["araf", "araaf", "heights", "aeraaf"],
    8: ["anfaal", "anfal", "spoils", "alanfaal"],
    9: ["tawba", "tawbah", "repentance", "attooba"],
    10: ["yunus", "younus", "jonah", "atyunus"],
    11: ["hud", "alhud", "al-hud", "hudhud"],
    12: ["yusuf", "yousef", "joseph", "ayyusuf"],
    13: ["raad", "rad", "thunder", "arrad"],
    14: ["ibrahim", "ibraheem", "abraham", "ibrahim14"],
    15: ["hijr", "al-hijr", "rock", "the-rock"],
    16: ["nahl", "bee", "the-bee"],
    17: ["israa", "isra", "night-journey", "alisra"],
    18: ["kahf", "cave", "the-cave", "alkahf"],
    19: ["maryam", "mary", "maryam19"],
    20: ["taha", "ta-ha", "taaha"],
    21: ["anbiyaa", "anbya", "prophets", "alanbiyaa"],
    22: ["hajj", "pilgrimage", "the-pilgrimage"],
    23: ["muminun", "mominun", "believers", "almuminun"],
    24: ["noor", "nur", "light", "an-noor"],
    25: ["furqan", "forqan", "criterion"],
    26: ["shuaraa", "shuara", "poets"],
    27: ["naml", "ant", "the-ant"],
    28: ["qasas", "stories", "alqasas"],
    29: ["ankaboot", "spider", "the-spider"],
    30: ["room", "rum", "romans", "arroom"],
    31: ["luqman", "lokman"],
    32: ["sajda", "sajdah", "prostration"],
    33: ["ahzaab", "ahzab", "clans"],
    34: ["saba", "sheba", "seba"],
    35: ["faatir", "fatir", "originator"],
    36: ["yaseen", "yasin", "yaseen36", "ya-sin"],
    37: ["saaffaat", "saaffat", "safafat"],
    38: ["saad", "sad", "the-letter-sad"],
    39: ["zumar", "zumarr", "groups"],
    40: ["ghafir", "ghafirr", "forgiver"],
    41: ["fussilat", "fussilat41", "explained"],
    42: ["shura", "shoora", "consultation"],
    43: ["zukhruf", "zukhruf43", "ornaments"],
    44: ["dukhaan", "dukhan", "smoke"],
    45: ["jaathiya", "jaatia", "crouching"],
    46: ["ahqaf", "ahqaaf", "dunes"],
    47: ["muhammad", "muhammad47", "prophet"],
    48: ["fath", "fat-h", "victory"],
    49: ["hujuraat", "hujurat", "apartments"],
    50: ["qaaf", "qaf", "the-letter-qaf"],
    51: ["dhaariyat", "dhariyat", "winnowing"],
    52: ["tur", "at-tur", "the-mount"],
    53: ["najm", "star", "the-star"],
    54: ["qamar", "moon", "the-moon"],
    55: ["rahmaan", "rahman", "beneficent"],
    56: ["waaqia", "waqiya", "inevitable"],
    57: ["hadid", "iron", "the-iron"],
    58: ["mujaadila", "mujadila", "pleading-woman"],
    59: ["hashr", "exile", "the-exile"],
    60: ["mumtahana", "mumtahanah", "examined"],
    61: ["saff", "saaff", "ranks"],
    62: ["jumua", "jumuah", "friday", "aljumua"],
    63: ["munaafiqoon", "munafiqoon", "hypocrites"],
    64: ["taghaabun", "taghabun", "mutual-disillusion"],
    65: ["talaaq", "talaq", "divorce"],
    66: ["tahrim", "the-prohibition"],
    67: ["mulk", "sovereignty", "almulk"],
    68: ["qalam", "pen", "the-pen"],
    69: ["haqqa", "haqqah", "reality"],
    70: ["maarij", "maarij70", "ascending-stairways"],
    71: ["nuh", "noah", "nooh"],
    72: ["jinn", "jinn72"],
    73: ["muzzammil", "muzzammel", "enshrouded"],
    74: ["muddaththir", "muddathir", "cloaked"],
    75: ["qiyaama", "qiyamah", "resurrection"],
    76: ["insaan", "insan", "man"],
    77: ["mursalaat", "mursalat", "emissaries"],
    78: ["naba", "nabaa", "announcement"],
    79: ["naaziaat", "naziat", "drag-forth"],
    80: ["abasa", "abasa80"],
    81: ["takwir", "at-takwir", "overthrowing"],
    82: ["infitaar", "infitar"],
    83: ["mutaffifin", "mutaffifin83"],
    84: ["inshiqaaq", "inshiqaq"],
    85: ["burooj", "buruj", "constellations"],
    86: ["taariq", "tariq", "morning-star"],
    87: ["alaa", "ala", "most-high"],
    88: ["ghaashiya", "ghashiyah", "overwhelming"],
    89: ["fajr", "fajr89", "dawn"],
    90: ["balad", "balad90", "city"],
    91: ["shams", "sun", "ash-shams"],
    92: ["lail", "layl", "night", "allail"],
    93: ["duhaa", "duha", "morning-hours"],
    94: ["sharh", "inshirah", "consolation"],
    95: ["tin", "at-tin", "fig"],
    96: ["alaq", "alaq96", "clot"],
    97: ["qadr", "qadar", "decree", "power"],
    98: ["bayyina", "bayyinah", "evidence"],
    99: ["zalzala", "zalzalah", "earthquake"],
    100: ["aadiyaat", "adiyat", "chargers"],
    101: ["qaaria", "qaari", "calamity"],
    102: ["takaathur", "takaathur102", "competition"],
    103: ["asr", "asr103", "time", "declining-day"],
    104: ["humaza", "humazah", "traducer"],
    105: ["fil", "feel", "elephant"],
    106: ["quraish", "quraysh"],
    107: ["maun", "almaaun", "almsaun", "almsaadq", "almsadqah"],
    108: ["kawthar", "kawthar108", "abundance"],
    109: ["kaafiroon", "kafirun", "disbelievers"],
    110: ["nasr", "an-nasr", "divine-support"],
    111: ["masad", "palm-fibre", "almasaad"],
    112: ["ikhlaas", "ikhlas", "sincerity", "almukhlis"],
    113: ["falaq", "alfalaq", "alfalaq113"],
    114: ["naas", "nas", "mankind", "annas", "an-naas"],
}

# Leading Arabic articles stripped when deriving the canonical slug.
ARTICLES = ("aal-i-", "al-", "as-", "an-", "at-", "ar-", "ad-", "ash-", "ath-", "az-", "adh-", "a-")

# Where upstream romanisation yields an ugly canonical slug, override it.
# The original slug is always kept as an alias.
SLUG_OVERRIDES: dict[int, str] = {
    6: "anam",
    13: "raad",
    20: "taha",
    23: "muminun",
    25: "furqan",
    69: "haqqa",
    70: "maarij",
    107: "maun",
}


def slugify(text: str) -> str:
    s = text.lower().replace("'", "").replace("`", "")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    for art in ARTICLES:
        if s.startswith(art) and len(s) > len(art):
            s = s[len(art):]
            break
    return s


def refresh() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    for fname, url in SOURCES.items():
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            (CACHE / fname).write_bytes(resp.read())
        print(f"refreshed {fname}")


def load(name: str):
    return json.loads((CACHE / name).read_text())


def build_surahs() -> list[dict]:
    meta = {s["number"]: s for s in load("alqurancloud_surah.json")["data"]}
    ar = {s["id"]: s["name"] for s in load("mp3quran_suwar.json")["suwar"]}
    noise = re.compile(r"^[a-z]+[0-9]+$")  # "baqara2", "yaseen36", ... are not real codes
    out = []
    for n in range(1, 115):
        m = meta[n]
        derived = slugify(m["englishName"])
        slug = SLUG_OVERRIDES.get(n, derived)
        aliases = {derived, slug, m["englishName"].lower().replace("'", "").replace(" ", "-")}
        aliases.update(SURAH_ALIASES.get(n, []))
        aliases = {a for a in aliases if a and not noise.match(a)}
        out.append({
            "number": n,
            "slug": slug,
            "name_ar": ar.get(n, m["name"]),
            "name_en": m["englishName"],
            "meaning_en": m["englishNameTranslation"],
            "ayahs": m["numberOfAyahs"],
            "revelation": m["revelationType"].lower(),  # "meccan" | "medinan"
            "aliases": sorted(aliases),
        })
    return out


def build_reciters() -> list[dict]:
    raw = {r["id"]: r for r in load("mp3quran_reciters_ar.json")["reciters"]}
    out = []
    for code, spec in RECITERS.items():
        r = raw[spec["reciter_id"]]
        mos = {m["id"]: m for m in r["moshaf"]}
        styles = []
        for mos_id, label in sorted(spec["styles"].items(), key=lambda kv: kv[0] != spec["default"]):
            m = mos[mos_id]
            have = {int(x) for x in m["surah_list"].split(",")}
            styles.append({
                "code": label,
                "moshaf_id": mos_id,
                "server": m["server"],
                "label_ar": STYLE_LABEL[label][0],
                "label_en": STYLE_LABEL[label][1],
                "surah_count": len(have),
                "missing": sorted(set(range(1, 115)) - have),
            })
        default = next(s for s in styles if s["moshaf_id"] == spec["default"])
        out.append({
            "code": code,
            "name_ar": spec["name_ar"],
            "name_en": spec["name_en"],
            "default_style": default["code"],
            "default_label_en": default["label_en"],
            "codes": sorted({code, *spec["codes"]}),
            "styles": styles,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refresh", action="store_true", help="re-download the upstream dumps first")
    args = ap.parse_args()
    if args.refresh:
        refresh()

    OUT.mkdir(parents=True, exist_ok=True)
    surahs, reciters = build_surahs(), build_reciters()
    (OUT / "surahs.json").write_text(json.dumps(surahs, ensure_ascii=False, indent=1) + "\n")
    (OUT / "reciters.json").write_text(json.dumps(reciters, ensure_ascii=False, indent=1) + "\n")

    print(f"surahs.json   {len(surahs)} surahs, "
          f"{sum(len(s['aliases']) for s in surahs)} aliases")
    print(f"reciters.json {len(reciters)} reciters, "
          f"{sum(len(r['styles']) for r in reciters)} styles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
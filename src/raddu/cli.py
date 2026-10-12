"""Command line interface for raddu."""

from __future__ import annotations

import argparse
import difflib
import json
import pathlib
import random
import signal
import sys

from . import __version__, config, net, player as player_mod, storage, ui
from .catalog import CatalogError, catalog, Reciter, Surah

PROG = "raddu"

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 130


def install_signal_handlers() -> None:
    """Make Ctrl-C (and SIGTERM) interrupt a blocked pipe write.

    Python's *default* SIGINT handler does not interrupt a blocking write to a
    full pipe, so a long surah could not be stopped once the player fell
    behind.  An explicit handler restores prompt interruption.
    """
    def handler(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handler)
        except (ValueError, OSError, AttributeError):
            pass

# argparse would read "-sud" as "-s -u -d", so we classify argv ourselves first
# and hand argparse only tokens it cannot misread.
FLAGS = frozenset({
    "-h", "--help", "-V", "--version",
    "-s", "--list-surahs", "-R", "--list-reciters",
    "--list-players", "--url", "--json", "--no-banner", "--config",
    "--offline", "--offline-list",
})
VALUE_OPTS = frozenset({
    "-n", "--count", "--surah", "--reciter", "--style", "-f", "--find",
    "--player", "--timeout", "--seed", "--set", "--remove-voice",
})
OPTIONAL_VALUE_OPTS = frozenset({
    "-r", "--random", "--check",
    "-d", "--download", "--download-voice", "--install",
})

ReciterSpec = tuple["Reciter", "str | None"]


# ---------------------------------------------------------------------------
# argv pre-scan
# ---------------------------------------------------------------------------
def _usage_error(msg: str) -> None:
    print(f"{PROG}: {msg}", file=sys.stderr)
    raise SystemExit(EXIT_USAGE)


def prescan(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv into (tokens argparse may see, bare -selector tokens)."""
    options: list[str] = []
    selectors: list[str] = []
    literal = False
    i = 0
    while i < len(argv):
        tok = argv[i]
        if literal:
            selectors.append(tok)
            i += 1
            continue
        if tok == "--":
            literal = True
            i += 1
            continue
        if tok in OPTIONAL_VALUE_OPTS:
            options.append(tok)
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is not None and not nxt.startswith("-"):
                options.append(nxt)
                i += 2
                continue
            i += 1
            continue
        if tok in VALUE_OPTS:
            if i + 1 >= len(argv):
                _usage_error(f"option {tok} requires a value")
            options.extend([tok, argv[i + 1]])
            i += 2
            continue
        if tok in FLAGS:
            options.append(tok)
            i += 1
            continue
        if tok.startswith("--") and len(tok) > 2:
            _usage_error(f"unknown option {tok!r} (try '{PROG} -h')")
        selectors.append(tok[1:] if tok.startswith("-") else tok)
        i += 1
    return options, selectors


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
def die(pm: ui.Palette, msg: str, code: int = EXIT_USAGE) -> int:
    print(f"{PROG}: {pm(msg, 'red')}", file=sys.stderr)
    return code


def near(token: str, options: list[str]) -> str:
    close = difflib.get_close_matches(token, options, n=3, cutoff=0.5)
    return "did you mean: " + ", ".join(close) + "?" if close else ""


# ---------------------------------------------------------------------------
# help
# ---------------------------------------------------------------------------
def build_epilog(pm: ui.Palette) -> str:
    cat = catalog()
    L: list[str] = []
    L.append("VOICES - pass as -<code> or --reciter <code>")
    L.append("  " + pm(f"{'code':<6}{'reciter':<32}{'default recitation':<20}alternates", "brand"))
    for r in cat.reciters:
        alts = ", ".join(s.code for s in r.styles if s.code != r.default_style)
        L.append(f"  {r.code:<6}{r.name_en:<32}{r.default_label_en:<20}{alts or '-'}")

    L.append("")
    L.append("SURAH SELECTORS - pass as -<name> or --surah <name|1-114>")
    entries = [f"{s.number:>3} {s.slug}" for s in cat.surahs]
    cols = 4 if ui.term_width() >= 76 else (3 if ui.term_width() >= 56 else 2)
    rows_n = -(-len(entries) // cols)
    width = max(len(e) for e in entries) + 2
    for row in range(rows_n):
        cells = [entries[c * rows_n + row] for c in range(cols)
                 if c * rows_n + row < len(entries)]
        L.append("  " + "".join(c.ljust(width) for c in cells).rstrip())

    L.append("")
    L.append("EXAMPLES")
    for line in (
        f"{PROG} -baqara -sud          play Surah Al-Baqara in the voice of as-Sudais",
        f"{PROG} -r sud                play a random surah in the voice of as-Sudais",
        f"{PROG} -yaseen -hus:mujawwad  Surah Yaseen, al-Husary, Mujawwad recitation",
        f"{PROG} -r -n 5 -aft           5 random surahs in the voice of al-Afasy",
        f"{PROG} -baqara -yaseen -aft -shr   playlist, voices alternate",
        f"{PROG} -d sud                install the voice of as-Sudais for offline use",
        f"{PROG} -baqara -sud -d       save Surah Al-Baqara offline in Sudais's voice",
        f"{PROG} --offline             play only from offline storage (no network)",
        f"{PROG} --offline-list        list all voices installed offline",
        f"{PROG} -R                    list every voice with its codes",
        f"{PROG} -s                    list all 114 surahs with their codes",
        f"{PROG} --check               verify which voices are reachable",
    ):
        L.append("  " + line)
    L.append("")
    L.append("Audio is streamed from mp3quran.net as one continuous MP3 per surah.")
    return "\n".join(L)


def build_parser(pm: ui.Palette) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=PROG,
        description="Play the Quran in your terminal. Pick a surah and a voice.",
        epilog=build_epilog(pm),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
    )
    ap.add_argument("-h", "--help", action="help",
                    help="show this help, including every voice and surah code")
    ap.add_argument("-V", "--version", action="version",
                    version=f"{PROG} {__version__}")

    sel = ap.add_argument_group("selection")
    sel.add_argument("-r", "--random", nargs="?", const="*", metavar="VOICE",
                     help="play a random surah (optionally in the given voice)")
    sel.add_argument("-n", "--count", type=int, metavar="N",
                     help="play N random surahs")
    sel.add_argument("--surah", action="append", metavar="SURAH", default=[],
                     help="surah by name or number 1-114 (repeatable)")
    sel.add_argument("--reciter", action="append", metavar="VOICE", default=[],
                     help="voice code, or code:style e.g. hus:qalun (repeatable)")
    sel.add_argument("--style", metavar="STYLE",
                     help="recitation style: hafs, mujawwad, warsh, qalun, ...")

    info = ap.add_argument_group("information")
    info.add_argument("-s", "--list-surahs", action="store_true",
                       help="list all 114 surahs with their codes")
    info.add_argument("-R", "--list-reciters", action="store_true",
                       help="list all voices with their codes")
    info.add_argument("-f", "--find", metavar="TEXT",
                      help="search surahs and voices by name")
    info.add_argument("--check", nargs="?", const="*", metavar="VOICE",
                      help="verify audio is reachable (plays nothing)")
    info.add_argument("--list-players", action="store_true",
                      help="show which audio players were found on PATH")

    off = ap.add_argument_group("offline")
    off.add_argument("-d", "--download", "--download-voice", "--install",
                     nargs="?", const="*", metavar="VOICE|DIR", dest="download",
                     help="save voice or surah(s) offline (or export to DIR)")
    off.add_argument("--offline", action="store_true",
                     help="play only from offline storage (never use network)")
    off.add_argument("--offline-list", action="store_true",
                     help="list voices installed for offline playback")
    off.add_argument("--remove-voice", metavar="VOICE",
                     help="remove an offline installed voice")

    out = ap.add_argument_group("output")
    out.add_argument("--url", action="store_true",
                     help="print the resolved URL(s) and exit")
    out.add_argument("--json", action="store_true",
                     help="print the resolved plan as JSON and exit")
    out.add_argument("--player", metavar="NAME",
                     help="force an audio player (default: auto-detect)")
    out.add_argument("--timeout", type=float, default=45.0, metavar="SEC",
                     help="network timeout in seconds (default: 45)")
    out.add_argument("--no-banner", action="store_true",
                     help="suppress the header and per-track details")
    out.add_argument("--seed", type=int, metavar="N",
                     help="seed the random choice for reproducible runs")

    misc = ap.add_argument_group("configuration")
    misc.add_argument("--set", metavar="KEY=VALUE",
                      help="save a setting: reciter, style, player")
    misc.add_argument("--config", action="store_true",
                      help="show the saved configuration and exit")
    return ap


# ---------------------------------------------------------------------------
# resolution
# ---------------------------------------------------------------------------
def parse_reciter(cat, token: str) -> ReciterSpec:
    """``code`` or ``code:style`` -> (Reciter, style_code or None)."""
    base, _, style_code = token.partition(":")
    rec = cat.find_reciter(token)
    if rec is None:
        hint = near(base, [c for r in cat.reciters for c in r.codes])
        raise CatalogError(f"unknown voice {base!r}" + (f"\n       {hint}" if hint else ""))
    return rec, (style_code or None)


def parse_surah(cat, token: str) -> Surah:
    s = cat.find_surah(token)
    if s is None:
        hint = near(token, [str(x.number) for x in cat.surahs]
                    + [a for x in cat.surahs for a in x.aliases])
        raise CatalogError(f"unknown surah {token!r}"
                           + (f"\n       {hint}" if hint else ""))
    return s


def resolve_selectors(pm: ui.Palette, cat, tokens: list[str]):
    surahs: list[Surah] = []
    reciters: list[ReciterSpec] = []
    for raw in tokens:
        tok = raw.strip()
        if not tok:
            continue
        base = tok.partition(":")[0]
        if ":" in tok and cat.find_reciter(base) is not None:
            # "<code>:<style>" is always a voice, so a bad style name must
            # surface as a style error rather than "unknown selector"
            spec = parse_reciter(cat, tok)
        else:
            try:
                spec = parse_reciter(cat, tok)
            except CatalogError:
                spec = None
        surah = cat.find_surah(tok)
        if surah and spec is not None:
            rec = spec[0]
            print(pm(f"{PROG}: note: '-{tok}' matches both Surah {surah.name_en} "
                     f"({surah.number}) and the voice of {rec.name_en}; "
                     f"using the surah. Use --reciter {tok} for the voice.",
                     "yellow"), file=sys.stderr)
        if surah:
            surahs.append(surah)
        elif spec is not None:
            reciters.append(spec)
        else:
            hint = near(tok, [str(x.number) for x in cat.surahs]
                        + [a for x in cat.surahs for a in x.aliases]
                        + [c for r in cat.reciters for c in r.codes])
            raise CatalogError(
                f"unknown selector '-{tok}'"
                + (f"\n       {hint}" if hint else
                   f"\n       see '{PROG} -s' and '{PROG} -R' for valid codes"))
    return surahs, reciters


def build_plan(pm: ui.Palette, cat, args, surahs, reciters) -> list:
    if not reciters:
        code = config.default_reciter()
        reciters = [(cat.find_reciter(code) or cat.reciters[0], None)]

    wants_random = args.random is not None or args.count
    if surahs and wants_random:
        print(pm(f"{PROG}: note: --random ignored because a surah was given "
                 f"explicitly.", "yellow"), file=sys.stderr)
        chosen = surahs
    elif args.count:
        chosen = cat.random_surahs(args.count)
    elif args.random is not None:
        chosen = cat.random_surahs(1)
    elif surahs:
        chosen = surahs
    else:
        chosen = cat.random_surahs(1)

    fallback_style = args.style or config.load().get("style")
    return [cat.track(chosen[i], reciters[i % len(reciters)][0],
                      reciters[i % len(reciters)][1] or fallback_style)
            for i in range(len(chosen))]


# ---------------------------------------------------------------------------
# information views
# ---------------------------------------------------------------------------
def show_reciters(pm: ui.Palette) -> None:
    flags = {"MR": "🇲🇷 Mauritanian", "SA": "🇸🇦 Saudi", "EG": "🇪🇬 Egyptian",
             "SD": "🇸🇩 Sudanese", "MA": "🇲🇦 Moroccan", "LY": "🇱🇾 Libyan",
             "QA": "🇶🇦 Qatari", "KW": "🇰🇼 Kuwaiti", "JO": "🇯🇴 Jordanian",
             "PS": "🇵🇸 Palestinian", "TR": "🇹🇷 Turkish", "ID": "🇮🇩 Indonesian",
             "IN": "🇮🇳 Indian", "PK": "🇵🇰 Pakistani", "DZ": "🇩🇿 Algerian",
             "TN": "🇹🇳 Tunisian", "IQ": "🇮🇶 Iraqi", "YE": "🇾🇪 Yemeni"}
    rows = []
    for r in catalog().reciters:
        alts = ", ".join(s.code for s in r.styles if s.code != r.default_style) or "-"
        where = flags.get(r.nationality or "", r.nationality or "")
        rows.append([r.code, r.name_en, r.name_ar, r.default_label_en, alts, where])
    ui.table(pm, ["code", "reciter", "بالاسم", "default", "alt styles", "from"],
             rows, aligns=["l", "l", "l", "l", "l", "l"])
    installed = storage.list_installed()
    if installed:
        print()
        print(pm("OFFLINE VOICES INSTALLED", "bold"))
        for item in installed:
            rec = catalog().find_reciter(item["reciter_code"])
            rec_name = rec.name_en if rec else item["reciter_code"]
            style = rec.style_by_code(item["style_code"]) if rec else None
            max_c = style.surah_count if style else 114
            print(f"  {pm('✓', 'green')} {item['reciter_code']:<6} {rec_name:<28} "
                  f"[{item['style_code']}] {item['count']}/{max_c} surahs "
                  f"({ui.human_size(item['total_bytes'])})")
    print()
    print("  " + pm(f"select one with:  {PROG} -<code>       e.g. {PROG} -sud", "brand"))
    print("  " + pm(f"alternate style:  {PROG} <code>:<style>  e.g. {PROG} -hus:qalun", "brand"))
    print("  " + pm(f"install offline:  {PROG} -d <code>      e.g. {PROG} -d sud", "brand"))
    print("  " + pm(f"search for a country:  {PROG} -f mauritanian", "brand"))


def show_surahs(pm: ui.Palette) -> None:
    rows = [[str(s.number), s.slug, s.name_en, s.name_ar, str(s.ayahs), s.revelation]
            for s in catalog().surahs]
    ui.table(pm, ["#", "code", "name", "بالاسم", "ayahs", "type"], rows,
             aligns=["r", "l", "l", "l", "r", "l"])


def show_find(pm: ui.Palette, text: str) -> int:
    surahs, reciters = catalog().search(text)
    if not surahs and not reciters:
        return die(pm, f"nothing matched {text!r}")
    if reciters:
        print(pm("VOICES", "bold"))
        ui.table(pm, ["code", "reciter", "default", "aliases"],
                 [[r.code, r.name_en, r.default_label_en, " ".join(r.codes)]
                  for r in reciters])
        print()
    if surahs:
        print(pm("SURAHS", "bold"))
        ui.table(pm, ["#", "code", "name", "ayahs", "meaning"],
                 [[str(s.number), s.slug, s.name_en, str(s.ayahs), s.meaning_en]
                  for s in surahs], aligns=["r", "l", "l", "r", "l"])
    return EXIT_OK


def show_players(pm: ui.Palette) -> None:
    ui.table(pm, ["name", "binary", "streams", "status", "note"],
             [[s.name, s.binary, "yes" if s.stdin_ok else "no (temp file)",
               "found" if p else "-", s.note] for s, p in player_mod.available()],
             aligns=["l", "l", "l", "l", "l"])


def show_config(pm: ui.Palette) -> None:
    path = config.config_path()
    data = config.load()
    if not data:
        print(pm(f"no settings saved ({path})", "brand"))
        print(f"  built-in default voice: {config.default_reciter()}")
        print(f"  offline storage dir:    {storage.offline_dir()}")
        return
    print(f"  {pm(str(path), 'grey')}")
    ui.table(pm, ["key", "value"],
             [[k, json.dumps(v, ensure_ascii=False)] for k, v in sorted(data.items())])
    print(f"  offline storage dir: {storage.offline_dir()}")


def show_offline_list(pm: ui.Palette) -> None:
    installed = storage.list_installed()
    if not installed:
        print(pm(f"no voices installed offline yet ({storage.offline_dir()})", "brand"))
        print(f"  install one with:  {PROG} -d <code>       e.g. {PROG} -d sud")
        print(f"  or install surah:  {PROG} -baqara -sud -d")
        return
    print(pm(f"OFFLINE VOICES ({storage.offline_dir()})", "bold"))
    rows = []
    cat = catalog()
    total_bytes = 0
    total_surahs = 0
    for item in installed:
        rec = cat.find_reciter(item["reciter_code"])
        rec_name = rec.name_en if rec else item["reciter_code"]
        size_str = ui.human_size(item["total_bytes"])
        total_bytes += item["total_bytes"]
        total_surahs += item["count"]
        style = rec.style_by_code(item["style_code"]) if rec else None
        max_count = style.surah_count if style else 114
        count_display = f"{item['count']}/{max_count}"
        rows.append([item["reciter_code"], rec_name, item["style_code"], count_display, size_str])
    ui.table(pm, ["code", "reciter", "style", "surahs", "size"], rows,
             aligns=["l", "l", "l", "r", "r"])
    print()
    print(pm(f"total: {total_surahs} surah(s), {ui.human_size(total_bytes)}", "brand"))


def do_remove_voice(pm: ui.Palette, cat, token: str) -> int:
    rec, style_code = parse_reciter(cat, token)
    count, size = storage.remove_voice(rec.code, style_code)
    if not count:
        print(pm(f"no offline files found for {rec.name_en} ({token})", "yellow"))
        return EXIT_OK
    style_msg = f" [{style_code}]" if style_code else ""
    print(pm(f"removed {count} offline file(s) for {rec.name_en}{style_msg} "
             f"({ui.human_size(size)} freed)", "green"))
    return EXIT_OK


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------
def describe_track(pm: ui.Palette, track) -> None:
    s, r, st = track.surah, track.reciter, track.style
    style = st.label_en if st.code == r.default_style else f"{st.label_en} ({st.code})"
    if track.is_offline:
        source_val = f"{track.local_path}  {pm('[offline]', 'green')}"
        voice_val = f"{r.name_en}  {r.code}   {r.name_ar}  {pm('[offline]', 'green')}"
    else:
        source_val = track.url
        voice_val = f"{r.name_en}  {r.code}   {r.name_ar}"
    ui.card(pm, [
        ("", ""),
        ("SURAH", f"{s.name_en}  #{s.number}   {s.name_ar}"),
        ("", f"{s.meaning_en} · {s.ayahs} ayahs · {s.revelation.capitalize()}"),
        ("", ""),
        ("VOICE", voice_val),
        ("", f"{style}" + (f"   🇲🇷 {r.nationality}" if r.nationality else "")),
        ("", ""),
        ("SOURCE", source_val),
    ])


def do_check(pm: ui.Palette, cat, args, planned: list, user_selected: bool) -> int:
    if args.check != "*":
        rec, style_code = parse_reciter(cat, args.check)
        style = cat.style_for(rec, style_code or args.style or config.load().get("style"))
        targets = [(s, rec, style) for s in cat.surahs if style.has(s.number)]
    elif user_selected:
        targets = [(t.surah, t.reciter, t.style) for t in planned]
    else:
        # no voice or surah named: smoke-test every voice on a few surahs
        samples = (cat.surahs[0], cat.surahs[1], cat.surahs[35], cat.surahs[113])
        targets = [(s, r, r.style) for r in cat.reciters for s in samples]

    print(pm(f"checking {len(targets)} audio URL(s) - nothing will be played", "brand"))
    ok = bad = 0
    seen: set = set()
    for surah, rec, style in targets:
        key = (surah.number, rec.code, style.code)
        if key in seen:
            continue
        seen.add(key)
        url = f"{style.server}{surah.number:03d}.mp3"
        try:
            size = net.probe(url)
            ok += 1
            print(f"  {pm('OK  ', 'green')} {rec.code:<5}{surah.number:>4} "
                  f"{surah.slug:<14}{ui.human_size(size):>10}  {style.label_en}")
        except net.NetworkError as exc:
            bad += 1
            print(f"  {pm('FAIL', 'red')} {rec.code:<5}{surah.number:>4} "
                  f"{surah.slug:<14}{pm(str(exc).splitlines()[0], 'red')}")
    print()
    line = f"  {pm(f'{ok} reachable', 'green')}"
    if bad:
        line += f", {pm(f'{bad} failed', 'red')}"
    print(line)
    return EXIT_OK if bad == 0 else EXIT_FAIL


def do_download(pm: ui.Palette, planned: list, dest: str | None = None) -> int:
    is_offline_install = dest is None or dest == "*"
    if is_offline_install:
        base_dir = storage.offline_dir()
        header = f"installing {len(planned)} surah(s) offline to {base_dir}"
    else:
        base_dir = pathlib.Path(dest).expanduser()
        base_dir.mkdir(parents=True, exist_ok=True)
        header = f"downloading {len(planned)} surah(s) to {base_dir}"

    print(pm(header, "brand"))
    print()

    failures = 0
    skipped = 0
    saved = 0
    total_bytes = 0

    for i, track in enumerate(planned, 1):
        if is_offline_install:
            dest_file = track.offline_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            dest_file = base_dir / track.filename

        if dest_file.is_file() and dest_file.stat().st_size > 0:
            print(f"  [{i}/{len(planned)}] {pm(track.describe(), 'bold')} "
                  f"{pm('(already installed)', 'grey')}")
            skipped += 1
            total_bytes += dest_file.stat().st_size
            continue

        tmp_file = dest_file.with_suffix(".tmp")
        viz = ui.Visualizer(f"[{i}/{len(planned)}] {track.surah.name_en}")
        print(f"  [{i}/{len(planned)}] {pm(track.describe(), 'bold')}")
        try:
            with open(tmp_file, "wb") as fh:
                written = net.download(
                    track.url, fh, timeout=90.0,
                    on_total=viz.start, on_chunk=viz.advance,
                )
            tmp_file.replace(dest_file)
            saved += 1
            total_bytes += written
            print(f"  {pm('saved', 'green')} {dest_file.name} "
                  f"{pm(ui.human_size(written), 'grey')}")
        except KeyboardInterrupt:
            viz.stop()
            tmp_file.unlink(missing_ok=True)
            raise
        except (net.NetworkError, OSError) as exc:
            tmp_file.unlink(missing_ok=True)
            print(f"  {pm('FAILED', 'red')} {exc}")
            failures += 1
        finally:
            viz.stop()

    print()
    if is_offline_install:
        if saved or skipped:
            summary = f"offline library: {saved} new, {skipped} existing ({ui.human_size(total_bytes)} total)"
            print(pm("  " + summary, "green"))
            reciters = {t.reciter.code for t in planned}
            if len(reciters) == 1:
                rec_code = next(iter(reciters))
                print(pm(f"  play anytime offline: {PROG} -{rec_code}", "brand"))
    else:
        if saved:
            print(pm(f"  saved {saved} file(s) to {base_dir}", "green"))

    return EXIT_FAIL if failures else EXIT_OK


def do_play(pm: ui.Palette, planned: list, args) -> int:
    try:
        spec = player_mod.detect(args.player or config.load().get("player") or None)
    except player_mod.PlayerError as exc:
        return die(pm, str(exc), EXIT_FAIL)
    print(pm(f"player: {spec.name} ({spec.binary})", "brand"))
    print()
    failures = 0
    for i, track in enumerate(planned):
        if i:
            print(pm("  " + "-" * 70, "brand"))
            print()
        if not args.no_banner:
            describe_track(pm, track)
        print()
        viz = ui.Visualizer(f"{track.surah.name_en} · {track.reciter.name_en}")
        try:
            if track.is_offline:
                play_source = track.local_path
            else:
                if getattr(args, "offline", False):
                    raise CatalogError(
                        f"Surah {track.surah.name_en} ({track.surah.number}) in the voice of "
                        f"{track.reciter.name_en} is not installed offline.\n"
                        f"       Run '{PROG} -d -{track.surah.slug} -{track.reciter.code}' to download."
                    )
                play_source = track.url

            with player_mod.Session(spec, on_total=viz.start,
                                     on_chunk=viz.advance) as s:
                s.play(play_source, timeout=args.timeout)
            print(pm("  done.", "brand"))
        except KeyboardInterrupt:
            print(pm("\n  stopped.", "brand"))
            return EXIT_OK
        except (net.NetworkError, player_mod.PlayerError, CatalogError) as exc:
            print(pm(f"  error: {exc}", "red"))
            if isinstance(exc, net.NetworkError) and not track.is_offline:
                print(pm(f"  note: voice '{track.reciter.code}' is not installed offline. "
                         f"Install with '{PROG} -d {track.reciter.code}'", "yellow"))
            failures += 1
        finally:
            viz.stop()
    return EXIT_FAIL if failures else EXIT_OK


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    install_signal_handlers()
    try:
        return _main(argv)
    except KeyboardInterrupt:
        print(f"\n{PROG}: stopped.", file=sys.stderr)
        return EXIT_INTERRUPTED


def _main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw:
        raw = ["-r"]

    pm = ui.palette()
    options, selectors = prescan(raw)
    ap = build_parser(pm)
    args = ap.parse_args(options)

    # -- configuration actions need no catalog --------------------------
    if args.set:
        if "=" not in args.set:
            return die(pm, f"--set expects KEY=VALUE, got {args.set!r}")
        key, _, value = args.set.partition("=")
        try:
            result = config.set_value(key.strip(), value.strip())
        except CatalogError as exc:
            return die(pm, str(exc))
        print(pm(result, "green"))
        print(pm(f"saved to {config.config_path()}", "brand"))
        return EXIT_OK
    if args.config:
        show_config(pm)
        return EXIT_OK
    if args.list_players:
        show_players(pm)
        return EXIT_OK
    if getattr(args, "offline_list", False):
        show_offline_list(pm)
        return EXIT_OK
    if getattr(args, "remove_voice", None):
        return do_remove_voice(pm, catalog(), args.remove_voice)

    cat = catalog()

    if args.list_reciters:
        show_reciters(pm)
        return EXIT_OK
    if args.list_surahs:
        show_surahs(pm)
        return EXIT_OK
    if args.find:
        return show_find(pm, args.find)

    try:
        if args.seed is not None:
            random.seed(args.seed)  # must be set before build_plan() chooses

        surahs, reciters = resolve_selectors(pm, cat, selectors)

        download_target_dir = None
        if args.download is not None and args.download != "*":
            try:
                rec_spec = parse_reciter(cat, args.download)
                reciters = [rec_spec, *reciters]
            except CatalogError:
                try:
                    sur_spec = parse_surah(cat, args.download)
                    surahs = [sur_spec, *surahs]
                except CatalogError:
                    download_target_dir = args.download

        if args.random not in (None, "*"):
            reciters = [parse_reciter(cat, args.random), *reciters]
        if args.surah:
            surahs = [parse_surah(cat, t) for t in args.surah] + surahs
        if args.reciter:
            reciters = [parse_reciter(cat, t) for t in args.reciter] + reciters
        if args.count is not None and args.count < 1:
            raise CatalogError("--count must be 1 or more")

        if args.download is not None and download_target_dir is None:
            if not surahs and args.random is None and args.count is None:
                if not reciters:
                    code = config.default_reciter()
                    reciters = [(cat.find_reciter(code) or cat.reciters[0], None)]
                planned = []
                fallback_style = args.style or config.load().get("style")
                for rec, style_code in reciters:
                    style = cat.style_for(rec, style_code or fallback_style)
                    for s in cat.surahs:
                        if style.has(s.number):
                            planned.append(cat.track(s, rec, style.code))
            else:
                planned = build_plan(pm, cat, args, surahs, reciters)
        else:
            planned = build_plan(pm, cat, args, surahs, reciters)
    except CatalogError as exc:
        return die(pm, str(exc))

    user_selected = bool(selectors or args.surah or args.reciter
                         or args.random not in (None, "*"))

    # machine-readable output must stay clean for scripts/pipes
    machine = args.url or args.json
    if not args.no_banner and not machine:
        ui.banner(pm, __version__)
        print()

    note = net.proxy_note()
    if note and not machine:
        print(pm(f"note: {note}", "yellow"), file=sys.stderr)

    if args.check is not None:
        return do_check(pm, cat, args, planned, user_selected)

    if getattr(args, "offline", False):
        missing_offline = [t for t in planned if not t.is_offline]
        if missing_offline:
            t = missing_offline[0]
            return die(pm, f"Surah {t.surah.name_en} ({t.surah.number}) in the voice of "
                           f"{t.reciter.name_en} is not installed offline.\n"
                           f"       Download it with: {PROG} -d -{t.surah.slug} -{t.reciter.code}")

    if args.json:
        print(json.dumps([
            {"surah": t.surah.number, "surah_name": t.surah.name_en,
             "slug": t.surah.slug, "ayahs": t.surah.ayahs,
             "reciter": t.reciter.code, "reciter_name": t.reciter.name_en,
             "style": t.style.code, "style_name": t.style.label_en, "url": t.url,
             "offline": t.is_offline,
             "local_path": str(t.local_path) if t.is_offline else None}
            for t in planned], ensure_ascii=False, indent=2))
        return EXIT_OK

    if args.url:
        for t in planned:
            if args.offline and t.is_offline:
                print(t.local_path)
            else:
                print(t.url)
        return EXIT_OK

    if args.download is not None:
        sys.stdout.flush()  # keep stdout ahead of the stderr progress bar
        return do_download(pm, planned, download_target_dir)

    sys.stdout.flush()
    return do_play(pm, planned, args)


if __name__ == "__main__":
    sys.exit(main())
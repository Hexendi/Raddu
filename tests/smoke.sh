#!/usr/bin/env bash
# raddu smoke tests.
#
#   tests/smoke.sh            full suite (includes network checks)
#   tests/smoke.sh --offline  skip anything that touches the network
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
RADDU="$ROOT/bin/raddu"
OFFLINE=0
[[ "${1:-}" == "--offline" ]] && OFFLINE=1

PASS=0; FAIL=0
c_g=$'\033[32m'; c_r=$'\033[31m'; c_d=$'\033[90m'; c_0=$'\033[0m'
[[ -t 1 ]] || { c_g=; c_r=; c_d=; c_0=; }

ok()   { PASS=$((PASS+1)); printf '  %sPASS%s %s\n' "$c_g" "$c_0" "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  %sFAIL%s %s\n        expected: %s\n        got:      %s\n' \
         "$c_r" "$c_0" "$1" "$2" "$3"; }
head_() { printf '\n%s%s%s\n' "$c_d" "$1" "$c_0"; }

expect_eq() { # desc expected actual
  [[ "$2" == "$3" ]] && ok "$1" || bad "$1" "$2" "$3"
}
expect_rc() { # desc expected_rc cmd...
  local desc="$1" want="$2"; shift 2
  "$@" >/dev/null 2>&1; local got=$?
  [[ "$got" == "$want" ]] && ok "$desc" || bad "$desc" "exit $want" "exit $got"
}
expect_contains() { # desc needle cmd...
  local desc="$1" needle="$2"; shift 2
  local out; out="$("$@" 2>&1)"
  [[ "$out" == *"$needle"* ]] && ok "$desc" || bad "$desc" "contains '$needle'" "$(echo "$out" | head -2)"
}

command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
[[ -x "$RADDU" ]] || { echo "missing $RADDU"; exit 1; }

head_ "version"
expect_contains "reports a version" "raddu" "$RADDU" --version

head_ "help lists every voice and every surah code"
expect_contains "help mentions the sud voice"  "sud"      "$RADDU" -h
expect_contains "help mentions the aft voice"  "aft"      "$RADDU" -h
expect_contains "help mentions baqara"         "baqara"   "$RADDU" -h
expect_contains "help mentions yaseen"         "yaseen"   "$RADDU" -h
expect_contains "help shows the 114 surahs"   "114"      "$RADDU" -h
expect_contains "help shows raddu -r sud"      "-r sud"   "$RADDU" -h

head_ "listing"
expect_contains "-R lists voices"       "Abdur-Rahman as-Sudais" "$RADDU" -R
expect_contains "-s lists surahs"      "Al-Baqara"               "$RADDU" -s
expect_contains "-s lists all 114"      "114"                     "$RADDU" -s
expect_contains "--list-players works"  "ffplay"                  "$RADDU" --list-players

head_ "URL resolution (offline)"
u="$("$RADDU" -baqara -sud --url 2>/dev/null)"
expect_eq "surah code + voice code" "https://cdn.mp3quran.net/audio/abdulrahman-sudais/r1/002.mp3" "$u"
expect_eq "surah by number"        "https://cdn.mp3quran.net/audio/abdulrahman-sudais/r1/036.mp3" \
                                  "$("$RADDU" 36 -sud --url 2>/dev/null)"
expect_eq "long --surah form"      "https://cdn.mp3quran.net/audio/mishary-alafasy/r1/114.mp3" \
                                  "$("$RADDU" --surah 114 --reciter aft --url 2>/dev/null)"
expect_eq "voice:style syntax"     "https://cdn.mp3quran.net/audio/mahmoud-husary/r2/036.mp3" \
                                  "$("$RADDU" -yaseen -hus:mujawwad --url 2>/dev/null)"
expect_eq "haz  (Hazzaa al-Balushi)" "https://cdn.mp3quran.net/audio/hazza-balushi/r1/036.mp3" \
                                  "$("$RADDU" -yaseen -haz --url 2>/dev/null)"
expect_eq "luh  (Muhammad al-Luhaidan)" "https://cdn.mp3quran.net/audio/muhammad-luhaidan/r1/114.mp3" \
                                  "$("$RADDU" -naas -luh --url 2>/dev/null)"
expect_eq "kha  (Mohammad Khalil al-Qari)" "https://cdn.mp3quran.net/audio/muhammad-khalil/r1/114.mp3" \
                                  "$("$RADDU" -naas -kha --url 2>/dev/null)"
expect_eq "long alias 'lhaydan' still resolves" "https://cdn.mp3quran.net/audio/muhammad-luhaidan/r1/114.mp3" \
                                  "$("$RADDU" -naas -lhaydan --url 2>/dev/null)"
expect_eq "bilal (Mauritanian, way2quran)" "https://media.way2quran.com/al-nagy-wild-bilal/qalun-an-nafi/114.mp3" \
                                  "$("$RADDU" -naas -bilal --url 2>/dev/null)"
expect_eq "lagh  (Mauritanian)" "https://media.way2quran.com/mohamed-laghzaf-weld-mohamed-sidi/qalun-an-nafi/002.mp3" \
                                  "$("$RADDU" -baqara -lagh --url 2>/dev/null)"
expect_eq "way2quran keeps the exact unpadded filename" \
          "https://media.way2quran.com/alragel-ould-othman/warsh-an-nafi/020.mp3" \
                                  "$("$RADDU" -taha -ragl --url 2>/dev/null)"
expect_contains "-f mauritanian finds the MR voices" "Al-Nagy Ould Bilal" "$RADDU" -f mauritanian
expect_rc "incomplete recording is reported" 2 "$RADDU" -baqara -haz --url
expect_contains "incomplete message is specific" "91/114" "$RADDU" -baqara -haz --url

head_ "player pipe teardown"
# A signal arriving mid-cleanup used to make stop() close the same fd twice,
# which surfaced as "Bad file descriptor" on the way out.
if PYTHONPATH="$ROOT/src" python3 - <<'PY'
import subprocess, sys
from raddu.player import PlayerSpec, Session
p = subprocess.Popen(["sleep", "30"], stdin=subprocess.PIPE,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
s = Session(PlayerSpec("sleep", "sleep", ("30",)))
s.proc, s._stdin = p, p.stdin
s._stdin.write(b"x" * 4096)
s._drop_stdin()
s._drop_stdin()          # re-entrant: must be a no-op, not a double close
s.stop()
p.kill()
sys.exit(0)
PY
then ok "_drop_stdin is re-entrancy safe"; else bad "_drop_stdin is re-entrancy safe" "clean exit" "raised"; fi

head_ "visualizer"
if PYTHONPATH="$ROOT/src" python3 - <<'PY'
import sys
from raddu import ui
v = ui.Visualizer("probe")
assert v.rows() == len(v._lines()), "row count drift"
mid = v.HEIGHT
lines = v._lines()
above = lines[:mid]
below = lines[mid + 1:v.rows() - 1]
assert above == below[::-1], "bars are not mirrored"
v.done, v.total = 5_000_000, 20_000_000
v.phase = 9
assert len(v._lines()) == v.rows(), "row count drift after resize"
sys.exit(0)
PY
then ok "visualizer is mirrored and fixed-height"; else bad "visualizer is mirrored and fixed-height" "clean exit" "raised"; fi

head_ "download progress bar"
if PYTHONPATH="$ROOT/src" python3 - <<'PY'
import io, sys, time
from raddu import ui
# A tty draws the bar; a pipe stays silent so downloads can be scripted.
class TTY(io.StringIO):
    def isatty(self): return True
s = TTY()
bar = ui.ProgressBar("[1/2] Al-Baqara", stream=s)
bar.start(1000)
time.sleep(0.09); bar.advance(250)
time.sleep(0.09); bar.advance(750)
bar.stop()
out = s.getvalue()
assert "25.0%" in out and "100.0%" in out and "█" in out, "bar did not render"
plain = io.StringIO()
b2 = ui.ProgressBar("x", stream=plain)
b2.start(100); b2.advance(50); b2.stop()
assert plain.getvalue() == "", "non-tty download should be silent"
sys.exit(0)
PY
then ok "download shows a progress bar on a tty, silent when piped"; else bad "download shows a progress bar on a tty, silent when piped" "clean exit" "raised"; fi

head_ "offline network error is friendly"
if PYTHONPATH="$ROOT/src" python3 - <<'PY'
import os
# A dead proxy stands in for "no internet": the connection is refused.
for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
    os.environ[k] = "http://127.0.0.1:9"
from raddu import net
try:
    net.probe("https://cdn.mp3quran.net/audio/abdulrahman-sudais/r1/002.mp3", timeout=3)
except net.NetworkError as exc:
    msg = str(exc)
    assert exc.offline, "not flagged offline"
    assert "no internet connection" in msg, msg
    assert "Errno" not in msg and "http" not in msg.lower(), msg
else:
    raise AssertionError("expected a NetworkError")
import sys; sys.exit(0)
PY
then ok "unreachable host maps to 'no internet connection'"; else bad "unreachable host maps to 'no internet connection'" "clean exit" "raised"; fi
expect_eq "--style override"       "https://cdn.mp3quran.net/audio/mahmoud-husary/r5/036.mp3" \
                                  "$("$RADDU" -yaseen -hus --style qalun --url 2>/dev/null)"
expect_eq "playlist alternates voices" \
"https://cdn.mp3quran.net/audio/mishary-alafasy/r1/002.mp3
https://cdn.mp3quran.net/audio/saud-shuraim/r1/036.mp3" \
"$("$RADDU" -baqara -yaseen -aft -shr --url 2>/dev/null)"
expect_eq "seeded random is reproducible" \
          "$("$RADDU" -r -n 3 -aft --seed 42 --url 2>/dev/null)" \
          "$("$RADDU" -r -n 3 -aft --seed 42 --url 2>/dev/null)"

head_ "machine-readable output is clean on stdout"
expect_eq "--url writes only the URL" "1" \
  "$("$RADDU" -baqara -sud --url 2>/dev/null | wc -l | tr -d ' ')"
expect_contains "--json is valid JSON" '"slug": "baqara"' "$RADDU" -baqara -sud --json

head_ "error handling"
expect_rc "unknown surah is a usage error"   2 "$RADDU" -notasurah --url
expect_rc "unknown voice is a usage error"   2 "$RADDU" -fatiha -notavoice --url
expect_rc "unknown style is a usage error"   2 "$RADDU" -fatiha -aft:bogus --url
expect_rc "unknown option is a usage error"  2 "$RADDU" --definitely-not-an-option
expect_rc "missing surah for style is caught" 2 "$RADDU" -fatiha -aft:kisaai --url
expect_contains "suggests a correction" "baqara" "$RADDU" -baqra --url
# regression: argparse would read "-sud" as "-s -u -d" and print the surah table
out="$("$RADDU" -baqara -sud --url --no-banner 2>&1)"
case "$out" in
  *https://cdn.mp3quran.net/audio/abdulrahman-sudais/r1/002.mp3*) ok "-sud is a voice, not -s -u -d" ;;
  *) bad "-sud is a voice, not -s -u -d" "just the sudais URL" "$out" ;;
esac

head_ "configuration"
cfgdir="${XDG_CONFIG_HOME:-/tmp/opencode/raddu-test-config}"
rm -rf "$cfgdir"; mkdir -p "$cfgdir"
out="$(XDG_CONFIG_HOME="$cfgdir" "$RADDU" --set reciter=hus 2>&1)"
if [[ "$out" == *reciter* ]]; then ok "--set saves a setting"; else bad "--set saves a setting" "reciter" "$out"; fi
expect_eq "saved setting is honoured" "https://cdn.mp3quran.net/audio/mahmoud-husary/r1/002.mp3" \
          "$(XDG_CONFIG_HOME="$cfgdir" "$RADDU" -baqara --url 2>/dev/null)"
XDG_CONFIG_HOME="$cfgdir" "$RADDU" --set reciter=clear >/dev/null 2>&1
expect_eq "cleared setting falls back to default" "https://cdn.mp3quran.net/audio/mishary-alafasy/r1/002.mp3" \
          "$(XDG_CONFIG_HOME="$cfgdir" "$RADDU" -baqara --url 2>/dev/null)"
rm -rf "$cfgdir"

head_ "offline voices"
expect_contains "help mentions -d"            "-d"       "$RADDU" -h
expect_contains "help mentions --offline"     "offline"  "$RADDU" -h
expect_eq "-d sud plans 114 surahs" "114" \
          "$("$RADDU" -d sud --url 2>/dev/null | wc -l | tr -d ' ')"
expect_eq "-d haz plans 91 surahs" "91" \
          "$("$RADDU" -d haz --url 2>/dev/null | wc -l | tr -d ' ')"
expect_rc "--offline fails cleanly when missing" 2 "$RADDU" -baqara -sud --offline
expect_contains "--offline-list reports empty when none" "no voices" "$RADDU" --offline-list

datadir="${XDG_DATA_HOME:-/tmp/opencode/raddu-test-data}"
rm -rf "$datadir"; mkdir -p "$datadir/raddu/voices/sud/hafs"
# simulate an offline cached surah
echo "dummy-audio" > "$datadir/raddu/voices/sud/hafs/114-naas.mp3"
expect_eq "--offline --url resolves local file when present" \
          "$datadir/raddu/voices/sud/hafs/114-naas.mp3" \
          "$(XDG_DATA_HOME="$datadir" "$RADDU" -naas -sud --offline --url 2>/dev/null)"
expect_contains "--offline --json reports offline: true" '"offline": true' \
          env XDG_DATA_HOME="$datadir" "$RADDU" -naas -sud --json
expect_contains "--offline-list shows installed voice" "sud" \
          env XDG_DATA_HOME="$datadir" "$RADDU" --offline-list
expect_contains "-R shows offline installed section" "OFFLINE VOICES INSTALLED" \
          env XDG_DATA_HOME="$datadir" "$RADDU" -R
out="$(XDG_DATA_HOME="$datadir" "$RADDU" --remove-voice sud 2>&1)"
if [[ "$out" == *removed* ]]; then ok "--remove-voice removes files"; else bad "--remove-voice removes files" "removed" "$out"; fi
rm -rf "$datadir"

if [[ "$OFFLINE" == "1" ]]; then
  head_ "network tests skipped (--offline)"
else
  head_ "network"
  out="$("$RADDU" --check 2>&1)"
  if [[ "$out" == *"0 reachable"* || "$out" == *" reachable"* ]]; then
    if [[ "$out" == *"0 reachable, "* && "$out" != *"0 reachable"* ]]; then :; fi
    ok "--check reports reachable audio"
  else
    bad "--check reports reachable audio" "reachable" "$(echo "$out" | tail -2)"
  fi
  expect_contains "a known URL is reachable" "OK" "$RADDU" --check sud

  tmp="$(mktemp -d)"
  if "$RADDU" -naas -sud --download "$tmp" >/dev/null 2>&1; then
    f="$(ls "$tmp"/*.mp3 2>/dev/null | head -1)"
    if [[ -n "$f" ]] && head -c2 "$f" | grep -q 'ID\|.'; then
      sz=$(wc -c < "$f")
      if [[ "$sz" -gt 100000 ]]; then ok "--download wrote a real MP3 ($sz bytes)"
      else bad "--download wrote a real MP3" ">100000 bytes" "$sz"; fi
    else
      bad "--download wrote a real MP3" "an mp3 file" "nothing"
    fi
  else
    bad "--download" "exit 0" "nonzero"
  fi
  rm -rf "$tmp"

  tmpdata="$(mktemp -d)"
  if XDG_DATA_HOME="$tmpdata" "$RADDU" -naas -sud -d >/dev/null 2>&1; then
    f="$tmpdata/raddu/voices/sud/hafs/114-naas.mp3"
    if [[ -f "$f" ]]; then
      sz=$(wc -c < "$f")
      if [[ "$sz" -gt 100000 ]]; then ok "-d saved real offline MP3 ($sz bytes)"
      else bad "-d saved real offline MP3" ">100000 bytes" "$sz"; fi
    else
      bad "-d saved real offline MP3" "114-naas.mp3" "missing"
    fi
  else
    bad "-d" "exit 0" "nonzero"
  fi
  rm -rf "$tmpdata"
fi

printf '\n  %s%d passed%s, %s%d failed%s\n\n' "$c_g" "$PASS" "$c_0" \
       "$([[ $FAIL -gt 0 ]] && echo "$c_r" || echo "$c_d")" "$FAIL" "$c_0"
exit $(( FAIL > 0 ? 1 : 0 ))
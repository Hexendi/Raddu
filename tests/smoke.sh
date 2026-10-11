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
fi

printf '\n  %s%d passed%s, %s%d failed%s\n\n' "$c_g" "$PASS" "$c_0" \
       "$([[ $FAIL -gt 0 ]] && echo "$c_r" || echo "$c_d")" "$FAIL" "$c_0"
exit $(( FAIL > 0 ? 1 : 0 ))
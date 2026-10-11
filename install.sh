#!/usr/bin/env bash
# Install raddu into ~/.local/bin (or $PREFIX/bin).
#
#   ./install.sh              -> symlink into ~/.local/bin
#   ./install.sh --copy       -> copy the launcher instead of symlinking
#   PREFIX=/usr/local ./install.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-${XDG_DATA_HOME:-$HOME/.local/share}}"
BIN_DIR="${PREFIX%/share}/bin"
MODE="link"

for arg in "$@"; do
  case "$arg" in
    --copy) MODE="copy" ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "install.sh: unknown option $arg" >&2; exit 2 ;;
  esac
done

PY="$(command -v python3 || true)"
if [[ -z "$PY" ]]; then
  echo "install.sh: python3 not found on PATH" >&2
  exit 1
fi
VER="$("$PY" -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
if ! "$PY" -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)'; then
  echo "install.sh: python 3.10+ required, found $VER" >&2
  exit 1
fi

mkdir -p "$BIN_DIR"
TARGET="$BIN_DIR/raddu"

if [[ "$MODE" == "copy" ]]; then
  cp -f "$HERE/bin/raddu" "$TARGET"
  chmod +x "$TARGET"
else
  ln -sfn "$HERE/bin/raddu" "$TARGET"
fi

echo "installed raddu -> $TARGET"

if ! command -v mpv >/dev/null && ! command -v ffplay >/dev/null \
   && ! command -v mpg123 >/dev/null && ! command -v cvlc >/dev/null; then
  echo
  echo "note: no audio player found. install one:"
  echo "      sudo apt install mpv        # recommended, gapless"
  echo "      sudo apt install mpg123     # lightest option"
fi

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo; echo "note: $BIN_DIR is not in PATH. add this to your ~/.bashrc:"
     echo "      export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
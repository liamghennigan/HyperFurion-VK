#!/usr/bin/env bash
# Build the download-and-run setup files attached to each release:
#
#   HyperFurion-VK-Setup.run  Linux: a self-extracting installer carrying
#                             this source tree; it runs install.sh (in a
#                             terminal window if it was double-clicked).
#   HyperFurion-VK-Setup.cmd  Windows: double-click to run the PowerShell
#                             installer (packaging/windows/install-hyperfurion-vk.ps1,
#                             which installs the release it is pinned to).
#
# Both install for the user who runs them and end in the settings
# walkthrough (`voice-keyboard setup`).
#
#   packaging/build-downloads.sh [OUT_DIR]     (default: dist)
#
# Run from a git checkout: the Linux archive is `git archive HEAD`, plus
# any uncommitted changes to install.sh / packaging (release.yml stamps the
# installers before building).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-dist}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$ROOT/pyproject.toml" | head -1)"

# ── Linux: self-extracting .run ──────────────────────────────────────
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
git -C "$ROOT" archive --format=tar HEAD | tar -x -C "$STAGE"
# The working copies win: the release job stamps the installers in place.
cp "$ROOT/install.sh" "$STAGE/install.sh"
cp -R "$ROOT/packaging/." "$STAGE/packaging/"
chmod +x "$STAGE/install.sh"

RUN="$OUT/HyperFurion-VK-Setup.run"
cat > "$RUN" <<'HEADER'
#!/bin/sh
# HyperFurion VK __VERSION__ — setup for Linux.
#
# Run it as yourself (not with sudo); it asks for your password only for
# the system steps (audio libraries, keyboard access):
#   sh HyperFurion-VK-Setup.run              install, then walk through settings
#   sh HyperFurion-VK-Setup.run --uninstall  remove it again
# Or make it executable (chmod +x) and double-click it.
set -eu

if [ "$(id -u)" -eq 0 ]; then
    echo "Run HyperFurion VK setup as yourself, not as root (no sudo)." >&2
    exit 1
fi

SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

# Double-clicked from a file manager: no terminal to ask questions in, so
# reopen in one (and keep it open at the end so the result can be read).
if [ ! -t 0 ] && [ -z "${HFVK_SETUP_IN_TERMINAL:-}" ] \
        && [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    export HFVK_SETUP_IN_TERMINAL=1
    for term in x-terminal-emulator gnome-terminal kgx konsole xfce4-terminal mate-terminal tilix alacritty kitty xterm; do
        command -v "$term" >/dev/null 2>&1 || continue
        case "$term" in
            gnome-terminal|kgx|tilix) exec "$term" -- sh "$SELF" "$@" ;;
            *) exec "$term" -e sh "$SELF" "$@" ;;
        esac
    done
fi

if ! command -v tar >/dev/null 2>&1; then
    echo "tar is required to unpack HyperFurion VK." >&2
    exit 1
fi

WORK="$(mktemp -d "${TMPDIR:-/tmp}/hyperfurion-vk-setup.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT INT TERM
LINE="$(awk '/^__HYPERFURION_VK_ARCHIVE__$/ { print NR + 1; exit }' "$SELF")"
tail -n +"$LINE" "$SELF" | tar -xz -C "$WORK"

set +e
bash "$WORK/install.sh" "$@"
STATUS=$?
set -e
if [ -n "${HFVK_SETUP_IN_TERMINAL:-}" ]; then
    printf "\nPress Enter to close this window."
    read -r _ || true
fi
exit "$STATUS"
__HYPERFURION_VK_ARCHIVE__
HEADER
sed -i "s/__VERSION__/v$VERSION/" "$RUN"
tar -C "$STAGE" -cz . >> "$RUN"
chmod +x "$RUN"
echo "built $RUN"

# ── Windows: double-clickable .cmd ───────────────────────────────────
# A batch/PowerShell polyglot: cmd runs the lines up to `exit /b` (the
# first line is a label to it), PowerShell reads that part as a block
# comment and runs the installer below it. The file's own path goes
# through an environment variable, so apostrophes and non-ASCII
# characters in it are safe.
CMD="$OUT/HyperFurion-VK-Setup.cmd"
{
    cat <<HEADER
<# : HyperFurion VK v$VERSION - setup for Windows. Double-click to install.
@echo off
setlocal
set "HFVK_SETUP_FILE=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "& ([ScriptBlock]::Create([IO.File]::ReadAllText(\$env:HFVK_SETUP_FILE)))"
set "HFVK_EXIT=%ERRORLEVEL%"
if not "%HYPERFURION_VK_NONINTERACTIVE%"=="1" pause
exit /b %HFVK_EXIT%
#>
HEADER
    cat "$ROOT/packaging/windows/install-hyperfurion-vk.ps1"
} | sed 's/\r$//; s/$/\r/' > "$CMD"
echo "built $CMD"

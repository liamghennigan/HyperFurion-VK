#!/usr/bin/env bash
# Remove a user-local HyperFurion VK install.
#   Default: systemd unit, venv, CLI links, GNOME overlay copy.
#   --purge   also delete config and local state
#   --system  also delete the curl-installer's udev / modules-load files
set -euo pipefail

PURGE=0
SYSTEM=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        --system) SYSTEM=1 ;;
        -h|--help)
            echo "Usage: $0 [--purge] [--system]"
            echo "  --purge    also remove ~/.config/voice-keyboard and state"
            echo "  --system   also remove installer udev/uinput files (needs sudo)"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 2
            ;;
    esac
done

if command -v voice-keyboard >/dev/null 2>&1; then
    extra=()
    [ "$PURGE" -eq 1 ] && extra+=(--purge)
    [ "$SYSTEM" -eq 1 ] && extra+=(--system)
    exec voice-keyboard uninstall "${extra[@]}"
fi

CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
OVERLAY_UUID="voice-keyboard-overlay@liam-hennigan"

echo "Uninstalling HyperFurion VK (user-local$([ "$PURGE" -eq 1 ] && echo ", purge"))..."

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now voice-keyboard-daemon.service >/dev/null 2>&1 || true
    rm -f "$CONFIG_HOME/systemd/user/voice-keyboard-daemon.service"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    echo "  stopped and disabled the user systemd unit (if present)"
fi

rm -f "$HOME/.local/bin/voice-keyboard" "$HOME/.local/bin/voice-keyboard-daemon"
rm -rf "$DATA_HOME/voice-keyboard-venv"
rm -rf "$DATA_HOME/gnome-shell/extensions/$OVERLAY_UUID"
echo "  removed CLI links, venv, and overlay copy (if present)"

if [ "$PURGE" -eq 1 ]; then
    rm -rf "$CONFIG_HOME/voice-keyboard" "$STATE_HOME/voice-keyboard"
    echo "  removed config and state"
fi

if [ "$SYSTEM" -eq 1 ]; then
    UDEV_FILE="/etc/udev/rules.d/99-uinput.rules"
    MODULES_FILE="/etc/modules-load.d/uinput.conf"
    if [ -f "$UDEV_FILE" ] && grep -qF 'KERNEL=="uinput", GROUP="input", MODE="0660"' "$UDEV_FILE"; then
        sudo rm -f "$UDEV_FILE"
        echo "  removed $UDEV_FILE"
    fi
    if [ -f "$MODULES_FILE" ] && grep -qx "uinput" "$MODULES_FILE"; then
        sudo rm -f "$MODULES_FILE"
        echo "  removed $MODULES_FILE"
    fi
    sudo udevadm control --reload-rules >/dev/null 2>&1 || true
    echo "  input group membership was left in place"
else
    echo "  left udev/uinput/input-group system files (pass --system to remove them)"
fi

echo "Done."

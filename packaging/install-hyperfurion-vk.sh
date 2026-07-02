#!/usr/bin/env bash
set -euo pipefail

REPO="${HYPERFURION_VK_REPO:-liamghennigan/HyperFurion-VK}"
VERSION="${HYPERFURION_VK_VERSION:-v1}"
ARCHIVE_URL="${HYPERFURION_VK_ARCHIVE_URL:-https://github.com/$REPO/archive/refs/tags/$VERSION.tar.gz}"
INSTALL_CACHE="${HYPERFURION_VK_INSTALL_CACHE:-$HOME/.cache/hyperfurion-vk-installer}"

echo "=== HyperFurion VK release installer ==="
echo "Repository: $REPO"
echo "Version:    $VERSION"
echo ""

if command -v curl >/dev/null 2>&1; then
    DOWNLOAD=(curl -fsSL "$ARCHIVE_URL")
elif command -v wget >/dev/null 2>&1; then
    DOWNLOAD=(wget -qO- "$ARCHIVE_URL")
else
    echo "ERROR: curl or wget is required to download HyperFurion VK." >&2
    exit 1
fi

if ! command -v tar >/dev/null 2>&1; then
    echo "ERROR: tar is required to unpack HyperFurion VK." >&2
    exit 1
fi

mkdir -p "$INSTALL_CACHE"
WORK_DIR="$(mktemp -d "$INSTALL_CACHE/source.XXXXXX")"
cleanup() {
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

echo "Downloading $ARCHIVE_URL"
"${DOWNLOAD[@]}" | tar -xz -C "$WORK_DIR" --strip-components=1

if [ ! -x "$WORK_DIR/install.sh" ]; then
    chmod +x "$WORK_DIR/install.sh"
fi

echo ""
echo "Running HyperFurion VK installer..."
exec "$WORK_DIR/install.sh"

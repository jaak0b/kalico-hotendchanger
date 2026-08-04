#!/bin/sh
# Symlink hotendchanger.py into a Kalico checkout's klippy/plugins/
# directory (see docs/design.md).
#
# Usage: ./install.sh [KALICO_DIR]
# KALICO_DIR can also be given via the KALICO_DIR environment variable.
# Defaults to $HOME/klipper, the documented clone location.

set -e

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUGIN_SRC="$SCRIPT_DIR/hotendchanger.py"

if [ ! -f "$PLUGIN_SRC" ]; then
    echo "install.sh: cannot find hotendchanger.py next to this script" >&2
    exit 1
fi

TARGET_DIR="${1:-${KALICO_DIR:-}}"

if [ -z "$TARGET_DIR" ]; then
    if [ -d "$HOME/klipper" ]; then
        TARGET_DIR="$HOME/klipper"
    else
        echo "install.sh: cannot find \$HOME/klipper; pass the Kalico directory as an argument or set KALICO_DIR" >&2
        exit 1
    fi
fi

if [ ! -d "$TARGET_DIR" ]; then
    echo "install.sh: Kalico directory not found: $TARGET_DIR" >&2
    exit 1
fi

# Kalico's module loader is klippy/printer.py scanning "klippy.plugins.";
# the plugins directory itself may not exist yet in a fresh checkout.
if [ -f "$TARGET_DIR/klippy/printer.py" ] \
        && grep -q 'klippy\.plugins' "$TARGET_DIR/klippy/printer.py"; then
    INSTALL_DIR="$TARGET_DIR/klippy/plugins"
else
    echo "install.sh: $TARGET_DIR is not a Kalico checkout: it has no klippy/printer.py loading klippy.plugins" >&2
    exit 1
fi

if [ ! -d "$INSTALL_DIR" ]; then
    mkdir -p "$INSTALL_DIR" || {
        echo "install.sh: failed to create $INSTALL_DIR" >&2
        exit 1
    }
    echo "install.sh: created $INSTALL_DIR"
fi

ln -sf "$PLUGIN_SRC" "$INSTALL_DIR/hotendchanger.py" || {
    echo "install.sh: failed to symlink into $INSTALL_DIR" >&2
    exit 1
}

echo "install.sh: linked $PLUGIN_SRC -> $INSTALL_DIR/hotendchanger.py"
echo "install.sh: restart the klippy service (or firmware restart) to load it"

#!/bin/sh
# Symlink the hotendchanger plugin modules into a Kalico checkout's
# klippy/plugins/ directory.
#
# Usage: ./install.sh [KALICO_DIR]
# KALICO_DIR can also be given via the KALICO_DIR environment variable.
# Defaults to $HOME/klipper, the usual location of the checkout the klippy
# service runs from.

set -e

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUGIN_FILES="hotendchanger.py hotendchanger_tool.py"

for f in $PLUGIN_FILES; do
    if [ ! -f "$SCRIPT_DIR/$f" ]; then
        echo "install.sh: cannot find $f next to this script" >&2
        exit 1
    fi
done

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

# klippy imports plugins as submodules of the klippy.plugins package, so the
# directory needs a package marker.
if [ ! -f "$INSTALL_DIR/__init__.py" ]; then
    : > "$INSTALL_DIR/__init__.py" || {
        echo "install.sh: failed to create $INSTALL_DIR/__init__.py" >&2
        exit 1
    }
    echo "install.sh: created $INSTALL_DIR/__init__.py"
fi

for f in $PLUGIN_FILES; do
    ln -sf "$SCRIPT_DIR/$f" "$INSTALL_DIR/$f" || {
        echo "install.sh: failed to symlink $f into $INSTALL_DIR" >&2
        exit 1
    }
    echo "install.sh: linked $SCRIPT_DIR/$f -> $INSTALL_DIR/$f"
done
echo "install.sh: restart the klippy service (or firmware restart) to load it"

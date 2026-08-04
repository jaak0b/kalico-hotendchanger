#!/bin/sh
# Symlink the hotendchanger plugin modules into a firmware checkout's
# module directory: klippy/plugins/ on Kalico, klippy/extras/ on stock
# Klipper.
#
# Usage: ./install.sh [FIRMWARE_DIR]
# FIRMWARE_DIR can also be given via the KALICO_DIR environment variable.
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
        echo "install.sh: cannot find \$HOME/klipper; pass the firmware directory as an argument or set KALICO_DIR" >&2
        exit 1
    fi
fi

if [ ! -d "$TARGET_DIR" ]; then
    echo "install.sh: firmware directory not found: $TARGET_DIR" >&2
    exit 1
fi

# Kalico's module loader is klippy/printer.py scanning "klippy.plugins."
# (printer.py:282-286); the plugins directory itself may not exist yet in a
# fresh checkout. Stock Klipper has no printer.py and klippy/klippy.py:93-103
# loads only from klippy/extras/, no package marker needed there.
if [ -f "$TARGET_DIR/klippy/printer.py" ] \
        && grep -q 'klippy\.plugins' "$TARGET_DIR/klippy/printer.py"; then
    INSTALL_DIR="$TARGET_DIR/klippy/plugins"
    NEED_MARKER=yes
elif [ -f "$TARGET_DIR/klippy/klippy.py" ] \
        && [ -d "$TARGET_DIR/klippy/extras" ]; then
    INSTALL_DIR="$TARGET_DIR/klippy/extras"
    NEED_MARKER=no
    echo "install.sh: $TARGET_DIR is a stock Klipper checkout, which loads modules only from klippy/extras/"
    echo "install.sh: the symlinks are untracked files there, so git and Moonraker's update manager will report the checkout as dirty until they are removed"
else
    echo "install.sh: $TARGET_DIR is neither a Kalico nor a stock Klipper checkout: it has no klippy/printer.py loading klippy.plugins, and no klippy/klippy.py with a klippy/extras/ directory" >&2
    exit 1
fi

if [ ! -d "$INSTALL_DIR" ]; then
    mkdir -p "$INSTALL_DIR" || {
        echo "install.sh: failed to create $INSTALL_DIR" >&2
        exit 1
    }
    echo "install.sh: created $INSTALL_DIR"
fi

# klippy imports plugins as submodules of the klippy.plugins package, so
# that directory needs a package marker.
if [ "$NEED_MARKER" = yes ] && [ ! -f "$INSTALL_DIR/__init__.py" ]; then
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

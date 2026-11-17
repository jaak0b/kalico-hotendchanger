#!/bin/sh
# Symlink the hotendchanger plugin modules into a firmware checkout's
# module directory: klippy/plugins/ on Kalico, klippy/extras/ on stock
# Klipper. Optionally install the filament service as a systemd unit.
#
# Usage: ./install.sh [--filament-service | --no-filament-service]
#                     [--printer-data DIR] [FIRMWARE_DIR]
# FIRMWARE_DIR can also be given via the KALICO_DIR environment variable.
# Defaults to $HOME/klipper, the usual location of the checkout the klippy
# service runs from. --printer-data defaults to $HOME/printer_data.

set -e

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUGIN_FILES="hotendchanger.py hotendchanger_tool.py"
SERVICE_NAME=hotendchanger-filament
SERVICE_DIR="$SCRIPT_DIR/service"
SERVICE_FILES="hotendchanger_filament.py filament_library.py moonraker_agent.py $SERVICE_NAME.service"

usage() {
    echo "Usage: $0 [--filament-service | --no-filament-service] [--printer-data DIR] [FIRMWARE_DIR]"
}

COMPONENTS=
TARGET_ARG=
PRINTER_DATA_DIR=
while [ $# -gt 0 ]; do
    case "$1" in
        --filament-service)
            COMPONENTS=service
            ;;
        --no-filament-service)
            COMPONENTS=core
            ;;
        --printer-data)
            if [ $# -lt 2 ]; then
                echo "install.sh: --printer-data needs a directory" >&2
                exit 1
            fi
            shift
            PRINTER_DATA_DIR="$1"
            ;;
        --printer-data=*)
            PRINTER_DATA_DIR="${1#--printer-data=}"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "install.sh: unknown option $1" >&2
            usage >&2
            exit 1
            ;;
        *)
            if [ -n "$TARGET_ARG" ]; then
                echo "install.sh: more than one firmware directory given" >&2
                usage >&2
                exit 1
            fi
            TARGET_ARG="$1"
            ;;
    esac
    shift
done

if [ -z "$COMPONENTS" ]; then
    if [ ! -t 0 ]; then
        echo "install.sh: pass --filament-service or --no-filament-service when running non-interactively" >&2
        exit 1
    fi
    echo "Choose what to install:"
    echo "  1) core plugin only"
    echo "  2) core plugin and filament service"
    printf "Selection [1 or 2]: "
    read -r answer
    case "$answer" in
        1) COMPONENTS=core ;;
        2) COMPONENTS=service ;;
        *)
            echo "install.sh: no valid selection made, nothing installed" >&2
            exit 1
            ;;
    esac
fi

for f in $PLUGIN_FILES; do
    if [ ! -f "$SCRIPT_DIR/$f" ]; then
        echo "install.sh: cannot find $f next to this script" >&2
        exit 1
    fi
done

if [ "$COMPONENTS" = service ]; then
    for f in $SERVICE_FILES; do
        if [ ! -f "$SERVICE_DIR/$f" ]; then
            echo "install.sh: cannot find service/$f next to this script" >&2
            exit 1
        fi
    done
    if [ "$(id -u)" = 0 ]; then
        echo "install.sh: run this script as the user that runs Moonraker, not as root; the service runs as the installing user" >&2
        exit 1
    fi
    PYTHON=$(command -v python3) || {
        echo "install.sh: python3 not found; the filament service needs Python 3.7 or newer" >&2
        exit 1
    }
    if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 7))'; then
        echo "install.sh: $PYTHON is older than Python 3.7, which the filament service needs" >&2
        exit 1
    fi
    if [ -z "$PRINTER_DATA_DIR" ]; then
        if [ -d "$HOME/printer_data" ]; then
            PRINTER_DATA_DIR="$HOME/printer_data"
        else
            echo "install.sh: cannot find \$HOME/printer_data; pass Moonraker's data directory with --printer-data" >&2
            exit 1
        fi
    fi
    ASVC_FILE="$PRINTER_DATA_DIR/moonraker.asvc"
    # Moonraker creates moonraker.asvc with its default service list on
    # first start (moonraker/components/machine.py:184-193); writing it
    # ourselves would drop those defaults.
    if [ ! -f "$ASVC_FILE" ]; then
        echo "install.sh: $ASVC_FILE not found; start Moonraker once so it creates the file, or pass the right directory with --printer-data" >&2
        exit 1
    fi
    SOCKET_PATH="$PRINTER_DATA_DIR/comms/moonraker.sock"
fi

TARGET_DIR="${TARGET_ARG:-${KALICO_DIR:-}}"

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

if [ "$COMPONENTS" = core ]; then
    echo "install.sh: restart the klippy service (or firmware restart) to load it"
    exit 0
fi

sed_escape() {
    printf '%s' "$1" | sed -e 's/[\\|&]/\\&/g'
}

UNIT_TMP=$(mktemp)
trap 'rm -f "$UNIT_TMP"' EXIT
sed -e "s|@USER@|$(sed_escape "$(id -un)")|" \
    -e "s|@PYTHON@|$(sed_escape "$PYTHON")|" \
    -e "s|@SERVICE_DIR@|$(sed_escape "$SERVICE_DIR")|" \
    -e "s|@SOCKET@|$(sed_escape "$SOCKET_PATH")|" \
    "$SERVICE_DIR/$SERVICE_NAME.service" > "$UNIT_TMP"

echo "install.sh: installing /etc/systemd/system/$SERVICE_NAME.service (needs sudo)"
sudo install -m 644 "$UNIT_TMP" "/etc/systemd/system/$SERVICE_NAME.service" || {
    echo "install.sh: failed to install the $SERVICE_NAME systemd unit" >&2
    exit 1
}
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
echo "install.sh: started $SERVICE_NAME; check it with: systemctl status $SERVICE_NAME"

if grep -qx "$SERVICE_NAME" "$ASVC_FILE"; then
    echo "install.sh: $ASVC_FILE already lists $SERVICE_NAME"
else
    if [ -s "$ASVC_FILE" ] && [ -n "$(tail -c 1 "$ASVC_FILE")" ]; then
        printf '\n' >> "$ASVC_FILE"
    fi
    printf '%s\n' "$SERVICE_NAME" >> "$ASVC_FILE" || {
        echo "install.sh: failed to add $SERVICE_NAME to $ASVC_FILE" >&2
        exit 1
    }
    echo "install.sh: added $SERVICE_NAME to $ASVC_FILE"
    echo "install.sh: restart Moonraker so it reads the updated $ASVC_FILE"
fi

echo "install.sh: restart the klippy service (or firmware restart) to load the plugin"

#!/bin/sh
# Symlink the hotendchanger plugin modules into a firmware checkout's
# module directory: klippy/plugins/ on Kalico, klippy/extras/ on stock
# Klipper. Optionally install the filament service as a systemd unit and
# the web page into the Mainsail nginx site.
#
# Usage: ./install.sh [--filament-service | --no-filament-service]
#                     [--printer-data DIR] [--nginx-site FILE]
#                     [--web-url URL] [FIRMWARE_DIR]
# FIRMWARE_DIR can also be given via the KALICO_DIR environment variable.
# Defaults to $HOME/klipper, the usual location of the checkout the klippy
# service runs from. --printer-data defaults to $HOME/printer_data.
# --nginx-site defaults to /etc/nginx/sites-available/mainsail, where KIAUH
# (kiauh/components/webui_client/mainsail_data.py:45) and MainsailOS
# (modules/generic/52-mainsail:34) install the Mainsail site.
# --web-url is the address the web page is opened with in the browser; the
# filament service prints it to the console each time Klipper becomes ready.
# Without it the script asks, suggesting http://<hostname>/hotendchanger/.

set -e

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PLUGIN_FILES="hotendchanger.py hotendchanger_tool.py"
SERVICE_NAME=hotendchanger-filament
SERVICE_DIR="$SCRIPT_DIR/service"
SERVICE_FILES="hotendchanger_filament.py filament_library.py moonraker_agent.py $SERVICE_NAME.service"
WEB_DIR="$SCRIPT_DIR/web"
WEB_URL_PATH=/hotendchanger/
NGINX_SNIPPET_TEMPLATE="$WEB_DIR/nginx-hotendchanger.conf"
NGINX_SNIPPET=/etc/nginx/snippets/hotendchanger.conf
NAVI_TITLE=Hotendchanger
# mdiPrinter3dNozzle from @mdi/js 7.4.47; Mainsail's sidebar renders an SVG
# path string as the icon (mainsail src/components/ui/SidebarItem.vue:14).
NAVI_ICON='M7,2H17V8H19V13H16.5L13,17H11L7.5,13H5V8H7V2M10,22H2V20H10A1,1 0 0,0 11,19V18H13V19A3,3 0 0,1 10,22Z'

usage() {
    echo "Usage: $0 [--filament-service | --no-filament-service] [--printer-data DIR] [--nginx-site FILE] [--web-url URL] [FIRMWARE_DIR]"
}

COMPONENTS=
TARGET_ARG=
PRINTER_DATA_DIR=
NGINX_SITE=/etc/nginx/sites-available/mainsail
WEB_URL=
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
        --nginx-site)
            if [ $# -lt 2 ]; then
                echo "install.sh: --nginx-site needs a file" >&2
                exit 1
            fi
            shift
            NGINX_SITE="$1"
            ;;
        --nginx-site=*)
            NGINX_SITE="${1#--nginx-site=}"
            ;;
        --web-url)
            if [ $# -lt 2 ]; then
                echo "install.sh: --web-url needs a URL" >&2
                exit 1
            fi
            shift
            WEB_URL="$1"
            ;;
        --web-url=*)
            WEB_URL="${1#--web-url=}"
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
    echo "  2) core plugin, filament service and web page"
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

if [ "$COMPONENTS" = core ] && [ -n "$WEB_URL" ]; then
    echo "install.sh: --web-url applies only with --filament-service, which installs the web page" >&2
    exit 1
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
    for f in index.html nginx-hotendchanger.conf; do
        if [ ! -f "$WEB_DIR/$f" ]; then
            echo "install.sh: cannot find web/$f next to this script" >&2
            exit 1
        fi
    done
    if [ ! -f "$NGINX_SITE" ]; then
        echo "install.sh: nginx site file $NGINX_SITE not found; pass the file of the site that serves Mainsail with --nginx-site" >&2
        exit 1
    fi
    CONFIG_DIR="$PRINTER_DATA_DIR/config"
    if [ ! -d "$CONFIG_DIR" ]; then
        echo "install.sh: $CONFIG_DIR not found; pass Moonraker's data directory with --printer-data" >&2
        exit 1
    fi
    if [ -z "$WEB_URL" ]; then
        if [ ! -t 0 ]; then
            echo "install.sh: pass --web-url with the address you will open the web page with, such as http://<printer>$WEB_URL_PATH, when running non-interactively" >&2
            exit 1
        fi
        WEB_URL_SUGGESTION="http://$(hostname)$WEB_URL_PATH"
        echo "Enter the address you will open the web page with in your browser."
        echo "It is your Mainsail address followed by $WEB_URL_PATH"
        printf "Web page address [press Enter for %s]: " "$WEB_URL_SUGGESTION"
        read -r WEB_URL
        WEB_URL=${WEB_URL:-$WEB_URL_SUGGESTION}
    fi
    "$PYTHON" - "$SERVICE_DIR" "$WEB_URL" <<'WEB_URL_PY' || exit 1
import sys

sys.path.insert(0, sys.argv[1])
import filament_library

try:
    filament_library.check_web_url(sys.argv[2])
except filament_library.LibraryError as e:
    sys.exit("install.sh: %s Pass the address with --web-url." % (e,))
WEB_URL_PY
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

# systemd expands % specifiers and $ variables inside ExecStart arguments,
# even in double quotes (systemd.service(5) "Command lines").
systemd_escape() {
    printf '%s' "$1" | sed -e 's/%/%%/g' -e 's/\$/$$/g'
}

UNIT_TMP=$(mktemp)
trap 'rm -f "$UNIT_TMP"' EXIT
sed -e "s|@USER@|$(sed_escape "$(id -un)")|" \
    -e "s|@PYTHON@|$(sed_escape "$PYTHON")|" \
    -e "s|@SERVICE_DIR@|$(sed_escape "$SERVICE_DIR")|" \
    -e "s|@SOCKET@|$(sed_escape "$SOCKET_PATH")|" \
    -e "s|@WEB_URL@|$(sed_escape "$(systemd_escape "$WEB_URL")")|" \
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

# nginx runs its workers as the user named in nginx.conf, or as nobody when
# the directive is absent (nginx core module documentation, "user").
NGINX_USER=$(sed -n 's/^[[:space:]]*user[[:space:]]\{1,\}\([^[:space:];]\{1,\}\).*/\1/p' /etc/nginx/nginx.conf | head -n 1)
NGINX_USER=${NGINX_USER:-nobody}
if ! sudo -u "$NGINX_USER" test -r "$WEB_DIR/index.html"; then
    echo "install.sh: nginx (user $NGINX_USER) cannot read $WEB_DIR/index.html, so the web page was not installed" >&2
    echo "install.sh: give $NGINX_USER execute access to every directory above $WEB_DIR and read access to its files; inspect the path with: namei -l $WEB_DIR/index.html" >&2
    exit 1
fi

SNIPPET_TMP=$(mktemp)
SITE_BACKUP=$(mktemp)
SITE_TMP=$(mktemp)
SNIPPET_BACKUP=$(mktemp)
trap 'rm -f "$UNIT_TMP" "$SNIPPET_TMP" "$SITE_BACKUP" "$SITE_TMP" "$SNIPPET_BACKUP"' EXIT
sed -e "s|@WEB_DIR@|$(sed_escape "$WEB_DIR")|" "$NGINX_SNIPPET_TEMPLATE" > "$SNIPPET_TMP"
sudo cat "$NGINX_SITE" > "$SITE_BACKUP"
SNIPPET_EXISTED=no
if sudo test -f "$NGINX_SNIPPET"; then
    SNIPPET_EXISTED=yes
    sudo cat "$NGINX_SNIPPET" > "$SNIPPET_BACKUP"
fi

revert_nginx() {
    sudo tee "$NGINX_SITE" < "$SITE_BACKUP" > /dev/null
    if [ "$SNIPPET_EXISTED" = yes ]; then
        sudo tee "$NGINX_SNIPPET" < "$SNIPPET_BACKUP" > /dev/null
    else
        sudo rm -f "$NGINX_SNIPPET"
    fi
}

echo "install.sh: installing $NGINX_SNIPPET (needs sudo)"
sudo install -D -m 644 "$SNIPPET_TMP" "$NGINX_SNIPPET" || {
    revert_nginx
    echo "install.sh: failed to install $NGINX_SNIPPET" >&2
    exit 1
}

INCLUDE_LINE="include $NGINX_SNIPPET;"
if grep -qF "$INCLUDE_LINE" "$SITE_BACKUP"; then
    echo "install.sh: $NGINX_SITE already includes $NGINX_SNIPPET"
else
    if ! awk -v line="    $INCLUDE_LINE" '
        { print }
        !done && /^[[:space:]]*server[[:space:]]*\{/ { print line; done = 1 }
        END { exit !done }
    ' "$SITE_BACKUP" > "$SITE_TMP"; then
        revert_nginx
        echo "install.sh: $NGINX_SITE has no 'server {' line to add the include to; pass the Mainsail site file with --nginx-site" >&2
        exit 1
    fi
    if ! sudo tee "$NGINX_SITE" < "$SITE_TMP" > /dev/null; then
        revert_nginx
        echo "install.sh: failed to write $NGINX_SITE" >&2
        exit 1
    fi
    echo "install.sh: added '$INCLUDE_LINE' to the server block of $NGINX_SITE"
fi

if ! sudo nginx -t; then
    revert_nginx
    echo "install.sh: nginx rejected the configuration (see its output above); $NGINX_SITE and $NGINX_SNIPPET were restored to their previous state" >&2
    exit 1
fi
sudo systemctl reload nginx || {
    echo "install.sh: nginx accepted the configuration but did not reload; check it with: systemctl status nginx" >&2
    exit 1
}
echo "install.sh: web page installed at $WEB_URL"

NAVI_FILE="$CONFIG_DIR/.theme/navi.json"
# Mainsail reads custom sidebar links from .theme/navi.json in the config
# directory as a JSON list of {title, icon, href, target, position}
# (mainsail src/components/mixins/navigation.ts:9-19 and :127-152).
"$PYTHON" - "$NAVI_FILE" "$NAVI_TITLE" "$WEB_URL_PATH" "$NAVI_ICON" <<'NAVI_PY' || exit 1
import json
import os
import sys

path, title, href, icon = sys.argv[1:5]
entries = []
if os.path.exists(path):
    try:
        with open(path) as f:
            entries = json.load(f)
    except ValueError as e:
        sys.exit("install.sh: %s is not valid JSON (%s); fix or remove it, then rerun" % (path, e))
    if not isinstance(entries, list):
        sys.exit("install.sh: %s does not hold a JSON list; fix or remove it, then rerun" % (path,))
entry = {"title": title, "href": href, "icon": icon}
ours = [e for e in entries if isinstance(e, dict) and e.get("href") == href]
for e in ours:
    e.update(entry)
if not ours:
    entries.append(entry)
os.makedirs(os.path.dirname(path), exist_ok=True)
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(entries, f, indent=4)
    f.write("\n")
os.replace(tmp, path)
print("install.sh: %s the %s link in %s" % ("updated" if ours else "added", title, path))
NAVI_PY
echo "install.sh: reload Mainsail in the browser to see the $NAVI_TITLE sidebar link"

echo "install.sh: restart the klippy service (or firmware restart) to load the plugin"

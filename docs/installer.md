# Installer

`install.sh` installs the plugin. Run it from the repository root:

```
sh install.sh
```

## Questions

The installer asks two questions. Press Enter to accept the default:

- **Install the filament service and web page as well?** Default: yes.
- **Add an update entry to moonraker.conf?** Default: yes. Mainsail's
  update screen can then update this plugin.

## What it does

It then does the following:

- Symlinks `hotendchanger.py` and `hotendchanger_tool.py` into the firmware
  checkout at `~/klipper`: into `klippy/plugins/` on Kalico, into
  `klippy/extras/` on stock Klipper.
- Runs the [first install setup](#first-install-setup) when no
  hotendchanger configuration exists yet.
- With the filament service: installs the systemd unit
  `hotendchanger-filament`, adds it to `moonraker.asvc`, and installs the
  [web page](web-page.md#what-the-installer-changes) with its
  Mainsail sidebar entry.
- With the update entry: saves `moonraker.conf.<date>-<time>.bak`, then adds
  `[update_manager hotendchanger]`, or `[update_manager hotendchanger-filament]`
  when the filament service is installed. The entry follows the branch and
  `origin` remote of this checkout. An existing section of either name is
  left unchanged.
- Restarts what it changed: Moonraker (only if `moonraker.conf` or
  `moonraker.asvc` changed), then Klipper, then the filament service.

## Restart behaviour

**If a print is running or paused, nothing is restarted.** The installer
prints the restart commands instead; run them after the print. It does the
same when it cannot reach Moonraker.

## Stock Klipper

On stock Klipper the symlinks are untracked files, so git and Moonraker's

## Flags

| Flag | Effect |
| --- | --- |
| `--filament-service` / `--no-filament-service` | Answer the filament service question. One of them is required when the script runs non-interactively. |
| `--update-manager` / `--no-update-manager` | Answer the update entry question. One of them is required when the script runs non-interactively. |
| `--setup` | Run the first install setup again and replace `hotendchanger.cfg`. It asks first and keeps a dated backup. |
| `--printer-data DIR` | Moonraker's data directory. Default: `~/printer_data`. |
| `--nginx-site FILE` | The nginx site file that serves Mainsail. Default: `/etc/nginx/sites-available/mainsail`. |
| `FIRMWARE_DIR` | The Kalico or Klipper checkout. Default: `~/klipper`. |

## Manual update entry

For a manual setup without the filament service, this is the update entry
the installer writes. The entry with the filament service is in
[filament-service-api.md](filament-service-api.md#install-and-update-entry).

```
[update_manager hotendchanger]
type: git_repo
path: ~/kalico-hotendchanger
origin: https://github.com/jaak0b/kalico-hotendchanger
primary_branch: main
managed_services: klipper
```

## First install setup

When no hotendchanger configuration exists, the installer asks:

- the number of tools;
- the extruder section of each tool after T0 (T0 always uses `[extruder]`):
  one already in your config, or a new heater-only `[extruderN]`;
- whether the docks have sensors, and whether the toolhead has one.

It writes `hotendchanger.cfg` into the config directory with `[hotendchanger]`,
starter `pickup_gcode` and `dropoff_gcode` templates, one
`[hotendchanger_tool T<n>]` per tool, and a section skeleton for each new
extruder section. It adds `[include hotendchanger.cfg]` to `printer.cfg` and
keeps a dated backup of `printer.cfg`.

**Fill in the pins, dock positions and hotend values yourself.** The setup
leaves them commented out and prints which options are missing in which
section. Klipper reports a config error until every new extruder section is
filled in. Uncomment an option only after filling in its value: a blank
uncommented value does not parse.

The setup is skipped when `hotendchanger.cfg` exists, when a
`[hotendchanger]` or `[hotendchanger_tool]` section is found in `printer.cfg`,
its included files or the config Klipper has loaded, and when the script
runs non-interactively. `--setup` refuses while hotendchanger sections exist
in any file other than `hotendchanger.cfg`.

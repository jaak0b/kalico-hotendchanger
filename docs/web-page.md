# Web page

The filament service install also serves a page at
`http://<printer>/hotendchanger/` and adds a Hotendchanger entry to
Mainsail's sidebar. Reload Mainsail once after installing to make the entry
appear.

The page has no login. Add the network you browse from to `trusted_clients`
in the `[authorization]` section of `moonraker.conf`, because Moonraker
refuses the page from any other network.

## Layout

- The connection state of Moonraker, Klipper and the filament service.
- One card per configured tool, showing the nozzle temperature, the loaded
  filament and its color. The header holds the tool name, the
  presence chip and the nozzle temperature as current / target (to
  0.1 °C). Below it: the loaded filament with a color dot, a filament
  selector, and the buttons **Heat** (to the filament's nozzle
  temperature), **Off** and **Mount** (`T<n>`). The mounted tool's card
  shows **Dock** (`UNSELECT_TOOL`) instead of **Mount**. Each button has
  a tooltip.
- The info button at the end of the button row opens the raw values:
  Changer state, Dock pin, Toolhead pin and the nozzle heater name.
- The filament library, with add, edit and delete, across the full window
  width.

All cards have the same width, the narrowest that keeps the buttons on
one row. Cards start at the left edge, as many per row as the window fits,
and continue on the next row. On a screen narrower than one card, the card
takes the full width.

Choosing a filament in a card's selector opens a color picker; the tool
changes only when you save a color. Click the color dot to change only
that tool's color.

Mount and Dock are disabled while a print is running or paused, and during
a tool change; the card names the reason. Errors from Klipper and from the
filament service appear next to the button or field that caused them.

Press Escape or click the close button in the top bar to return to
Mainsail. While a dialog or menu is open, Escape closes only that dialog or
menu.

## Presence chip

The chip in the card header shows where the plugin sees the tool. It shows
the plugin's `presence` value for the tool; the page does not derive it. The
exact rules are in [how-it-works.md](how-it-works.md#tool-presence).

| Chip | Color | Presence | Meaning |
| --- | --- | --- | --- |
| Mounted | Primary | `mounted` | The tool is on the carriage. |
| Docked | Green | `docked` | Its dock sensor sees it in the dock. |
| Not mounted | Gray | `not_mounted` | It is not on the carriage. It has no dock sensor to confirm it is in the dock. |
| Missing | Red | `missing` | Its dock sensor is empty and the tool is not on the carriage. |
| Conflict | Red | `conflict` | The tool is the active tool, but its dock sensor reports it docked. Check the hotend, then run `INITIALIZE_HOTENDCHANGER`. |
| Changing | Blue | `changing` | A tool change is running and the tool is not in its dock. |
| Unknown | Yellow | `unknown` | A hotend may be on the carriage and the plugin does not know which. Run `INITIALIZE_HOTENDCHANGER`. |
| The value itself | Red | Any other value, including `invalid` or a missing value. | An unexpected value; the cause is in `klippy.log`. |

## Offline assets

The page loads nothing from the internet: Vue 3.5.44, Vuetify 3.13.7, the
Roboto font and a subset of Material Design Icons ship in `web/vendor/`
with their licenses.

## What the installer changes

- It writes `/etc/nginx/snippets/hotendchanger.conf`, which serves `web/`
  from this checkout.
- It adds `include /etc/nginx/snippets/hotendchanger.conf;` to the
  `server` block of `/etc/nginx/sites-available/mainsail`, where KIAUH and
  MainsailOS install the Mainsail site. Pass `--nginx-site FILE` for a
  different site file. If `nginx -t` rejects the result, both files are
  restored and the installer stops.
- It adds the sidebar entry to `<printer_data>/config/.theme/navi.json`,
  keeping existing entries.

nginx must be able to read `web/`. If it cannot, the installer stops and
names the file. Check the permissions along the path with
`namei -l ~/kalico-hotendchanger/web/index.html`.

## Removing the page

1. Delete the line `include /etc/nginx/snippets/hotendchanger.conf;` from
   the Mainsail site file.
2. Run `sudo rm /etc/nginx/snippets/hotendchanger.conf`, then
   `sudo nginx -t && sudo systemctl reload nginx`.
3. Delete the Hotendchanger entry from
   `~/printer_data/config/.theme/navi.json`.

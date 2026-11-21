# Filament service and API

The filament service is an optional process that records which filament
each tool holds and publishes it for OrcaSlicer. Setup and the OrcaSlicer
steps are in the [README](../README.md#orcaslicer-filament-sync).

## Architecture

- The library lives in Moonraker's database, namespace `hotendchanger`.
- The service publishes each tool's filament to Moonraker's `lane_data`
  database namespace, which OrcaSlicer reads to fill its filament list:
  slot N is tool N. A tool without a filament is published as an empty
  slot, so the numbering stays aligned.
- A library filament is a product: a name, a material and a nozzle
  temperature. The color belongs to the tool that holds the filament, so
  one product in two colors is one library entry assigned to two tools.
- The list of tools always comes from the `[hotendchanger_tool]` sections
  Klipper has loaded. While Klipper is not ready, assigning a filament is
  refused and `lane_data` is left unchanged.
- The service connects to Moonraker as an agent and serves the web page
  and other programs through Moonraker's `/server/extensions/request`.

Libraries saved by earlier versions are converted when the service
starts: each tool keeps the color of the filament it held, and the color
is removed from every library filament. A filament no tool held loses its
color.

## Install and update entry

```
cd ~/kalico-hotendchanger
sh install.sh --filament-service
```

The installer installs the systemd unit `hotendchanger-filament`, which
runs the service from this checkout with the system `python3` (3.7 or
newer, no third-party packages). It connects to Moonraker through
`~/printer_data/comms/moonraker.sock`; pass `--printer-data DIR` if
Moonraker's data directory is elsewhere. The installer also adds the unit
to `moonraker.asvc` so Moonraker may restart it, and installs the
[web page](web-page.md). Check the service with
`systemctl status hotendchanger-filament`.

With the service installed, the installer writes this update entry, so an
update restarts both Klipper and the service. For a manual setup, use it in
place of the `[update_manager hotendchanger]` entry:

```
[update_manager hotendchanger-filament]
type: git_repo
path: ~/kalico-hotendchanger
origin: https://github.com/jaak0b/kalico-hotendchanger
primary_branch: main
managed_services: klipper hotendchanger-filament
```

Moonraker accepts only the section's own name, `klipper` and `moonraker`
in `managed_services`, so the section must be named after the service.

## Console commands

The `HOTENDCHANGER_FILAMENT_*` commands are registered by the Klipper
plugin, which forwards them to the service; the service prints the result
in the console.

- If the service is not connected, the command fails with an error naming
  the unit to check.
- If no reply arrives within `filament_reply_timeout`, the console shows
  an error. A reply that comes later is still printed, marked as late.

## API

Other programs reach the service through Moonraker's
`/server/extensions/request` with `"agent": "hotendchanger"`, a `method`
and an `arguments` object.

Every reply is an object with exactly one key. Check for `error` first:

- `{"result": <payload>}`: the request succeeded; the payload is listed
  below.
- `{"error": "<message>"}`: the request was refused or failed; the message
  says why and what to do.

Moonraker returns this object as its own result, over HTTP and over the
websocket alike. A successful `filaments.delete` returns
`{"result": {"deleted": "3f2a9c"}}`; assigning to an unconfigured tool
returns `{"error": "Tool number 4 is not configured in Klipper.
Configured tool numbers: 0, 1, 2."}`.

| method | arguments | payload |
| --- | --- | --- |
| `filaments.list` | none | `{"filaments": [record, ...]}` |
| `filaments.create` | `name`, `material`, `nozzle_temp` | `{"filament": record}` |
| `filaments.update` | `id`, plus any of the three fields | `{"filament": record}` |
| `filaments.delete` | `id` | `{"deleted": id}` |
| `tools.list` | none | `{"tools": [{"number", "name", "filament": record or null, "color": color or null}, ...]}` |
| `tools.assign` | `tool` (number), `filament_id` (id or null), `color` (required with an id, absent with null) | `{"tool", "filament_id", "color"}` |
| `tools.set_color` | `tool` (number), `color` | `{"tool", "filament_id", "color"}` |

A record is `{"id", "name", "material", "nozzle_temp"}`. A color is given
as six hex digits, with or without a leading `#`, and stored as `#RRGGBB`
in upper case.

Refused requests:

- a `color` given to `filaments.create` or `filaments.update`;
- `tools.set_color` on a tool without a filament;
- deleting a filament that a tool still holds;
- assigning to a tool number missing from the Klipper config. Clearing is
  allowed, so an assignment left on a removed tool can still be cleared.

## Events

After every saved change, and whenever the list of configured tools
changes, the service sends the agent event `library_changed`. Moonraker
delivers it to other clients as `notify_agent_event` with
`{"agent": "hotendchanger", "event": "library_changed"}`. Reload
`tools.list` and `filaments.list` when it arrives.

# hotendchanger

A plugin for Kalico and stock Klipper supporting toolchangers that swap
only the hotend: heater block, thermistor, nozzle and hotend fan move
between docks, while the extruder motor and the part cooling fan stay
fixed on the carriage.

[![License: GPL v3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

- **Filament stays loaded.** Each docked hotend keeps its own filament, so
  a tool change is dock motion plus an electrical remap. No retract, no
  unload, no handoff.
- **Native heaters, not a parallel abstraction.** Every hotend is a
  standard `[extruderN]` section, so per-hotend PID tuning, `M104`/`M109`
  `T<n>` dispatch and Mainsail/Fluidd temperature display come from Kalico/Klipper
  itself, not from this plugin.

## How it works

Kalico/Klipper builds a heater-only extruder object for any `[extruderN]` section
configured without step pins. `[extruder]` is T0 and carries the real
stepper options; `[extruder1]` and up are the remaining tools with heater
and sensor options only. That single fact carries most of the design: each
hotend gets its own PID control, `PID_CALIBRATE`, `min_extrude_temp` and
web interface readout through existing Kalico/Klipper code, and a `[heater_fan]`
pointed at each `extruderN` ties every hotend fan to its own hotend's
temperature.

The plugin adds only what a toolchanger needs on top: `T<n>` commands that
run your dock motion templates, per-tool gcode offsets that replace only
the tool's own component of the gcode origin (your babystepping is
preserved), tracking of which tool is mounted, optional dock and toolhead
sensors to confirm it, and a wait for the new hotend to reach its target
temperature after pickup.

One motor drives all filament: only `[extruder]` (T0's section) has step
pins, and on every tool change the plugin re-syncs that stepper to the
active tool's extruder section with `SYNC_EXTRUDER_MOTION`, so extrusion
and E bookkeeping follow the active hotend. Whenever no tool is mounted
(including after any failed change) the stepper is detached, and
extrusion commands move nothing.

Pressure advance is per tool through the plugin: set `pressure_advance`
(and optionally `pressure_advance_smooth_time`) in each
`[hotendchanger_tool]` section, and the plugin programs the stepper at
every activation. Do not put `pressure_advance` in a heater-only
`[extruderN]` section; the firmware rejects it as an unused option.
Tools without their own value use `[extruder]`'s configured one.

## Tool change sequence

A `T<n>` command runs, in order:

1. Refuse unless the plugin may start a change (no change in progress,
   not in the error state). A `T<n>` addressed to the already active tool
   then reports it is already active and returns. Refuse unless XYZ is
   homed.
2. Run `before_change_gcode`.
3. Remove the old tool's gcode offset contribution from the gcode
   origin; babystepping stays.
4. Run `dropoff_gcode` for the mounted tool. Skipped when no tool is
   known to be mounted, since there is no dock to return it to. With
   detect pins, a reading that does not resolve to "no tool mounted"
   after dropoff fails the change the same way as step 6. A `T<n>` is
   refused up front when the toolhead pin reads a hotend of unknown
   identity (assert it first with `INITIALIZE_HOTENDCHANGER T=<n>`).
5. Run `pickup_gcode` for the new tool.
6. If detect pins are configured, verify the pickup: the dock pins must
   identify the new tool and the toolhead pin must read a mounted
   hotend, whichever exist. During a print, a failure prints one
   message naming each failed check and the raw readings, pauses the
   print (through `[pause_resume]`), and leaves the plugin in the error
   state; it does not raise, so the paused print stays resumable. With
   no print running, the same diagnostic is raised as a normal command
   error instead.
7. Run `ACTIVATE_EXTRUDER` for the new tool's extruder section, so bare
   `M104`/`M109`/`M105` and E axis bookkeeping follow it, and re-sync
   the extruder stepper to that section's motion queue.
8. If the new hotend has a nonzero target temperature, wait (via
   `TEMPERATURE_WAIT`) until it is within `temp_wait_tolerance` of that
   target. No target, no wait.
9. Apply the new tool's gcode offset and run `after_change_gcode`.

A change that fails partway leaves the plugin in the `error` state with
no active tool, because a hotend may still be on the carriage without the
plugin knowing which. Further `T<n>` commands are refused until
`INITIALIZE_HOTENDCHANGER` resolves the mounted tool: through the detect
pins, or on a pinless machine through your own `T=<n>` assertion.

## Commands

`T0` through `T<N-1>`: one command per configured tool, registered at
startup. Remove any `[gcode_macro Tn]` of the same name from your config;
the plugin reports the collision as a config error, because a printer with
both would have two competing definitions of the same command.

`SET_TOOL_OFFSET T=<n> [X=<x>] [Y=<y>] [Z=<z>] [SAVE=1]`: set a tool's
gcode offset at runtime. Axes left out keep their current value. If the
tool is active, the new offset is applied immediately. With `SAVE=1` the
values are staged so a subsequent `SAVE_CONFIG` persists them.

`HOTENDCHANGER_STATUS`: print labeled rows: active tool, detected tool,
state, each configured detect pin's reading, the toolhead detect pin's
reading, the extruder stepper's motion queue (`none` when detached), and
each tool's current offset and extruder section.

`INITIALIZE_HOTENDCHANGER [T=<n>]`: re-run detection from the configured
detect pins. Run it after moving or servicing a tool by hand, or to clear
the error state; it is refused while a change is in progress. Without
detect pins it resets state to unknown (the next `T<n>` runs pickup
only), or, with `T=<n>`, asserts that tool as the one mounted and applies
its offset. With detect pins configured `T=` is refused, since detection
determines the mounted tool.

Macros and the web interface can read `printer.hotendchanger`:
`active_tool` and `detected_tool` (tool numbers or null), `state`,
`toolhead_detect` (the toolhead pin reading, or null without the pin),
`stepper_motion_queue` (the extruder section the stepper is synced to,
or null when detached), and a `tools` dictionary keyed by tool name
(`"T0"` style) whose entries carry `number`, `extruder`,
`gcode_x_offset`, `gcode_y_offset`, `gcode_z_offset` and `detect` (the
pin reading, or null without a pin).

## Nozzle offset calibration

This plugin does not measure offsets; it only stores and applies them.
The companion plugin
[eddy_tool_calibration](https://github.com/jaak0b/kalico-eddy-offset-calibration)
measures per-tool XYZ offsets with an eddy-current coil and writes them
back through `SET_TOOL_OFFSET`.

## Install

Requires Kalico/Klipper and Python 3 with no third-party packages.

```
cd ~
git clone https://github.com/jaak0b/kalico-hotendchanger
cd kalico-hotendchanger
sh install.sh
sudo service klipper restart
```

`install.sh` symlinks `hotendchanger.py` and `hotendchanger_tool.py` into
the checkout at `~/klipper` (pass another path as an argument): into
`klippy/plugins/` when the checkout's loader supports it (Kalico), into
`klippy/extras/` otherwise (stock Klipper). On stock Klipper the symlinks
are untracked files, so git and Moonraker's update manager report the
checkout as dirty until they are removed. Update manager entry for
moonraker.conf:

```
[update_manager hotendchanger]
type: git_repo
path: ~/kalico-hotendchanger
origin: https://github.com/jaak0b/kalico-hotendchanger
primary_branch: main
is_system_service: False
```

## Config reference

One `[hotendchanger]` section, plus one `[hotendchanger_tool T<n>]`
section per tool, numbered `T0` upward with no gaps. Options shown
commented out may be left out. Blank values depend on your machine and
have no default.

```
[hotendchanger]
pickup_gcode:
#   A list of G-Code commands to pick up a hotend at its dock. This
#   parameter must be provided. The template is shared by all tools and
#   is evaluated using the standard command template expansion, with
#   the tool being picked up bound as "tool" (fields: number, name,
#   extruder, params) and its params_* values bound as "params".
dropoff_gcode:
#   A list of G-Code commands to drop off the mounted hotend at its
#   dock. This parameter must be provided. Same template context as
#   pickup_gcode, bound to the tool being dropped off.
#before_change_gcode:
#   A list of G-Code commands run at the start of a tool change, before
#   any offset or motion change. The template receives "old_tool" (or
#   None when no tool is mounted) and "new_tool". The default is to run
#   nothing.
#after_change_gcode:
#   A list of G-Code commands run after a tool change completes, once
#   the new offset is applied and the temperature wait, if any, has
#   finished. Same template context as before_change_gcode. The default
#   is to run nothing.
#temp_wait_tolerance: 2.0
#   Temperature window (in degrees Celsius) around the new tool's
#   target used after pickup. If the new hotend has a nonzero target,
#   the change waits until its temperature is within this value of the
#   target, in either direction, so a preheated hotend overshooting on
#   the way down completes the wait as soon as it re-enters the window.
#   Must be above 0. The default is 2.0.
#toolhead_detect_pin:
#   Endstop-style pin reading whether some hotend is mounted on the
#   toolhead; triggered means a hotend is held. The pin cannot tell
#   which tool. It may be used with or without per-tool detect_pin
#   sensors. Normalize polarity with the usual "!" prefix. The default
#   is no toolhead sensor.
#detect_settle_time: 1.0
#   Seconds. While printing, how long toolhead_detect_pin must read
#   untriggered continuously before the tool-loss response fires (a
#   contact bounce filter for the coupling under print vibration).
#   While no print is running, how long after the last detect pin
#   change a manual tool swap may settle before detection re-runs.
#   Must be above 0. The default is 1.0.
#detect_report_time: 0.05
#   Seconds the in-change pin checks wait after motion finishes for the
#   sensor report to arrive; a failed reading is re-read once after
#   another window before the change is declared failed. Raise this on
#   slow transports such as CAN. Must be above 0. The default is 0.05.

[hotendchanger_tool T0]
extruder:
#   Name of this tool's extruder section (for example "extruder" or
#   "extruder1"). The section must exist in the config. This parameter
#   must be provided.
#gcode_x_offset: 0
#gcode_y_offset: 0
#gcode_z_offset: 0
#   Gcode offset applied while this tool is active. The plugin replaces
#   only this tool component of the gcode origin, so babystepping
#   applied on top is preserved. SET_TOOL_OFFSET with SAVE=1 writes
#   measured values back into these options. The default is 0 on each
#   axis.
#detect_pin:
#   Endstop-style pin for this tool's dock sensor. Triggered means the
#   hotend is sitting in its dock; untriggered means it is not (it is
#   mounted on the carriage, or missing). Use the usual "!" prefix to
#   normalize your switch's polarity to that convention. Either every
#   tool sets a detect_pin or none does; a mix is a config error. The
#   default is no dock sensors.
#pressure_advance:
#pressure_advance_smooth_time:
#   Pressure advance used while this tool is active, programmed into
#   the extruder stepper at every activation. Left out, the tool uses
#   the values configured on the [extruder] section. Do not set
#   pressure_advance on a heater-only [extruderN] section; the firmware
#   rejects it as an unused option.
#params_dock_x:
#params_dock_y:
#params_dock_z:
#   Any option starting with "params_" defines a named value passed to
#   the pickup and dropoff templates as params.<name> for this tool.
#   Put dock coordinates and other per-printer values here; they have
#   no defaults the plugin can supply, and the dock position usually
#   differs per tool.
```

### Detect pin semantics

Detect pins are all-or-nothing: either every tool has one or none does,
so detection always reads every dock together. Exactly one untriggered
dock identifies that tool as mounted on the carriage; all docks
triggered means no tool is mounted; more than one untriggered dock is a
fault.

The optional `toolhead_detect_pin` reads whether some hotend is held on
the toolhead, without identifying which. Combined with dock pins, the
readings must agree (a contradiction is a fault). Alone, a triggered
reading means a mounted tool of unknown identity: run
`INITIALIZE_HOTENDCHANGER T=<n>` to name it, and note the command
refuses an assertion while the pin reads untriggered.

Detection runs automatically shortly after startup, so the active tool
is known before the first print. Startup detection and
`INITIALIZE_HOTENDCHANGER` are pure discovery: a fault there prints a
console message and sets state to `unknown`. Only the checks during a
`T<n>` change pause the print on a mismatch.

### Tool loss protection and manual swaps

With a `toolhead_detect_pin`, the plugin watches the coupling while a
print is running. If the pin reads no hotend for `detect_settle_time`
continuously, the plugin reports the loss once, turns off all heaters
(the failure cause is unknown, so extruders and bed all go cold),
detaches the extruder stepper, and then pauses the print. Re-seat the
hotend, run `INITIALIZE_HOTENDCHANGER` (with dock pins) or
`INITIALIZE_HOTENDCHANGER T=<n>` (without, to name the re-seated tool),
then `RESUME`.

While no print is running (idle or paused), detect pin changes trigger
automatic rediscovery `detect_settle_time` after the last change: pull a
tool off and seat another by hand and the plugin follows, applying the
new tool's offset and extruder, without `INITIALIZE_HOTENDCHANGER`. An
offset applied while a print is paused survives `RESUME`: the plugin
shifts the saved pause snapshot along with it.

### Example config

Commented-out lines are placeholders for machine-specific numbers: fill
in your own value when uncommenting, since a blank uncommented value
does not parse. If you use dock sensors, give every tool a `detect_pin`.

```
[hotendchanger]
pickup_gcode:
    G1 X{params.dock_x} Y{params.dock_y} F6000
    G1 Z{params.dock_z} F600
dropoff_gcode:
    G1 X{params.dock_x} Y{params.dock_y} F6000
    G1 Z{params.dock_z} F600

[hotendchanger_tool T0]
extruder: extruder
#detect_pin:
#params_dock_x:
#params_dock_y:
#params_dock_z:

[hotendchanger_tool T1]
extruder: extruder1
#gcode_x_offset: 0
#gcode_y_offset: 0
#gcode_z_offset: 0
#detect_pin:
#params_dock_x:
#params_dock_y:
#params_dock_z:

[extruder1]
#heater_pin:
#sensor_type:
#sensor_pin:
control: pid
#pid_Kp:
#pid_Ki:
#pid_Kd:
min_temp: 0
max_temp: 300

[heater_fan hotend1_fan]
#pin:
heater: extruder1
```

`[extruder]` (T0) is a normal full extruder section with step pins;
`[extruder1]` has no step pins, so Kalico/Klipper builds it as a heater-only
extruder.

## Limitations

Deliberate exclusions, not gaps to be filled later:

- No offset calibration (see the companion plugin above).
- No filament handling of any kind and no temperature policy beyond the
  pickup wait: no standby temperatures, no cooldown on idle.
- No crash or dropped-tool detection beyond the detect pin checks
  described above.
- No per-tool input shaper configuration, and one changer per printer.

## License

GNU GPLv3, see [LICENSE](LICENSE).

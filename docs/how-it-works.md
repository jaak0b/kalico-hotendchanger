# How it works

This page describes what the plugin does during a tool change and how it
reads its sensors. Setup and the config reference are in the
[README](../README.md).

## Native heaters, one extruder motor

The firmware builds a heater-only extruder object for any `[extruderN]`
section configured without step pins. `[extruder]` is T0 and carries the
real stepper options; `[extruder1]` and up are the remaining tools with
heater and sensor options only. Each hotend therefore gets its own PID
control, `PID_CALIBRATE`, `min_extrude_temp` and temperature readout in the
web interface from existing Kalico or Klipper code, and a `[heater_fan]`
pointed at each `extruderN` ties every hotend fan to its own hotend's
temperature.

The plugin adds only what a toolchanger needs on top: `T<n>` commands that
run your dock motion templates, per-tool gcode offsets, tracking of which
tool is mounted, optional dock and toolhead sensors to confirm it, and a
wait for the new hotend to reach its target temperature after pickup.

One motor drives all filament. Only `[extruder]` has step pins, and on
every tool change the plugin syncs that stepper to the active tool's
extruder section with `SYNC_EXTRUDER_MOTION`, so extrusion and E
bookkeeping follow the active hotend. Whenever no tool is mounted
(including after any failed change) the stepper is detached, and
extrusion commands move nothing.

Pressure advance is set per tool in `[hotendchanger_tool]` and programmed
into the stepper at every activation. Tools without their own value use
the value configured in `[extruder]`.

## Tool change sequence

A `T<n>` command runs, in order:

1. Refuse unless the plugin may start a change (no change in progress,
   not in the `error` state). A `T<n>` addressed to the already active
   tool then reports that it is already active and returns. Refuse unless
   X, Y and Z are homed.
2. Run `before_change_gcode`.
3. Remove the old tool's offset from the gcode origin; babystepping stays.
4. Run `dropoff_gcode` for the mounted tool. Skipped when no tool is
   known to be mounted, since there is no dock to return it to. With
   detect pins, a reading that does not resolve to "no tool mounted"
   after dropoff fails the change the same way as step 6. A `T<n>` is
   refused up front when the toolhead pin reads a hotend of unknown
   identity (declare it first with `INITIALIZE_HOTENDCHANGER T=<n>`).
5. Run `pickup_gcode` for the new tool.
6. If detect pins are configured, verify the pickup: the dock pins must
   identify the new tool and the toolhead pin must read a mounted hotend,
   whichever exist. During a print, a failure prints one message naming
   each failed check and the raw readings, pauses the print (through
   `[pause_resume]`), and leaves the plugin in the `error` state; it does
   not raise, so the paused print stays resumable. With no print running,
   the same diagnostic is raised as a normal command error instead.
7. Run `ACTIVATE_EXTRUDER` for the new tool's extruder section, so bare
   `M104`/`M109`/`M105` and E axis bookkeeping follow it, and sync the
   extruder stepper to that section's motion queue.
8. If the new hotend has a nonzero target temperature, wait (through
   `TEMPERATURE_WAIT`) until it is within `temp_wait_tolerance` of that
   target. No target, no wait.
9. Apply the new tool's gcode offset and run `after_change_gcode`.

Both sensor checks wait for queued motion to finish, then
`detect_report_time` for the sensor report. A failed reading is read once
more after another window before the change is declared failed.

`UNSELECT_TOOL` runs steps 1 to 4 with no new tool, then detaches the
extruder stepper and runs `after_change_gcode`. With detect pins, the
check after dropoff in step 4 is its verification. It is refused in the
same states as `T<n>`, and when a hotend may be mounted with no known
identity.

## States

The plugin's `state` is one of `uninitialized`, `ready`, `changing`,
`error` and `unknown`.

- `uninitialized`: between config load and the automatic detection
  shortly after startup. `T<n>` is refused.
- `ready`: the mounted tool is known, or the carriage is known to be
  empty (no active tool; a `T<n>` then skips dropoff).
- `unknown`: a hotend may be mounted and the plugin does not know which.
  Without detect pins the state starts here. A `T<n>` from `unknown` runs
  pickup only.
- `changing`: a tool change is running.
- `error`: a change failed partway, a declared tool contradicted the
  sensors, or the hotend was lost during a print.

A failed change leaves the plugin in `error` with no active tool and the
extruder stepper detached, because a hotend may still be on the carriage
without the plugin knowing which. Further `T<n>` commands are refused
until `INITIALIZE_HOTENDCHANGER` resolves the mounted tool: through the
detect pins, or on a machine without dock pins through `T=<n>`.

## Detect pin rules

Dock pins are all or nothing: either every tool has one or none does, so
detection always reads every dock together.

- Exactly one untriggered dock: that tool is mounted on the carriage.
- All docks triggered: no tool is mounted.
- More than one untriggered dock: a fault.

The optional `toolhead_detect_pin` reads whether some hotend is held on
the toolhead, without identifying which.

- With dock pins, the readings must agree; a contradiction in either
  direction is a fault.
- Alone, a triggered reading means a mounted tool of unknown identity:
  state becomes `unknown`, and `INITIALIZE_HOTENDCHANGER T=<n>` names it.
  An untriggered reading means no tool is mounted.
- `INITIALIZE_HOTENDCHANGER T=<n>` is refused while the toolhead pin
  reads untriggered.

Detection runs automatically shortly after startup, so the active tool is
known before the first print. Startup detection and
`INITIALIZE_HOTENDCHANGER` only discover: a fault there prints a console
message and sets the state to `unknown`. Only the checks during a `T<n>`
change pause the print on a mismatch.

## Tool presence

The plugin publishes one presence value per tool in
`printer.hotendchanger.tools.<name>.presence`. The first matching rule
applies; the rules are listed in evaluation order.

| Presence | Rule |
| --- | --- |
| `docked` or `changing` | A tool change is in progress: `docked` if this tool's dock pin is triggered, otherwise `changing`. |
| `conflict` or `mounted` | This is the active tool: `conflict` if its own dock pin is triggered, otherwise `mounted`. The toolhead pin is not consulted; a lost tool is reported by the plugin clearing the active tool. |
| `docked` | This is not the active tool and its dock pin is triggered. |
| `unknown` | No tool is active, and the toolhead pin is triggered or the state is `unknown` or `uninitialized`. |
| `missing` | This tool has a dock pin and it is not triggered. |
| `not_mounted` | This tool has no dock pin. |

If an input is outside these sets, the value is `invalid` and the cause
is written once to `klippy.log`.

## Tool loss protection and manual swaps

With a `toolhead_detect_pin`, the plugin watches the coupling while a
print is running. If the pin reads no hotend for `detect_settle_time`
continuously, the plugin:

1. reports the loss once in the console;
2. turns off all heaters (the cause is unknown, so extruders and bed all
   go cold);
3. detaches the extruder stepper;
4. pauses the print.

Re-seat the hotend, run `INITIALIZE_HOTENDCHANGER` (with dock pins) or
`INITIALIZE_HOTENDCHANGER T=<n>` (without, to name the re-seated tool),
then `RESUME`.

While no print is running (idle or paused), a detect pin change starts
detection again `detect_settle_time` after the last change. Pull a tool
off and seat another by hand, and the plugin follows: it applies the new
tool's offset and extruder without `INITIALIZE_HOTENDCHANGER`.

## Offset handling

The plugin reads the live gcode origin before every offset change and
rebuilds it as the current origin, minus the tool component it last
applied, plus the new tool's component. Only the tool's own component is
replaced:

- babystepping and other `SET_GCODE_OFFSET` adjustments stay in the
  origin across tool changes;
- an origin changed outside the plugin is taken over once, not
  compounded.

An offset applied while a print is paused (by detection or
`INITIALIZE_HOTENDCHANGER`) survives `RESUME`: the plugin shifts the saved
pause snapshot by the same amount.

`SET_TOOL_OFFSET` replaces the stored offset of one tool; axes left out
keep their value. With `SAVE=1` the values are written to the
`gcode_x_offset`, `gcode_y_offset` and `gcode_z_offset` options of that
tool's section on the next `SAVE_CONFIG`.

## Status fields

Macros and the web page read `printer.hotendchanger`:

| Field | Value |
| --- | --- |
| `active_tool` | The mounted tool number, or null. |
| `detected_tool` | The tool number the sensors last identified, or null. |
| `state` | `uninitialized`, `ready`, `changing`, `error` or `unknown`. |
| `toolhead_detect` | `triggered` or `untriggered`, or null without the pin. |
| `stepper_motion_queue` | The extruder section the stepper is synced to, or null when detached. |
| `tools` | A dictionary keyed by tool name (`"T0"` style). |

Each `tools` entry carries `number`, `extruder`, `gcode_x_offset`,
`gcode_y_offset`, `gcode_z_offset`, `detect` (`triggered` or
`untriggered`, or null without a pin) and `presence` (see
[Tool presence](#tool-presence)). A macro reads one tool's presence as
`printer.hotendchanger.tools.T0.presence`.

## Template context

A tool in a template is a mapping with `number`, `name` (`"T0"` style),
`extruder` (the extruder section name) and `params` (all `params_*`
values of that tool, keyed without the prefix).

- `pickup_gcode` and `dropoff_gcode` receive `tool` and `params`, the
  same mapping as `tool.params`, so dock motion reads `{params.dock_x}`.
- `before_change_gcode` and `after_change_gcode` receive `old_tool` and
  `new_tool`. `old_tool` is `None` when no tool is mounted, and
  `new_tool` is `None` for `UNSELECT_TOOL`, so guard every field access,
  for example `{old_tool.name if old_tool else "none"}`.

## Limitations

Deliberate exclusions, not gaps to be filled later:

- No offset measurement; use
  [eddy_tool_calibration](https://github.com/jaak0b/kalico-eddy-offset-calibration).
- No filament handling of any kind (the optional filament service only
  records which filament each tool holds) and no temperature policy
  beyond the pickup wait: no standby temperatures, no cooldown on idle.
- No crash or dropped tool detection beyond the detect pin checks
  described above.
- No per-tool input shaper configuration, and one changer per printer.

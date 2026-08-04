# hotendchanger: Design Document

## Summary

This plugin adds support to Kalico (a Klipper fork) for a specific class of toolchanger printer: one where only the hotend assembly (heater block, thermistor, nozzle, and hotend fan) moves between docks. The extruder motor that drives filament and the part cooling fan stay fixed on the carriage. Each dock keeps its own filament already loaded in its hotend, so a tool change is a motion sequence (go to the dock, drop off the old hotend, pick up the new one) plus switching which heater and thermistor the printer treats as active. There is no filament unload, no filament handoff between tools, and no retract logic.

The plugin deliberately does not handle everything a toolchanger could touch. It does not calibrate nozzle offsets (a separate plugin does that and writes results into this plugin through a command). It does not manage filament. It does not detect crashes or dropped tools beyond a simple presence check. It does not support per-tool input shaper tuning or more than one changer per printer.

The design keeps the plugin small by leaning on features Kalico already has for heaters, fans, and gcode offsets, rather than reimplementing them. Each hotend is configured as its own heater in the standard config format, which gives per-hotend temperature control, tuning, and web interface display for free. The plugin's own code is limited to the parts that are genuinely toolchanger-specific: running the pickup and dropoff motion, tracking which tool is active, applying per-tool nozzle offsets, and optionally checking dock sensors.

## Background and provenance

The tool change sequencing in this design was checked against two existing community implementations for behavioral correctness, used as reference only:

- CxChanger's macro-based toolchanger implementation (GPLv3), for the shape of the pickup and dropoff sequence and the ordering of offset changes relative to motion.
- viesturz/Contomo's klipper-toolchanger plugin, for the pattern of registering T0..Tn gcode commands dynamically from config, the params_ convention for exposing arbitrary per-tool config values to templates, and the protocol of clearing a tool's gcode offset before dropoff and reapplying it after pickup.

The Kalico surfaces this design depends on were verified against a local Kalico source clone before being relied on:

- klippy/kinematics/extruder.py: an `[extruderN]` section becomes a heater-only extruder object when no step pins are configured for it, and a full `PrinterExtruder` with an `ExtruderStepper` when they are. This is what lets T0 be the only extruder with real stepper motion while T1..Tn are heater-only.
- klippy/extras/gcode_move.py: `SET_GCODE_OFFSET` supports incremental (MOVE=1-relative) adjustment of the X, Y, Z gcode offset, which composes with user babystepping instead of overwriting it.
- klippy/extras/heater_fan.py: the `heater` option accepts any configured heater section name, so a `[heater_fan]` can be bound to `extruder1`, `extruder2`, and so on.
- klippy/extras/gcode_macro.py: `load_template` is the standard way a plugin loads a user-supplied gcode template from its own config section.
- klippy/extras/toolhead.py and printer.py: plugin loading and the `ACTIVATE_EXTRUDER` command, which switches the active extruder for bare M104/M109/M105 and resets extruder-relative E axis bookkeeping.

Because a local Kalico clone can be ahead of the firmware actually running on a given printer, every one of these surfaces must be reached defensively (checked for existence rather than assumed) at the point the plugin uses it, and the point in Kalico's history each was added should be confirmed before depending on it on an older build.

## Architecture

The central design decision is to represent each hotend as a native Kalico heater section instead of inventing a parallel heater abstraction inside the plugin.

`[extruder]` is tool T0: it is the one hotend with a real stepper, so it carries `step_pin`, `dir_pin`, `enable_pin`, and the rest of a normal extruder definition, in addition to heater and sensor options. `[extruder1]` through `[extruderN]` are the remaining tools: they carry heater and sensor options only, with no step pins. Kalico's extruder kinematics module builds a heater-only extruder object for a section with no step pins configured, so these sections get PID control, `PID_CALIBRATE`, sensor configuration, `min_extrude_temp`, and M104/M109/M105 `T<n>` heater resolution entirely through existing Kalico code. Mainsail and Fluidd already display multiple extruder heaters without any plugin-side work. A `[heater_fan]` per hotend, pointed at the corresponding `extruderN` through its `heater` option, gives each dock's hotend fan control tied to that hotend's own temperature.

This leaves the plugin responsible for exactly the parts that are specific to a toolchanger and that Kalico has no existing concept of:

- Interpreting `T<n>` as a tool change, not just a heater selection.
- Running the pickup and dropoff motion sequence from user-supplied gcode templates.
- Tracking per-tool XYZ gcode offsets and applying only the active tool's contribution.
- Tracking which tool is currently mounted, including the unknown state before the first successful pickup.
- Reading optional per-tool detection pins to confirm the expected hotend is actually present.
- Waiting for the newly picked up hotend to reach temperature before continuing, when it has a nonzero target.

Everything else (heater PID, sensor readout, fan control, temperature display, M104/M109/M105 dispatch) is Kalico's existing extruder and heater machinery, reused rather than duplicated.

## Config reference

### `[hotendchanger]` (one global section)

| Option | Required | Default | Meaning |
|---|---|---|---|
| `pickup_gcode` | yes | none | Gcode template run to pick up a hotend at its dock. Shared by all tools; the tool being picked up is passed into the template context. |
| `dropoff_gcode` | yes | none | Gcode template run to drop off the currently mounted hotend at its dock. Shared by all tools; the tool being dropped off is passed into the template context. |
| `before_change_gcode` | no | empty | Template run before a tool change begins (before any offset or motion change). |
| `after_change_gcode` | no | empty | Template run after a tool change completes (after the new offset is applied and the temperature wait, if any, finishes). |
| `temp_wait_tolerance` | no | `2.0` | Degrees C. After pickup, if the new tool's heater has a nonzero target, the plugin waits until the hotend temperature is within target plus or minus this value. A symmetric window is used deliberately: a hotend that was preheated and is overshooting its target on the way down should not stall the wait the way a MIN/MAX-only-above check would. No target set means no wait is performed. Dropoff never waits. This is an algorithm tunable, not a machine-specific value, so it has a documented default. |

If any configured tool has a `detect_pin`, the plugin runs detection automatically at Kalico startup (equivalent to `INITIALIZE_HOTENDCHANGER`) so the active tool is known before the first print starts.

Post-change verification behavior is fixed, not configurable. After a `T<n>` change, if any tool has a `detect_pin` configured and the reading contradicts the tool the plugin expected to find mounted, the plugin pauses the print through Kalico's standard pause mechanism and prints a console message naming the expected tool and the actual reading. Startup detection and `INITIALIZE_HOTENDCHANGER` remain pure discovery: an ambiguous reading there produces a console message and sets state to `unknown`, never a pause.

### `[hotendchanger_tool T0]`, `[hotendchanger_tool T1]`, ... (one section per tool)

The tool number is parsed from the section name, so section names must follow the `T<n>` pattern exactly, one section per tool, numbered from 0 with no gaps.

| Option | Required | Default | Meaning |
|---|---|---|---|
| `extruder` | yes | none | Name of this tool's extruder section (`extruder`, `extruder1`, `extruder2`, ...). Must exist in the config. |
| `gcode_x_offset` | no | `0` | Gcode X offset applied while this tool is active. |
| `gcode_y_offset` | no | `0` | Gcode Y offset applied while this tool is active. |
| `gcode_z_offset` | no | `0` | Gcode Z offset applied while this tool is active. |
| `detect_pin` | no | none | Endstop-style pin for this tool's dock sensor. Triggered means the hotend is physically present in the dock (not mounted on the carriage). |
| `params_*` | no | none | Arbitrary named values (for example dock coordinates) exposed to the pickup and dropoff templates under this tool's params. Any number of `params_` options may be defined per tool. |

Dock coordinates and any other physical, per-printer values belong in `params_*` options. They are required for a working machine but have no correct default the plugin can supply, so the example config below leaves them blank.

Detect pin semantics: a hotend sitting in its own dock holds that dock's switch triggered; a hotend mounted on the carriage leaves its dock switch untriggered. Electrical polarity does not matter here: users normalize wiring with Klipper's standard `!` pin inversion prefix, and the plugin only ever reasons about triggered versus untriggered. Resolution is computed over all configured detect pins together, not one pin in isolation: exactly one untriggered dock identifies that tool as the one mounted on the carriage; all docks triggered means no tool is currently mounted; more than one untriggered dock is a fault. During post-change verification this fault is handled the same way as an expectation mismatch (pause, with a console message). During startup or `INITIALIZE_HOTENDCHANGER` discovery it produces a console message and state `unknown`.

### Example config

```
[hotendchanger]
pickup_gcode:
    G1 X{params.dock_x} Y{params.dock_y} F6000
    G1 Z{params.dock_z} F600
    ; mechanical pickup motion for this printer's dock design
dropoff_gcode:
    G1 X{params.dock_x} Y{params.dock_y} F6000
    G1 Z{params.dock_z} F600
    ; mechanical dropoff motion for this printer's dock design
temp_wait_tolerance: 2.0

[hotendchanger_tool T0]
extruder: extruder
gcode_x_offset: 0
gcode_y_offset: 0
gcode_z_offset: 0
params_dock_x:
params_dock_y:
params_dock_z:

[hotendchanger_tool T1]
extruder: extruder1
gcode_x_offset:
gcode_y_offset:
gcode_z_offset:
detect_pin:
params_dock_x:
params_dock_y:
params_dock_z:

[heater_fan hotend1_fan]
pin:
heater: extruder1
```

Blank values above (dock coordinates, T1's offsets, the detect pin, the fan pin) are placeholders for values that depend on the specific printer and must be filled in before this config is usable.

## Tool change sequence (`T<n>`)

1. Guard checks: XYZ must be homed (docks are absolute positions), no tool change already in progress, and `n` must name a configured tool. `T<n>` equal to the currently active tool is a no-op and returns immediately.
2. Run `before_change_gcode`.
3. Remove the currently applied tool gcode offset via `SET_GCODE_OFFSET`, so only the tool's own X/Y/Z contribution is cleared. Any offset from user babystepping is untouched, because the plugin tracks and removes only the component it added.
4. If a tool is currently mounted (state is known), run `dropoff_gcode` with the old tool in the template context. If the mounted tool is unknown, dropoff is skipped: there is nothing safe to send back to a dock without knowing which dock it belongs to.
5. Run `pickup_gcode` with the new tool in the template context.
6. If any configured tool has a `detect_pin`, verify the newly picked up tool is the one detected mounted. On a mismatch or a detection fault, pause the print through Kalico's standard pause mechanism and print a console message naming the expected tool and the actual reading.
7. Call `ACTIVATE_EXTRUDER` with the new tool's extruder section, so bare M104/M109/M105 and E axis bookkeeping follow the new active hotend.
8. If the new tool's heater has a nonzero target temperature, wait until it is within `temp_wait_tolerance` of that target.
9. Reapply the new tool's gcode offset, run `after_change_gcode`, and mark the tool change complete.

## Commands

- `T0` through `T<N-1>`: registered dynamically, one per configured tool, at config load time. If a user has separately defined a `[gcode_macro Tn]` for a tool number the plugin also owns, this is a config error and must be reported as such; a printer with both would have two competing definitions of the same command name.
- `SET_TOOL_OFFSET T=<n> X=<x> Y=<y> Z=<z> [SAVE=1]`: sets a tool's gcode offset at runtime. This is the interface a separate nozzle offset calibration plugin uses to write results back. With `SAVE=1`, the value is written through `configfile.set()` so a subsequent `SAVE_CONFIG` persists it.
- `HOTENDCHANGER_STATUS`: prints labeled rows: active tool, current state, each configured tool's detect pin state (if any), and each configured tool's current X/Y/Z offset.
- `INITIALIZE_HOTENDCHANGER`: re-runs detection against configured detect pins. Intended for use after a tool was moved or serviced by hand.

## State

State is one of: `uninitialized`, `ready`, `changing`, `error`, `unknown`.

- If any tool has a `detect_pin`, the plugin runs detection at Kalico startup and after `INITIALIZE_HOTENDCHANGER`, moving to `ready` (a tool identified) or `unknown` (no switches or an ambiguous read) as the detection result dictates.
- If no tool has a `detect_pin`, state starts `unknown` and stays there until the first successful `T<n>`. A `T<n>` issued from `unknown` runs pickup only (step 4 of the sequence above is skipped, per its own rule, since there is nothing known to drop off).
- During a tool change, state is `changing`. A change that fails partway (a template gcode error, or a verification mismatch, which also pauses the print) leaves state at `error`, and further `T<n>` commands are refused until `INITIALIZE_HOTENDCHANGER` or a successful change clears it.

`get_status` (the plugin's status object read by macros and the web interface) exposes: `active_tool` (a tool number, or `None`), `detected_tool`, `state`, and a per-tool dictionary keyed by tool number holding each tool's current offset and extruder section name.

Every branch over this state enum is written to handle each member explicitly with an unhandled case raising a command error naming the unhandled value; none end in a bare `else` that silently absorbs a state added later.

## Template context

`pickup_gcode`, `dropoff_gcode`, `before_change_gcode`, and `after_change_gcode` templates receive tool objects carrying: tool number, tool name (the `T<n>` section suffix), extruder section name, and all `params_*` values defined for that tool. Where a template concerns a transition between two tools (the two change-hook templates), both the old and new tool objects are available in context, distinguished by name.

## Error handling

Every failure a user or a template can trigger raises a Kalico `CommandError` carrying a message naming the actual condition (missing tool, ambiguous or contradictory detection, template gcode error, not homed, change already in progress). No exception from plugin code is allowed to reach klippy's bare gcode dispatcher uncaught, since an uncaught exception there is a printer shutdown, not an error message reaching the operator.

## Testing

Detection resolution from pin states, offset bookkeeping (which XYZ component belongs to which tool, and what remains after a tool's contribution is cleared), and state transitions are written as plain Python taking plain values, importing nothing from klippy, and are covered by pytest with independently constructed expected outcomes rather than expectations computed by calling the same code under test. The klippy-facing layer (the plugin class itself: config loading, command registration, calls into gcode_move, heaters, and ACTIVATE_EXTRUDER) cannot be exercised by these tests, since they never import klippy, and is proven correct by running on the owner's printer instead.

## Out of scope

The following are deliberate exclusions from this plugin, not omissions to revisit later:

- Nozzle offset calibration. A separate plugin performs calibration and writes results through `SET_TOOL_OFFSET`.
- Any temperature policy beyond the pickup wait described above (no standby temperature management, no cooldown-on-idle behavior).
- Filament handling of any kind: no retract, no unload, no purge, no handoff between tools.
- Crash or dropped-tool detection beyond the presence check a configured `detect_pin` performs at the moments described above.
- Per-tool input shaper configuration.
- More than one changer per printer.

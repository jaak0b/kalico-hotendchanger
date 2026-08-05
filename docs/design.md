# hotendchanger: Design Document

## Summary

This plugin adds support to Kalico and stock Klipper for a specific class of toolchanger printer: one where only the hotend assembly (heater block, thermistor, nozzle, and hotend fan) moves between docks. The extruder motor that drives filament and the part cooling fan stay fixed on the carriage. Each dock keeps its own filament already loaded in its hotend, so a tool change is a motion sequence (go to the dock, drop off the old hotend, pick up the new one) plus switching which heater and thermistor the printer treats as active. There is no filament unload, no filament handoff between tools, and no retract logic.

The plugin deliberately does not handle everything a toolchanger could touch. It does not calibrate nozzle offsets (a separate plugin does that and writes results into this plugin through a command). It does not manage filament. It does not detect crashes or dropped tools beyond a simple presence check. It does not support per-tool input shaper tuning or more than one changer per printer.

The design keeps the plugin small by leaning on features Kalico already has for heaters, fans, and gcode offsets, rather than reimplementing them. Each hotend is configured as its own heater in the standard config format, which gives per-hotend temperature control, tuning, and web interface display for free. The plugin's own code is limited to the parts that are genuinely toolchanger-specific: running the pickup and dropoff motion, tracking which tool is active, applying per-tool nozzle offsets, and optionally checking dock sensors.

## Background and provenance

The tool change sequencing in this design was checked against two existing community implementations for behavioral correctness, used as reference only:

- CxChanger's macro-based toolchanger implementation (GPLv3), for the shape of the pickup and dropoff sequence and the ordering of offset changes relative to motion.
- viesturz/Contomo's klipper-toolchanger plugin, for the pattern of registering T0..Tn gcode commands dynamically from config, the params_ convention for exposing arbitrary per-tool config values to templates, and the protocol of clearing a tool's gcode offset before dropoff and reapplying it after pickup.

The Kalico surfaces this design depends on were verified against a local Kalico source clone before being relied on:

- klippy/kinematics/extruder.py: an `[extruderN]` section becomes a heater-only extruder object when no step pins are configured for it, and a full `PrinterExtruder` with an `ExtruderStepper` when they are. This is what lets T0 be the only extruder with real stepper motion while T1..Tn are heater-only.
- klippy/extras/gcode_move.py: the absolute X/Y/Z form of `SET_GCODE_OFFSET` sets the gcode origin (`homing_position`) directly, and `gcode_move`'s status exposes the live origin as `homing_origin`. The plugin reads the live origin before every offset command and rebuilds it as (current origin minus the tool component it last applied, plus the new tool component), so user babystepping stays in the origin and an origin changed outside the plugin is picked up once instead of compounded.
- klippy/extras/heater_fan.py: the `heater` option accepts any configured heater section name, so a `[heater_fan]` can be bound to `extruder1`, `extruder2`, and so on.
- klippy/extras/gcode_macro.py: `load_template` is the standard way a plugin loads a user-supplied gcode template from its own config section.
- klippy/extras/toolhead.py and printer.py: plugin loading and the `ACTIVATE_EXTRUDER` command, which switches the active extruder for bare M104/M109/M105 and resets extruder-relative E axis bookkeeping.

Because a local Kalico clone can be ahead of the firmware actually running on a given printer, every one of these surfaces must be reached defensively (checked for existence rather than assumed) at the point the plugin uses it, and the point in Kalico's history each was added should be confirmed before depending on it on an older build.

## Architecture

The central design decision is to represent each hotend as a native Kalico heater section instead of inventing a parallel heater abstraction inside the plugin.

`[extruder]` is tool T0: it is the one hotend with a real stepper, so it carries `step_pin`, `dir_pin`, `enable_pin`, and the rest of a normal extruder definition, in addition to heater and sensor options. `[extruder1]` through `[extruderN]` are the remaining tools: they carry heater and sensor options only, with no step pins. Kalico's extruder kinematics module builds a heater-only extruder object for a section with no step pins configured, so these sections get PID control, `PID_CALIBRATE`, sensor configuration, `min_extrude_temp`, and M104/M109/M105 `T<n>` heater resolution entirely through existing Kalico code. Mainsail and Fluidd already display multiple extruder heaters without any plugin-side work. A `[heater_fan]` per hotend, pointed at the corresponding `extruderN` through its `heater` option, gives each dock's hotend fan control tied to that hotend's own temperature.

On disk the plugin is two modules. `hotendchanger.py` holds all logic; `hotendchanger_tool.py` is a loader shim, required because both firmwares map a config section to the module named by the section's first word, so `[hotendchanger_tool Tn]` sections are loaded through a module of exactly that name, which forwards to the tool loader in `hotendchanger.py`. The firmwares' config handling also lowercases section names before the plugin sees them, so the `T<n>` suffix is parsed case-insensitively while tool names are always reported as `T<n>`.

Both firmwares are supported from the same files, and every difference is capability-detected at the point of use (check whether the running firmware has the surface, fall back otherwise), never firmware-identified. Exactly two divergences exist, each verified against both source trees:

- Module loading. Kalico imports plugins under the `klippy.plugins` package from `klippy/plugins/`; stock Klipper imports modules from `klippy/extras/` as an absolute `extras` package with no `klippy` namespace. The shim's sibling import tries `klippy.plugins` first and falls back to `extras`, so both files work from either directory.
- Install layout. `install.sh` and the integration harness probe the checkout: a `klippy/printer.py` loading `klippy.plugins` selects `klippy/plugins/` (with a package marker), a `klippy/klippy.py` with `klippy/extras/` selects `klippy/extras/` (no marker), and anything else is an error.

A single physical filament stepper serves every tool: it lives in the one extruder section configured with step pins (T0's), derived at connect rather than configured, and it is a connect-time error if zero or more than one tool's extruder section carries a stepper. On every activation the plugin issues `SYNC_EXTRUDER_MOTION` to attach that stepper to the active extruder section's motion queue (heater-only extruder sections still allocate a motion queue, so the attachment works), skipping the re-sync when the resolved tool is already active on that queue. Whenever the active tool becomes none (detection resolving no tool, any failed change, a mid-print tool loss), the stepper is detached with an empty `MOTION_QUEUE`, so `G1 E` moves no motor until the next activation; the invariant is "no active tool means detached", enforced at the single place the active tool is cleared. The firmware's own `motion_queue` field is made authoritative by one explicit self-sync before the first discovery, since the firmware never records the boot-time attachment.

Pressure advance is per tool through the plugin. Activation sets the carrier stepper's pressure advance to the tool's configured value (or the carrier section's own configured value as fallback) via `SET_PRESSURE_ADVANCE`. The two firmwares source the value differently: stock applies the carrier stepper's value to every motion queue, so the command alone suffices; Kalico reads the per-move value from the active section's `extruder_stepper` attribute and zeroes pressure advance when that section has none, so during activation of a heater-only section the plugin additionally hosts the carrier's stepper object on that section's `extruder_stepper` attribute (removed again on deactivation), which feeds the per-tool value into Kalico's move stream and is inert on stock.

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
| `toolhead_detect_pin` | no | none | Endstop-style pin reading whether some hotend is mounted on the toolhead; triggered means a hotend is held. The pin cannot identify which tool. Independent of the per-tool `detect_pin`: legal setups are neither, dock pins only, the toolhead pin only, or both. Polarity is normalized with the `!` prefix as with `detect_pin`. |
| `detect_settle_time` | no | `1.0` | Seconds, above 0. One tunable for two timing roles: while printing, how long the toolhead pin must read untriggered continuously before the loss response fires (contact bounce filter for the coupling under print vibration); while no print is running, how long after the last detect pin edge a manual tool swap is allowed to settle before detection re-runs. |
| `detect_report_time` | no | `0.05` | Seconds, above 0. How long after motion finishes the in-change pin checks wait for the MCU's button report to arrive before reading the cached level; a failed reading is re-read once after another window before a failure is declared. The default covers local transports; raise it on slow links (CAN). |
| `temp_wait_tolerance` | no | `2.0` | Degrees C. After pickup, if the new tool's heater has a nonzero target, the plugin runs `TEMPERATURE_WAIT` with a symmetric `MINIMUM`/`MAXIMUM` window of target plus or minus this value, so a hotend that was preheated and is overshooting its target on the way down completes the wait as soon as it re-enters the window. The fixed window is safe because the gcode mutex is held for the whole tool change: no other command can change the target mid-wait, and a shutdown or command interrupt aborts `TEMPERATURE_WAIT`'s own loop. No target set means no wait is performed. Dropoff never waits. This is an algorithm tunable, not a machine-specific value, so it has a documented default. |

The plugin runs detection automatically shortly after Kalico reaches ready (equivalent to `INITIALIZE_HOTENDCHANGER`) so the active tool is known before the first print starts. Detection cannot run inside the ready handler itself: button state reports arrive through reactor callbacks that only run after the ready handlers return, so discovery runs from a one-shot reactor timer half a second later.

Post-change verification behavior is fixed, not configurable. After a `T<n>` change, if detect pins are configured and the reading contradicts the tool the plugin expected to find mounted, the response depends on whether a print is running, read from `pause_resume`, `print_stats` and `virtual_sdcard` (each reached defensively, since each is optional in config). During an active print, the plugin prints one console message naming the expected tool, the actual reading and what to check, pauses the print through the standard pause mechanism, and sets the state to `error`; it does not additionally raise, because an error raised into a printing job aborts the print instead of leaving it paused. With no print running (standby, a finished print, or one already paused), the same diagnostic is raised as a normal command error instead, and the state still goes to `error`. Startup detection and `INITIALIZE_HOTENDCHANGER` remain pure discovery: an ambiguous reading there produces a console message and sets state to `unknown`, never a pause.

### `[hotendchanger_tool T0]`, `[hotendchanger_tool T1]`, ... (one section per tool)

The tool number is parsed from the section name, so section names must follow the `T<n>` pattern exactly, one section per tool, numbered from 0 with no gaps.

| Option | Required | Default | Meaning |
|---|---|---|---|
| `extruder` | yes | none | Name of this tool's extruder section (`extruder`, `extruder1`, `extruder2`, ...). Must exist in the config. |
| `gcode_x_offset` | no | `0` | Gcode X offset applied while this tool is active. |
| `gcode_y_offset` | no | `0` | Gcode Y offset applied while this tool is active. |
| `gcode_z_offset` | no | `0` | Gcode Z offset applied while this tool is active. |
| `detect_pin` | no | none | Endstop-style pin for this tool's dock sensor. Triggered means the hotend is physically present in the dock (not mounted on the carriage). All-or-nothing across the tools: either every tool sets a `detect_pin` or none does; a mix is a config error at connect naming the tools missing it. |
| `pressure_advance` | no | none | Pressure advance applied while this tool is active, set on the physical stepper at activation through `SET_PRESSURE_ADVANCE`. Without it the tool uses the stepper carrier section's configured value. `pressure_advance` under a heater-only `[extruderN]` section is rejected by the firmware as an unused option; per-tool values belong here. |
| `pressure_advance_smooth_time` | no | none | Smooth time companion to `pressure_advance`, same activation mechanism and fallback. |
| `params_*` | no | none | Arbitrary named values (for example dock coordinates) exposed to the pickup and dropoff templates under this tool's params. Any number of `params_` options may be defined per tool. |

Dock coordinates and any other physical, per-printer values belong in `params_*` options. They are required for a working machine but have no correct default the plugin can supply, so the example config below leaves them blank.

Detect pin semantics: a hotend sitting in its own dock holds that dock's switch triggered; a hotend mounted on the carriage leaves its dock switch untriggered. Electrical polarity does not matter here: users normalize wiring with Klipper's standard `!` pin inversion prefix, and the plugin only ever reasons about triggered versus untriggered. Because detect pins are all-or-nothing across the tools, resolution always sees every dock: exactly one untriggered dock identifies that tool as the one mounted on the carriage; all docks triggered means no tool is currently mounted; more than one untriggered dock is a fault. During post-change verification a fault is handled the same way as an expectation mismatch (pause, with a console message). During startup or `INITIALIZE_HOTENDCHANGER` discovery it produces a console message and state `unknown`.

The toolhead pin folds into the same resolution when configured. With dock pins and the toolhead pin, the readings must agree: a hotend on the toolhead plus exactly one empty dock identifies that tool; no hotend on the toolhead plus all docks full means no tool; a toolhead reading contradicting the docks (either direction) is a fault. With the toolhead pin alone, triggered means a mounted tool of unknown identity: state becomes `unknown` with a console message directing the user to `INITIALIZE_HOTENDCHANGER T=<n>`, and untriggered resolves as no tool mounted (`ready`, no active tool). `INITIALIZE_HOTENDCHANGER T=<n>` is refused while the toolhead pin reads untriggered, since asserting a mounted tool would contradict the sensor.

Pin levels are cached from the buttons module's change reports, whose baseline level is untriggered: a dock switch that never reports reads untriggered, which is exactly the reading a mounted tool's own dock produces. The toolhead pin has the same baseline, and a hotend mounted at boot drives it triggered, which the MCU's first poll reports as an edge, so the boot state is known by the time startup detection runs. Because tool change templates only queue motion, both in-change checks run after an `M400` plus a short report delay, so the cached levels reflect the finished motion.

Two automatic behaviors run outside tool changes, both driven by pin edges and the `detect_settle_time` window:

- Mid-print loss monitor. While a print is active, a tool is active and no change is in progress, a toolhead pin that reads untriggered continuously for `detect_settle_time` fires the loss response exactly once per loss event, safety actions first: one console message naming the lost tool and the recovery (`INITIALIZE_HOTENDCHANGER`, or `INITIALIZE_HOTENDCHANGER T=<n>` on a machine without dock pins, then `RESUME`), `TURN_OFF_HEATERS` (the failure cause is unknown, so every heater goes off), detaching of the extruder stepper, and only then the pause through the standard pause mechanism, so a failing user `PAUSE` macro can never skip the heater shutdown. State goes to `error` with no active tool. The monitor re-arms when the pin reads triggered again.
- Manual-swap rediscovery. When any detect pin changes while no change is in progress and no print is actively running (idle and paused both qualify), detection re-runs `detect_settle_time` after the last edge; each new edge of the swap burst restarts the delay. The rerun is the ordinary discovery path (same resolution, messages, extruder activation and offset application), so pulling a tool off and seating another by hand while idle is followed without `INITIALIZE_HOTENDCHANGER`. An ambiguous settled reading keeps the discovery semantics (message plus `unknown`), and the next edge simply retriggers.

Both behaviors are served by one settled-edge timer: pin edges record a timestamp and arm it, and the response is chosen when the settle window expires, from the state at that moment. A loss window that begins while printing and expires after the print was paused or cancelled therefore still resolves (through rediscovery) instead of going unanswered, and a window expiring during a tool change is re-checked after the change. Edges arriving before startup detection has run are ignored, so a boot-time report burst cannot double-run discovery. When an offset is applied while a print is paused (rediscovery or `INITIALIZE_HOTENDCHANGER` during pause), the saved `PAUSE_STATE` gcode snapshot is shifted by the same amount, so `RESUME` keeps the new tool component instead of reverting it.

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
#temp_wait_tolerance: 2.0

[hotendchanger_tool T0]
extruder: extruder
#gcode_x_offset: 0
#gcode_y_offset: 0
#gcode_z_offset: 0
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

[heater_fan hotend1_fan]
#pin:
heater: extruder1
```

Options shown commented out are either optional or have no value the plugin can supply: dock coordinates, offsets, the detect pin and the fan pin depend on the specific printer. A blank uncommented value does not parse, so uncomment such a line only when filling in the machine's own value. The `params_dock_*` trio is shown per tool because dock positions normally differ per tool.

## Tool change sequence (`T<n>`)

1. Guard checks, in order: the state must allow starting a change (refusals are checked before the no-op so `T<n>` in the `error` or `changing` state reports the refusal instead of silently succeeding), `T<n>` equal to the currently active tool responds that it is already active and returns, and XYZ must be homed (docks are absolute positions).
2. Run `before_change_gcode`.
3. Remove the currently applied tool gcode offset: the plugin reads the live gcode origin, subtracts the tool component it last applied, and issues an absolute `SET_GCODE_OFFSET`. Any offset from user babystepping stays in the origin, because only the tracked tool component is replaced.
4. If a tool is currently mounted (state is known), run `dropoff_gcode` with the old tool in the template context. If the mounted tool is unknown, dropoff is skipped: there is nothing safe to send back to a dock without knowing which dock it belongs to. When any detect pin is configured, the reading after dropoff must resolve to "no tool mounted" (all docks occupied, toolhead pin untriggered, whichever exist); anything else means the hotend failed to release or land and routes through the same failure handling as step 6. A `T<n>` is also refused up front when the toolhead pin reads a mounted hotend of unknown identity, since it cannot be dropped off safely.
5. Run `pickup_gcode` with the new tool in the template context.
6. If any detect pin is configured, verify the pickup: the combined reading must resolve to the new tool (dock pins identify it, the toolhead pin confirms presence, whichever exist); the failure message carries the raw readings. Both in-change checks wait for queued motion to finish and the pin reports to settle (`detect_report_time`), and a failed reading is re-read once after another report window before the failure is declared. On a mismatch or a detection fault during an active print, print one console message naming the expected tool, the actual reading and what to check, pause the print, set the state to `error`, and return without raising; with no print running, raise the same diagnostic as a command error, with the state likewise `error`. Every failed change, whatever the cause, converges to the same footprint: state `error`, no active tool, extruder stepper detached; the previously activated extruder object keeps receiving bare M104/M105 until the next successful activation.
7. Call `ACTIVATE_EXTRUDER` with the new tool's extruder section, so bare M104/M109/M105 and E axis bookkeeping follow the new active hotend, then `SYNC_EXTRUDER_MOTION` to bind the physical extruder stepper to the new section's motion queue: only one extruder section carries step pins, so extrusion follows the active tool by re-syncing that one stepper (T0 included, since `ACTIVATE_EXTRUDER` alone does not restore a stepper synced away).
8. If the new tool's heater has a nonzero target temperature, run `TEMPERATURE_WAIT` until it is within `temp_wait_tolerance` of that target.
9. Reapply the new tool's gcode offset (same absolute-origin scheme as step 3), run `after_change_gcode`, and mark the tool change complete.

## Commands

- `T0` through `T<N-1>`: registered dynamically, one per configured tool, at config load time. If a user has separately defined a `[gcode_macro Tn]` for a tool number the plugin also owns, this is a config error and must be reported as such; a printer with both would have two competing definitions of the same command name.
- `SET_TOOL_OFFSET T=<n> [X=<x>] [Y=<y>] [Z=<z>] [SAVE=1]`: sets a tool's gcode offset at runtime; axes left out keep their current value, and the offset is reapplied immediately when the tool is active. This is the interface a separate nozzle offset calibration plugin uses to write results back. With `SAVE=1`, the value is written through `configfile.set()` so a subsequent `SAVE_CONFIG` persists it.
- `HOTENDCHANGER_STATUS`: prints labeled rows: active tool, detected tool, current state, each tool's detect pin reading (when pins are configured), the toolhead detect pin reading (when configured), the extruder stepper's current motion queue (`none` means detached; the plugin self-syncs the stepper before the first discovery so the field is authoritative from startup), and per tool one row with the current X/Y/Z offset and the extruder section name.
- `INITIALIZE_HOTENDCHANGER [T=<n>]`: re-runs detection against configured detect pins. Intended for use after a tool was moved or serviced by hand, and it is the only way to leave the `error` state. On a printer without detect pins, `T=<n>` asserts by hand which tool is mounted (state becomes `ready` with that tool active and its offset applied); with detect pins configured `T=` is refused, since detection determines the mounted tool. Refused while a change is in progress.

## State

State is one of: `uninitialized`, `ready`, `changing`, `error`, `unknown`.

- `uninitialized` is the pre-ready window: the interval between config load and the startup discovery that runs shortly after klippy reaches ready. `T<n>` is refused in it.
- With detect pins, the plugin runs detection shortly after Kalico startup and on `INITIALIZE_HOTENDCHANGER`, moving to `ready` or `unknown` (an ambiguous or faulted read) as the detection result dictates. Discovery resolving "no tool mounted" (all docks triggered) also yields `ready`, with `active_tool` none; the plugin distinguishes that known-empty carriage (`ready`, no active tool: a `T<n>` skips dropoff because nothing is mounted) from an unknown mounted tool (`unknown`: dropoff is skipped because whatever may be mounted has no known dock).
- Without detect pins, state starts `unknown` and stays there until the first successful `T<n>` or an `INITIALIZE_HOTENDCHANGER T=<n>` assertion. A `T<n>` issued from `unknown` runs pickup only (step 4 of the sequence above is skipped, per its own rule, since there is nothing known to drop off).
- During a tool change, state is `changing`. A change that fails partway (a template gcode error, or a verification mismatch, which also pauses the print) leaves state at `error` with no active tool, because a hotend may still be on the carriage without the plugin knowing which: further `T<n>` commands are refused until `INITIALIZE_HOTENDCHANGER` clears the state, through detection or, on a pinless machine, through the operator's `T=<n>` assertion.

A mid-print tool loss (see the detection section) and a failed `INITIALIZE_HOTENDCHANGER T=<n>` assertion leave the same footprint as a failed change: state `error`, no active tool, extruder stepper detached; `INITIALIZE_HOTENDCHANGER` (or a manual re-seat plus the automatic rediscovery, when no print is running) resolves it.

`get_status` (the plugin's status object read by macros and the web interface) exposes exactly these keys: `active_tool` (a tool number, or `None`), `detected_tool` (a tool number, or `None`), `state`, `toolhead_detect` (the toolhead pin reading as `"triggered"`/`"untriggered"`, or `None` without the pin), `stepper_motion_queue` (the extruder section the physical stepper is synced to, or `None`), and `tools`, a dictionary keyed by tool name (`"T0"`, `"T1"`, ...; string keys, so the mapping survives JSON serialization) whose entries carry `number`, `extruder`, `gcode_x_offset`, `gcode_y_offset`, `gcode_z_offset`, and `detect` (the pin reading as `"triggered"`/`"untriggered"`, or `None` without a pin).

Every branch over this state enum is written to handle each member explicitly with an unhandled case raising a command error naming the unhandled value; none end in a bare `else` that silently absorbs a state added later.

## Template context

A tool object in template context is a mapping with the keys `number` (the tool number), `name` (`"T0"` style), `extruder` (the extruder section name), and `params` (all `params_*` values defined for that tool, keyed without the prefix).

- `pickup_gcode` and `dropoff_gcode` receive `tool` (the tool being picked up or dropped off) and `params`, an alias of the same mapping as `tool.params`, so dock motion reads naturally as `{params.dock_x}`.
- `before_change_gcode` and `after_change_gcode` receive `old_tool` and `new_tool`. `old_tool` is `None` when no tool is mounted (the first change, or a change from a known-empty carriage), so templates must guard accesses like `{old_tool.name if old_tool else "none"}`.

## Error handling

Every failure a user or a template can trigger raises a Kalico `CommandError` carrying a message naming the actual condition (missing tool, template gcode error, not homed, change already in progress), with one deliberate exception: a post-change verification mismatch pauses the print and returns without raising, because raising into a printing job aborts it instead of leaving it paused. No exception from plugin code is allowed to reach klippy's bare gcode dispatcher uncaught, since an uncaught exception there is a printer shutdown, not an error message reaching the operator; non-command entry points (the startup discovery timer) catch everything, log the traceback and move to the `error` state instead of letting an exception escape.

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

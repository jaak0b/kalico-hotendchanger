# Support for hotend-only toolchanger printers
import logging

STATE_UNINITIALIZED = "uninitialized"
STATE_READY = "ready"
STATE_CHANGING = "changing"
STATE_ERROR = "error"
STATE_UNKNOWN = "unknown"
ALL_STATES = (
    STATE_UNINITIALIZED,
    STATE_READY,
    STATE_CHANGING,
    STATE_ERROR,
    STATE_UNKNOWN,
)

DETECT_MOUNTED = "mounted"
DETECT_NONE = "none_mounted"
DETECT_FAULT = "fault"
DETECT_NO_PINS = "no_pins"

CHANGE_PROCEED = "proceed"
CHANGE_NOOP = "noop"
CHANGE_REFUSE = "refuse"

TEMP_WAIT_DONE = "done"
TEMP_WAIT_WAITING = "waiting"
TEMP_WAIT_CANCELED = "canceled"


def format_tool(tool_number):
    return "T%d" % (tool_number,)


def parse_tool_name(name):
    # Config section names arrive lowercased by klippy's config handling, so
    # both "T0" and "t0" are accepted; tool display names are always "T0".
    # isdecimal instead of isdigit: superscript digits pass isdigit but make
    # int() raise ValueError.
    if len(name) < 2 or name[0] not in ("T", "t") or not name[1:].isdecimal():
        return None
    if name[1:] != str(int(name[1:])):
        return None
    return int(name[1:])


def validate_tool_numbers(numbers):
    numbers = sorted(numbers)
    if not numbers:
        return "no hotendchanger_tool sections configured"
    if numbers != list(range(len(numbers))):
        return (
            "hotendchanger_tool sections must be numbered T0..T%d with no"
            " gaps or duplicates, found: %s"
            % (len(numbers) - 1, ", ".join(format_tool(n) for n in numbers))
        )
    return None


def validate_detect_pin_coverage(tools_with_pin, all_tools):
    with_pin = set(tools_with_pin)
    missing = sorted(set(all_tools) - with_pin)
    if not with_pin or not missing:
        return None
    return (
        "either every hotendchanger_tool must set detect_pin or none may;"
        " missing on: %s" % (", ".join(format_tool(n) for n in missing),)
    )


def validate_tool_extruders(extruders_by_tool):
    tools_by_extruder = {}
    for tool_number in sorted(extruders_by_tool):
        extruder = extruders_by_tool[tool_number]
        if extruder in tools_by_extruder:
            return (
                "hotendchanger_tool sections %s and %s both name extruder"
                " section '%s'; each tool needs its own extruder section"
                % (
                    format_tool(tools_by_extruder[extruder]),
                    format_tool(tool_number),
                    extruder,
                )
            )
        tools_by_extruder[extruder] = tool_number
    return None


def describe_pin_state(state):
    return "triggered" if state else "untriggered"


def describe_pin_states(pin_states):
    return ", ".join(
        "%s=%s" % (format_tool(t), describe_pin_state(s))
        for t, s in sorted(pin_states.items())
    )


def resolve_detection(pin_states):
    if not pin_states:
        return (
            DETECT_NO_PINS,
            None,
            "no detect pins configured, the mounted tool is unknown",
        )
    reading = describe_pin_states(pin_states)
    untriggered = sorted(t for t, s in pin_states.items() if not s)
    if len(untriggered) == 1:
        return (
            DETECT_MOUNTED,
            untriggered[0],
            "%s detected mounted (%s)" % (format_tool(untriggered[0]), reading),
        )
    if not untriggered:
        return (
            DETECT_NONE,
            None,
            "all docks triggered, no tool mounted (%s)" % (reading,),
        )
    return (
        DETECT_FAULT,
        None,
        "multiple docks read untriggered: %s (%s)"
        % (", ".join(format_tool(t) for t in untriggered), reading),
    )


def state_after_discovery(verdict, detected_tool):
    if verdict == DETECT_MOUNTED:
        return (STATE_READY, detected_tool)
    if verdict == DETECT_NONE:
        return (STATE_READY, None)
    if verdict == DETECT_FAULT:
        return (STATE_UNKNOWN, None)
    if verdict == DETECT_NO_PINS:
        return (STATE_UNKNOWN, None)
    raise ValueError("unhandled detection verdict %r" % (verdict,))


def verify_detected(pin_states, expected_tool):
    verdict, detected, message = resolve_detection(pin_states)
    if verdict == DETECT_MOUNTED:
        if detected == expected_tool:
            return None
        return "expected %s mounted, but %s" % (format_tool(expected_tool), message)
    if verdict == DETECT_NONE:
        return "expected %s mounted, but %s" % (format_tool(expected_tool), message)
    if verdict == DETECT_FAULT:
        return "expected %s mounted, but %s" % (format_tool(expected_tool), message)
    if verdict == DETECT_NO_PINS:
        # Verification is only reached when detect pins exist, so an empty
        # reading here is a caller bug, not an operator condition.
        raise ValueError("verification requires detect pin readings")
    raise ValueError("unhandled detection verdict %r" % (verdict,))


def begin_change_refusal(state):
    if state == STATE_READY:
        return None
    if state == STATE_UNKNOWN:
        return None
    if state == STATE_UNINITIALIZED:
        return (
            "The hotendchanger is not initialized yet. Wait for startup"
            " detection to finish, or run INITIALIZE_HOTENDCHANGER."
        )
    if state == STATE_CHANGING:
        return "A tool change is already in progress."
    if state == STATE_ERROR:
        return (
            "The hotendchanger is in the error state after a failed tool"
            " change. Run INITIALIZE_HOTENDCHANGER to clear it."
        )
    raise ValueError("unhandled hotendchanger state %r" % (state,))


def change_decision(state, active_tool, requested_tool):
    refusal = begin_change_refusal(state)
    if refusal is not None:
        return (CHANGE_REFUSE, refusal)
    if requested_tool == active_tool:
        return (
            CHANGE_NOOP,
            "%s is already the active tool" % (format_tool(requested_tool),),
        )
    return (CHANGE_PROCEED, None)


def evaluate_temp_wait(temp, target, tolerance):
    if target <= 0.0:
        return TEMP_WAIT_CANCELED
    if abs(temp - target) <= tolerance:
        return TEMP_WAIT_DONE
    return TEMP_WAIT_WAITING


class OffsetLedger:
    def __init__(self):
        self.tool_component = (0.0, 0.0, 0.0)
        self.commanded_origin = None

    def plan(self, current_origin, target):
        current_origin = tuple(float(v) for v in current_origin)
        target = tuple(float(v) for v in target)
        if self.commanded_origin is None:
            drift = (0.0, 0.0, 0.0)
        else:
            drift = tuple(
                c - e for c, e in zip(current_origin, self.commanded_origin)
            )
        new_origin = tuple(
            c - a + t
            for c, a, t in zip(current_origin, self.tool_component, target)
        )
        return (new_origin, drift)

    def commit(self, new_origin, target):
        self.commanded_origin = tuple(float(v) for v in new_origin)
        self.tool_component = tuple(float(v) for v in target)


# Button state reports need the reactor running: klippy:ready handlers run
# inline before the reactor loop resumes (klippy/printer.py:418-428) and
# buttons deliver through register_async_callback
# (klippy/extras/buttons.py:84-93), so startup detection runs from a timer.
# The MCU polls buttons every QUERY_TIME=0.002s (buttons.py:12), so 0.5s
# covers many poll cycles plus transport latency.
STARTUP_DETECT_DELAY = 0.5


class HotendchangerTool:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.section_name = config.get_name()
        self.tool_number = parse_tool_name(self.section_name.split(None, 1)[1])
        if self.tool_number is None:
            raise config.error(
                "hotendchanger_tool section names must follow the T<n>"
                " pattern, got '%s'" % (config.get_name(),)
            )
        self.name = format_tool(self.tool_number)
        self.extruder_name = config.get("extruder")
        self.offset = [
            config.getfloat("gcode_x_offset", 0.0),
            config.getfloat("gcode_y_offset", 0.0),
            config.getfloat("gcode_z_offset", 0.0),
        ]
        self.params = {
            opt[len("params_"):]: config.get(opt)
            for opt in config.get_prefix_options("params_")
        }
        self.detect_pin = config.get("detect_pin", None)
        self.detect_state = None
        if self.detect_pin is not None:
            # Detect pins are read through the buttons module
            # (klippy/extras/buttons.py:363 register_buttons). Callbacks fire
            # only when the normalized level changes from the module's
            # baseline of 0 (buttons.py:23,84-93), so a pin steady at 0
            # never reports; False is that baseline, making the cached value
            # correct from the start.
            self.detect_state = False
            buttons = self.printer.load_object(config, "buttons")
            buttons.register_buttons([self.detect_pin], self._detect_callback)
        changer = self.printer.load_object(config, "hotendchanger")
        changer.register_tool(config, self)

    def _detect_callback(self, eventtime, state):
        self.detect_state = bool(state)

    def template_context(self):
        return {
            "number": self.tool_number,
            "name": self.name,
            "extruder": self.extruder_name,
            "params": dict(self.params),
        }


class Hotendchanger:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object("gcode")
        gcode_macro = self.printer.load_object(config, "gcode_macro")
        self.pickup_template = gcode_macro.load_template(config, "pickup_gcode")
        self.dropoff_template = gcode_macro.load_template(config, "dropoff_gcode")
        self.before_template = gcode_macro.load_template(
            config, "before_change_gcode", ""
        )
        self.after_template = gcode_macro.load_template(
            config, "after_change_gcode", ""
        )
        self.temp_wait_tolerance = config.getfloat(
            "temp_wait_tolerance", 2.0, above=0.0
        )
        self.tools = {}
        self.state = STATE_UNINITIALIZED
        self.active_tool = None
        self.detected_tool = None
        self.ledger = OffsetLedger()
        self.printer.register_event_handler("klippy:connect", self._handle_connect)
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.gcode.register_command(
            "SET_TOOL_OFFSET",
            self._guarded(self.cmd_SET_TOOL_OFFSET),
            desc="Set a tool's gcode offset (SET_TOOL_OFFSET T= [X=] [Y=] [Z=] [SAVE=1])",
        )
        self.gcode.register_command(
            "HOTENDCHANGER_STATUS",
            self._guarded(self.cmd_HOTENDCHANGER_STATUS),
            desc="Report hotendchanger state, detect pins and tool offsets",
        )
        self.gcode.register_command(
            "INITIALIZE_HOTENDCHANGER",
            self._guarded(self.cmd_INITIALIZE_HOTENDCHANGER),
            desc="Re-run tool detection, or assert the mounted tool with T=<n>",
        )

    def register_tool(self, config, tool):
        if tool.tool_number in self.tools:
            raise config.error(
                "duplicate hotendchanger_tool section for %s" % (tool.name,)
            )
        self.tools[tool.tool_number] = tool
        command = format_tool(tool.tool_number)
        try:
            self.gcode.register_command(
                command,
                self._guarded(self._make_tool_command(tool.tool_number)),
                desc="Change to tool %s" % (tool.name,),
            )
        except self.printer.config_error as e:
            raise config.error(
                "cannot register gcode command %s for [hotendchanger_tool %s]:"
                " %s. If a [gcode_macro %s] defines the same command, remove"
                " either it or this tool section." % (command, tool.name, e, command)
            )

    def _make_tool_command(self, tool_number):
        def handler(gcmd):
            self._do_tool_change(gcmd, tool_number)

        return handler

    def _guarded(self, func):
        def wrapper(gcmd):
            try:
                func(gcmd)
            except self.printer.command_error:
                raise
            except Exception as e:
                logging.exception("hotendchanger: unexpected error")
                raise self.printer.command_error(
                    "hotendchanger internal error: %s: %s. Check the klippy"
                    " log for the traceback." % (type(e).__name__, e)
                )

        return wrapper

    def _handle_connect(self):
        for message in (
            validate_tool_numbers(self.tools),
            validate_detect_pin_coverage(
                [n for n, t in self.tools.items() if t.detect_pin is not None],
                self.tools,
            ),
            validate_tool_extruders(
                {n: t.extruder_name for n, t in self.tools.items()}
            ),
        ):
            if message is not None:
                raise self.printer.config_error(message)
        for tool in self.tools.values():
            extruder = self.printer.lookup_object(tool.extruder_name, None)
            if extruder is None:
                raise self.printer.config_error(
                    "hotendchanger_tool %s names extruder section '%s' which"
                    " does not exist" % (tool.name, tool.extruder_name)
                )
            if not hasattr(extruder, "get_heater"):
                raise self.printer.config_error(
                    "hotendchanger_tool %s names section '%s' which is not a"
                    " heater-bearing extruder" % (tool.name, tool.extruder_name)
                )

    def _handle_ready(self):
        # In debugoutput batch mode no MCU pushes button reports and the test
        # gcode runs immediately after ready, so discovery runs inline there.
        if self.printer.get_start_args().get("debugoutput") is not None:
            self._startup_discovery()
            return
        reactor = self.printer.get_reactor()
        reactor.register_timer(
            self._startup_discovery_timer,
            reactor.monotonic() + STARTUP_DETECT_DELAY,
        )

    def _startup_discovery_timer(self, eventtime):
        self._startup_discovery()
        return self.printer.get_reactor().NEVER

    def _startup_discovery(self):
        try:
            self._run_discovery(self.gcode.run_script)
        except Exception:
            logging.exception("hotendchanger: startup detection failed")
            self.state = STATE_ERROR
            self.gcode.respond_info(
                "hotendchanger: startup detection failed. Check the klippy"
                " log for the traceback, fix the cause, then run"
                " INITIALIZE_HOTENDCHANGER."
            )

    def _pin_states(self):
        return {
            n: tool.detect_state
            for n, tool in self.tools.items()
            if tool.detect_pin is not None
        }

    def _run_discovery(self, script_runner):
        verdict, detected, message = resolve_detection(self._pin_states())
        self.state, self.active_tool = state_after_discovery(verdict, detected)
        self.detected_tool = detected
        self.gcode.respond_info("hotendchanger detection: %s" % (message,))
        if self.active_tool is not None:
            target = self.tools[self.active_tool].offset
        else:
            target = (0.0, 0.0, 0.0)
        self._set_tool_offset(target, script_runner)

    def _set_tool_offset(self, target, script_runner):
        gcode_move = self.printer.lookup_object("gcode_move")
        current = tuple(gcode_move.get_status()["homing_origin"][:3])
        new_origin, drift = self.ledger.plan(current, target)
        if drift != (0.0, 0.0, 0.0):
            self.gcode.respond_info(
                "hotendchanger: preserving gcode offset adjustments made"
                " outside the plugin: X=%.6f Y=%.6f Z=%.6f" % drift
            )
        if new_origin != current:
            # The absolute X/Y/Z form of SET_GCODE_OFFSET sets homing_position
            # directly (klippy/extras/gcode_move.py:258-270). The new origin
            # keeps the non-tool component (current minus the applied tool
            # component), so babystepping is preserved, and the live origin
            # is re-read each time, so an outside change is carried once
            # instead of compounded.
            script_runner(
                "SET_GCODE_OFFSET X=%.6f Y=%.6f Z=%.6f" % new_origin
            )
        self.ledger.commit(new_origin, target)

    def _render_and_run(self, template, context_extra):
        context = template.create_template_context()
        context.update(context_extra)
        template.run_gcode_from_command(context)

    def _check_homed(self, gcmd):
        toolhead = self.printer.lookup_object("toolhead")
        curtime = self.printer.get_reactor().monotonic()
        homed = toolhead.get_status(curtime)["homed_axes"]
        missing = [a for a in "xyz" if a not in homed]
        if missing:
            raise gcmd.error(
                "Home all axes with G28 before a tool change. Not homed: %s"
                % (", ".join(missing),)
            )

    def _pause_print(self, message):
        if self.printer.lookup_object("pause_resume", None) is None:
            raise self.printer.command_error(
                "%s No [pause_resume] section is configured, so the print"
                " cannot be paused automatically. Add one to enable pausing"
                " here." % (message,)
            )
        self.gcode.respond_info(message)
        # PAUSE is registered by klippy/extras/pause_resume.py:79 and runs
        # any user PAUSE macro override.
        self.gcode.run_script_from_command("PAUSE")

    def _wait_for_temperature(self, extruder_name):
        heater = self.printer.lookup_object(extruder_name).get_heater()
        eventtime = self.printer.get_reactor().monotonic()
        _, target = heater.get_temp(eventtime)
        if target <= 0.0:
            return
        self.gcode.respond_info(
            "hotendchanger: waiting for %s to reach %.1fC (within %.1fC)"
            % (extruder_name, target, self.temp_wait_tolerance)
        )
        # Wait loop per TEMPERATURE_WAIT (klippy/extras/heaters.py:1482-1507)
        # including its debugoutput skip, but polled here instead of run as
        # that command because TEMPERATURE_WAIT compares against fixed bounds
        # and never re-reads the target: a target lowered mid-wait (M104 S0)
        # would make it wait forever.
        if self.printer.get_start_args().get("debugoutput") is not None:
            return
        canceled = [False]

        def check(eventtime):
            temp, current_target = heater.get_temp(eventtime)
            result = evaluate_temp_wait(
                temp, current_target, self.temp_wait_tolerance
            )
            if result == TEMP_WAIT_WAITING:
                return True
            if result == TEMP_WAIT_DONE:
                return False
            if result == TEMP_WAIT_CANCELED:
                canceled[0] = True
                return False
            raise ValueError("unhandled temperature wait result %r" % (result,))

        wait_while = getattr(self.printer, "wait_while", None)
        if wait_while is not None:
            wait_while(check)
        else:
            # printer.wait_while postdates stock Klipper, so without it the
            # loop follows stock's own TEMPERATURE_WAIT idiom
            # (klippy/extras/heaters.py:383-389 in stock: poll while not
            # printer.is_shutdown(), reactor.pause one second ahead).
            reactor = self.printer.get_reactor()
            eventtime = reactor.monotonic()
            while not self.printer.is_shutdown() and check(eventtime):
                eventtime = reactor.pause(eventtime + 1.0)
        if canceled[0]:
            raise self.printer.command_error(
                "The target temperature of %s was cleared during the wait."
                " Set a temperature again, then rerun the tool change."
                % (extruder_name,)
            )

    def _do_tool_change(self, gcmd, tool_number):
        decision, message = change_decision(
            self.state, self.active_tool, tool_number
        )
        if decision == CHANGE_REFUSE:
            raise gcmd.error(message)
        elif decision == CHANGE_NOOP:
            gcmd.respond_info(message)
            return
        elif decision == CHANGE_PROCEED:
            pass
        else:
            raise ValueError("unhandled tool change decision %r" % (decision,))
        self._check_homed(gcmd)
        new_tool = self.tools[tool_number]
        old_tool = None
        if self.active_tool is not None:
            old_tool = self.tools[self.active_tool]
        change_context = {
            "old_tool": old_tool.template_context() if old_tool else None,
            "new_tool": new_tool.template_context(),
        }
        self.state = STATE_CHANGING
        outcome = None
        try:
            self._render_and_run(self.before_template, change_context)
            self._set_tool_offset(
                (0.0, 0.0, 0.0), self.gcode.run_script_from_command
            )
            if old_tool is not None:
                self._render_and_run(
                    self.dropoff_template,
                    {
                        "tool": old_tool.template_context(),
                        "params": dict(old_tool.params),
                    },
                )
            self._render_and_run(
                self.pickup_template,
                {
                    "tool": new_tool.template_context(),
                    "params": dict(new_tool.params),
                },
            )
            pin_states = self._pin_states()
            if pin_states:
                mismatch = verify_detected(pin_states, tool_number)
                if mismatch is not None:
                    self.detected_tool = None
                    outcome = "verify_failed"
                    self._pause_print(
                        "Tool change to %s paused the print: %s. Check the"
                        " hotend seating on the carriage and the dock switch"
                        " wiring, then run INITIALIZE_HOTENDCHANGER and"
                        " RESUME." % (new_tool.name, mismatch)
                    )
                    return
                self.detected_tool = tool_number
            self.gcode.run_script_from_command(
                "ACTIVATE_EXTRUDER EXTRUDER=%s" % (new_tool.extruder_name,)
            )
            self._wait_for_temperature(new_tool.extruder_name)
            self._set_tool_offset(
                new_tool.offset, self.gcode.run_script_from_command
            )
            self._render_and_run(self.after_template, change_context)
            outcome = "success"
        finally:
            if outcome == "success":
                self.active_tool = tool_number
                self.state = STATE_READY
            elif outcome == "verify_failed":
                self.active_tool = None
                self.state = STATE_ERROR
            elif outcome is None:
                # An exception (of any kind, BaseException included) left the
                # change unfinished; the mounted tool is no longer known.
                self.active_tool = None
                self.state = STATE_ERROR
            else:
                logging.error(
                    "hotendchanger: unhandled tool change outcome %r", outcome
                )
                self.active_tool = None
                self.state = STATE_ERROR

    def cmd_SET_TOOL_OFFSET(self, gcmd):
        tool_number = gcmd.get_int("T", minval=0)
        if tool_number not in self.tools:
            raise gcmd.error(
                "no hotendchanger_tool %s configured" % (format_tool(tool_number),)
            )
        tool = self.tools[tool_number]
        for axis_index, axis in enumerate("XYZ"):
            value = gcmd.get_float(axis, None)
            if value is not None:
                tool.offset[axis_index] = value
        if tool_number == self.active_tool:
            self._set_tool_offset(
                tool.offset, self.gcode.run_script_from_command
            )
        if gcmd.get_int("SAVE", 0):
            configfile = self.printer.lookup_object("configfile")
            for axis_index, option in enumerate(
                ("gcode_x_offset", "gcode_y_offset", "gcode_z_offset")
            ):
                configfile.set(
                    tool.section_name, option, "%.6f" % (tool.offset[axis_index],)
                )
            gcmd.respond_info("%s offset stored for SAVE_CONFIG" % (tool.name,))

    def cmd_HOTENDCHANGER_STATUS(self, gcmd):
        rows = [
            "active_tool: %s"
            % ("none" if self.active_tool is None else format_tool(self.active_tool)),
            "detected_tool: %s"
            % ("none" if self.detected_tool is None else format_tool(self.detected_tool)),
            "state: %s" % (self.state,),
        ]
        for n in sorted(self.tools):
            tool = self.tools[n]
            if tool.detect_pin is not None:
                rows.append(
                    "%s detect_pin: %s"
                    % (tool.name, describe_pin_state(tool.detect_state))
                )
        for n in sorted(self.tools):
            tool = self.tools[n]
            rows.append(
                "%s offset: X=%.6f Y=%.6f Z=%.6f extruder=%s"
                % (
                    tool.name,
                    tool.offset[0],
                    tool.offset[1],
                    tool.offset[2],
                    tool.extruder_name,
                )
            )
        gcmd.respond_info("\n".join(rows))

    def cmd_INITIALIZE_HOTENDCHANGER(self, gcmd):
        if self.state == STATE_CHANGING:
            raise gcmd.error(
                "A tool change is in progress. Let it finish before"
                " reinitializing."
            )
        asserted = gcmd.get_int("T", None, minval=0)
        if asserted is not None:
            if self._pin_states():
                raise gcmd.error(
                    "Detect pins are configured, so detection determines the"
                    " mounted tool. Run INITIALIZE_HOTENDCHANGER without T."
                )
            if asserted not in self.tools:
                raise gcmd.error(
                    "no hotendchanger_tool %s configured"
                    % (format_tool(asserted),)
                )
            tool = self.tools[asserted]
            self.active_tool = asserted
            self.detected_tool = None
            self.state = STATE_READY
            self._set_tool_offset(
                tool.offset, self.gcode.run_script_from_command
            )
            gcmd.respond_info(
                "hotendchanger: %s asserted as the mounted tool" % (tool.name,)
            )
            return
        self._run_discovery(self.gcode.run_script_from_command)

    def get_status(self, eventtime):
        return {
            "active_tool": self.active_tool,
            "detected_tool": self.detected_tool,
            "state": self.state,
            "tools": {
                tool.name: {
                    "number": n,
                    "extruder": tool.extruder_name,
                    "gcode_x_offset": tool.offset[0],
                    "gcode_y_offset": tool.offset[1],
                    "gcode_z_offset": tool.offset[2],
                    "detect": (
                        describe_pin_state(tool.detect_state)
                        if tool.detect_pin is not None
                        else None
                    ),
                }
                for n, tool in self.tools.items()
            },
        }


def load_config(config):
    return Hotendchanger(config)


def load_config_prefix(config):
    return HotendchangerTool(config)

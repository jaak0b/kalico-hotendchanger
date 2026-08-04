# Hotend changer toolchange plugin; design, Kalico surface provenance and
# behavioral references are recorded in docs/design.md.
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


def format_tool(tool_number):
    return "T%d" % (tool_number,)


def parse_tool_name(name):
    if len(name) < 2 or name[0] != "T" or not name[1:].isdigit():
        return None
    if name[1:] != str(int(name[1:])):
        return None
    return int(name[1:])


def validate_tool_numbers(numbers):
    if not numbers:
        return "no hotendchanger_tool sections configured"
    expected = list(range(len(numbers)))
    if sorted(numbers) != expected:
        return (
            "hotendchanger_tool sections must be numbered T0..T%d with no"
            " gaps or duplicates, found: %s"
            % (len(numbers) - 1, ", ".join(format_tool(n) for n in sorted(numbers)))
        )
    return None


def describe_pin_states(pin_states):
    return ", ".join(
        "%s=%s"
        % (
            format_tool(t),
            "unreported" if s is None else ("triggered" if s else "untriggered"),
        )
        for t, s in sorted(pin_states.items())
    )


def resolve_detection(pin_states, all_tool_numbers):
    if not pin_states:
        return (DETECT_NO_PINS, None, "no detect pins configured")
    reading = describe_pin_states(pin_states)
    unreported = sorted(t for t, s in pin_states.items() if s is None)
    if unreported:
        return (
            DETECT_FAULT,
            None,
            "detect pin state not yet reported for %s (%s)"
            % (", ".join(format_tool(t) for t in unreported), reading),
        )
    untriggered = sorted(t for t, s in pin_states.items() if not s)
    if len(untriggered) == 1:
        return (
            DETECT_MOUNTED,
            untriggered[0],
            "%s detected mounted (%s)" % (format_tool(untriggered[0]), reading),
        )
    if len(untriggered) > 1:
        return (
            DETECT_FAULT,
            None,
            "multiple docks read untriggered: %s (%s)"
            % (", ".join(format_tool(t) for t in untriggered), reading),
        )
    uncovered = sorted(set(all_tool_numbers) - set(pin_states))
    if uncovered:
        return (
            DETECT_FAULT,
            None,
            "all monitored docks triggered but %s have no detect pin, so the"
            " mounted tool cannot be identified (%s)"
            % (", ".join(format_tool(t) for t in uncovered), reading),
        )
    return (DETECT_NONE, None, "all docks triggered, no tool mounted (%s)" % (reading,))


def state_after_discovery(verdict, mounted_tool):
    if verdict == DETECT_MOUNTED:
        return (STATE_READY, mounted_tool)
    if verdict == DETECT_NONE:
        return (STATE_READY, None)
    if verdict == DETECT_FAULT:
        return (STATE_UNKNOWN, None)
    if verdict == DETECT_NO_PINS:
        return (STATE_UNKNOWN, None)
    raise ValueError("unhandled detection verdict %r" % (verdict,))


def verify_mounted(pin_states, expected_tool):
    reading = describe_pin_states(pin_states)
    unreported = sorted(t for t, s in pin_states.items() if s is None)
    if unreported:
        return (
            False,
            "detect pin state not yet reported for %s (%s)"
            % (", ".join(format_tool(t) for t in unreported), reading),
        )
    untriggered = sorted(t for t, s in pin_states.items() if not s)
    if expected_tool in pin_states:
        expected_untriggered = [expected_tool]
    else:
        expected_untriggered = []
    if untriggered == expected_untriggered:
        return (True, reading)
    return (
        False,
        "expected %s mounted but docks read: %s" % (format_tool(expected_tool), reading),
    )


def begin_change_refusal(state):
    if state == STATE_READY:
        return None
    if state == STATE_UNKNOWN:
        return None
    if state == STATE_UNINITIALIZED:
        return (
            "hotendchanger is not initialized yet; wait for startup detection"
            " or run INITIALIZE_HOTENDCHANGER"
        )
    if state == STATE_CHANGING:
        return "tool change already in progress"
    if state == STATE_ERROR:
        return (
            "hotendchanger is in the error state after a failed tool change;"
            " run INITIALIZE_HOTENDCHANGER to clear it"
        )
    raise ValueError("unhandled hotendchanger state %r" % (state,))


class OffsetLedger:
    def __init__(self):
        self.applied = (0.0, 0.0, 0.0)

    def delta_to(self, target):
        target = tuple(float(v) for v in target)
        delta = tuple(t - a for t, a in zip(target, self.applied))
        self.applied = target
        return delta

    def clear(self):
        return self.delta_to((0.0, 0.0, 0.0))


class HotendchangerTool:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.name = config.get_name().split(None, 1)[1]
        self.tool_number = parse_tool_name(self.name)
        if self.tool_number is None:
            raise config.error(
                "hotendchanger_tool section names must follow the T<n>"
                " pattern, got '%s'" % (config.get_name(),)
            )
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
            # (klippy/extras/buttons.py:363 register_buttons): the MCU pushes
            # state reports and the callback caches the latest level, so a
            # read here is the last reported state, None before the first
            # report arrives after connect.
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
            desc="Re-run tool detection from the configured detect pins",
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
        except self.printer.config_error:
            raise config.error(
                "gcode command %s is already defined (a [gcode_macro %s]"
                " conflicts with [hotendchanger_tool %s]); remove one of them"
                % (command, command, tool.name)
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
                    "hotendchanger internal error: %s: %s"
                    % (type(e).__name__, e)
                )

        return wrapper

    def _handle_connect(self):
        error = validate_tool_numbers(sorted(self.tools))
        if error is not None:
            raise self.printer.config_error(error)
        for tool in self.tools.values():
            if self.printer.lookup_object(tool.extruder_name, None) is None:
                raise self.printer.config_error(
                    "hotendchanger_tool %s names extruder section '%s' which"
                    " does not exist" % (tool.name, tool.extruder_name)
                )

    def _handle_ready(self):
        if self._monitored_pin_states():
            self._run_discovery(self.gcode.respond_info)
        else:
            self.state = STATE_UNKNOWN

    def _monitored_pin_states(self):
        return {
            n: tool.detect_state
            for n, tool in self.tools.items()
            if tool.detect_pin is not None
        }

    def _run_discovery(self, respond_info):
        verdict, mounted, message = resolve_detection(
            self._monitored_pin_states(), self.tools
        )
        self.state, self.active_tool = state_after_discovery(verdict, mounted)
        self.detected_tool = mounted
        respond_info("hotendchanger detection: %s" % (message,))
        if self.active_tool is not None:
            self._apply_tool_offset(self.tools[self.active_tool].offset)
        else:
            self._clear_tool_offset()

    def _send_offset_adjust(self, delta):
        if delta == (0.0, 0.0, 0.0):
            return
        # X_ADJUST/Y_ADJUST/Z_ADJUST adjust the offset incrementally
        # (klippy/extras/gcode_move.py:258-270), so a babystepping offset the
        # user applied on top is preserved.
        self.gcode.run_script_from_command(
            "SET_GCODE_OFFSET X_ADJUST=%.6f Y_ADJUST=%.6f Z_ADJUST=%.6f"
            % delta
        )

    def _apply_tool_offset(self, offset):
        self._send_offset_adjust(self.ledger.delta_to(offset))

    def _clear_tool_offset(self):
        self._send_offset_adjust(self.ledger.clear())

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
                "tool change requires XYZ homed; not homed: %s"
                % (", ".join(missing),)
            )

    def _pause_print(self, message):
        self.gcode.respond_info(message)
        if self.printer.lookup_object("pause_resume", None) is None:
            raise self.printer.command_error(
                "%s (and [pause_resume] is not configured, so the print"
                " cannot be paused automatically)" % (message,)
            )
        # PAUSE is registered by klippy/extras/pause_resume.py:79 and runs
        # any user PAUSE macro override.
        self.gcode.run_script_from_command("PAUSE")

    def _wait_for_temperature(self, extruder_name):
        heater = self.printer.lookup_object(extruder_name).get_heater()
        eventtime = self.printer.get_reactor().monotonic()
        _, target = heater.get_temp(eventtime)
        if target <= 0.0:
            return
        # Wait loop pattern from TEMPERATURE_WAIT
        # (klippy/extras/heaters.py:1482-1507), including the debugoutput
        # skip so batch test runs do not hang; the window here is symmetric
        # around the target per docs/design.md so an overshooting preheated
        # hotend does not stall the wait.
        if self.printer.get_start_args().get("debugoutput") is not None:
            return
        tolerance = self.temp_wait_tolerance

        def check(eventtime):
            temp, current_target = heater.get_temp(eventtime)
            return abs(temp - current_target) > tolerance

        self.printer.wait_while(check)

    def _do_tool_change(self, gcmd, tool_number):
        if tool_number == self.active_tool:
            return
        refusal = begin_change_refusal(self.state)
        if refusal is not None:
            raise gcmd.error(refusal)
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
        try:
            self._render_and_run(self.before_template, change_context)
            self._clear_tool_offset()
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
            pin_states = self._monitored_pin_states()
            if pin_states:
                ok, detail = verify_mounted(pin_states, tool_number)
                self.detected_tool = tool_number if ok else None
                if not ok:
                    self._pause_print(
                        "hotendchanger: tool verification failed after picking"
                        " up %s: %s" % (new_tool.name, detail)
                    )
                    raise gcmd.error(
                        "tool verification failed after picking up %s: %s"
                        % (new_tool.name, detail)
                    )
            self.gcode.run_script_from_command(
                "ACTIVATE_EXTRUDER EXTRUDER=%s" % (new_tool.extruder_name,)
            )
            self._wait_for_temperature(new_tool.extruder_name)
            self._apply_tool_offset(new_tool.offset)
            self._render_and_run(self.after_template, change_context)
        except Exception:
            self.state = STATE_ERROR
            self.active_tool = None
            raise
        self.active_tool = tool_number
        self.state = STATE_READY

    def cmd_SET_TOOL_OFFSET(self, gcmd):
        tool_number = gcmd.get_int("T", minval=0)
        if tool_number not in self.tools:
            raise gcmd.error("no hotendchanger_tool %s configured" % (format_tool(tool_number),))
        tool = self.tools[tool_number]
        for axis_index, axis in enumerate("XYZ"):
            value = gcmd.get_float(axis, None)
            if value is not None:
                tool.offset[axis_index] = value
        if tool_number == self.active_tool:
            self._apply_tool_offset(tool.offset)
        if gcmd.get_int("SAVE", 0):
            configfile = self.printer.lookup_object("configfile")
            section = "hotendchanger_tool %s" % (tool.name,)
            for axis_index, option in enumerate(
                ("gcode_x_offset", "gcode_y_offset", "gcode_z_offset")
            ):
                configfile.set(section, option, "%.6f" % (tool.offset[axis_index],))
            gcmd.respond_info(
                "%s offset stored for SAVE_CONFIG" % (tool.name,)
            )

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
                if tool.detect_state is None:
                    reading = "unreported"
                elif tool.detect_state:
                    reading = "triggered"
                else:
                    reading = "untriggered"
                rows.append("%s detect_pin: %s" % (tool.name, reading))
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
        if not self._monitored_pin_states():
            self.state = STATE_UNKNOWN
            self.active_tool = None
            self.detected_tool = None
            self._clear_tool_offset()
            gcmd.respond_info(
                "hotendchanger: no detect pins configured, state set to"
                " unknown; the next T<n> command runs pickup only"
            )
            return
        self._run_discovery(gcmd.respond_info)

    def get_status(self, eventtime):
        return {
            "active_tool": self.active_tool,
            "detected_tool": self.detected_tool,
            "state": self.state,
            "tools": {
                n: {
                    "name": tool.name,
                    "extruder": tool.extruder_name,
                    "gcode_x_offset": tool.offset[0],
                    "gcode_y_offset": tool.offset[1],
                    "gcode_z_offset": tool.offset[2],
                }
                for n, tool in self.tools.items()
            },
        }


def load_config(config):
    return Hotendchanger(config)


def load_config_prefix(config):
    return HotendchangerTool(config)

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
DETECT_UNIDENTIFIED = "unidentified_mounted"
DETECT_FAULT = "fault"
DETECT_NO_PINS = "no_pins"

LOSS_FIRE = "fire"
LOSS_WAIT = "wait"
LOSS_IDLE = "idle"

REDISCOVER_RUN = "run"
REDISCOVER_WAIT = "wait"
REDISCOVER_CANCEL = "cancel"

CHANGE_PROCEED = "proceed"
CHANGE_NOOP = "noop"
CHANGE_REFUSE = "refuse"

PRINT_STATE_PRINTING = "printing"
PRINT_STATE_PAUSED = "paused"
PRINT_STATE_STANDBY = "standby"

# The full state set print_stats reports, identical in Kalico
# (klippy/extras/print_stats.py:46-111) and stock Klipper
# (klippy/extras/print_stats.py:44-92).
PRINT_STATS_FINISHED_STATES = ("standby", "complete", "cancelled", "error")

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


def validate_stepper_carrier(tools_with_stepper):
    if len(tools_with_stepper) == 1:
        return None
    if not tools_with_stepper:
        return (
            "no tool's extruder section has a stepper; exactly one extruder"
            " section must carry the physical stepper (step_pin, dir_pin,"
            " rotation_distance)"
        )
    return (
        "the extruder sections of %s all have steppers; only one extruder"
        " section may carry the physical stepper, the others hold heater and"
        " sensor options only"
        % (", ".join(format_tool(n) for n in sorted(tools_with_stepper)),)
    )


def describe_pin_state(state):
    return "triggered" if state else "untriggered"


def describe_pin_states(pin_states):
    return ", ".join(
        "%s=%s" % (format_tool(t), describe_pin_state(s))
        for t, s in sorted(pin_states.items())
    )


def describe_toolhead_state(toolhead_present):
    return "toolhead_detect_pin=%s" % (describe_pin_state(toolhead_present),)


def resolve_detection(pin_states, toolhead_present):
    if not pin_states and toolhead_present is None:
        return (
            DETECT_NO_PINS,
            None,
            "no detect pins configured, the mounted tool is unknown",
        )
    if not pin_states:
        if toolhead_present:
            return (
                DETECT_UNIDENTIFIED,
                None,
                "a hotend is mounted on the toolhead (%s) but there are no"
                " dock pins to identify it; run INITIALIZE_HOTENDCHANGER"
                " T=<n> to name it" % (describe_toolhead_state(True),),
            )
        return (
            DETECT_NONE,
            None,
            "no tool mounted (%s)" % (describe_toolhead_state(False),),
        )
    reading = describe_pin_states(pin_states)
    if toolhead_present is not None:
        reading = "%s, %s" % (reading, describe_toolhead_state(toolhead_present))
    untriggered = sorted(t for t, s in pin_states.items() if not s)
    if len(untriggered) == 1:
        if toolhead_present is False:
            return (
                DETECT_FAULT,
                None,
                "%s's dock reads empty but the toolhead pin reads no hotend"
                " mounted (%s)" % (format_tool(untriggered[0]), reading),
            )
        return (
            DETECT_MOUNTED,
            untriggered[0],
            "%s detected mounted (%s)" % (format_tool(untriggered[0]), reading),
        )
    if not untriggered:
        if toolhead_present is True:
            return (
                DETECT_FAULT,
                None,
                "the toolhead pin reads a hotend mounted but every dock reads"
                " occupied (%s)" % (reading,),
            )
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
    if verdict == DETECT_UNIDENTIFIED:
        return (STATE_UNKNOWN, None)
    if verdict == DETECT_FAULT:
        return (STATE_UNKNOWN, None)
    if verdict == DETECT_NO_PINS:
        return (STATE_UNKNOWN, None)
    raise ValueError("unhandled detection verdict %r" % (verdict,))


def verify_detected(pin_states, toolhead_present, expected_tool):
    if not pin_states and toolhead_present is None:
        # Verification is only reached when detect pins exist, so an empty
        # reading here is a caller bug, not an operator condition.
        raise ValueError("verification requires detect pin readings")
    problems = []
    if pin_states:
        verdict, detected, message = resolve_detection(pin_states, None)
        if verdict == DETECT_MOUNTED:
            if detected != expected_tool:
                problems.append(
                    "expected %s mounted, but %s"
                    % (format_tool(expected_tool), message)
                )
        elif verdict == DETECT_NONE:
            problems.append(
                "expected %s mounted, but %s"
                % (format_tool(expected_tool), message)
            )
        elif verdict == DETECT_FAULT:
            problems.append(
                "expected %s mounted, but %s"
                % (format_tool(expected_tool), message)
            )
        elif verdict == DETECT_UNIDENTIFIED:
            raise ValueError(
                "dock-only resolution returned %r" % (verdict,)
            )
        elif verdict == DETECT_NO_PINS:
            raise ValueError(
                "dock-only resolution returned %r" % (verdict,)
            )
        else:
            raise ValueError("unhandled detection verdict %r" % (verdict,))
    if toolhead_present is False:
        problems.append(
            "the toolhead reads no hotend mounted after pickup (%s)"
            % (describe_toolhead_state(False),)
        )
    if not problems:
        return None
    return "; ".join(problems)


def verify_dropoff_released(toolhead_present):
    if toolhead_present is None:
        return None
    if toolhead_present:
        return (
            "the toolhead still reads a hotend mounted after dropoff (%s),"
            " the hotend failed to release"
            % (describe_toolhead_state(True),)
        )
    return None


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


def classify_print_state(is_paused, print_stats_state, sd_active):
    if is_paused:
        return PRINT_STATE_PAUSED
    if print_stats_state is None:
        if sd_active:
            return PRINT_STATE_PRINTING
        return PRINT_STATE_STANDBY
    if print_stats_state == "printing":
        return PRINT_STATE_PRINTING
    if print_stats_state == "paused":
        return PRINT_STATE_PAUSED
    if print_stats_state in PRINT_STATS_FINISHED_STATES:
        return PRINT_STATE_STANDBY
    raise ValueError("unhandled print_stats state %r" % (print_stats_state,))


def mismatch_pauses(print_state):
    if print_state == PRINT_STATE_PRINTING:
        return True
    if print_state == PRINT_STATE_PAUSED:
        return False
    if print_state == PRINT_STATE_STANDBY:
        return False
    raise ValueError("unhandled print state %r" % (print_state,))


def _monitor_conditions_met(changer_state, print_state, active_tool):
    if changer_state == STATE_READY:
        pass
    elif changer_state in ALL_STATES:
        return False
    else:
        raise ValueError("unhandled hotendchanger state %r" % (changer_state,))
    if active_tool is None:
        return False
    if print_state == PRINT_STATE_PRINTING:
        return True
    if print_state in (PRINT_STATE_PAUSED, PRINT_STATE_STANDBY):
        return False
    raise ValueError("unhandled print state %r" % (print_state,))


def evaluate_toolhead_loss(
    toolhead_present,
    absent_since,
    now,
    settle_time,
    changer_state,
    print_state,
    active_tool,
    already_fired,
):
    if toolhead_present:
        return LOSS_IDLE
    if absent_since is None:
        return LOSS_IDLE
    if already_fired:
        return LOSS_IDLE
    if not _monitor_conditions_met(changer_state, print_state, active_tool):
        return LOSS_IDLE
    if now - absent_since < settle_time:
        return LOSS_WAIT
    return LOSS_FIRE


def evaluate_rediscovery(last_edge, now, settle_time, changer_state, print_state):
    if last_edge is None:
        return REDISCOVER_CANCEL
    if changer_state == STATE_CHANGING:
        return REDISCOVER_CANCEL
    if changer_state not in ALL_STATES:
        raise ValueError("unhandled hotendchanger state %r" % (changer_state,))
    if print_state == PRINT_STATE_PRINTING:
        return REDISCOVER_CANCEL
    if print_state not in (PRINT_STATE_PAUSED, PRINT_STATE_STANDBY):
        raise ValueError("unhandled print state %r" % (print_state,))
    if now - last_edge < settle_time:
        return REDISCOVER_WAIT
    return REDISCOVER_RUN


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

# Delay between finishing motion and reading a cached pin state: the MCU
# button poll (buttons.py:12, QUERY_TIME=0.002s) plus the async callback
# delivery (buttons.py:84-93) lag the physical event by a few poll cycles,
# so 0.05s covers the report path.
DETECT_REPORT_DELAY = 0.05


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
        self.changer = self.printer.load_object(config, "hotendchanger")
        self.changer.register_tool(config, self)

    def _detect_callback(self, eventtime, state):
        self.detect_state = bool(state)
        self.changer.note_detect_edge(eventtime)

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
        self.detect_settle_time = config.getfloat(
            "detect_settle_time", 1.0, above=0.0
        )
        self.toolhead_detect_pin = config.get("toolhead_detect_pin", None)
        self.toolhead_state = None
        if self.toolhead_detect_pin is not None:
            # Same buttons baseline as the per-tool detect pins (see
            # HotendchangerTool): a pin steady at the normalized 0 level never
            # reports, and False is that baseline; a hotend mounted at boot
            # drives the pin to 1, which the MCU's first poll reports as an
            # edge.
            self.toolhead_state = False
            buttons = self.printer.load_object(config, "buttons")
            buttons.register_buttons(
                [self.toolhead_detect_pin], self._toolhead_callback
            )
        self.toolhead_absent_since = None
        self.toolhead_loss_fired = False
        self.last_detect_edge = None
        self.loss_timer = None
        self.rediscovery_timer = None
        self.stepper_extruder_name = None
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
        stepper_tools = []
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
            if getattr(extruder, "extruder_stepper", None) is not None:
                stepper_tools.append(tool.tool_number)
        message = validate_stepper_carrier(stepper_tools)
        if message is not None:
            raise self.printer.config_error(message)
        self.stepper_extruder_name = self.tools[stepper_tools[0]].extruder_name

    def _handle_ready(self):
        # In debugoutput batch mode no MCU pushes button reports and the test
        # gcode runs immediately after ready, so discovery runs inline there.
        if self.printer.get_start_args().get("debugoutput") is not None:
            self._run_discovery_guarded()
            return
        reactor = self.printer.get_reactor()
        self.loss_timer = reactor.register_timer(self._toolhead_loss_timer)
        self.rediscovery_timer = reactor.register_timer(
            self._rediscovery_timer
        )
        reactor.register_timer(
            self._startup_discovery_timer,
            reactor.monotonic() + STARTUP_DETECT_DELAY,
        )

    def _startup_discovery_timer(self, eventtime):
        self._run_discovery_guarded()
        return self.printer.get_reactor().NEVER

    def _run_discovery_guarded(self):
        try:
            self._run_discovery(self.gcode.run_script)
        except Exception:
            logging.exception("hotendchanger: detection failed")
            self.state = STATE_ERROR
            self.gcode.respond_info(
                "hotendchanger: detection failed. Check the klippy log for"
                " the traceback, fix the cause, then run"
                " INITIALIZE_HOTENDCHANGER."
            )

    def note_detect_edge(self, eventtime):
        try:
            if self.rediscovery_timer is None:
                return
            action = evaluate_rediscovery(
                eventtime,
                eventtime,
                self.detect_settle_time,
                self.state,
                self._print_state(),
            )
            if action == REDISCOVER_CANCEL:
                return
            if action == REDISCOVER_WAIT or action == REDISCOVER_RUN:
                self.last_detect_edge = eventtime
                self.printer.get_reactor().update_timer(
                    self.rediscovery_timer,
                    eventtime + self.detect_settle_time,
                )
                return
            raise ValueError("unhandled rediscovery action %r" % (action,))
        except Exception:
            logging.exception("hotendchanger: detect edge handling failed")

    def _toolhead_callback(self, eventtime, state):
        try:
            present = bool(state)
            self.toolhead_state = present
            if present:
                self.toolhead_absent_since = None
                self.toolhead_loss_fired = False
            else:
                self.toolhead_absent_since = eventtime
                if self.loss_timer is not None:
                    self.printer.get_reactor().update_timer(
                        self.loss_timer,
                        eventtime + self.detect_settle_time,
                    )
            self.note_detect_edge(eventtime)
        except Exception:
            logging.exception("hotendchanger: toolhead pin handling failed")

    def _toolhead_loss_timer(self, eventtime):
        reactor = self.printer.get_reactor()
        try:
            action = evaluate_toolhead_loss(
                self.toolhead_state,
                self.toolhead_absent_since,
                eventtime,
                self.detect_settle_time,
                self.state,
                self._print_state(),
                self.active_tool,
                self.toolhead_loss_fired,
            )
            if action == LOSS_IDLE:
                return reactor.NEVER
            if action == LOSS_WAIT:
                return self.toolhead_absent_since + self.detect_settle_time
            if action == LOSS_FIRE:
                self.toolhead_loss_fired = True
                self._handle_toolhead_loss()
                return reactor.NEVER
            raise ValueError("unhandled loss action %r" % (action,))
        except Exception:
            logging.exception("hotendchanger: toolhead monitor failed")
            return reactor.NEVER

    def _handle_toolhead_loss(self):
        lost = format_tool(self.active_tool)
        self.active_tool = None
        self.detected_tool = None
        self.state = STATE_ERROR
        self.gcode.respond_info(
            "hotendchanger: %s was lost from the toolhead"
            " (toolhead_detect_pin read untriggered for %.1fs). Pausing the"
            " print and turning off all heaters. Re-seat the hotend, run"
            " INITIALIZE_HOTENDCHANGER, then RESUME."
            % (lost, self.detect_settle_time)
        )
        pause_resume = self.printer.lookup_object("pause_resume", None)
        if pause_resume is not None:
            # Pause-from-event pattern per stock
            # klippy/extras/filament_switch_sensor.py:45-53:
            # send_pause_command immediately, then the PAUSE script.
            pause_resume.send_pause_command()
            self.gcode.run_script("PAUSE\nTURN_OFF_HEATERS")
        else:
            self.gcode.respond_info(
                "hotendchanger: no [pause_resume] section is configured, so"
                " the print cannot be paused automatically."
            )
            self.gcode.run_script("TURN_OFF_HEATERS")
        self._detach_stepper(self.gcode.run_script)

    def _rediscovery_timer(self, eventtime):
        reactor = self.printer.get_reactor()
        try:
            action = evaluate_rediscovery(
                self.last_detect_edge,
                eventtime,
                self.detect_settle_time,
                self.state,
                self._print_state(),
            )
            if action == REDISCOVER_WAIT:
                return self.last_detect_edge + self.detect_settle_time
            if action == REDISCOVER_CANCEL:
                self.last_detect_edge = None
                return reactor.NEVER
            if action == REDISCOVER_RUN:
                self.last_detect_edge = None
                self.gcode.respond_info(
                    "hotendchanger: detect pins changed while no print is"
                    " running, re-running detection"
                )
                self._run_discovery_guarded()
                return reactor.NEVER
            raise ValueError("unhandled rediscovery action %r" % (action,))
        except Exception:
            logging.exception("hotendchanger: rediscovery failed")
            return reactor.NEVER

    def _pin_states(self):
        return {
            n: tool.detect_state
            for n, tool in self.tools.items()
            if tool.detect_pin is not None
        }

    def _run_discovery(self, script_runner):
        verdict, detected, message = resolve_detection(
            self._pin_states(), self.toolhead_state
        )
        self.state, self.active_tool = state_after_discovery(verdict, detected)
        self.detected_tool = detected
        self.gcode.respond_info("hotendchanger detection: %s" % (message,))
        if self.active_tool is not None:
            self._activate_extruder(
                self.tools[self.active_tool].extruder_name, script_runner
            )
            target = self.tools[self.active_tool].offset
        else:
            if verdict == DETECT_NONE:
                self._detach_stepper(script_runner)
            target = (0.0, 0.0, 0.0)
        self._set_tool_offset(target, script_runner)

    def _activate_extruder(self, extruder_name, script_runner):
        script_runner("ACTIVATE_EXTRUDER EXTRUDER=%s" % (extruder_name,))
        # ACTIVATE_EXTRUDER switches only the toolhead's active extruder
        # (Kalico klippy/kinematics/extruder.py:413-421, stock :283-290); a
        # stepper synced to another motion queue stays there, so the single
        # physical stepper is re-synced on every activation, T0 included.
        # SYNC_EXTRUDER_MOTION is keyed EXTRUDER=<section with the stepper>
        # (Kalico extruder.py:64-69, stock :38-40) and MOTION_QUEUE accepts a
        # heater-only extruder section: every PrinterExtruder allocates a
        # trapq (Kalico :240, stock :174) and sync_to_extruder attaches the
        # stepper to it (Kalico :88-103, stock :50-66).
        if self.stepper_extruder_name is not None:
            script_runner(
                "SYNC_EXTRUDER_MOTION EXTRUDER=%s MOTION_QUEUE=%s"
                % (self.stepper_extruder_name, extruder_name)
            )

    def _detach_stepper(self, script_runner):
        # An empty MOTION_QUEUE detaches the stepper from every motion queue
        # (sync_to_extruder's empty-name branch, Kalico
        # klippy/kinematics/extruder.py:91-94, stock :53-57), so G1 E moves
        # no motor until the next activation re-syncs it.
        if self.stepper_extruder_name is not None:
            script_runner(
                "SYNC_EXTRUDER_MOTION EXTRUDER=%s MOTION_QUEUE="
                % (self.stepper_extruder_name,)
            )

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

    def _print_state(self):
        # Print activity is read from the same surfaces pause_resume itself
        # uses, present in both firmwares: pause_resume's is_paused status
        # (stock klippy/extras/pause_resume.py:41-44), print_stats' state
        # (stock print_stats.py:44-92, Kalico print_stats.py:46-111), and
        # virtual_sdcard.is_active() (stock virtual_sdcard.py:116, Kalico
        # :135; the activity test pause_resume.is_sd_active applies at stock
        # pause_resume.py:45-46). Each object is optional in config, so each
        # is reached defensively.
        eventtime = self.printer.get_reactor().monotonic()
        pause_resume = self.printer.lookup_object("pause_resume", None)
        is_paused = bool(
            pause_resume is not None
            and pause_resume.get_status(eventtime)["is_paused"]
        )
        print_stats = self.printer.lookup_object("print_stats", None)
        stats_state = (
            print_stats.get_status(eventtime)["state"]
            if print_stats is not None
            else None
        )
        virtual_sdcard = self.printer.lookup_object("virtual_sdcard", None)
        sd_active = bool(
            virtual_sdcard is not None and virtual_sdcard.is_active()
        )
        return classify_print_state(is_paused, stats_state, sd_active)

    def _settle_pin_reports(self):
        # Templates queue motion; the pins reflect reality only after the
        # queued moves finish (M400) plus the button report path covered by
        # DETECT_REPORT_DELAY.
        self.gcode.run_script_from_command("M400")
        reactor = self.printer.get_reactor()
        reactor.pause(reactor.monotonic() + DETECT_REPORT_DELAY)

    def _fail_verification(self, gcmd, diagnostic):
        if mismatch_pauses(self._print_state()):
            self._pause_print(
                "%s The print is paused; RESUME continues it." % (diagnostic,)
            )
            return
        raise gcmd.error(diagnostic)

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
        # TEMPERATURE_WAIT (Kalico klippy/extras/heaters.py:1482-1507, stock
        # klippy/extras/heaters.py:367-389) bounds the temperature on both
        # sides and accepts the extruder section name as SENSOR. Its fixed
        # window is safe because the gcode mutex is held for the whole tool
        # change handler, so nothing can change the target mid-wait; shutdown
        # or a command interrupt aborts the wait's own loop.
        self.gcode.run_script_from_command(
            "TEMPERATURE_WAIT SENSOR=%s MINIMUM=%.6f MAXIMUM=%.6f"
            % (
                extruder_name,
                target - self.temp_wait_tolerance,
                target + self.temp_wait_tolerance,
            )
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
                if self.toolhead_detect_pin is not None:
                    self._settle_pin_reports()
                    release_problem = verify_dropoff_released(
                        self.toolhead_state
                    )
                    if release_problem is not None:
                        self.detected_tool = None
                        outcome = "verify_failed"
                        self._fail_verification(
                            gcmd,
                            "Tool change to %s failed after dropoff of %s:"
                            " %s. Check the dock and the coupling, then run"
                            " INITIALIZE_HOTENDCHANGER."
                            % (new_tool.name, old_tool.name, release_problem),
                        )
                        return
            self._render_and_run(
                self.pickup_template,
                {
                    "tool": new_tool.template_context(),
                    "params": dict(new_tool.params),
                },
            )
            has_dock_pins = bool(self._pin_states())
            if has_dock_pins or self.toolhead_detect_pin is not None:
                self._settle_pin_reports()
                pin_states = self._pin_states()
                mismatch = verify_detected(
                    pin_states, self.toolhead_state, tool_number
                )
                if mismatch is not None:
                    self.detected_tool = None
                    outcome = "verify_failed"
                    self._fail_verification(
                        gcmd,
                        "Tool change to %s failed verification: %s. Check"
                        " the hotend seating on the carriage and the dock"
                        " switch wiring, then run INITIALIZE_HOTENDCHANGER."
                        % (new_tool.name, mismatch),
                    )
                    return
                if pin_states:
                    self.detected_tool = tool_number
            self._activate_extruder(
                new_tool.extruder_name, self.gcode.run_script_from_command
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
        if self.toolhead_detect_pin is not None:
            rows.append(
                "toolhead_detect_pin: %s"
                % (describe_pin_state(self.toolhead_state),)
            )
        motion_queue = self._stepper_motion_queue()
        rows.append(
            "stepper motion_queue: %s"
            % ("none" if motion_queue is None else motion_queue,)
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
            if self.toolhead_state is False:
                raise gcmd.error(
                    "The toolhead_detect_pin reads untriggered, so no hotend"
                    " is mounted; %s cannot be asserted as the mounted tool."
                    % (format_tool(asserted),)
                )
            tool = self.tools[asserted]
            self.active_tool = asserted
            self.detected_tool = None
            self.state = STATE_READY
            self._activate_extruder(
                tool.extruder_name, self.gcode.run_script_from_command
            )
            self._set_tool_offset(
                tool.offset, self.gcode.run_script_from_command
            )
            gcmd.respond_info(
                "hotendchanger: %s asserted as the mounted tool" % (tool.name,)
            )
            return
        self._run_discovery(self.gcode.run_script_from_command)

    def _stepper_motion_queue(self):
        if self.stepper_extruder_name is None:
            return None
        extruder = self.printer.lookup_object(self.stepper_extruder_name, None)
        stepper = getattr(extruder, "extruder_stepper", None)
        if stepper is None:
            return None
        eventtime = self.printer.get_reactor().monotonic()
        return stepper.get_status(eventtime)["motion_queue"]

    def get_status(self, eventtime):
        return {
            "active_tool": self.active_tool,
            "detected_tool": self.detected_tool,
            "state": self.state,
            "toolhead_detect": (
                describe_pin_state(self.toolhead_state)
                if self.toolhead_detect_pin is not None
                else None
            ),
            "stepper_motion_queue": self._stepper_motion_queue(),
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

import argparse
import fnmatch
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hotendchanger  # noqa: E402

CONFIG_FILE_NAME = "hotendchanger.cfg"
MAIN_CONFIG_NAME = "printer.cfg"
INCLUDE_LINE = "[include %s]" % (CONFIG_FILE_NAME,)
PLUGIN_SECTION = "hotendchanger"
TOOL_SECTION_PREFIX = "hotendchanger_tool "
MAIN_EXTRUDER = "extruder"
# Klipper loads [extruder], [extruder1] ... [extruder98] and stops at the
# first missing one (Kalico klippy/kinematics/extruder.py:451-458), so each
# tool's own section caps the tool count at 99.
MAX_TOOLS = 99
# Kalico klippy/configfile.py:335-338: the line opening the SAVE_CONFIG
# block; every line after it carries this prefix and is parsed as config.
AUTOSAVE_MARKER = "#*# <---------------------- SAVE_CONFIG ---------------------->"
AUTOSAVE_PREFIX = "#*# "
# configparser.RawConfigParser.SECTCRE, which Kalico matches against each
# unindented line (klippy/configfile.py:478-479).
SECTION_RE = re.compile(r"\[(?P<header>.+)\]")
EXTRUDER_RE = re.compile(r"extruder(?P<index>[1-9][0-9]*)?")

SETUP_NONE = 0
SETUP_OWN_FILE = 3
SETUP_ELSEWHERE = 4
INCLUDE_WRITTEN = 0
INCLUDE_PRESENT = 3

NOTE = "note"
VALUE = "value"
DEFAULT = "default"
FILL = "fill"

DOCK_PARAMS = ("params_dock_x", "params_dock_y", "params_dock_z")
GCODE_OFFSETS = ("gcode_x_offset", "gcode_y_offset", "gcode_z_offset")
STARTER_MOVES = (
    "G1 X{params.dock_x} Y{params.dock_y} F6000",
    "G1 Z{params.dock_z} F600",
)
FILE_HEADER = (
    "# Uncomment an option only after filling in its value:"
    " a blank option does not parse."
)


class SetupError(Exception):
    pass


def normalize_section(header):
    return " ".join(header.split()).lower()


def parse_config_text(text):
    sections = []
    includes = []
    in_autosave = False
    for line in text.split("\n"):
        if line.rstrip() == AUTOSAVE_MARKER:
            in_autosave = True
            continue
        if in_autosave:
            if not line.startswith(AUTOSAVE_PREFIX):
                continue
            line = line[len(AUTOSAVE_PREFIX):]
        pos = line.find("#")
        if pos >= 0:
            line = line[:pos]
        match = SECTION_RE.match(line)
        if match is None:
            continue
        header = match.group("header")
        if header.startswith("include "):
            includes.append(header[len("include "):].strip())
        else:
            sections.append(normalize_section(header))
    return sections, includes


def _read_text(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError as e:
        raise SetupError("cannot read %s (%s)" % (path, e))


def _scan_file(path, found, visited):
    if path in visited:
        raise SetupError(
            "%s includes itself through its [include] sections; Klipper"
            " refuses to start with this config" % (path,)
        )
    visited.add(path)
    sections, includes = parse_config_text(_read_text(path))
    for section in sections:
        found.setdefault(section, []).append(path)
    for spec in includes:
        # Include paths resolve against the including file's directory
        # (Kalico klippy/configfile.py:444-463).
        pattern = os.path.join(os.path.dirname(path), spec)
        names = sorted(glob.glob(pattern, recursive=True))
        if not names and not glob.has_magic(pattern):
            raise SetupError(
                "%s includes %s, which does not exist; Klipper refuses to"
                " start with this include. Remove the include or restore the"
                " file, then rerun." % (path, pattern)
            )
        for name in names:
            _scan_file(os.path.abspath(name), found, visited)
    visited.remove(path)


def scan_config(config_dir):
    main_path = os.path.abspath(os.path.join(config_dir, MAIN_CONFIG_NAME))
    if not os.path.isfile(main_path):
        raise SetupError(
            "%s not found; pass Moonraker's data directory with --printer-data"
            % (main_path,)
        )
    found = {}
    _scan_file(main_path, found, set())
    return found


def is_plugin_section(section):
    return section == PLUGIN_SECTION or section.startswith(TOOL_SECTION_PREFIX)


def extruder_index(section):
    match = EXTRUDER_RE.fullmatch(section)
    if match is None:
        return None
    index = match.group("index")
    return 0 if index is None else int(index)


def extruder_name(index):
    return MAIN_EXTRUDER if index == 0 else "%s%d" % (MAIN_EXTRUDER, index)


def classify_existing_setup(own_file, own_exists, other_sections, loaded_sections, own_sections):
    reasons = []
    elsewhere = False
    for section in sorted(other_sections):
        if is_plugin_section(section):
            elsewhere = True
            for path in other_sections[section]:
                reasons.append("[%s] in %s" % (section, path))
    for section in sorted(loaded_sections):
        if (
            is_plugin_section(section)
            and section not in other_sections
            and section not in own_sections
        ):
            elsewhere = True
            reasons.append("[%s] in the config Klipper has loaded" % (section,))
    if elsewhere:
        return SETUP_ELSEWHERE, reasons
    if own_exists:
        return SETUP_OWN_FILE, ["%s exists" % (own_file,)]
    return SETUP_NONE, []


def existing_extruders(other_sections, loaded_sections, own_sections):
    names = set(s for s in other_sections if extruder_index(s) is not None)
    names.update(
        s
        for s in loaded_sections
        if extruder_index(s) is not None and s not in own_sections
    )
    return sorted(names, key=extruder_index)


def suggest_new_extruder(existing, chosen):
    taken = set(existing) | set(chosen)
    for index in range(1, MAX_TOOLS):
        name = extruder_name(index)
        if name not in taken:
            return name
    raise SetupError(
        "every extruder section name Klipper loads ([extruder1] to [%s]) is"
        " already taken" % (extruder_name(MAX_TOOLS - 1),)
    )


def validate_answers(extruders):
    if not extruders:
        raise SetupError("at least one tool is needed")
    if len(extruders) > MAX_TOOLS:
        raise SetupError(
            "%d tools requested; Klipper loads at most %d extruder sections"
            % (len(extruders), MAX_TOOLS)
        )
    if extruders[0] != MAIN_EXTRUDER:
        raise SetupError(
            "T0 must use [%s], the section with the extruder motor, got [%s]"
            % (MAIN_EXTRUDER, extruders[0])
        )
    for number, name in enumerate(extruders):
        if extruder_index(name) is None:
            raise SetupError(
                "%s names [%s], which Klipper does not load as an extruder;"
                " use [extruder] or [extruderN]"
                % (hotendchanger.format_tool(number), name)
            )
    error = hotendchanger.validate_tool_extruders(dict(enumerate(extruders)))
    if error is not None:
        raise SetupError(error)


def plugin_section(toolhead_sensor):
    entries = [
        (NOTE, "Starter templates: adapt the moves to your machine before the first tool change."),
        (VALUE, "pickup_gcode", STARTER_MOVES),
        (VALUE, "dropoff_gcode", STARTER_MOVES),
    ]
    if toolhead_sensor:
        entries += [
            (NOTE, "Fill in the toolhead sensor pin."),
            (FILL, "toolhead_detect_pin"),
        ]
    return (PLUGIN_SECTION, entries)


def tool_section(number, extruder, dock_sensors):
    entries = [(VALUE, "extruder", extruder)]
    if number > 0:
        entries += [(DEFAULT, option, "0") for option in GCODE_OFFSETS]
    entries.append((NOTE, "Fill in this tool's dock position."))
    entries += [(FILL, option) for option in DOCK_PARAMS]
    if dock_sensors:
        entries += [(NOTE, "Fill in this dock's sensor pin."), (FILL, "detect_pin")]
    return ("hotendchanger_tool %s" % (hotendchanger.format_tool(number),), entries)


def heater_only_extruder_section(name):
    # Every extruder section, heater-only included, must set nozzle_diameter
    # and filament_diameter (Kalico klippy/kinematics/extruder.py:208-211),
    # min_temp and max_temp (klippy/extras/heaters.py:75-76) and heater_pin
    # (heaters.py:115).
    return (
        name,
        [
            (NOTE, "Fill in these values for this hotend."),
            (FILL, "heater_pin"),
            (FILL, "sensor_type"),
            (FILL, "sensor_pin"),
            (VALUE, "control", "pid"),
            (FILL, "pid_Kp"),
            (FILL, "pid_Ki"),
            (FILL, "pid_Kd"),
            (FILL, "min_temp"),
            (FILL, "max_temp"),
            (FILL, "nozzle_diameter"),
            (FILL, "filament_diameter"),
        ],
    )


def new_extruders(extruders, existing):
    return [name for name in extruders if name not in existing]


def build_sections(extruders, dock_sensors, toolhead_sensor, existing):
    validate_answers(extruders)
    sections = [plugin_section(toolhead_sensor)]
    sections += [
        tool_section(number, name, dock_sensors)
        for number, name in enumerate(extruders)
    ]
    for name in new_extruders(extruders, existing):
        if name == MAIN_EXTRUDER:
            raise SetupError(
                "no [extruder] section found; T0 needs the full [extruder]"
                " section with the extruder motor's step pins. Add it to"
                " printer.cfg, then rerun."
            )
        sections.append(heater_only_extruder_section(name))
    return sections


def _render_entry(entry):
    kind = entry[0]
    if kind == NOTE:
        return ["# %s" % (entry[1],)]
    if kind == VALUE:
        option, value = entry[1], entry[2]
        if isinstance(value, tuple):
            return ["%s:" % (option,)] + ["    %s" % (line,) for line in value]
        return ["%s: %s" % (option, value)]
    if kind == DEFAULT:
        return ["#%s: %s" % (entry[1], entry[2])]
    if kind == FILL:
        return ["#%s:" % (entry[1],)]
    raise SetupError("unhandled config entry kind %r" % (kind,))


def render_config(sections):
    lines = [FILE_HEADER, ""]
    for name, entries in sections:
        lines.append("[%s]" % (name,))
        for entry in entries:
            lines += _render_entry(entry)
        lines.append("")
    return "\n".join(lines)


def values_to_fill(sections):
    result = []
    for name, entries in sections:
        options = [entry[1] for entry in entries if entry[0] == FILL]
        if options:
            result.append((name, options))
    return result


def _join_names(names):
    if len(names) == 1:
        return names[0]
    return "%s and %s" % (", ".join(names[:-1]), names[-1])


def next_steps(config_path, sections, created):
    lines = ["Fill in these options in %s, then uncomment them:" % (config_path,)]
    for name, options in values_to_fill(sections):
        lines.append("  [%s] %s" % (name, ", ".join(options)))
    if created:
        lines.append(
            "Klipper reports a config error until %s %s filled in."
            % (
                _join_names(["[%s]" % (n,) for n in created]),
                "is" if len(created) == 1 else "are",
            )
        )
    lines.append("Tool changes fail until every tool's dock position is filled in.")
    lines.append("Adapt pickup_gcode and dropoff_gcode in [hotendchanger] to your machine.")
    return lines


def add_include(text):
    lines = text.split("\n")
    marker = None
    for number, line in enumerate(lines):
        if line.rstrip() == AUTOSAVE_MARKER:
            marker = number
            break
    regular = lines if marker is None else lines[:marker]
    _, includes = parse_config_text("\n".join(regular))
    if any(fnmatch.fnmatchcase(CONFIG_FILE_NAME, spec) for spec in includes):
        return None
    while regular and not regular[-1].strip():
        regular = regular[:-1]
    head = regular + [""] if regular else []
    if marker is None:
        return "\n".join(head + [INCLUDE_LINE, ""])
    return "\n".join(head + [INCLUDE_LINE, ""] + lines[marker:])


def _read_loaded(path):
    if path is None:
        return set()
    return set(
        normalize_section(line) for line in _read_text(path).split("\n") if line.strip()
    )


class ConfigState:
    def __init__(self, config_dir, loaded_path):
        self.own_file = os.path.abspath(os.path.join(config_dir, CONFIG_FILE_NAME))
        self.own_exists = os.path.exists(self.own_file)
        self.own_sections = set()
        if self.own_exists:
            self.own_sections = set(parse_config_text(_read_text(self.own_file))[0])
        self.other_sections = {}
        for section, paths in scan_config(config_dir).items():
            others = [p for p in paths if p != self.own_file]
            if others:
                self.other_sections[section] = others
        self.loaded_sections = _read_loaded(loaded_path)

    def existing_extruders(self):
        return existing_extruders(
            self.other_sections, self.loaded_sections, self.own_sections
        )


def _split_names(text):
    return [name for name in text.split(",") if name]


def _yes_no(text):
    if text == "yes":
        return True
    if text == "no":
        return False
    raise SetupError("expected yes or no, got %r" % (text,))


def _write_text(path, text):
    try:
        with open(path, "w") as f:
            f.write(text)
    except OSError as e:
        raise SetupError("cannot write %s (%s)" % (path, e))


def main(argv):
    parser = argparse.ArgumentParser(prog="setup_config.py")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("existing", "extruders", "suggest", "render"):
        command = commands.add_parser(name)
        command.add_argument("config_dir")
        command.add_argument("--loaded")
        if name == "suggest":
            command.add_argument("--chosen", required=True)
        if name == "render":
            command.add_argument("--extruders", required=True)
            command.add_argument("--dock-sensors", required=True)
            command.add_argument("--toolhead-sensor", required=True)
            command.add_argument("--output", required=True)
    include = commands.add_parser("add-include")
    include.add_argument("printer_cfg")
    include.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    if args.command == "add-include":
        text = add_include(_read_text(args.printer_cfg))
        if text is None:
            return INCLUDE_PRESENT
        _write_text(args.output, text)
        return INCLUDE_WRITTEN

    state = ConfigState(args.config_dir, args.loaded)
    if args.command == "existing":
        kind, reasons = classify_existing_setup(
            state.own_file,
            state.own_exists,
            state.other_sections,
            state.loaded_sections,
            state.own_sections,
        )
        for reason in reasons:
            print(reason)
        return kind
    if args.command == "extruders":
        for name in state.existing_extruders():
            print(name)
        return 0
    if args.command == "suggest":
        print(suggest_new_extruder(state.existing_extruders(), _split_names(args.chosen)))
        return 0
    if args.command == "render":
        extruders = _split_names(args.extruders)
        existing = state.existing_extruders()
        sections = build_sections(
            extruders,
            _yes_no(args.dock_sensors),
            _yes_no(args.toolhead_sensor),
            existing,
        )
        _write_text(args.output, render_config(sections))
        for line in next_steps(state.own_file, sections, new_extruders(extruders, existing)):
            print(line)
        return 0
    raise SetupError("unhandled command %r" % (args.command,))


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SetupError as e:
        print("install.sh: %s" % (e,), file=sys.stderr)
        sys.exit(1)

# Filament library logic for the hotendchanger filament service
import json
import math
import re

AGENT_NAME = "hotendchanger"
SERVICE_UNIT = "hotendchanger-filament"
STORE_NAMESPACE = "hotendchanger"
STORE_KEY = "library"
STORE_VERSION = 2
LANE_DATA_NAMESPACE = "lane_data"
STATUS_OBJECT = "hotendchanger"
STATUS_TOOLS_FIELD = "tools"
CHANGE_EVENT = "library_changed"

CONSOLE_REMOTE_METHOD = "hotendchanger_filament_console"
REPLY_COMMAND = "_HOTENDCHANGER_FILAMENT_REPLY"
REPLY_INFO = "info"
REPLY_ERROR = "error"

CONSOLE_ADD = "add"
CONSOLE_ASSIGN = "assign"
CONSOLE_LIST = "list"
CONSOLE_COLOR = "color"

FILAMENT_FIELDS = ("name", "material", "nozzle_temp")
VERSION_1_FILAMENT_FIELDS = FILAMENT_FIELDS + ("color",)
ASSIGNMENT_FIELDS = ("filament", "color")

_COLOR_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")


class LibraryError(Exception):
    pass


class ToolsUnknownError(LibraryError):
    def __init__(self):
        LibraryError.__init__(
            self,
            "Klipper is not ready, so the configured tools are unknown."
            " Retry once Klipper reports ready.",
        )


def empty_store():
    return {"version": STORE_VERSION, "filaments": {}, "assignments": {}}


def normalize_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise LibraryError("%s must be a non-empty text value." % (field,))
    return value.strip()


def normalize_color(value):
    match = _COLOR_RE.match(value.strip()) if isinstance(value, str) else None
    if match is None:
        raise LibraryError(
            "color must be six hex digits as RRGGBB or #RRGGBB, got %r."
            % (value,)
        )
    return "#" + match.group(1).upper()


def normalize_nozzle_temp(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise LibraryError(
            "nozzle_temp must be a positive number of degrees Celsius,"
            " got %r." % (value,)
        )
    return value


def normalize_fields(fields):
    return {
        "name": normalize_text(fields["name"], "name"),
        "material": normalize_text(fields["material"], "material"),
        "nozzle_temp": normalize_nozzle_temp(fields["nozzle_temp"]),
    }


def filament_record(store, filament_id):
    record = dict(store["filaments"][filament_id])
    record["id"] = filament_id
    return record


def find_filament_by_name(store, name):
    wanted = name.strip().casefold()
    for filament_id, fields in store["filaments"].items():
        if fields["name"].casefold() == wanted:
            return filament_id
    return None


def _require_filament(store, filament_id):
    if not isinstance(filament_id, str) or filament_id not in store["filaments"]:
        raise LibraryError(
            "No filament with id %r exists. List the filaments to get"
            " valid ids." % (filament_id,)
        )


def _require_unique_name(store, name, own_id):
    holder = find_filament_by_name(store, name)
    if holder is not None and holder != own_id:
        raise LibraryError(
            "A filament named %r already exists. Filament names must be"
            " unique, ignoring case." % (store["filaments"][holder]["name"],)
        )


def _refuse_filament_color(params, param, assign_with):
    if isinstance(params, dict) and param in params:
        raise LibraryError(
            "%s is not a filament field: the color belongs to the tool that"
            " holds the filament. Give it when assigning the filament with %s."
            % (param, assign_with)
        )


def _require_tool_number(tool_number):
    if isinstance(tool_number, bool) or not isinstance(tool_number, int):
        raise LibraryError("tool must be a tool number, got %r." % (tool_number,))


def _require_configured_tool(tools, tool_number):
    if tools is None:
        raise ToolsUnknownError()
    if tool_number not in tools.values():
        raise _unknown_tool_error(tools, tool_number)


def _copy_store(store):
    return {
        "version": STORE_VERSION,
        "filaments": {k: dict(v) for k, v in store["filaments"].items()},
        "assignments": {k: dict(v) for k, v in store["assignments"].items()},
    }


def check_params(params, allowed, required):
    if not isinstance(params, dict):
        raise LibraryError("Parameters must be given as an object of names to values.")
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        raise LibraryError(
            "Unknown parameter(s): %s. Valid parameters are: %s."
            % (", ".join(unknown), ", ".join(allowed) or "none")
        )
    missing = [p for p in required if p not in params]
    if missing:
        raise LibraryError("Missing parameter(s): %s." % (", ".join(missing),))


def _store_location_error(problem):
    return LibraryError(
        "The filament library stored in Moonraker database namespace"
        " '%s', key '%s', %s" % (STORE_NAMESPACE, STORE_KEY, problem)
    )


def _load_filament(store, filament_id, fields, field_names):
    if not isinstance(fields, dict) or set(fields) != set(field_names):
        raise LibraryError(
            "filament %r does not have exactly the fields %s."
            % (filament_id, ", ".join(field_names))
        )
    normalized = normalize_fields(fields)
    _require_unique_name(store, normalized["name"], filament_id)
    store["filaments"][filament_id] = normalized


def _load_filaments(store, raw_filaments):
    for filament_id, fields in raw_filaments.items():
        _load_filament(store, filament_id, fields, FILAMENT_FIELDS)


def _require_tool_key(tool):
    if parse_tool_key(tool) is None:
        raise LibraryError("assignment key %r is not a tool number." % (tool,))


def _load_version_1(store, raw):
    colors = {}
    for filament_id, fields in raw["filaments"].items():
        _load_filament(store, filament_id, fields, VERSION_1_FILAMENT_FIELDS)
        colors[filament_id] = normalize_color(fields["color"])
    for tool, filament_id in raw["assignments"].items():
        _require_tool_key(tool)
        _require_filament(store, filament_id)
        store["assignments"][tool] = {
            "filament": filament_id,
            "color": colors[filament_id],
        }


def _load_assignments(store, raw_assignments):
    for tool, assignment in raw_assignments.items():
        _require_tool_key(tool)
        if not isinstance(assignment, dict) or set(assignment) != set(
            ASSIGNMENT_FIELDS
        ):
            raise LibraryError(
                "assignment of tool number %s does not have exactly the"
                " fields %s." % (tool, ", ".join(ASSIGNMENT_FIELDS))
            )
        _require_filament(store, assignment["filament"])
        store["assignments"][tool] = {
            "filament": assignment["filament"],
            "color": normalize_color(assignment["color"]),
        }


def _load_version_2(store, raw):
    _load_filaments(store, raw["filaments"])
    _load_assignments(store, raw["assignments"])


def _is_current_version(version):
    return (
        not isinstance(version, bool)
        and isinstance(version, int)
        and version == STORE_VERSION
    )


def load_store(raw):
    if raw is None:
        return empty_store(), False
    if not isinstance(raw, dict):
        raise _store_location_error(
            "is malformed. Restore it from a database backup or delete that"
            " key to start empty."
        )
    # Version 1 stores predate the version field, give every filament a
    # color and hold each assignment as a bare filament id.
    if "version" not in raw:
        fields = {"filaments", "assignments"}
        load = _load_version_1
        migrated = True
    elif _is_current_version(raw["version"]):
        fields = {"version", "filaments", "assignments"}
        load = _load_version_2
        migrated = False
    else:
        raise _store_location_error(
            "has format version %r, which this filament service does not"
            " know. Update the filament service to the version that wrote"
            " it, or restore the library from a database backup."
            % (raw["version"],)
        )
    if (
        set(raw) != fields
        or not isinstance(raw["filaments"], dict)
        or not isinstance(raw["assignments"], dict)
    ):
        raise _store_location_error(
            "is malformed. Restore it from a database backup or delete that"
            " key to start empty."
        )
    store = empty_store()
    try:
        load(store, raw)
    except LibraryError as e:
        raise _store_location_error(
            "is invalid: %s Restore it from a database backup or correct the"
            " entry." % (e,)
        )
    return store, migrated


def parse_tool_key(key):
    if not isinstance(key, str) or not key.isdecimal() or key != str(int(key)):
        return None
    return int(key)


def list_filaments(store):
    return [
        filament_record(store, filament_id)
        for filament_id in sorted(
            store["filaments"],
            key=lambda i: (store["filaments"][i]["name"].casefold(), i),
        )
    ]


def create_filament(store, fields, new_id):
    _refuse_filament_color(fields, "color", "tools.assign")
    check_params(fields, FILAMENT_FIELDS, FILAMENT_FIELDS)
    normalized = normalize_fields(fields)
    _require_unique_name(store, normalized["name"], None)
    new_store = _copy_store(store)
    new_store["filaments"][new_id] = normalized
    return new_store, filament_record(new_store, new_id)


def update_filament(store, filament_id, changes):
    _require_filament(store, filament_id)
    check_params(changes, FILAMENT_FIELDS, ())
    if not changes:
        raise LibraryError(
            "No field to change. Give at least one of: %s."
            % (", ".join(FILAMENT_FIELDS),)
        )
    merged = dict(store["filaments"][filament_id])
    merged.update(changes)
    normalized = normalize_fields(merged)
    _require_unique_name(store, normalized["name"], filament_id)
    new_store = _copy_store(store)
    new_store["filaments"][filament_id] = normalized
    return new_store, filament_record(new_store, filament_id)


def delete_filament(store, filament_id):
    _require_filament(store, filament_id)
    holders = sorted(
        parse_tool_key(tool)
        for tool, assignment in store["assignments"].items()
        if assignment["filament"] == filament_id
    )
    if holders:
        raise LibraryError(
            "Filament %r is still assigned to tool number(s) %s. Clear the"
            " filament from those tools first."
            % (
                store["filaments"][filament_id]["name"],
                ", ".join(str(n) for n in holders),
            )
        )
    new_store = _copy_store(store)
    del new_store["filaments"][filament_id]
    return new_store


def assign_filament(store, tools, tool_number, filament_id, color=None):
    _require_tool_number(tool_number)
    if filament_id is None and color is not None:
        raise LibraryError("color cannot be given while clearing a tool's filament.")
    key = str(tool_number)
    new_store = _copy_store(store)
    # Clearing needs no tool list, so an assignment left on a tool removed
    # from the Klipper config can still be cleared and never blocks
    # deleting its filament.
    if filament_id is None and key in store["assignments"]:
        del new_store["assignments"][key]
        return new_store
    _require_configured_tool(tools, tool_number)
    if filament_id is None:
        return new_store
    _require_filament(store, filament_id)
    if color is None:
        raise LibraryError(
            "A color is required to assign a filament to a tool. Give the"
            " color of the filament this tool holds as RRGGBB."
        )
    new_store["assignments"][key] = {
        "filament": filament_id,
        "color": normalize_color(color),
    }
    return new_store


def set_tool_color(store, tools, tool_number, color):
    _require_tool_number(tool_number)
    _require_configured_tool(tools, tool_number)
    key = str(tool_number)
    if key not in store["assignments"]:
        raise LibraryError(
            "Tool number %d has no filament assigned, so it has no color to"
            " set. Assign a filament to it first." % (tool_number,)
        )
    new_store = _copy_store(store)
    new_store["assignments"][key]["color"] = normalize_color(color)
    return new_store


def _unknown_tool_error(tools, tool_number):
    return LibraryError(
        "Tool number %d is not configured in Klipper. Configured tool"
        " numbers: %s."
        % (tool_number, ", ".join(str(n) for n in sorted(tools.values())) or "none")
    )


def parse_status_tools(status_tools):
    if not isinstance(status_tools, dict):
        raise LibraryError(
            "The Klipper status field %s.%s is not a mapping of tool names;"
            " the installed hotendchanger plugin does not match this service."
            % (STATUS_OBJECT, STATUS_TOOLS_FIELD)
        )
    tools = {}
    for name, info in status_tools.items():
        number = info.get("number") if isinstance(info, dict) else None
        if isinstance(number, bool) or not isinstance(number, int):
            raise LibraryError(
                "The Klipper status entry %s.%s.%s has no integer number;"
                " the installed hotendchanger plugin does not match this"
                " service." % (STATUS_OBJECT, STATUS_TOOLS_FIELD, name)
            )
        tools[name] = number
    return tools


def list_tools(store, tools):
    if tools is None:
        raise ToolsUnknownError()
    result = []
    for name, number in sorted(tools.items(), key=lambda item: item[1]):
        assignment = store["assignments"].get(str(number))
        result.append(
            {
                "number": number,
                "name": name,
                "filament": None
                if assignment is None
                else filament_record(store, assignment["filament"]),
                "color": None if assignment is None else assignment["color"],
            }
        )
    return result


def build_lane_data(store, tools):
    # A lane holding only "lane" still counts toward the slot count while
    # reading as empty: OrcaSlicer's MoonrakerPrinterAgent.cpp:1039-1071
    # takes the slot index from "lane" and has_filament from a non-empty
    # "material".
    lanes = {}
    for name, number in tools.items():
        entry = {"lane": str(number)}
        assignment = store["assignments"].get(str(number))
        if assignment is not None:
            fields = store["filaments"][assignment["filament"]]
            entry["material"] = fields["material"]
            entry["color"] = assignment["color"]
            entry["nozzle_temp"] = fields["nozzle_temp"]
        lanes[name] = entry
    return lanes


def lane_data_changes(current, desired):
    writes = {
        key: value for key, value in desired.items() if current.get(key) != value
    }
    deletes = sorted(key for key in current if key not in desired)
    return writes, deletes


def _console_temp(text):
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        raise LibraryError(
            "NOZZLE_TEMP must be a number of degrees Celsius, got %r." % (text,)
        )


def format_filament(fields):
    return "%s: material=%s nozzle_temp=%s" % (
        fields["name"],
        fields["material"],
        fields["nozzle_temp"],
    )


def format_tool(tool):
    filament = tool["filament"]
    if filament is None:
        return "%s: filament=none" % (tool["name"],)
    return "%s: filament=%s color=%s" % (tool["name"], filament["name"], tool["color"])


def _format_tool_number(store, tools, tool_number):
    for tool in list_tools(store, tools):
        if tool["number"] == tool_number:
            return format_tool(tool)
    raise _unknown_tool_error(tools, tool_number)


def format_library(store, tools):
    rows = [format_tool(tool) for tool in list_tools(store, tools)]
    for record in list_filaments(store):
        rows.append(format_filament(record))
    if not store["filaments"]:
        rows.append("filaments: none")
    return "\n".join(rows)


def _console_tool_number(params):
    tool_text = params["T"]
    tool_number = parse_tool_key(tool_text.strip())
    if tool_number is None:
        raise LibraryError("T must be a tool number, got %r." % (tool_text,))
    return tool_number


def run_console_command(store, tools, command, params, new_id):
    if command == CONSOLE_ADD:
        _refuse_filament_color(
            params, "COLOR", "HOTENDCHANGER_FILAMENT_ASSIGN T=<n> NAME=<name> COLOR=RRGGBB"
        )
        check_params(
            params,
            ("NAME", "MATERIAL", "NOZZLE_TEMP"),
            ("NAME", "MATERIAL", "NOZZLE_TEMP"),
        )
        new_store, record = create_filament(
            store,
            {
                "name": params["NAME"],
                "material": params["MATERIAL"],
                "nozzle_temp": _console_temp(params["NOZZLE_TEMP"]),
            },
            new_id,
        )
        return new_store, "Filament added. " + format_filament(record)
    if command == CONSOLE_ASSIGN:
        check_params(params, ("T", "NAME", "CLEAR", "COLOR"), ("T",))
        tool_number = _console_tool_number(params)
        clear = params.get("CLEAR")
        if clear is not None and clear != "1":
            raise LibraryError("CLEAR only accepts 1, got %r." % (clear,))
        if (clear is None) == ("NAME" not in params):
            raise LibraryError(
                "Give exactly one of NAME=<filament name> or CLEAR=1."
            )
        if clear is not None:
            new_store = assign_filament(
                store, tools, tool_number, None, params.get("COLOR")
            )
            return new_store, "Tool number %d has no filament assigned." % (
                tool_number,
            )
        filament_id = find_filament_by_name(store, normalize_text(params["NAME"], "NAME"))
        if filament_id is None:
            raise LibraryError(
                "No filament named %r exists. Add it with"
                " HOTENDCHANGER_FILAMENT_ADD or check the names with"
                " HOTENDCHANGER_FILAMENT_LIST." % (params["NAME"].strip(),)
            )
        new_store = assign_filament(
            store, tools, tool_number, filament_id, params.get("COLOR")
        )
        return new_store, "Filament assigned. " + _format_tool_number(
            new_store, tools, tool_number
        )
    if command == CONSOLE_COLOR:
        check_params(params, ("T", "COLOR"), ("T", "COLOR"))
        tool_number = _console_tool_number(params)
        new_store = set_tool_color(store, tools, tool_number, params["COLOR"])
        return new_store, "Tool color set. " + _format_tool_number(
            new_store, tools, tool_number
        )
    if command == CONSOLE_LIST:
        check_params(params, (), ())
        return None, format_library(store, tools)
    raise LibraryError(
        "Unknown console command %r; the hotendchanger plugin and the"
        " filament service are from different versions." % (command,)
    )


def _quote_param(value):
    # Klipper splits extended parameters with shlex in posix mode
    # (klippy/gcode.py:715-731 Kalico, :266-281 Klipper), where a double
    # quoted value unescapes only backslash and double quote.
    return '"%s"' % (value.replace("\\", "\\\\").replace('"', '\\"'),)


def reply_script(kind, text, request):
    # The JSON payload carries newlines and non-ASCII text through the
    # shlex splitting.
    line = "%s KIND=%s MSG=%s" % (REPLY_COMMAND, kind, _quote_param(json.dumps(text)))
    if kind == REPLY_INFO or kind == REPLY_ERROR:
        if request is None:
            return line
        return "%s REQUEST=%s" % (line, _quote_param(request))
    raise ValueError("unknown reply kind %r" % (kind,))


def api_error(message):
    return {"error": message}


def api_result(payload):
    return {"result": payload}


API_METHODS = (
    "filaments.list",
    "filaments.create",
    "filaments.update",
    "filaments.delete",
    "tools.list",
    "tools.assign",
    "tools.set_color",
)


def _tool_payload(store, tool_number):
    assignment = store["assignments"].get(str(tool_number))
    if assignment is None:
        return {"tool": tool_number, "filament_id": None, "color": None}
    return {
        "tool": tool_number,
        "filament_id": assignment["filament"],
        "color": assignment["color"],
    }


def run_api_method(store, tools, method, params, new_id):
    if params is None:
        params = {}
    if method == "filaments.list":
        check_params(params, (), ())
        return None, {"filaments": list_filaments(store)}
    if method == "filaments.create":
        new_store, record = create_filament(store, params, new_id)
        return new_store, {"filament": record}
    if method == "filaments.update":
        _refuse_filament_color(params, "color", "tools.assign")
        check_params(params, ("id",) + FILAMENT_FIELDS, ("id",))
        changes = {k: v for k, v in params.items() if k != "id"}
        new_store, record = update_filament(store, params["id"], changes)
        return new_store, {"filament": record}
    if method == "filaments.delete":
        check_params(params, ("id",), ("id",))
        return delete_filament(store, params["id"]), {"deleted": params["id"]}
    if method == "tools.list":
        check_params(params, (), ())
        return None, {"tools": list_tools(store, tools)}
    if method == "tools.assign":
        check_params(
            params, ("tool", "filament_id", "color"), ("tool", "filament_id")
        )
        new_store = assign_filament(
            store, tools, params["tool"], params["filament_id"], params.get("color")
        )
        return new_store, _tool_payload(new_store, params["tool"])
    if method == "tools.set_color":
        check_params(params, ("tool", "color"), ("tool", "color"))
        new_store = set_tool_color(store, tools, params["tool"], params["color"])
        return new_store, _tool_payload(new_store, params["tool"])
    raise LibraryError(
        "Unknown method %r. Available methods: %s."
        % (method, ", ".join(API_METHODS))
    )

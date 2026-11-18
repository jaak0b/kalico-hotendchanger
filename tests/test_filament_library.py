import math
import shlex

import pytest

from hotendchanger import (
    FilamentRequests,
    decode_filament_reply,
    format_error_reply,
    format_filament_reply,
    format_filament_timeout,
)
from service.filament_library import (
    LibraryError,
    ToolsUnknownError,
    assign_filament,
    build_lane_data,
    create_filament,
    delete_filament,
    empty_store,
    format_library,
    lane_data_changes,
    list_filaments,
    list_tools,
    load_store,
    parse_status_tools,
    reply_script,
    run_api_method,
    run_console_command,
    set_tool_color,
    update_filament,
)

ASA = {"name": "Elegoo ASA", "material": "ASA", "nozzle_temp": 250}
PLA = {"name": "Basic PLA", "material": "PLA", "nozzle_temp": 215}
TOOLS = {"T0": 0, "T1": 1, "T2": 2}
ASA_BLACK = {"filament": "asa", "color": "#000000"}
PLA_WHITE = {"filament": "pla", "color": "#FFFFFF"}


def library(assignments=None):
    return {
        "version": 2,
        "filaments": {"asa": dict(ASA), "pla": dict(PLA)},
        "assignments": {k: dict(v) for k, v in (assignments or {}).items()},
    }


class TestCreateFilament:
    def test_stores_trimmed_fields(self):
        store, record = create_filament(
            empty_store(),
            {"name": "  Elegoo ASA ", "material": " ASA", "nozzle_temp": 250},
            "new-id",
        )
        assert record == {
            "id": "new-id",
            "name": "Elegoo ASA",
            "material": "ASA",
            "nozzle_temp": 250,
        }
        assert store == {
            "version": 2,
            "filaments": {
                "new-id": {"name": "Elegoo ASA", "material": "ASA", "nozzle_temp": 250}
            },
            "assignments": {},
        }

    def test_accepts_decimal_temperature(self):
        _, record = create_filament(
            empty_store(), {"name": "X", "material": "PETG", "nozzle_temp": 240.5}, "x"
        )
        assert record["nozzle_temp"] == 240.5

    @pytest.mark.parametrize(
        "field,value",
        [
            ("name", "   "),
            ("name", ""),
            ("material", " "),
            ("material", 5),
            ("nozzle_temp", 0),
            ("nozzle_temp", -10),
            ("nozzle_temp", True),
            ("nozzle_temp", "240"),
            ("nozzle_temp", math.nan),
            ("nozzle_temp", math.inf),
        ],
    )
    def test_rejects_invalid_field_values(self, field, value):
        fields = dict(PLA)
        fields[field] = value
        with pytest.raises(LibraryError, match=field):
            create_filament(empty_store(), fields, "x")

    def test_rejects_missing_field(self):
        fields = dict(PLA)
        del fields["material"]
        with pytest.raises(LibraryError, match="Missing parameter.*material"):
            create_filament(empty_store(), fields, "x")

    def test_rejects_unknown_field(self):
        fields = dict(PLA, bed_temp=60)
        with pytest.raises(LibraryError, match="Unknown parameter.*bed_temp"):
            create_filament(empty_store(), fields, "x")

    def test_rejects_color_naming_where_it_belongs(self):
        with pytest.raises(LibraryError) as raised:
            create_filament(empty_store(), dict(PLA, color="#FFFFFF"), "x")
        assert str(raised.value) == (
            "color is not a filament field: the color belongs to the tool that"
            " holds the filament. Give it when assigning the filament with"
            " tools.assign."
        )

    def test_rejects_name_differing_only_in_case(self):
        with pytest.raises(LibraryError, match="'Elegoo ASA' already exists"):
            create_filament(library(), dict(PLA, name="  elegoo asa"), "x")


class TestUpdateFilament:
    def test_changes_only_given_fields(self):
        store, record = update_filament(library(), "asa", {"nozzle_temp": 260})
        assert record == {
            "id": "asa",
            "name": "Elegoo ASA",
            "material": "ASA",
            "nozzle_temp": 260,
        }

    def test_allows_changing_case_of_own_name(self):
        _, record = update_filament(library(), "asa", {"name": "ELEGOO ASA"})
        assert record["name"] == "ELEGOO ASA"

    def test_rejects_rename_onto_another_filament_case_insensitively(self):
        with pytest.raises(LibraryError, match="'Basic PLA' already exists"):
            update_filament(library(), "asa", {"name": "basic pla"})

    def test_rejects_unknown_id(self):
        with pytest.raises(LibraryError, match="No filament with id 'nope'"):
            update_filament(library(), "nope", {"nozzle_temp": 200})

    def test_rejects_empty_change(self):
        with pytest.raises(LibraryError, match="No field to change"):
            update_filament(library(), "asa", {})

    def test_tools_holding_the_filament_keep_their_color(self):
        store, _ = update_filament(library({"0": ASA_BLACK}), "asa", {"nozzle_temp": 260})
        assert store["assignments"] == {"0": {"filament": "asa", "color": "#000000"}}


class TestDeleteFilament:
    def test_refuses_while_assigned_and_names_the_tools(self):
        store = library({"2": ASA_BLACK, "0": ASA_BLACK, "1": PLA_WHITE})
        with pytest.raises(LibraryError) as raised:
            delete_filament(store, "asa")
        assert str(raised.value) == (
            "Filament 'Elegoo ASA' is still assigned to tool number(s) 0, 2."
            " Clear the filament from those tools first."
        )

    def test_removes_unassigned_filament(self):
        store = delete_filament(library({"1": PLA_WHITE}), "asa")
        assert store == {
            "version": 2,
            "filaments": {"pla": {"name": "Basic PLA", "material": "PLA", "nozzle_temp": 215}},
            "assignments": {"1": {"filament": "pla", "color": "#FFFFFF"}},
        }


class TestAssignFilament:
    def test_stores_filament_and_normalized_color(self):
        store = assign_filament(library(), TOOLS, 1, "pla", "ff0000")
        assert store["assignments"] == {"1": {"filament": "pla", "color": "#FF0000"}}

    def test_two_tools_hold_one_filament_in_different_colors(self):
        store = assign_filament(library(), TOOLS, 0, "asa", "#000000")
        store = assign_filament(store, TOOLS, 2, "asa", "#FF8800")
        assert store["assignments"] == {
            "0": {"filament": "asa", "color": "#000000"},
            "2": {"filament": "asa", "color": "#FF8800"},
        }

    def test_refuses_assignment_without_color(self):
        with pytest.raises(LibraryError) as raised:
            assign_filament(library(), TOOLS, 1, "pla")
        assert str(raised.value) == (
            "A color is required to assign a filament to a tool. Give the"
            " color of the filament this tool holds as RRGGBB."
        )

    @pytest.mark.parametrize("color", ["red", "#12345", "1234567", 0xFF0000])
    def test_refuses_invalid_color(self, color):
        with pytest.raises(LibraryError, match="color must be six hex digits"):
            assign_filament(library(), TOOLS, 1, "pla", color)

    def test_refuses_color_while_clearing(self):
        with pytest.raises(LibraryError, match="color cannot be given while clearing"):
            assign_filament(library({"1": PLA_WHITE}), TOOLS, 1, None, "#FF0000")

    def test_refuses_tool_not_configured_in_klipper(self):
        with pytest.raises(LibraryError, match="Tool number 3 is not configured.*0, 1, 2"):
            assign_filament(library(), TOOLS, 3, "pla", "#FFFFFF")

    def test_refuses_while_tool_list_unknown(self):
        with pytest.raises(ToolsUnknownError):
            assign_filament(library(), None, 0, "pla", "#FFFFFF")

    def test_refuses_unknown_filament(self):
        with pytest.raises(LibraryError, match="No filament with id 'nope'"):
            assign_filament(library(), TOOLS, 0, "nope", "#FFFFFF")

    def test_clears_assignment_of_tool_no_longer_configured(self):
        store = assign_filament(library({"7": ASA_BLACK, "0": PLA_WHITE}), TOOLS, 7, None)
        assert store["assignments"] == {"0": {"filament": "pla", "color": "#FFFFFF"}}

    def test_clears_existing_assignment_while_tool_list_unknown(self):
        store = assign_filament(library({"0": PLA_WHITE}), None, 0, None)
        assert store["assignments"] == {}

    def test_does_not_modify_input_store(self):
        original = library({"0": PLA_WHITE})
        assign_filament(original, TOOLS, 0, "asa", "#000000")
        assert original["assignments"] == {"0": {"filament": "pla", "color": "#FFFFFF"}}


class TestSetToolColor:
    def test_changes_only_that_tools_color(self):
        store = set_tool_color(library({"0": ASA_BLACK, "2": ASA_BLACK}), TOOLS, 2, "00ff00")
        assert store["assignments"] == {
            "0": {"filament": "asa", "color": "#000000"},
            "2": {"filament": "asa", "color": "#00FF00"},
        }

    def test_refuses_tool_without_filament(self):
        with pytest.raises(LibraryError) as raised:
            set_tool_color(library(), TOOLS, 1, "00ff00")
        assert str(raised.value) == (
            "Tool number 1 has no filament assigned, so it has no color to set."
            " Assign a filament to it first."
        )

    def test_refuses_invalid_color(self):
        with pytest.raises(LibraryError, match="color must be six hex digits"):
            set_tool_color(library({"0": ASA_BLACK}), TOOLS, 0, "#12345")

    def test_refuses_tool_not_configured_in_klipper(self):
        with pytest.raises(LibraryError, match="Tool number 7 is not configured"):
            set_tool_color(library({"7": ASA_BLACK}), TOOLS, 7, "00ff00")

    def test_does_not_modify_input_store(self):
        original = library({"0": ASA_BLACK})
        set_tool_color(original, TOOLS, 0, "00ff00")
        assert original["assignments"] == {"0": {"filament": "asa", "color": "#000000"}}


class TestLaneData:
    def test_builds_one_entry_per_tool_with_tool_color_and_empty_tool_holding_lane_only(self):
        lanes = build_lane_data(
            library({"0": ASA_BLACK, "2": {"filament": "asa", "color": "#FF8800"}}), TOOLS
        )
        assert lanes == {
            "T0": {"lane": "0", "material": "ASA", "color": "#000000", "nozzle_temp": 250},
            "T1": {"lane": "1"},
            "T2": {"lane": "2", "material": "ASA", "color": "#FF8800", "nozzle_temp": 250},
        }

    def test_writes_changed_and_new_entries_and_deletes_stale_keys(self):
        current = {
            "T0": {"lane": "0", "material": "ASA", "color": "#1A2B3C", "nozzle_temp": 250},
            "T1": {"lane": "1", "material": "PLA", "color": "#FFFFFF", "nozzle_temp": 215},
            "T5": {"lane": "5"},
            "lane1": {"lane": "0"},
        }
        desired = {
            "T0": {"lane": "0", "material": "ASA", "color": "#1A2B3C", "nozzle_temp": 250},
            "T1": {"lane": "1"},
            "T2": {"lane": "2"},
        }
        writes, deletes = lane_data_changes(current, desired)
        assert writes == {"T1": {"lane": "1"}, "T2": {"lane": "2"}}
        assert deletes == ["T5", "lane1"]


class TestToolList:
    def test_reads_tool_numbers_from_plugin_status(self):
        status = {
            "T0": {"number": 0, "extruder": "extruder", "gcode_x_offset": 0.0},
            "T1": {"number": 1, "extruder": "extruder1", "gcode_x_offset": 0.1},
        }
        assert parse_status_tools(status) == {"T0": 0, "T1": 1}

    @pytest.mark.parametrize(
        "status", [None, [], {"T0": {"extruder": "extruder"}}, {"T0": {"number": "0"}}]
    )
    def test_rejects_status_without_integer_numbers(self, status):
        with pytest.raises(LibraryError, match="does not match this service"):
            parse_status_tools(status)

    def test_lists_tools_by_number_with_filament_and_color_or_none(self):
        assert list_tools(
            library({"1": {"filament": "pla", "color": "#00FF00"}}), {"T1": 1, "T0": 0}
        ) == [
            {"number": 0, "name": "T0", "filament": None, "color": None},
            {
                "number": 1,
                "name": "T1",
                "filament": {"id": "pla", "name": "Basic PLA", "material": "PLA", "nozzle_temp": 215},
                "color": "#00FF00",
            },
        ]

    def test_listing_tools_needs_klipper_ready(self):
        with pytest.raises(ToolsUnknownError, match="Klipper is not ready"):
            list_tools(library(), None)

    def test_lists_filaments_sorted_by_name_ignoring_case(self):
        names = [r["name"] for r in list_filaments(library())]
        assert names == ["Basic PLA", "Elegoo ASA"]


class TestLoadStore:
    def test_missing_store_loads_empty(self):
        assert load_store(None) == (
            {"version": 2, "filaments": {}, "assignments": {}},
            False,
        )

    def test_loads_current_store_unchanged(self):
        raw = {
            "version": 2,
            "filaments": {"asa": {"name": "Elegoo ASA", "material": "ASA", "nozzle_temp": 250}},
            "assignments": {"3": {"filament": "asa", "color": "#000000"}},
        }
        assert load_store(raw) == (
            {
                "version": 2,
                "filaments": {"asa": {"name": "Elegoo ASA", "material": "ASA", "nozzle_temp": 250}},
                "assignments": {"3": {"filament": "asa", "color": "#000000"}},
            },
            False,
        )

    def test_converts_version_1_store_moving_each_color_to_the_tools_holding_it(self):
        raw = {
            "filaments": {
                "f1": {"name": "Elegoo ASA", "material": "ASA", "color": "#1A2B3C", "nozzle_temp": 250},
                "f2": {"name": "Basic PLA", "material": "PLA", "color": "#FFFFFF", "nozzle_temp": 215},
                "f3": {"name": "Spare PETG", "material": "PETG", "color": "#00FF00", "nozzle_temp": 240},
            },
            "assignments": {"0": "f1", "1": "f2", "2": "f1"},
        }
        assert load_store(raw) == (
            {
                "version": 2,
                "filaments": {
                    "f1": {"name": "Elegoo ASA", "material": "ASA", "nozzle_temp": 250},
                    "f2": {"name": "Basic PLA", "material": "PLA", "nozzle_temp": 215},
                    "f3": {"name": "Spare PETG", "material": "PETG", "nozzle_temp": 240},
                },
                "assignments": {
                    "0": {"filament": "f1", "color": "#1A2B3C"},
                    "1": {"filament": "f2", "color": "#FFFFFF"},
                    "2": {"filament": "f1", "color": "#1A2B3C"},
                },
            },
            True,
        )

    @pytest.mark.parametrize("version", [1, 3, "2", 2.5, True, None])
    def test_rejects_unknown_format_version(self, version):
        raw = {"version": version, "filaments": {}, "assignments": {}}
        with pytest.raises(LibraryError, match="format version .* does not know"):
            load_store(raw)

    @pytest.mark.parametrize(
        "raw",
        [
            [],
            {"filaments": {}},
            {"filaments": {}, "assignments": {}, "extra": 1},
            {"filaments": {"a": {"name": "A"}}, "assignments": {}},
            {"filaments": {"a": dict(ASA)}, "assignments": {}},
            {"filaments": {"a": dict(ASA, color="red")}, "assignments": {}},
            {
                "filaments": {"a": dict(ASA, color="#000000"), "b": dict(ASA, color="#000000")},
                "assignments": {},
            },
            {"filaments": {"a": dict(ASA, color="#000000")}, "assignments": {"T0": "a"}},
            {"filaments": {"a": dict(ASA, color="#000000")}, "assignments": {"0": "gone"}},
            {"version": 2, "filaments": {}},
            {"version": 2, "filaments": {"a": dict(ASA, color="#000000")}, "assignments": {}},
            {"version": 2, "filaments": {"a": dict(ASA)}, "assignments": {"0": "a"}},
            {"version": 2, "filaments": {"a": dict(ASA)}, "assignments": {"0": {"filament": "a"}}},
            {"version": 2, "filaments": {"a": dict(ASA)}, "assignments": {"0": {"filament": "a", "color": "red"}}},
            {"version": 2, "filaments": {"a": dict(ASA)}, "assignments": {"0": {"filament": "b", "color": "#000000"}}},
        ],
    )
    def test_rejects_malformed_store_naming_its_location(self, raw):
        with pytest.raises(LibraryError, match="namespace 'hotendchanger', key 'library'"):
            load_store(raw)


class TestConsoleCommands:
    def test_add_parses_integer_temperature(self):
        store, text = run_console_command(
            empty_store(),
            None,
            "add",
            {"NAME": "Elegoo ASA", "MATERIAL": "ASA", "NOZZLE_TEMP": "250"},
            "id1",
        )
        assert store["filaments"]["id1"] == {"name": "Elegoo ASA", "material": "ASA", "nozzle_temp": 250}
        assert text == "Filament added. Elegoo ASA: material=ASA nozzle_temp=250"

    def test_add_parses_decimal_temperature(self):
        store, _ = run_console_command(
            empty_store(), None, "add", {"NAME": "A", "MATERIAL": "PLA", "NOZZLE_TEMP": "212.5"}, "id1"
        )
        assert store["filaments"]["id1"]["nozzle_temp"] == 212.5

    def test_add_rejects_non_numeric_temperature(self):
        with pytest.raises(LibraryError, match="NOZZLE_TEMP must be a number"):
            run_console_command(
                empty_store(), None, "add", {"NAME": "A", "MATERIAL": "PLA", "NOZZLE_TEMP": "hot"}, "id1"
            )

    def test_add_rejects_missing_parameter(self):
        with pytest.raises(LibraryError, match="Missing parameter.*MATERIAL"):
            run_console_command(empty_store(), None, "add", {"NAME": "A", "NOZZLE_TEMP": "200"}, "x")

    def test_add_rejects_color_pointing_to_assign(self):
        with pytest.raises(LibraryError) as raised:
            run_console_command(
                empty_store(),
                None,
                "add",
                {"NAME": "A", "MATERIAL": "PLA", "COLOR": "FFFFFF", "NOZZLE_TEMP": "200"},
                "x",
            )
        assert str(raised.value) == (
            "COLOR is not a filament field: the color belongs to the tool that"
            " holds the filament. Give it when assigning the filament with"
            " HOTENDCHANGER_FILAMENT_ASSIGN T=<n> NAME=<name> COLOR=RRGGBB."
        )

    def test_assign_matches_name_ignoring_case_and_stores_color(self):
        store, text = run_console_command(
            library(), TOOLS, "assign", {"T": "2", "NAME": " elegoo asa ", "COLOR": "ff8800"}, "unused"
        )
        assert store["assignments"] == {"2": {"filament": "asa", "color": "#FF8800"}}
        assert text == "Filament assigned. T2: filament=Elegoo ASA color=#FF8800"

    def test_color_command_sets_only_the_tool_color(self):
        store, text = run_console_command(
            library({"0": ASA_BLACK}), TOOLS, "color", {"T": "0", "COLOR": "#00ff00"}, "unused"
        )
        assert store["assignments"] == {"0": {"filament": "asa", "color": "#00FF00"}}
        assert text == "Tool color set. T0: filament=Elegoo ASA color=#00FF00"

    @pytest.mark.parametrize(
        "params,message",
        [
            ({"T": "0"}, "Missing parameter.*COLOR"),
            ({"COLOR": "00ff00"}, "Missing parameter.*T"),
            ({"T": "x", "COLOR": "00ff00"}, "T must be a tool number"),
            ({"T": "1", "COLOR": "00ff00"}, "Tool number 1 has no filament assigned"),
            ({"T": "0", "COLOR": "green"}, "color must be six hex digits"),
        ],
    )
    def test_color_command_rejects_bad_parameters(self, params, message):
        with pytest.raises(LibraryError, match=message):
            run_console_command(library({"0": ASA_BLACK}), TOOLS, "color", params, "unused")

    def test_assign_clear_removes_assignment(self):
        store, text = run_console_command(
            library({"1": PLA_WHITE}), TOOLS, "assign", {"T": "1", "CLEAR": "1"}, "unused"
        )
        assert store["assignments"] == {}
        assert text == "Tool number 1 has no filament assigned."

    @pytest.mark.parametrize(
        "params,message",
        [
            ({"T": "1"}, "exactly one of NAME"),
            ({"T": "1", "NAME": "Basic PLA", "CLEAR": "1"}, "exactly one of NAME"),
            ({"T": "1", "CLEAR": "yes"}, "CLEAR only accepts 1"),
            ({"T": "one", "NAME": "Basic PLA", "COLOR": "FFFFFF"}, "T must be a tool number"),
            ({"T": "-1", "NAME": "Basic PLA", "COLOR": "FFFFFF"}, "T must be a tool number"),
            ({"T": "1", "NAME": "Unknown", "COLOR": "FFFFFF"}, "No filament named 'Unknown'"),
            ({"T": "1", "NAME": "Basic PLA"}, "A color is required"),
            ({"NAME": "Basic PLA"}, "Missing parameter.*T"),
            ({"T": "1", "TOOL": "1"}, "Unknown parameter.*TOOL"),
            ({"T": "1", "CLEAR": "1", "COLOR": "000000"}, "color cannot be given while clearing"),
        ],
    )
    def test_assign_rejects_bad_parameters(self, params, message):
        with pytest.raises(LibraryError, match=message):
            run_console_command(library(), TOOLS, "assign", params, "unused")

    def test_list_prints_tool_rows_with_color_then_filament_rows(self):
        store, text = run_console_command(
            library({"0": ASA_BLACK}), {"T0": 0, "T1": 1}, "list", {}, "unused"
        )
        assert store is None
        assert text == (
            "T0: filament=Elegoo ASA color=#000000\n"
            "T1: filament=none\n"
            "Basic PLA: material=PLA nozzle_temp=215\n"
            "Elegoo ASA: material=ASA nozzle_temp=250"
        )

    def test_list_of_empty_library(self):
        assert format_library(empty_store(), {"T0": 0}) == "T0: filament=none\nfilaments: none"

    def test_rejects_unknown_console_command(self):
        with pytest.raises(LibraryError, match="different versions"):
            run_console_command(library(), TOOLS, "remove", {}, "unused")


def klipper_parse(line):
    command, raw = line.split(" ", 1)
    # The same splitting klippy/gcode.py _get_extended_params applies.
    lexer = shlex.shlex(raw, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = "#;"
    return command, dict(arg.split("=", 1) for arg in lexer)


class TestConsoleReply:
    @pytest.mark.parametrize(
        "text",
        [
            "plain",
            'quote " and backslash \\ inside',
            "two\nlines; with # comment chars",
            "ends with *42",
            "Grad °C und ä",
        ],
    )
    def test_text_survives_klipper_extended_parameter_parsing(self, text):
        command, params = klipper_parse(reply_script("info", text, "c0ffee"))
        assert command == "_HOTENDCHANGER_FILAMENT_REPLY"
        assert params["KIND"] == "info"
        assert params["REQUEST"] == "c0ffee"
        assert decode_filament_reply(params["MSG"]) == text

    def test_reply_without_request_id_omits_the_parameter(self):
        _, params = klipper_parse(reply_script("error", "x", None))
        assert sorted(params) == ["KIND", "MSG"]

    @pytest.mark.parametrize("encoded", ["not json", "42", '["a"]', '"unterminated'])
    def test_malformed_payload_decodes_to_none(self, encoded):
        assert decode_filament_reply(encoded) is None

    def test_error_reply_prefixes_every_line(self):
        assert format_error_reply("first\nsecond") == "!! first\n!! second"


class TestFilamentRequests:
    def test_reply_before_timeout_is_on_time_and_names_the_command(self):
        requests = FilamentRequests()
        request_id = requests.open("HOTENDCHANGER_FILAMENT_LIST")
        assert requests.reply(request_id) == ("on_time", "HOTENDCHANGER_FILAMENT_LIST")

    def test_timeout_reports_the_command_once(self):
        requests = FilamentRequests()
        request_id = requests.open("HOTENDCHANGER_FILAMENT_ADD")
        assert requests.expire(request_id) == "HOTENDCHANGER_FILAMENT_ADD"
        assert requests.expire(request_id) is None

    def test_reply_after_timeout_is_late(self):
        requests = FilamentRequests()
        request_id = requests.open("HOTENDCHANGER_FILAMENT_ADD")
        requests.expire(request_id)
        assert requests.reply(request_id) == ("late", "HOTENDCHANGER_FILAMENT_ADD")

    def test_timeout_after_reply_reports_nothing(self):
        requests = FilamentRequests()
        request_id = requests.open("HOTENDCHANGER_FILAMENT_LIST")
        requests.reply(request_id)
        assert requests.expire(request_id) is None

    def test_second_reply_to_one_request_is_unmatched(self):
        requests = FilamentRequests()
        request_id = requests.open("HOTENDCHANGER_FILAMENT_LIST")
        requests.reply(request_id)
        assert requests.reply(request_id) == ("unmatched", None)

    def test_reply_to_unknown_id_is_unmatched(self):
        assert FilamentRequests().reply("feed") == ("unmatched", None)

    def test_discarded_request_never_times_out(self):
        requests = FilamentRequests()
        request_id = requests.open("HOTENDCHANGER_FILAMENT_LIST")
        requests.discard(request_id)
        assert requests.expire(request_id) is None

    def test_requests_get_distinct_ids(self):
        requests = FilamentRequests()
        assert requests.open("A") != requests.open("A")

    def test_no_entry_remains_after_reply_or_late_reply(self):
        requests = FilamentRequests()
        on_time = requests.open("A")
        late = requests.open("B")
        requests.reply(on_time)
        requests.expire(late)
        requests.reply(late)
        assert (requests.pending, requests.timed_out) == ({}, {})


class TestFilamentReplyText:
    def test_timeout_message_names_command_timeout_and_unit(self):
        assert format_filament_timeout("HOTENDCHANGER_FILAMENT_ADD", 10.0) == (
            "HOTENDCHANGER_FILAMENT_ADD got no answer from the filament service"
            " within 10.0 s (filament_reply_timeout).\n"
            "Check that the service runs with: systemctl status hotendchanger-filament\n"
            "Then look for errors in the Moonraker log. A reply that arrives"
            " later is still printed, marked as late."
        )

    def test_on_time_reply_is_printed_unchanged(self):
        assert format_filament_reply("on_time", "X", "id", "T0 filament: none", 10.0) == (
            "T0 filament: none"
        )

    def test_late_reply_is_marked_late(self):
        text = format_filament_reply(
            "late", "HOTENDCHANGER_FILAMENT_LIST", "id", "T0 filament: none", 2.5
        )
        assert text == (
            "Late reply to HOTENDCHANGER_FILAMENT_LIST, received after the 2.5 s"
            " timeout:\nT0 filament: none"
        )

    def test_unmatched_reply_is_marked_with_its_request_id(self):
        text = format_filament_reply("unmatched", None, "abc", "T0 filament: none", 10.0)
        assert text == "Reply to no pending filament command (REQUEST=abc):\nT0 filament: none"

    def test_unknown_match_raises(self):
        with pytest.raises(ValueError):
            format_filament_reply("early", None, "abc", "x", 10.0)




class TestApiMethods:
    def test_list_filaments_payload(self):
        assert run_api_method(library(), TOOLS, "filaments.list", None, "unused") == (
            None,
            {
                "filaments": [
                    {"id": "pla", "name": "Basic PLA", "material": "PLA", "nozzle_temp": 215},
                    {"id": "asa", "name": "Elegoo ASA", "material": "ASA", "nozzle_temp": 250},
                ]
            },
        )

    def test_create_returns_new_record(self):
        store, payload = run_api_method(
            empty_store(),
            TOOLS,
            "filaments.create",
            {"name": "PETG", "material": "PETG", "nozzle_temp": 240},
            "n1",
        )
        assert payload == {
            "filament": {"id": "n1", "name": "PETG", "material": "PETG", "nozzle_temp": 240}
        }
        assert store["filaments"] == {"n1": {"name": "PETG", "material": "PETG", "nozzle_temp": 240}}

    def test_update_returns_changed_record(self):
        _, payload = run_api_method(
            library(), TOOLS, "filaments.update", {"id": "asa", "nozzle_temp": 255}, "unused"
        )
        assert payload == {
            "filament": {"id": "asa", "name": "Elegoo ASA", "material": "ASA", "nozzle_temp": 255}
        }

    def test_update_rejects_color(self):
        with pytest.raises(LibraryError, match="color is not a filament field"):
            run_api_method(library(), TOOLS, "filaments.update", {"id": "asa", "color": "#000000"}, "unused")

    def test_delete_returns_deleted_id(self):
        store, payload = run_api_method(library(), TOOLS, "filaments.delete", {"id": "asa"}, "unused")
        assert payload == {"deleted": "asa"}
        assert sorted(store["filaments"]) == ["pla"]

    def test_assign_returns_tool_filament_and_color(self):
        store, payload = run_api_method(
            library(), TOOLS, "tools.assign", {"tool": 1, "filament_id": "pla", "color": "#abcdef"}, "unused"
        )
        assert payload == {"tool": 1, "filament_id": "pla", "color": "#ABCDEF"}
        assert store["assignments"] == {"1": {"filament": "pla", "color": "#ABCDEF"}}

    def test_assign_without_color_is_refused(self):
        with pytest.raises(LibraryError, match="A color is required"):
            run_api_method(library(), TOOLS, "tools.assign", {"tool": 1, "filament_id": "pla"}, "unused")

    def test_clear_returns_no_filament_and_no_color(self):
        _, payload = run_api_method(
            library({"1": PLA_WHITE}), TOOLS, "tools.assign", {"tool": 1, "filament_id": None}, "unused"
        )
        assert payload == {"tool": 1, "filament_id": None, "color": None}

    def test_set_color_returns_tool_filament_and_new_color(self):
        store, payload = run_api_method(
            library({"0": ASA_BLACK}), TOOLS, "tools.set_color", {"tool": 0, "color": "123abc"}, "unused"
        )
        assert payload == {"tool": 0, "filament_id": "asa", "color": "#123ABC"}
        assert store["assignments"] == {"0": {"filament": "asa", "color": "#123ABC"}}

    def test_set_color_requires_color(self):
        with pytest.raises(LibraryError, match="Missing parameter.*color"):
            run_api_method(library({"0": ASA_BLACK}), TOOLS, "tools.set_color", {"tool": 0}, "unused")

    def test_tools_list_payload(self):
        assert run_api_method(library(), {"T0": 0}, "tools.list", {}, "unused") == (
            None,
            {"tools": [{"number": 0, "name": "T0", "filament": None, "color": None}]},
        )

    def test_unknown_method_names_the_available_ones(self):
        with pytest.raises(LibraryError) as excinfo:
            run_api_method(library(), TOOLS, "filaments.purge", {}, "unused")
        assert str(excinfo.value) == (
            "Unknown method 'filaments.purge'. Available methods: filaments.list,"
            " filaments.create, filaments.update, filaments.delete, tools.list,"
            " tools.assign, tools.set_color."
        )

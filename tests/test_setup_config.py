import pytest

from installer.setup_config import (
    SETUP_ELSEWHERE,
    SETUP_NONE,
    SETUP_OWN_FILE,
    ConfigState,
    SetupError,
    add_include,
    build_sections,
    classify_existing_setup,
    next_steps,
    render_config,
    suggest_new_extruder,
)

AUTOSAVE = (
    "#*# <---------------------- SAVE_CONFIG ---------------------->\n"
    "#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.\n"
    "#*#\n"
    "#*# [extruder]\n"
    "#*# pid_kp = 22.2\n"
)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def state_for(config_dir, loaded=None):
    loaded_path = None
    if loaded is not None:
        loaded_path = config_dir / "loaded.txt"
        loaded_path.write_text(loaded)
    return ConfigState(str(config_dir), loaded_path and str(loaded_path))


def classify(state):
    return classify_existing_setup(
        state.own_file,
        state.own_exists,
        state.other_sections,
        state.loaded_sections,
        state.own_sections,
    )


def test_one_tool_without_sensors_writes_only_the_plugin_and_t0_sections():
    sections = build_sections(["extruder"], False, False, ["extruder"])

    assert render_config(sections) == (
        "# Uncomment an option only after filling in its value: a blank option does not parse.\n"
        "\n"
        "[hotendchanger]\n"
        "# Starter templates: adapt the moves to your machine before the first tool change.\n"
        "pickup_gcode:\n"
        "    G1 X{params.dock_x} Y{params.dock_y} F6000\n"
        "    G1 Z{params.dock_z} F600\n"
        "dropoff_gcode:\n"
        "    G1 X{params.dock_x} Y{params.dock_y} F6000\n"
        "    G1 Z{params.dock_z} F600\n"
        "\n"
        "[hotendchanger_tool T0]\n"
        "extruder: extruder\n"
        "# Fill in this tool's dock position.\n"
        "#params_dock_x:\n"
        "#params_dock_y:\n"
        "#params_dock_z:\n"
    )


def test_three_tools_with_sensors_add_pin_lines_and_skeletons_for_new_extruders():
    sections = build_sections(
        ["extruder", "extruder1", "extruder2"], True, True, ["extruder"]
    )

    assert render_config(sections) == (
        "# Uncomment an option only after filling in its value: a blank option does not parse.\n"
        "\n"
        "[hotendchanger]\n"
        "# Starter templates: adapt the moves to your machine before the first tool change.\n"
        "pickup_gcode:\n"
        "    G1 X{params.dock_x} Y{params.dock_y} F6000\n"
        "    G1 Z{params.dock_z} F600\n"
        "dropoff_gcode:\n"
        "    G1 X{params.dock_x} Y{params.dock_y} F6000\n"
        "    G1 Z{params.dock_z} F600\n"
        "# Fill in the toolhead sensor pin.\n"
        "#toolhead_detect_pin:\n"
        "\n"
        "[hotendchanger_tool T0]\n"
        "extruder: extruder\n"
        "# Fill in this tool's dock position.\n"
        "#params_dock_x:\n"
        "#params_dock_y:\n"
        "#params_dock_z:\n"
        "# Fill in this dock's sensor pin.\n"
        "#detect_pin:\n"
        "\n"
        "[hotendchanger_tool T1]\n"
        "extruder: extruder1\n"
        "#gcode_x_offset: 0\n"
        "#gcode_y_offset: 0\n"
        "#gcode_z_offset: 0\n"
        "# Fill in this tool's dock position.\n"
        "#params_dock_x:\n"
        "#params_dock_y:\n"
        "#params_dock_z:\n"
        "# Fill in this dock's sensor pin.\n"
        "#detect_pin:\n"
        "\n"
        "[hotendchanger_tool T2]\n"
        "extruder: extruder2\n"
        "#gcode_x_offset: 0\n"
        "#gcode_y_offset: 0\n"
        "#gcode_z_offset: 0\n"
        "# Fill in this tool's dock position.\n"
        "#params_dock_x:\n"
        "#params_dock_y:\n"
        "#params_dock_z:\n"
        "# Fill in this dock's sensor pin.\n"
        "#detect_pin:\n"
        "\n"
        "[extruder1]\n"
        "# Fill in these values for this hotend.\n"
        "#heater_pin:\n"
        "#sensor_type:\n"
        "#sensor_pin:\n"
        "control: pid\n"
        "#pid_Kp:\n"
        "#pid_Ki:\n"
        "#pid_Kd:\n"
        "#min_temp:\n"
        "#max_temp:\n"
        "#nozzle_diameter:\n"
        "#filament_diameter:\n"
        "\n"
        "[extruder2]\n"
        "# Fill in these values for this hotend.\n"
        "#heater_pin:\n"
        "#sensor_type:\n"
        "#sensor_pin:\n"
        "control: pid\n"
        "#pid_Kp:\n"
        "#pid_Ki:\n"
        "#pid_Kd:\n"
        "#min_temp:\n"
        "#max_temp:\n"
        "#nozzle_diameter:\n"
        "#filament_diameter:\n"
    )


def test_existing_extruders_get_no_skeleton_and_only_dock_sensor_lines():
    sections = build_sections(
        ["extruder", "extruder1"], True, False, ["extruder", "extruder1"]
    )

    assert render_config(sections) == (
        "# Uncomment an option only after filling in its value: a blank option does not parse.\n"
        "\n"
        "[hotendchanger]\n"
        "# Starter templates: adapt the moves to your machine before the first tool change.\n"
        "pickup_gcode:\n"
        "    G1 X{params.dock_x} Y{params.dock_y} F6000\n"
        "    G1 Z{params.dock_z} F600\n"
        "dropoff_gcode:\n"
        "    G1 X{params.dock_x} Y{params.dock_y} F6000\n"
        "    G1 Z{params.dock_z} F600\n"
        "\n"
        "[hotendchanger_tool T0]\n"
        "extruder: extruder\n"
        "# Fill in this tool's dock position.\n"
        "#params_dock_x:\n"
        "#params_dock_y:\n"
        "#params_dock_z:\n"
        "# Fill in this dock's sensor pin.\n"
        "#detect_pin:\n"
        "\n"
        "[hotendchanger_tool T1]\n"
        "extruder: extruder1\n"
        "#gcode_x_offset: 0\n"
        "#gcode_y_offset: 0\n"
        "#gcode_z_offset: 0\n"
        "# Fill in this tool's dock position.\n"
        "#params_dock_x:\n"
        "#params_dock_y:\n"
        "#params_dock_z:\n"
        "# Fill in this dock's sensor pin.\n"
        "#detect_pin:\n"
    )


def test_next_steps_name_every_value_to_fill_and_the_new_sections():
    sections = build_sections(["extruder", "extruder1"], False, True, ["extruder"])

    assert next_steps("/cfg/hotendchanger.cfg", sections, ["extruder1"]) == [
        "Fill in these options in /cfg/hotendchanger.cfg, then uncomment them:",
        "  [hotendchanger] toolhead_detect_pin",
        "  [hotendchanger_tool T0] params_dock_x, params_dock_y, params_dock_z",
        "  [hotendchanger_tool T1] params_dock_x, params_dock_y, params_dock_z",
        "  [extruder1] heater_pin, sensor_type, sensor_pin, pid_Kp, pid_Ki,"
        " pid_Kd, min_temp, max_temp, nozzle_diameter, filament_diameter",
        "Klipper reports a config error until [extruder1] is filled in.",
        "Tool changes fail until every tool's dock position is filled in.",
        "Adapt pickup_gcode and dropoff_gcode in [hotendchanger] to your machine.",
    ]


@pytest.mark.parametrize(
    "extruders, message",
    [
        (["extruder", "extruder1", "extruder1"], "both name extruder section 'extruder1'"),
        (["extruder1"], "T0 must use [extruder]"),
        (["extruder", "extruder_stepper x"], "T1 names [extruder_stepper x]"),
        (["extruder", "extruder01"], "T1 names [extruder01]"),
    ],
)
def test_rejects_answers_klipper_or_the_plugin_would_refuse(extruders, message):
    with pytest.raises(SetupError, match=message.replace("[", r"\[").replace("]", r"\]")):
        build_sections(extruders, False, False, ["extruder"])


def test_refuses_to_write_a_skeleton_for_a_missing_main_extruder():
    with pytest.raises(SetupError, match="no \\[extruder\\] section found"):
        build_sections(["extruder", "extruder1"], False, False, [])


@pytest.mark.parametrize(
    "existing, chosen, expected",
    [
        (["extruder"], ["extruder"], "extruder1"),
        (["extruder", "extruder1", "extruder3"], ["extruder"], "extruder2"),
        (["extruder"], ["extruder", "extruder1"], "extruder2"),
    ],
)
def test_suggests_the_lowest_free_extruder_number(existing, chosen, expected):
    assert suggest_new_extruder(existing, chosen) == expected


def test_appends_the_include_at_the_end_of_a_file_without_save_config():
    assert add_include("[printer]\nkinematics: corexy\n\n\n") == (
        "[printer]\nkinematics: corexy\n\n[include hotendchanger.cfg]\n"
    )


def test_puts_the_include_before_the_save_config_block():
    assert add_include("[printer]\nkinematics: corexy\n\n" + AUTOSAVE) == (
        "[printer]\nkinematics: corexy\n\n[include hotendchanger.cfg]\n\n" + AUTOSAVE
    )


@pytest.mark.parametrize(
    "text",
    [
        "[include hotendchanger.cfg]\n[printer]\n",
        "[include  hotendchanger.cfg]  # ours\n",
        "[include *.cfg]\n",
    ],
)
def test_leaves_a_file_that_already_includes_hotendchanger_cfg_unchanged(text):
    assert add_include(text) is None


def test_adding_the_include_twice_changes_nothing_the_second_time():
    once = add_include("[printer]\n\n" + AUTOSAVE)

    assert add_include(once) is None


def test_a_fresh_config_has_no_existing_setup(tmp_path):
    write(tmp_path / "printer.cfg", "[extruder]\n[extruder1]\n")

    assert classify(state_for(tmp_path)) == (SETUP_NONE, [])


def test_an_existing_hotendchanger_cfg_counts_even_when_not_included(tmp_path):
    write(tmp_path / "printer.cfg", "[extruder]\n")
    write(tmp_path / "hotendchanger.cfg", "")

    kind, reasons = classify(state_for(tmp_path))

    assert kind == SETUP_OWN_FILE
    assert reasons == ["%s exists" % (tmp_path / "hotendchanger.cfg",)]


def test_finds_a_tool_section_in_a_globbed_include(tmp_path):
    write(tmp_path / "printer.cfg", "[include parts/*.cfg]\n[extruder]\n")
    write(tmp_path / "parts" / "tools.cfg", "[hotendchanger_tool T0]\nextruder: extruder\n")

    kind, reasons = classify(state_for(tmp_path))

    assert kind == SETUP_ELSEWHERE
    assert reasons == [
        "[hotendchanger_tool t0] in %s" % (tmp_path / "parts" / "tools.cfg",)
    ]


def test_finds_a_tool_section_only_in_the_save_config_block(tmp_path):
    write(
        tmp_path / "printer.cfg",
        "[extruder]\n\n"
        "#*# <---------------------- SAVE_CONFIG ---------------------->\n"
        "#*# [hotendchanger_tool T1]\n"
        "#*# gcode_x_offset = 0.1\n",
    )

    kind, _ = classify(state_for(tmp_path))

    assert kind == SETUP_ELSEWHERE


def test_ignores_a_commented_out_plugin_section(tmp_path):
    write(tmp_path / "printer.cfg", "#[hotendchanger]\n[extruder]\n")

    assert classify(state_for(tmp_path)) == (SETUP_NONE, [])


def test_finds_a_plugin_section_only_klipper_has_loaded(tmp_path):
    write(tmp_path / "printer.cfg", "[extruder]\n")

    kind, reasons = classify(state_for(tmp_path, loaded="extruder\nhotendchanger\n"))

    assert kind == SETUP_ELSEWHERE
    assert reasons == ["[hotendchanger] in the config Klipper has loaded"]


def test_sections_loaded_from_hotendchanger_cfg_do_not_block_replacing_it(tmp_path):
    write(tmp_path / "printer.cfg", "[extruder]\n[include hotendchanger.cfg]\n")
    write(tmp_path / "hotendchanger.cfg", "[hotendchanger]\n[extruder1]\n")

    state = state_for(tmp_path, loaded="extruder\nextruder1\nhotendchanger\n")

    assert classify(state)[0] == SETUP_OWN_FILE
    assert state.existing_extruders() == ["extruder"]


def test_lists_extruders_from_files_and_loaded_config_in_klipper_load_order(tmp_path):
    write(
        tmp_path / "printer.cfg",
        "[extruder10]\n[extruder]\n[extruder_stepper belt]\n[Extruder2]\n",
    )

    state = state_for(tmp_path, loaded="extruder1\nextruder0\n")

    assert state.existing_extruders() == ["extruder", "extruder1", "extruder2", "extruder10"]


def test_reports_an_include_of_a_missing_file(tmp_path):
    write(tmp_path / "printer.cfg", "[include gone.cfg]\n")

    with pytest.raises(SetupError, match="gone.cfg, which does not exist"):
        state_for(tmp_path)


def test_reports_a_missing_printer_cfg(tmp_path):
    with pytest.raises(SetupError, match="printer.cfg not found"):
        state_for(tmp_path)

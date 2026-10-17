"""N4S4 toolchange configuration: what printer_n4s4.cfg sets and wires up.

The behaviour of the toolchange itself is in test_toolchange_n4s4.py."""

import ast
import importlib.util
import re
import types

import pytest

from lib.paths import ROOT


MODULE = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" /
          "klippy" / "extras" / "ff_toolchange.py")
CONFIG = (ROOT / "pkgs" / "klipper-config" / "payload" / "config" /
          "printer_n4s4.cfg")


@pytest.fixture(scope="module")
def ff_toolchange():
    spec = importlib.util.spec_from_file_location("test_ff_toolchange", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_status_command_is_exposed_as_a_mainsail_macro():
    module_source = MODULE.read_text(encoding="utf-8")
    config_source = CONFIG.read_text(encoding="utf-8")

    assert "'FF_TOOLCHANGE_STATUS', self.cmd_TOOLCHANGE_STATUS" in module_source
    assert "[gcode_macro TOOLCHANGE_STATUS]" in config_source
    assert "    FF_TOOLCHANGE_STATUS" in config_source


def test_purge_and_first_tower_pickups_use_stronger_retract():
    config_source = CONFIG.read_text(encoding="utf-8")

    assert "purge_retract: 0.9" in config_source
    assert "purge_retract_dwell_ms: 250" in config_source
    assert "tower_repeat_retract: 0.0" in config_source
    assert "TOOLCHANGE_BEGIN_JOB" in config_source
    adaptive_mesh = config_source.split(
        "[gcode_macro ADAPTIVE_MESH]", 1
    )[1].split("[gcode_macro DEFINE_PRIME_TOWER_OBJECT]", 1)[0]
    assert "TOOLCHANGE_PREPARE_PICKUP\n    T{tool}" in adaptive_mesh
    before_print = config_source.split(
        "[gcode_macro _NS_BEFORE_PRINT]", 1
    )[1].split("[gcode_macro _NS_AFTER_PRINT]", 1)[0]
    assert "TOOLCHANGE_PREPARE_PICKUP ENABLE=0" in before_print
    purge_line = config_source.split(
        "[gcode_macro _PURGE_NEAR_OBJECT]", 1
    )[1]
    assert "[gcode_macro PURGE_NEAR_OBJECT]" not in config_source
    assert "params.E|default(10)|float" in purge_line
    assert "params.LEAD|default(3)|float" in purge_line
    assert "G1 E{lead} F{feed}" in purge_line
    assert "G1 X{x2} E{e - lead} F{feed}" in purge_line


def test_repeat_tower_pickup_does_not_stack_another_retract(ff_toolchange):
    toolchanger = types.SimpleNamespace(
        purge_retract=0.9,
        restore_retract=0.4,
        tower_repeat_retract=0.0,
        prime_tower_geometry=object(),
        job_tool_mask=1 << 2,
    )

    select = ff_toolchange.FFToolchange._pickup_retract
    assert select(toolchanger, 1) == (0.9, True)
    assert select(toolchanger, 2) == (0.0, False)
    assert select(toolchanger, 2, explicit_purge=True) == (0.9, True)

    toolchanger.prime_tower_geometry = None
    assert select(toolchanger, 2) == (0.4, False)


def test_return_prime_never_exceeds_actual_dock_retract(ff_toolchange):
    scripts = []
    toolchanger = types.SimpleNamespace(
        restore_unretract=0.4,
        restore_unretract_feed=200,
        restore_z_hop=0.0,
        restore_z_feed=1200,
        restore_feed=30000,
        restore_via_corridor=False,
        _run=scripts.append,
    )

    ff_toolchange.FFToolchange._restore_position(
        toolchanger, "XY", [10.0, 20.0, 1.0],
        protected=True, return_retract=0.2)

    assert "G1 E0.200 F200" in scripts
    assert "G1 E0.400 F200" not in scripts


def test_start_purge_preheats_the_next_tool_during_current_cleanup():
    config_source = CONFIG.read_text(encoding="utf-8")
    clean = config_source.split(
        "[gcode_macro _FF_NOZZLE_CLEAN]", 1
    )[1].split("[gcode_macro PURGE]", 1)[0]

    prep = clean.index("_FF_FILAMENT_PREP TOOL={tool}")
    preheat = clean.index("M104 S{next_temp} T{next_tool}")
    purge = clean.index("G1 E{ff.purge_length} F{speed}")
    assert prep < preheat < purge
    assert "{% if not loop.last %}" in clean
    assert "{% set next_tool = tools[loop.index] %}" in clean


def test_chute_purges_use_the_front_right_lip_wipe():
    config_source = CONFIG.read_text(encoding="utf-8")

    assert "variable_lip_wipe_enabled: 1" in config_source
    assert "variable_clean_wipe_z_absolute: -0.9" in config_source
    assert (
        "variable_cooldown_pad_x_offsets: "
        "[0.0, 1.0, -4.0, -3.0, -2.0, -1.0]"
    ) in config_source
    assert (
        "variable_cooldown_pad_y_offsets: "
        "[0.0, -4.0, -1.0, -2.0, -3.0]"
    ) in config_source
    assert "variable_cooldown_pad_index: 0" in config_source
    assert "variable_cooldown_pad_seed: -1" in config_source
    assert "variable_lip_wipe_z: -1.0" in config_source
    assert "variable_lip_wipe_x_left: 262.0" in config_source
    assert "variable_lip_wipe_x_right: 273.0" in config_source
    assert "variable_lip_wipe_feed: 9000" in config_source
    assert "[gcode_macro _NS_CHUTE_LIP_WIPE]" in config_source
    assert "{% set wipe_z = ff.lip_wipe_z|float %}" in config_source
    assert "{% set exit_z = params.EXIT_Z|default(safe_z)|float %}" in config_source
    assert "G1 Z{exit_z} F{ff.clean_wipe_z_feed}" in config_source
    assert (
        "_NS_CHUTE_LIP_WIPE TOOL={tool} "
        "EXIT_Z={ff.lip_wipe_z|float + 2.0}"
    ) in config_source
    assert "{% set wipe_z = ff.clean_wipe_z_absolute|float %}" in config_source
    assert (
        "SET_GCODE_VARIABLE MACRO=_FF_FILAMENT "
        "VARIABLE=cooldown_pad_index"
    ) in config_source
    assert (
        "SET_GCODE_VARIABLE MACRO=_FF_FILAMENT "
        "VARIABLE=cooldown_pad_seed"
    ) in config_source
    assert "printer.system_stats.cputime" in config_source
    assert "printer.print_stats.total_duration" in config_source
    assert "G1 X{xr} F{ff.clean_wipe_feed}" in config_source
    assert "G1 X{xl} Y{y0 + 0.5} F{ff.lip_wipe_feed}" in config_source
    assert "G1 X{xr} Y{y0 + 7.0} F{ff.lip_wipe_feed}" in config_source
    lip_wipe_body = config_source.split(
        "[gcode_macro _NS_CHUTE_LIP_WIPE]", 1
    )[1].split("[gcode_macro _FF_NOZZLE_WIPE]", 1)[0]
    assert lip_wipe_body.count("F{ff.lip_wipe_feed}") == 14
    assert config_source.count("_NS_CHUTE_LIP_WIPE TOOL=") == 2
    assert "variable_exit_x:" not in config_source
    assert "variable_exit_y:" not in config_source


def test_cooldown_pad_cycle_covers_every_safe_grid_point_once():
    config_source = CONFIG.read_text(encoding="utf-8")

    def value(name):
        match = re.search(r"^variable_%s:\s*(.+)$" % name,
                          config_source, re.MULTILINE)
        assert match, "missing macro variable %s" % name
        return ast.literal_eval(match.group(1))

    x_offsets = value("cooldown_pad_x_offsets")
    y_offsets = value("cooldown_pad_y_offsets")
    count = len(x_offsets) * len(y_offsets)
    for seed in range(count):
        points = [
            (x_offsets[(seed + index) % count % len(x_offsets)],
             y_offsets[(seed + index) % count % len(y_offsets)])
            for index in range(count)
        ]

        assert len(points) == 30
        assert len(set(points)) == 30
        assert {x for x, _y in points} == set(range(-4, 2))
        assert {y for _x, y in points} == set(range(-4, 1))
        assert max(266.5 + x for x, _y in points) <= 268.0
        assert max(13.8 + y for _x, y in points) <= 14.0


def test_the_host_stall_guards_are_on_in_the_n4s4_config():
    config_source = CONFIG.read_text(encoding="utf-8")
    section = re.search(r"^\[ff_toolchange\]\n(.*?)(?=^\[)", config_source,
                        re.S | re.M)
    assert section, "no [ff_toolchange] section"

    assert re.search(r"^flush_steps_before_wait: 1$", section.group(1),
                     re.M)
    assert re.search(r"^\[ff_lagwatch\]$", config_source, re.M)


def test_the_no_tower_macro_the_toolchanger_calls_exists():
    """ff_toolchange checks at connect that every macro it will call exists
    and refuses to start otherwise; the name in printer_n4s4.cfg must be one."""
    config_source = CONFIG.read_text(encoding="utf-8")
    # the section header at the start of a line, not a mention in a comment
    section = re.search(r"^\[ff_toolchange\]\n(.*?)(?=^\[)", config_source,
                        re.S | re.M)
    assert section, "no [ff_toolchange] section"
    toolchange = section.group(1)
    named = re.search(r"^no_tower_prime_macro:\s*(\S+)", toolchange, re.M)

    assert named, "no_tower_prime_macro is not set"
    assert re.search(r"^\[gcode_macro %s\]$" % re.escape(named.group(1)),
                     config_source, re.M)

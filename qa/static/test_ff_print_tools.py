"""Which tools a print file uses, and which one comes second (N4S4).

ff_print reads these from the file so the print start can purge every colour
(`TOOLS=`) and heat the second colour once the mesh is done. Orca names its
tools in a `; filament: 1,3` header (one-based); any other file is scanned for
bare `Tn` lines.
"""

import importlib.util
from types import SimpleNamespace

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.static

MODULE = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" /
          "klippy" / "extras" / "ff_print.py")


def _module():
    spec = importlib.util.spec_from_file_location("ff_print_tools", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse(tmp_path, text):
    path = tmp_path / "job.gcode"
    path.write_text(text, encoding="utf-8")
    return _module()._parse_metadata(str(path))


# --------------------------------------------------------------- used tools

def test_orcas_filament_header_names_the_used_tools(tmp_path):
    metadata = _parse(tmp_path, "; filament: 1,3\nM104 S220\nT0\nG1 X1\n")

    assert metadata["tools"] == [0, 2]


def test_the_initial_tool_is_always_listed_first(tmp_path):
    metadata = _parse(tmp_path, "; filament: 2,4\nM104 S220\nT0\nG1 X1\n")

    assert metadata["tools"] == [0, 1, 3]


def test_a_header_value_outside_the_four_tools_is_ignored(tmp_path):
    metadata = _parse(tmp_path, "; filament: 1,7,2\nT0\n")

    assert metadata["tools"] == [0, 1]


def test_without_the_header_the_file_is_scanned_for_bare_tool_selects(tmp_path):
    metadata = _parse(
        tmp_path, "M104 S220\nT0\nG1 X1\nT3\nG1 X2\nT1\nG1 X3\nT3\nT7\n")

    assert metadata["tools"] == [0, 3, 1]       # in order of first use


def test_a_file_with_no_tool_commands_lists_none(tmp_path):
    assert "tools" not in _parse(tmp_path, "G28\nG1 X1 Y1\n")


def test_the_scan_sees_a_tool_that_is_far_into_the_file(tmp_path):
    module = _module()
    filler = "G1 X1 Y1 E1\n" * (module.HEAD_BYTES // 12 + 10)

    metadata = _parse(tmp_path, "T0\n" + filler + "T2\n")

    assert metadata["tools"] == [0, 2]


def test_the_scan_gives_up_when_it_takes_too_long(tmp_path, monkeypatch):
    """It runs on the thread that keeps the heaters alive."""
    module = _module()
    monkeypatch.setattr(module, "SCAN_BUDGET", -1.)
    path = tmp_path / "slow.gcode"
    path.write_text("T0\nG1 X1\nT3\n", encoding="utf-8")

    metadata = module._parse_metadata(str(path))

    assert metadata["tools"] == [0]            # the initial tool, nothing more


# ---------------------------------------------------------------- next tool

def test_the_second_tool_and_its_first_target_come_from_the_file(tmp_path):
    metadata = _parse(
        tmp_path,
        "M140 S60\nM104 S220\nT0\nG1 X1 Y1\nM104 S245 T2\nT2\n"
        "M109 S245\nG1 X2 Y2\nT0\nM109 S220\n")

    assert metadata["tool"] == 0
    assert metadata["next_tool"] == 2
    assert metadata["next_nozzle"] == 245


def test_a_target_given_for_a_tool_that_is_not_selected_yet_counts(tmp_path):
    metadata = _parse(
        tmp_path, "T0\nM104 S200\nT1\nM109 T1 S235\nT0\nM109 S200\n")

    assert metadata["next_tool"] == 1
    assert metadata["next_nozzle"] == 235


def test_the_first_non_zero_target_wins(tmp_path):
    metadata = _parse(
        tmp_path, "T0\nT1\nM109 S0\nM109 S230\nM109 S250\n")

    assert metadata["next_nozzle"] == 230


def test_a_second_tool_without_a_target_still_has_no_temperature(tmp_path):
    metadata = _parse(tmp_path, "T0\nG1 X1\nT3\nG1 X2\n")

    assert metadata["next_tool"] == 3
    assert "next_nozzle" not in metadata


def test_the_second_tool_is_found_in_the_head_of_the_file(tmp_path):
    module = _module()
    filler = "G1 X1 Y1 E1\n" * (module.NEXT_TOOL_SCAN_BYTES // 2 // 12)

    metadata = _parse(tmp_path, "M104 S220\nT0\n" + filler + "T1\nM109 S230\n")

    assert metadata["next_tool"] == 1
    assert metadata["next_nozzle"] == 230


def test_a_second_tool_beyond_the_head_is_not_looked_for(tmp_path):
    """Orca's own preheat lands in the object body by then, heating it from
    the start would leave a hot nozzle idle, and the scan runs on the thread
    that keeps the heaters alive."""
    module = _module()
    filler = "G1 X1 Y1 E1\n" * (module.NEXT_TOOL_SCAN_BYTES // 12 + 10)

    metadata = _parse(tmp_path, "M104 S220\nT0\n" + filler + "T1\nM109 S230\n")

    assert "next_tool" not in metadata
    assert "next_nozzle" not in metadata
    assert metadata["tools"] == [0, 1]         # the tool list is not bounded


def test_a_single_colour_file_has_no_second_tool(tmp_path):
    metadata = _parse(tmp_path, "M104 S220\nT0\nG1 X1 Y1\nT0\n")

    assert "next_tool" not in metadata
    assert "next_nozzle" not in metadata


# ------------------------------------------------ what the macro is told

class Gcode:
    def __init__(self):
        self.scripts = []

    def run_script_from_command(self, script):
        self.scripts.append(script)


def _announce(tmp_path, text):
    """Start `text` through ff_print and return it with what it ran."""
    module = _module()
    (tmp_path / "two.gcode").write_text(text, encoding="utf-8")
    gcode = Gcode()
    objects = {"gcode": gcode,
               "virtual_sdcard": SimpleNamespace(sdcard_dirname=str(tmp_path))}
    printer = SimpleNamespace(
        get_reactor=lambda: None,
        lookup_object=lambda name, default=None: objects.get(name, default),
        register_event_handler=lambda event, handler: None)
    config = SimpleNamespace(
        get_printer=lambda: printer,
        get=lambda name, default=None: default,
        getlist=lambda name, default=None: default,
        getboolean=lambda name, default=None: default)
    ff = module.FFPrint(config)
    ff.previous_handlers["SDCARD_PRINT_FILE"] = lambda gcmd: None
    ff._cmd_start("SDCARD_PRINT_FILE",
                  SimpleNamespace(get=lambda name, default="": "two.gcode"))
    return ff, gcode


def test_the_print_start_macro_is_not_given_the_tools(tmp_path):
    """The original's START_PRINT would clean every tool it is given; a start
    macro that wants them reads printer.ff_print.tools."""
    _, gcode = _announce(
        tmp_path, "; filament: 1,3\nM104 S220\nT0\nT2\nM109 S240\n")

    assert gcode.scripts == [
        "FF_BEFORE_PRINT_START ORIGIN=SDCARD_PRINT_FILE TOOL=0 NOZZLE=220"]


def test_a_file_without_tools_is_announced_the_same_way(tmp_path):
    _, gcode = _announce(tmp_path, "M104 S220\nG1 X1\n")

    assert gcode.scripts == [
        "FF_BEFORE_PRINT_START ORIGIN=SDCARD_PRINT_FILE NOZZLE=220"]


def test_the_status_reports_the_tools_and_the_second_one(tmp_path):
    ff, _ = _announce(
        tmp_path, "; filament: 1,3\nM104 S220\nT0\nT2\nM109 S240\n")

    status = ff.get_status(0.)

    assert status["tools"] == [0, 2]
    assert (status["next_tool"], status["next_nozzle"]) == (2, 240)

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
EXTRA = (ROOT / "pkgs/klipper/payload/klipper/klippy/extras"
         / "ff_tool_offset.py")
CONFIG = ROOT / "pkgs/klipper-config/payload/config/ff-tool-offset.cfg"


def load_module():
    spec = importlib.util.spec_from_file_location("anvil_ff_tool_offset", EXTRA)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CommandError(Exception):
    pass


class GCmd:
    def __init__(self, values=None):
        self.values = values or {}
        self.info = []

    def get_int(self, name, default, **_kwargs):
        return int(self.values.get(name, default))

    def get_float(self, name, default, **_kwargs):
        return float(self.values.get(name, default))

    def respond_info(self, message):
        self.info.append(message)

    def error(self, message):
        return CommandError(message)


def test_plate_delta_requires_the_configured_recess():
    module = load_module()
    assert module.plate_removed(0.05, -0.75, 0.8)
    assert not module.plate_removed(0.05, -0.749, 0.8)
    assert not module.plate_removed(0.02, 0.01, 0.8)


def make_plate_check(module, readings):
    check = module.FFToolOffset.__new__(module.FFToolOffset)
    check.plate_reference_x = 155.0
    check.plate_reference_y = 130.0
    check.plate_min_drop = 0.8
    check.z_start = 10.0
    check._cylinder = lambda: (28.5, 214.5)
    check._enter_raw_frame = lambda: None
    check._run = lambda command: check.commands.append(command)
    check._normal_probe = lambda: next(readings)
    check.commands = []
    check.printer = SimpleNamespace(command_error=CommandError)
    return check


def test_plate_check_uses_only_normal_probe_before_accepting():
    module = load_module()
    check = make_plate_check(module, iter((0.1, -1.1)))
    gcmd = GCmd()
    check._plate_check(gcmd)
    assert len([line for line in check.commands if line.startswith("G1 X")]) == 2
    assert not any("ESTOP" in line for line in check.commands)
    assert any("drop 1.200" in line for line in gcmd.info)


def test_plate_check_refuses_a_sheet_spanning_both_points():
    module = load_module()
    check = make_plate_check(module, iter((0.1, 0.08)))
    with pytest.raises(module.FFToolOffsetError, match="remove the build plate"):
        check._plate_check(GCmd())
    assert not any("ESTOP" in line for line in check.commands)


def test_tool_heater_is_turned_off_when_temperature_wait_fails():
    module = load_module()
    offset = module.FFToolOffset.__new__(module.FFToolOffset)
    offset.name = "ff_tool_offset"
    offset.reactor = SimpleNamespace(monotonic=lambda: 0.0)
    offset.printer = SimpleNamespace(command_error=CommandError)
    offset.toolchange = SimpleNamespace(
        get_status=lambda _eventtime: {"current_tool": 0, "state_ok": True})
    offset.tools = [SimpleNamespace(extruder_name="extruder")]
    offset.calibration_temp = 200.0
    offset.temperature_tolerance = 3.0
    offset.nozzle_x_shift = 12.5
    offset._check_homed = lambda _gcmd: None
    offset._run_plate_check = lambda _gcmd: None
    offset._cylinder = lambda: (28.5, 214.5)
    offset._leave_raw_frame = lambda *_args: None
    commands = []

    def run(command):
        commands.append(command)
        if command.startswith("TEMPERATURE_WAIT"):
            raise CommandError("heater timeout")

    offset._run = run
    with pytest.raises(CommandError, match="heater timeout"):
        offset.cmd_TOOL_CALIBRATE_TOOL_OFFSET(GCmd())
    assert commands[0] == (
        "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=200.0")
    assert commands[1] == (
        "TEMPERATURE_WAIT SENSOR=extruder MINIMUM=197.0 MAXIMUM=203.0")
    assert commands[-1] == "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=0"


def test_public_macro_measures_station_once_and_skips_duplicate_checks():
    text = CONFIG.read_text()
    macro = text.split("[gcode_macro CALIBRATE_TOOL_OFFSETS]", 1)[1]
    assert "params.TOOL" in macro
    assert macro.count("TOOL_LOCATE_SENSOR") == 1
    assert "TOOL_CALIBRATE_TOOL_OFFSET PLATE_CHECK=0" in macro
    assert "TEMP={params.TEMP}" in macro

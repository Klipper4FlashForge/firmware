"""The trip across the part during a tool change: hop, in-dock retract, unretract.

These run the real _toolchange, _restore_position, _retract_in_dock and
_raise_before_docking. The dock mechanics (_release, _grab's lock dance) are
substituted, so they do not validate motion on a printer; they pin the order
of the commands and which changes are left exactly as they were.
"""
import importlib.util
from types import SimpleNamespace

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.static

MODULE = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" / "klippy" /
          "extras" / "ff_toolchange.py")
POS = [100., 50., 3.2, 0.]


class CommandError(Exception):
    pass


class Extruder:
    def __init__(self, can_extrude=True):
        self.can_extrude = can_extrude

    def get_status(self, eventtime):
        return {"can_extrude": self.can_extrude}


@pytest.fixture
def travel(monkeypatch):
    spec = importlib.util.spec_from_file_location("anvil_ff_toolchange", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tc = module.FFToolchange.__new__(module.FFToolchange)
    tc.log = []
    tc.changing = False
    tc.restore_axis = "xy"
    tc.restore_feed = 9000
    tc.restore_z_hop = 2.0
    tc.restore_z_feed = 1200
    tc.restore_retract = 0.4
    tc.restore_retract_feed = 1800
    tc.restore_unretract = 0.3
    tc.restore_unretract_feed = 200
    tc.extruder = Extruder()
    tc.mounted = 0
    tc.gcode = SimpleNamespace(respond_info=tc.log.append)
    tc.reactor = SimpleNamespace(monotonic=lambda: 0.)
    tc.printer = SimpleNamespace(
        command_error=CommandError,
        lookup_object=lambda name, default=None: SimpleNamespace(
            get_extruder=lambda: tc.extruder))
    tc.tools = [SimpleNamespace(
        extruder_name="extruder" if i == 0 else "extruder%d" % i)
        for i in range(4)]

    monkeypatch.setattr(tc, "_run", lambda script: tc.log.append(script))
    monkeypatch.setattr(tc, "_wait_moves", lambda: None)
    monkeypatch.setattr(tc, "_ensure_homed", lambda *a: None)
    monkeypatch.setattr(tc, "_capture_position", lambda: list(POS))
    monkeypatch.setattr(tc, "_derive_current_tool",
                        lambda: (tc.mounted, "sensors"))
    monkeypatch.setattr(tc, "_set_tool_frame", lambda tool: None)
    monkeypatch.setattr(tc, "_arm_runout", lambda tool: None)
    monkeypatch.setattr(tc, "_release",
                        lambda tool: tc.log.append("release T%d" % tool))

    def grab(tool, retract_in_dock=False):
        tc.log.append("grab T%d in_dock=%s" % (tool, retract_in_dock))
        return tc._retract_in_dock() if retract_in_dock else 0.

    monkeypatch.setattr(tc, "_grab", grab)
    gcmd = SimpleNamespace(get=lambda name, default=None: default,
                           error=CommandError)
    return tc, gcmd


def change(tc, gcmd, tool=2):
    tc._toolchange(gcmd, tool)
    return tc.log


def test_nothing_set_leaves_the_change_exactly_as_it_was(travel):
    tc, gcmd = travel
    tc.restore_z_hop = tc.restore_retract = 0.
    tc.restore_unretract = 0.

    assert change(tc, gcmd) == [
        "release T0", "grab T2 in_dock=False",
        "SAVE_GCODE_STATE NAME=_ff_restore_axis", "G90",
        "G1 X100.000 Y50.000 F9000",
        "RESTORE_GCODE_STATE NAME=_ff_restore_axis"]


def test_a_protected_change_raises_retracts_travels_lowers_and_unretracts(travel):
    tc, gcmd = travel

    assert change(tc, gcmd) == [
        # the old tool rises before it crosses the part to its dock
        "SAVE_GCODE_STATE NAME=_ff_dock_zhop", "G90", "G1 Z5.200 F1200",
        "RESTORE_GCODE_STATE NAME=_ff_dock_zhop",
        "release T0",
        # the new tool pulls filament back while it is still seated
        "grab T2 in_dock=True",
        "SAVE_GCODE_STATE NAME=_ff_dock_retract", "M83", "G1 E-0.400 F1800",
        "RESTORE_GCODE_STATE NAME=_ff_dock_retract",
        # travel high, descend, give back part of it
        "SAVE_GCODE_STATE NAME=_ff_restore_axis", "G90",
        "G1 Z5.200 F1200", "G1 X100.000 Y50.000 F9000", "G1 Z3.200 F1200",
        "M83", "G1 E0.300 F200",
        "RESTORE_GCODE_STATE NAME=_ff_restore_axis"]


def test_the_unretract_never_exceeds_what_was_retracted(travel, monkeypatch):
    tc, gcmd = travel
    tc.restore_unretract = 0.4
    monkeypatch.setattr(tc, "_retract_in_dock", lambda: 0.1)

    log = change(tc, gcmd)

    assert "G1 E0.100 F200" in log


def test_a_hop_alone_does_not_touch_the_filament(travel):
    tc, gcmd = travel
    tc.restore_retract = tc.restore_unretract = 0.

    log = change(tc, gcmd)

    assert "G1 Z5.200 F1200" in log and "G1 Z3.200 F1200" in log
    assert not any(command.startswith("G1 E") for command in log)


def test_a_retract_alone_does_not_move_z(travel):
    tc, gcmd = travel
    tc.restore_z_hop = 0.

    log = change(tc, gcmd)

    assert "G1 E-0.400 F1800" in log and "G1 E0.300 F200" in log
    assert not any(command.startswith("G1 Z") for command in log)


def test_a_cold_tool_is_not_retracted_or_unretracted(travel):
    tc, gcmd = travel
    tc.extruder = Extruder(can_extrude=False)

    log = change(tc, gcmd)

    assert not any(command.startswith("G1 E") for command in log)
    assert any("below minimum extrusion temperature" in line
               for line in log if not line.startswith(("G", "M", "SAVE")))


def test_the_first_pickup_has_nothing_to_raise_but_still_retracts(travel):
    tc, gcmd = travel
    tc.mounted = -1

    log = change(tc, gcmd)

    assert "SAVE_GCODE_STATE NAME=_ff_dock_zhop" not in log
    assert "G1 E-0.400 F1800" in log and "G1 E0.300 F200" in log


def test_reselecting_the_mounted_tool_keeps_the_plain_return(travel):
    tc, gcmd = travel
    tc.mounted = 2

    log = change(tc, gcmd, tool=2)

    assert "release T2" not in log
    assert log[-4:] == ["SAVE_GCODE_STATE NAME=_ff_restore_axis", "G90",
                        "G1 X100.000 Y50.000 F9000",
                        "RESTORE_GCODE_STATE NAME=_ff_restore_axis"]
    assert not any(command.startswith("G1 E") for command in log)
    assert not any(command.startswith("G1 Z") for command in log)


def test_without_xy_restored_the_protection_is_off(travel):
    tc, gcmd = travel
    tc.restore_axis = ""

    log = change(tc, gcmd)

    assert log == ["release T0", "grab T2 in_dock=False"]


def test_z_only_restore_is_not_protected(travel):
    tc, gcmd = travel
    tc.restore_axis = "Z"

    log = change(tc, gcmd)

    assert "grab T2 in_dock=False" in log
    assert "SAVE_GCODE_STATE NAME=_ff_dock_zhop" not in log
    assert not any(command.startswith("G1 E") for command in log)


def test_the_hop_follows_the_captured_z_not_a_fixed_height(travel, monkeypatch):
    tc, gcmd = travel
    monkeypatch.setattr(tc, "_capture_position",
                        lambda: [10., 20., 0.6, 0.])

    log = change(tc, gcmd)

    assert log.count("G1 Z2.600 F1200") == 2
    assert "G1 Z0.600 F1200" in log

"""The trip back to the print after a tool change, under AFC.

AFC owns the move back ([AFC] z_hop lifts and travels high; restore_pos puts
the head back). ff_toolchange adds the in-dock retract: the new head pulls
filament back while still seated, and gives part of it back right after
AFC's restore_pos. These run the real _retract_in_dock, _hook_afc_restore,
_unretract_at_target and _toolchange against a stand-in AFC; the dock
mechanics are substituted, so they pin the order of commands, not motion on
a printer.
"""
import importlib.util
from types import SimpleNamespace

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.static

MODULE = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" / "klippy" /
          "extras" / "ff_toolchange.py")

RETRACT = ["ACTIVATE_EXTRUDER EXTRUDER=extruder2",
           "SAVE_GCODE_STATE NAME=_ff_dock_retract", "M83",
           "G1 E-0.400 F1800", "RESTORE_GCODE_STATE NAME=_ff_dock_retract"]
UNRETRACT = ["SAVE_GCODE_STATE NAME=_ff_dock_unretract", "M83",
             "G1 E0.300 F200", "RESTORE_GCODE_STATE NAME=_ff_dock_unretract"]


class CommandError(Exception):
    pass


class Extruder:
    def __init__(self, can_extrude=True):
        self.can_extrude = can_extrude

    def get_status(self, eventtime):
        return {"can_extrude": self.can_extrude}


class FakeAFC:
    """The two flags and the one method ff_toolchange relies on."""

    def __init__(self, log):
        self.log = log
        self.in_toolchange = True
        self.position_saved = True
        self.fail = False

    def restore_pos(self, move_z_first=True):
        if self.fail:
            raise CommandError("restore failed")
        self.log.append("AFC restore_pos")


@pytest.fixture
def travel(monkeypatch):
    spec = importlib.util.spec_from_file_location("anvil_ff_toolchange", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tc = module.FFToolchange.__new__(module.FFToolchange)
    tc.log = []
    tc.changing = False
    tc.dock_retract = 0.4
    tc.dock_retract_feed = 1800
    tc.dock_unretract = 0.3
    tc.dock_unretract_feed = 200
    tc.pending_unretract = 0.
    tc.extruder = Extruder()
    tc.afc = FakeAFC(tc.log)
    tc.mounted = 0
    tc.gcode = SimpleNamespace(respond_info=tc.log.append)
    tc.reactor = SimpleNamespace(monotonic=lambda: 0.)
    toolhead = SimpleNamespace(get_extruder=lambda: tc.extruder)

    def lookup_object(name, default=None):
        if name == "AFC":
            return tc.afc
        if name == "toolhead":
            return toolhead
        return default

    tc.printer = SimpleNamespace(command_error=CommandError,
                                 lookup_object=lookup_object)
    tc.tools = [SimpleNamespace(
        extruder_name="extruder" if i == 0 else "extruder%d" % i)
        for i in range(4)]

    monkeypatch.setattr(tc, "_run", lambda script: tc.log.append(script))
    monkeypatch.setattr(tc, "_wait_moves", lambda: None)
    monkeypatch.setattr(tc, "_ensure_homed", lambda *a: None)
    monkeypatch.setattr(tc, "_derive_current_tool",
                        lambda: (tc.mounted, "sensors"))
    monkeypatch.setattr(tc, "_set_tool_frame", lambda tool: None)
    monkeypatch.setattr(tc, "_arm_runout", lambda tool: None)
    monkeypatch.setattr(tc, "_release",
                        lambda tool: tc.log.append("release T%d" % tool))

    def grab(tool):
        # The real _grab calls this between the lock and the withdraw.
        tc.log.append("lock T%d" % tool)
        tc._retract_in_dock(tool)
        tc.log.append("withdraw T%d" % tool)

    monkeypatch.setattr(tc, "_grab", grab)
    gcmd = SimpleNamespace(get=lambda name, default=None: default,
                           error=CommandError)
    return tc, gcmd


def afc_change(tc, gcmd, tool=2):
    """What AFC's CHANGE_TOOL does around our SELECT_TOOL."""
    tc._hook_afc_restore()
    tc._toolchange(gcmd, tool)
    tc.afc.restore_pos()
    return tc.log


def test_a_change_retracts_in_the_dock_and_gives_back_after_afcs_restore(travel):
    tc, gcmd = travel

    assert afc_change(tc, gcmd) == (
        ["release T0", "lock T2"] + RETRACT + ["withdraw T2",
         "AFC restore_pos"] + UNRETRACT)
    assert tc.pending_unretract == 0.


def test_nothing_set_leaves_the_change_and_afc_untouched(travel):
    tc, gcmd = travel
    tc.dock_retract = tc.dock_unretract = 0.
    original = tc.afc.restore_pos

    assert afc_change(tc, gcmd) == [
        "release T0", "lock T2", "withdraw T2", "AFC restore_pos"]
    assert tc.afc.restore_pos == original


def test_no_retract_for_a_change_afc_will_not_move_back_from(travel):
    """A SELECT_TOOL from the console, or a change before AFC saved a
    position: nothing would give the filament back at a target."""
    tc, gcmd = travel
    tc._hook_afc_restore()
    tc.afc.in_toolchange = False

    tc._toolchange(gcmd, 2)

    assert tc.log == ["release T0", "lock T2", "withdraw T2"]
    assert tc.pending_unretract == 0.


def test_a_cold_head_is_not_retracted_and_nothing_is_owed(travel):
    tc, gcmd = travel
    tc.extruder.can_extrude = False

    log = afc_change(tc, gcmd)

    assert not any(line.startswith("G1 E") for line in log), log
    assert any("below minimum extrusion temperature" in line for line in log)


def test_the_unretract_never_exceeds_what_was_retracted(travel):
    tc, gcmd = travel
    tc.dock_unretract = 0.4
    tc._hook_afc_restore()
    tc.pending_unretract = 0.1

    tc.afc.restore_pos()

    assert "G1 E0.100 F200" in tc.log


def test_a_failed_restore_drops_the_owed_filament(travel):
    tc, gcmd = travel
    tc._hook_afc_restore()
    tc._toolchange(gcmd, 2)
    tc.afc.fail = True

    with pytest.raises(CommandError):
        tc.afc.restore_pos()

    assert tc.pending_unretract == 0.
    assert not any(line.startswith("SAVE_GCODE_STATE NAME=_ff_dock_unretract")
                   for line in tc.log)


def test_a_new_change_drops_a_retract_still_owed_from_the_last(travel):
    tc, gcmd = travel
    tc.pending_unretract = 0.4
    tc.mounted = 2

    tc._toolchange(gcmd, 2)    # re-select: no grab, no retract

    assert tc.pending_unretract == 0.


def test_the_hook_wraps_afc_once(travel):
    tc, gcmd = travel
    tc._hook_afc_restore()
    wrapped = tc.afc.restore_pos
    tc._hook_afc_restore()

    assert tc.afc.restore_pos is wrapped

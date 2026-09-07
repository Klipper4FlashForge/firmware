"""The tool map: which physical tool answers each number a sliced file names.

THE BUG THIS EXISTS FOR. A sliced file says `T1`; until ASSIGN_TOOL existed,
that was always physical tool 1, and printing a file whose filaments sat in
other heads meant re-slicing. The map makes `T1` movable -- and makes every
number in the tree ambiguous until you know which vocabulary it is written in.
Get the direction of a lookup backwards and nothing raises: the machine grabs
the wrong head, or TOOL_CALIBRATE_TOOL_OFFSET measures one tool and saves the
result into another tool's section, which is a wrong nozzle_z and ~3 mm in the
crash direction.

WHY A UNIT TEST, AND WHY IT IS THIS SHAPE. The replica cannot exercise any of
this end to end: there is no /dev/ttyS4 to hand a board over on, so klippy
never reaches ready -- qa/replica/test_mcu_bringup.py's header says so, and
names the simulated-MCU lane as what would close it. `_ToolMap` was extracted
from FFToolchange precisely so the decision logic does not have to wait for
that lane. It takes no printer, no config and no gcode object, so it imports
and runs on the host in milliseconds. What is NOT covered here is the wiring
-- that the map is consulted at each call site -- which is what the macro
expansion tests in test_klipper_config.py are for.
"""
import importlib.util

import pytest

from qa.lib.paths import ROOT

EXTRUDER_COUNT = 4


def _load_module():
    """Import ff_toolchange off the shipped payload, with no klippy present.

    The module imports only contextlib and logging at module level, so it
    loads on the host interpreter. Importing the file we SHIP is the point:
    a copy of the class in the test would pass forever after the real one
    changed.
    """
    path = (ROOT / "pkgs/klipper/payload/klipper/klippy/extras"
            / "ff_toolchange.py")
    spec = importlib.util.spec_from_file_location("ff_toolchange", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pytestmark = pytest.mark.static
_ff = _load_module()
_ToolMap = _ff._ToolMap
IDENTITY = list(range(EXTRUDER_COUNT))
UNSET = -1


def _map(baseline=None):
    return _ToolMap(IDENTITY if baseline is None else baseline)


def test_a_fresh_map_is_the_identity():
    m = _map()
    assert m.is_identity()
    assert m.as_list() == IDENTITY
    assert m.inverse() == IDENTITY
    assert [m.number_of(p) for p in IDENTITY] == IDENTITY
    assert [m.physical_of(n) for n in IDENTITY] == IDENTITY


def test_assigning_moves_the_number_and_displaces_the_previous_holder():
    """klipper-toolchanger's replace=True: the displaced tool is left
    unnumbered, NOT handed the assigner's old number. Swapping silently moves
    a second tool, which is not what the operator typed."""
    m = _map()
    displaced = m.assign(3, 1)
    assert displaced == 1
    assert m.physical_of(1) == 3
    assert m.number_of(3) == 1
    assert m.number_of(1) == UNSET
    assert not m.is_identity()


def test_a_displaced_tool_is_unreachable_by_number_but_still_a_tool():
    m = _map()
    m.assign(3, 1)
    # Tool 1 is the one left unnumbered -- the NUMBER 1 is very much still
    # assigned, to tool 3. Only the bare T<n> route into tool 1 is gone;
    # TOOL=T1 still names it, which is what every console command, dock,
    # sensor and status object uses.
    assert m.number_of(1) == UNSET
    assert m.as_list()[1] == UNSET
    assert m.physical_of(1) == 3
    assert 1 not in [m.physical_of(n) for n in m.numbers()]


def test_a_swap_takes_two_assignments():
    m = _map()
    m.assign(3, 1)
    m.assign(1, 3)
    assert m.physical_of(1) == 3
    assert m.physical_of(3) == 1
    assert m.physical_of(0) == 0
    assert m.numbers() == IDENTITY
    assert not m.is_identity()


def test_reassigning_a_tool_its_own_number_changes_nothing():
    m = _map()
    assert m.assign(2, 2) == UNSET
    assert m.is_identity()


def test_an_unassigned_number_resolves_to_nothing():
    m = _map()
    m.assign(3, 1)
    # 3 was vacated by the displacement; nothing answers to it.
    assert m.physical_of(3) == UNSET
    assert m.inverse() == [0, 3, 2, UNSET]


def test_a_negative_or_absent_number_is_not_a_lookup():
    """physical_of() must never treat the -1 sentinel as a number to find --
    list.index(-1) would happily return the first unnumbered tool."""
    m = _map()
    m.assign(3, 1)
    assert m.physical_of(UNSET) == UNSET
    assert m.physical_of(None) == UNSET


def test_reset_restores_the_configured_baseline_not_bare_identity():
    m = _map([1, 0, 2, 3])
    m.assign(3, 1)
    m.reset()
    assert m.as_list() == [1, 0, 2, 3]


def test_identity_is_measured_against_true_identity():
    """A machine that configures a permanent remap earns the same standing
    notice as one that types ASSIGN_TOOL -- otherwise the map lies quietly."""
    m = _map([1, 0, 2, 3])
    assert not m.is_identity()


def test_tool_numbers_are_sorted_and_tool_names_stay_parallel():
    """klipper-toolchanger's contract, and what a tool-changer-aware UI
    reads: tool_names[i] is the tool answering tool_numbers[i]."""
    m = _map()
    m.assign(3, 1)
    # 3 dropped out (nothing answers to it now) and 1 moved to tool T3.
    assert m.numbers() == [0, 1, 2]
    assert m.names() == ['T0', 'T3', 'T2']
    m.assign(1, 3)
    assert m.numbers() == [0, 1, 2, 3]
    assert m.names() == ['T0', 'T3', 'T2', 'T1']


def test_the_inverse_is_dense_and_marks_gaps():
    m = _map()
    m.assign(0, 2)
    assert len(m.inverse()) == EXTRUDER_COUNT
    assert m.inverse()[2] == 0
    assert m.inverse()[0] == UNSET


def test_describe_is_one_quiet_line_when_the_map_is_identity():
    assert _map().describe() == [
        "tool map: identity (the file's T<n> is tool T<n>)"]


def test_describe_names_every_number_and_flags_the_assigned_ones():
    m = _map()
    m.assign(3, 1)
    text = "\n".join(m.describe())
    assert "NOT IDENTITY" in text
    assert "file T1  ->  tool T3" in text
    assert "<- assigned" in text
    # The displaced tool must say how it can still be reached, and the map
    # must say it does not survive a restart.
    assert "tool T1 has no number" in text
    assert "TOOL=T1" in text
    assert "RESET=1" in text


def test_describe_flags_a_number_nothing_answers_to():
    m = _map()
    m.assign(3, 1)
    assert any("file T3  ->  NOTHING" in line for line in m.describe())


# ---------------------------------------------------------------------------
# ASSIGN_TOOL itself. The command is exercised against the shipped method,
# with the handful of klippy objects it touches stubbed -- the guards are the
# safety-relevant half and the replica cannot reach them.
# ---------------------------------------------------------------------------

class _CommandError(Exception):
    pass


class _Gcmd:
    """Just enough of Klipper's GCodeCommand for these paths."""

    _MISSING = object()

    def __init__(self, **params):
        self.params = {k: str(v) for k, v in params.items()}
        self.responses = []

    def get(self, name, default=_MISSING):
        if name in self.params:
            return self.params[name]
        if default is self._MISSING:
            raise _CommandError("missing %s" % name)
        return default

    def get_int(self, name, default=_MISSING, minval=None, maxval=None):
        if name not in self.params:
            if default is self._MISSING:
                raise _CommandError("missing %s" % name)
            return default
        value = int(self.params[name])
        if minval is not None and value < minval:
            raise _CommandError("%s below %s" % (name, minval))
        if maxval is not None and value > maxval:
            raise _CommandError("%s above %s" % (name, maxval))
        return value

    def respond_info(self, message):
        self.responses.append(message)

    def error(self, message):
        return _CommandError(message)


class _Status:
    def __init__(self, status):
        self._status = status

    def get_status(self, _eventtime):
        return self._status


class _Printer:
    def __init__(self, objects):
        self._objects = objects

    def lookup_object(self, name, default=None):
        return self._objects.get(name, default)


class _Reactor:
    def monotonic(self):
        return 0.0


def _changer(baseline=None, changing=False, printing=False, paused=False):
    """A FFToolchange with only what cmd_ASSIGN_TOOL touches."""
    changer = _ff.FFToolchange.__new__(_ff.FFToolchange)
    changer.tool_map = _map(baseline)
    changer.changing = changing
    changer.reactor = _Reactor()
    changer.printer = _Printer({
        'print_stats': _Status({'state': 'printing' if printing else 'ready'}),
        'pause_resume': _Status({'is_paused': paused}),
    })
    return changer


def test_assign_tool_points_a_number_at_a_tool():
    changer, gcmd = _changer(), _Gcmd(TOOL='T3', N=1)
    changer.cmd_ASSIGN_TOOL(gcmd)
    assert changer.tool_map.physical_of(1) == 3
    assert "T3 now answers to 1" in gcmd.responses[0]


def test_assign_tool_says_who_it_displaced_and_how_to_reach_them():
    """The displaced tool is the surprise, so it must be in the response
    along with the way back."""
    changer, gcmd = _changer(), _Gcmd(TOOL='T3', N=1)
    changer.cmd_ASSIGN_TOOL(gcmd)
    said = gcmd.responses[0]
    assert "T1 has no number" in said
    assert "RESET=1" in said


def test_assign_tool_accepts_a_bare_number_as_the_tool():
    changer, gcmd = _changer(), _Gcmd(TOOL='3', N=1)
    changer.cmd_ASSIGN_TOOL(gcmd)
    assert changer.tool_map.physical_of(1) == 3


def test_assign_tool_reports_a_no_op_rather_than_displacing_anyone():
    changer, gcmd = _changer(), _Gcmd(TOOL='T2', N=2)
    changer.cmd_ASSIGN_TOOL(gcmd)
    assert changer.tool_map.is_identity()
    assert "already answers" in gcmd.responses[0]


def test_reset_restores_the_map():
    changer = _changer()
    changer.cmd_ASSIGN_TOOL(_Gcmd(TOOL='T3', N=1))
    gcmd = _Gcmd(RESET=1)
    changer.cmd_ASSIGN_TOOL(gcmd)
    assert changer.tool_map.is_identity()
    assert "reset" in gcmd.responses[0]


def test_a_number_outside_the_machines_range_is_refused():
    """Bounded because a file for this machine only ever emits T0..T3, and
    the bound is what keeps every status list dense."""
    changer = _changer()
    with pytest.raises(_CommandError):
        changer.cmd_ASSIGN_TOOL(_Gcmd(TOOL='T3', N=EXTRUDER_COUNT))


def test_remapping_during_a_toolchange_is_refused():
    changer = _changer(changing=True)
    with pytest.raises(_CommandError, match="toolchange is running"):
        changer.cmd_ASSIGN_TOOL(_Gcmd(TOOL='T3', N=1))


def test_remapping_mid_print_is_refused():
    """The next T<n> would go to a different nozzle, with different offsets,
    in the middle of an object."""
    changer = _changer(printing=True)
    with pytest.raises(_CommandError, match="print is running"):
        changer.cmd_ASSIGN_TOOL(_Gcmd(TOOL='T3', N=1))


def test_remapping_while_paused_is_allowed():
    """Paused is exactly when somebody swaps a spool and wants the rest of
    the file pointed at another tool."""
    changer = _changer(printing=True, paused=True)
    changer.cmd_ASSIGN_TOOL(_Gcmd(TOOL='T3', N=1))
    assert changer.tool_map.physical_of(1) == 3


def test_reset_works_even_mid_print():
    """RESET is the way out of a bad map, so it must not be gated behind the
    state that makes a bad map dangerous."""
    changer = _changer(printing=True)
    changer.cmd_ASSIGN_TOOL(_Gcmd(RESET=1))
    assert changer.tool_map.is_identity()

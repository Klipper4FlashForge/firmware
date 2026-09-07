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


def _load_tool_map():
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
    return module._ToolMap


pytestmark = pytest.mark.static
_ToolMap = _load_tool_map()
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

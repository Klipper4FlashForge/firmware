"""Regression tests for the multi-extruder exclude-object transform.

Upstream stores the skipped-move offset by extruder, but stores the maxima
used for retraction compensation globally. The old code therefore turns the
final XY-only move below into +99 mm of physical E after T2 -> T0.
"""
import importlib.util
import os

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.static

MODULE = os.path.join(
    ROOT, "pkgs", "klipper", "payload", "klipper", "klippy", "extras",
    "exclude_object.py")


def load_module():
    spec = importlib.util.spec_from_file_location(
        "anvil_exclude_object", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Extruder:
    def __init__(self, name):
        self.name = name

    def get_name(self):
        return self.name


class Toolhead:
    def __init__(self, name):
        self.extruder = Extruder(name)

    def get_extruder(self):
        return self.extruder

    def select(self, name):
        self.extruder = Extruder(name)


class Downstream:
    def __init__(self, position):
        self.position = list(position)
        self.moves = []

    def get_position(self):
        return list(self.position)

    def move(self, position, speed):
        self.position = list(position)
        self.moves.append((list(position), speed))


def build_transform(tool="extruder2", position=(0., 0., 0., 100.)):
    module = load_module()
    eo = module.ExcludeObject.__new__(module.ExcludeObject)
    eo.toolhead = Toolhead(tool)
    eo.next_transform = Downstream(position)
    eo.xyz_offset = [0., 0., 0.]
    eo.physical_xyz = None
    eo.last_excluded_xyz = [0., 0., 0.]
    eo.e_states = {}
    eo.active_extruder = None
    eo.initial_extrusion_moves = 0
    eo.last_position = [0., 0., 0., 0.]
    eo.current_object = None
    eo.excluded_objects = ["BAD"]
    eo.get_position()
    return eo


def select(eo, name, physical_e):
    eo.toolhead.select(name)
    eo.next_transform.position[3] = physical_e
    eo.get_position()


def test_tool_change_does_not_put_old_tools_e_history_on_xy_travel():
    eo = build_transform()

    # T2's E=100 must never enter T0's retraction calculation.
    select(eo, "extruder", 0.)
    eo.move([0., 0., 0., 1.], 20.)

    eo.current_object = "BAD"
    eo.move([10., 10., 0., 2.], 20.)       # suppressed
    assert eo.next_transform.position == [0., 0., 0., 1.]

    eo.current_object = None
    eo.move([20., 20., 0., 2.], 500.)      # XY-only exit travel

    assert eo.next_transform.position == [20., 20., 0., 1.]
    assert eo.e_states["extruder2"]["max_normal"] == 100.
    # The virtual E=2 travel becomes the new normal high-water mark for T0,
    # but its downstream E correctly remains 1. It must not affect T2.
    assert eo.e_states["extruder"]["max_normal"] == 2.


def test_each_tool_keeps_its_own_pending_retraction_adjustment():
    eo = build_transform(tool="extruder", position=(0., 0., 0., 10.))
    eo.move([0., 0., 0., 8.], 20.)         # T0 retracted by 2
    eo.current_object = "BAD"
    eo.move([5., 5., 0., 7.], 20.)         # skipped retract
    eo.move([6., 6., 0., 9.], 20.)         # skipped partial unretract

    # T1 may consume the shared spatial offset, but not T0's E correction.
    eo.current_object = None
    select(eo, "extruder1", 0.)
    eo.move([20., 20., 0., 0.], 100.)
    assert eo.next_transform.position[3] == 0.

    # Returning to T0 does not put its pending correction on an XY-only move.
    # It is retained until T0's own next E change.
    select(eo, "extruder", 8.)
    eo.move([21., 20., 0., 9.], 100.)
    assert eo.next_transform.position[3] == 8.
    assert eo.e_states["extruder"]["pending_adjustment"] == -1.
    assert eo.e_states["extruder1"]["pending_adjustment"] == 0.

    eo.move([21., 20., 0., 10.], 20.)
    assert eo.next_transform.position[3] == 10.
    assert eo.e_states["extruder"]["pending_adjustment"] == 0.


def test_first_tool_state_uses_its_real_negative_e_origin():
    eo = build_transform(tool="extruder", position=(0., 0., 0., -20.))
    state = eo.e_states["extruder"]
    assert state["last_normal"] == -20.
    assert state["max_normal"] == -20.
    assert state["last_excluded"] == -20.
    assert state["max_excluded"] == -20.


def test_single_extruder_retraction_compensation_is_preserved():
    eo = build_transform(tool="extruder", position=(0., 0., 0., 10.))
    eo.move([0., 0., 0., 8.], 20.)         # normal retract: 2 mm debt
    eo.current_object = "BAD"
    eo.move([5., 5., 0., 7.], 20.)         # suppressed retract
    eo.move([6., 6., 0., 9.], 20.)         # suppressed partial unretract

    eo.current_object = None
    eo.move([20., 20., 0., 9.], 100.)      # leaving object, XY only

    # Excluded debt is 1 mm and normal debt was 2 mm, so the -1 adjustment
    # combines with the skipped net +1 and virtual E9 maps to physical E9.
    assert eo.next_transform.position == [20., 20., 0., 9.]
    assert eo.e_states["extruder"]["pending_adjustment"] == 0.


def test_initial_five_extrusions_remain_one_global_grace_period():
    eo = build_transform(tool="extruder", position=(0., 0., 0., 0.))
    eo.initial_extrusion_moves = 5
    eo.current_object = "BAD"

    for e in range(1, 6):
        eo.move([float(e), 0., 0., float(e)], 20.)
    assert len(eo.next_transform.moves) == 5

    select(eo, "extruder1", 0.)
    eo.move([6., 0., 0., 1.], 20.)
    assert len(eo.next_transform.moves) == 5


def test_large_adjustment_logs_complete_diagnostic(caplog):
    eo = build_transform(tool="extruder", position=(0., 0., 0., 0.))
    state = eo.e_states["extruder"]
    state.update(max_normal=20., last_normal=0.,
                 max_excluded=1., last_excluded=1., was_ignored=True)

    eo.move([1., 1., 0., 0.], 100.)

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "large E correction -20.000000 mm for extruder" in messages
    assert "normal=0.000000/20.000000" in messages
    assert "xyz_offset=" in messages

"""The rounded turn into a dock lane stays on the bed side of x_safe.

corner_radius replaces the 90-degree corner at (x_safe, dock_y) with a
quarter arc. Y travel while X > x_safe would hit the parked tools, so the
arc must lie entirely at X <= x_safe and end in the lane heading +X.
"""
import importlib.util
import math
from types import SimpleNamespace

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.static

MODULE = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" / "klippy" /
          "extras" / "ff_toolchange.py")


def make(position, corner_radius=20., dock_shortcuts=True):
    spec = importlib.util.spec_from_file_location("anvil_ff_toolchange", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tc = module.FFToolchange.__new__(module.FFToolchange)
    toolhead = SimpleNamespace(get_position=lambda: list(position) + [0., 0.])
    tc.printer = SimpleNamespace(lookup_object=lambda name, default=None: toolhead)
    tc.x_safe = 250.
    tc.fast_feed = 36000
    tc.dock_shortcuts = dock_shortcuts
    tc.corner_radius = corner_radius
    commands = []
    tc._run = commands.append
    return tc, commands


def words(command):
    parts = command.split()
    return parts[0], {p[0]: float(p[1:]) for p in parts[1:]}


@pytest.mark.parametrize("start,dock_y,arc", [
    ((250., 55.7), 205.3, 'G2'),   # T0 -> T3, travelling +Y
    ((250., 205.3), 55.7, 'G3'),   # T3 -> T0, travelling -Y
    ((120., 130.), 106.3, 'G3'),   # from the print, just above T1's lane
])
def test_arc_stays_behind_x_safe_and_enters_lane_heading_x(start, dock_y, arc):
    tc, commands = make(start)
    tc._move_to_dock_y(dock_y)
    assert len(commands) == 2
    (line, lw), (circle, cw) = map(words, commands)
    assert line == 'G1' and circle == arc
    r = tc.corner_radius
    sx, sy = lw['X'], lw['Y']
    assert sx == pytest.approx(tc.x_safe - r)
    assert (cw['X'], cw['Y']) == pytest.approx((tc.x_safe, dock_y))
    cx, cy = sx + cw['I'], sy + cw['J']
    # Both ends on the circle, and its rightmost point is the lane entry.
    assert math.hypot(sx - cx, sy - cy) == pytest.approx(r)
    assert math.hypot(cw['X'] - cx, cw['Y'] - cy) == pytest.approx(r)
    assert cx == pytest.approx(tc.x_safe)
    # Clockwise from the left point heads +Y; counter-clockwise heads -Y.
    # Either way the tangent at (x_safe, dock_y) is +X.
    assert (arc == 'G2') == (dock_y > sy)
    assert max(sx, cx) <= tc.x_safe


@pytest.mark.parametrize("start,radius,shortcuts,expected", [
    ((250., 100.), 20., True, ['G1 X250.000 Y106.300 F36000']),   # too close
    ((250., 55.7), 0., True, ['G1 X250.000 Y205.300 F36000']),    # disabled
    ((280., 55.7), 20., True, ['G1 X250.000 F36000', 'G1 Y205.300']),
    ((250., 55.7), 20., False, ['G1 X250.000 F36000', 'G1 Y205.300']),
])
def test_corner_falls_back_without_room_or_shortcuts(start, radius, shortcuts,
                                                     expected):
    tc, commands = make(start, radius, shortcuts)
    tc._move_to_dock_y(106.3 if start[1] == 100. else 205.3)
    assert commands == expected

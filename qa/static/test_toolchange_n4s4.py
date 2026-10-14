"""N4S4's additions to the tool change, on top of upstream's.

Upstream's test_toolchange_travel.py pins the plain hop / in-dock retract /
unretract. What is here covers what N4S4 adds, and it runs the same way: the
real _toolchange, _restore_position, _retract_in_dock and friends, with the
dock mechanics substituted. So these pin the ORDER of the commands and which
changes are left exactly as they were; they do not validate motion on a
printer.

  * a pickup that a purge line follows (TOOLCHANGE_PREPARE_PICKUP) and the
    registered prime tower (TOOLCHANGE_SET_PRIME_TOWER): which retract the
    pickup gets, and whether X/Y is replayed over the model;
  * the no-prime-tower rule (no_tower_prime_macro);
  * the shared extruder stepper ([ff_extruder]);
  * the build-plate and material Z components of the print offset;
  * the command names, and that none of this is on unless it is asked for.
"""
import contextlib
import importlib.util
import math
from types import SimpleNamespace

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.static

MODULE = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" / "klippy" /
          "extras" / "ff_toolchange.py")
POS = [100., 50., 3.2, 0.]
TOWER = dict(CENTER_X=20., CENTER_Y=200., WIDTH=28., DEPTH=14., BRIM=2.)
ON_THE_TOWER = [20., 200., 3.2, 0.]

REQUIRED = object()


class CommandError(Exception):
    pass


class Gcmd:
    """The part of Klipper's GCodeCommand these commands use."""

    error = CommandError

    def __init__(self, **params):
        self.params = {k.upper(): str(v) for k, v in params.items()}
        self.info = []

    def respond_info(self, message, log=True):
        self.info.append(message)

    def get(self, name, default=REQUIRED):
        if name in self.params:
            return self.params[name]
        if default is REQUIRED:
            raise CommandError("missing %s" % name)
        return default

    def _number(self, kind, name, default, minval, maxval, above):
        if name not in self.params:
            if default is REQUIRED:
                raise CommandError("missing %s" % name)
            return default
        value = kind(self.params[name])
        if minval is not None and value < minval or \
                maxval is not None and value > maxval or \
                above is not None and value <= above:
            raise CommandError("%s out of range" % name)
        return value

    def get_float(self, name, default=REQUIRED, minval=None, maxval=None,
                  above=None):
        return self._number(float, name, default, minval, maxval, above)

    def get_int(self, name, default=REQUIRED, minval=None, maxval=None):
        return self._number(int, name, default, minval, maxval, None)


class Extruder:
    def __init__(self, can_extrude=True):
        self.can_extrude = can_extrude

    def get_status(self, eventtime):
        return {"can_extrude": self.can_extrude}


def load_module():
    spec = importlib.util.spec_from_file_location("n4s4_ff_toolchange", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def rig(monkeypatch):
    """A bare FFToolchange in the N4S4 configuration, dock mechanics stubbed."""
    module = load_module()
    tc = module.FFToolchange.__new__(module.FFToolchange)
    tc.module = module
    tc.log = []
    tc.changing = False
    tc.mounted = 0
    tc.extruder = Extruder()
    tc.restore_axis = "xy"
    tc.restore_feed = 9000
    tc.restore_z_hop = 2.0
    tc.restore_z_feed = 1200
    tc.restore_retract = 0.4
    tc.restore_retract_feed = 1800
    tc.restore_unretract = 0.4
    tc.restore_unretract_feed = 200
    tc.purge_retract = 0.9
    tc.purge_retract_dwell_ms = 250
    tc.tower_repeat_retract = 0.0
    tc.gcode = SimpleNamespace(respond_info=tc.log.append)
    tc.reactor = SimpleNamespace(monotonic=lambda: 0.)
    tc.printer = SimpleNamespace(
        command_error=CommandError,
        lookup_object=lambda name, default=None: SimpleNamespace(
            get_extruder=lambda: tc.extruder))
    tc.tools = [SimpleNamespace(
        extruder_name="extruder" if i == 0 else "extruder%d" % i)
        for i in range(4)]
    tc.position = list(POS)

    monkeypatch.setattr(tc, "_run", lambda script: tc.log.append(script))
    monkeypatch.setattr(tc, "_wait_moves", lambda: None)
    monkeypatch.setattr(tc, "_ensure_homed", lambda *a: None)
    monkeypatch.setattr(tc, "_capture_position", lambda: list(tc.position))
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
    return tc


def change(tc, tool=2, **params):
    gcmd = Gcmd(**params)
    gcmd.get = lambda name, default=None: default
    tc._toolchange(gcmd, tool)
    return tc.log


def register_tower(tc, **overrides):
    params = dict(TOWER)
    params.update(overrides)
    tc.cmd_TOOLCHANGE_SET_PRIME_TOWER(Gcmd(**params))


HOP = ["SAVE_GCODE_STATE NAME=_ff_dock_zhop", "G90", "G1 Z5.200 F1200",
       "RESTORE_GCODE_STATE NAME=_ff_dock_zhop"]


def retract(distance, dwell=None):
    steps = ["SAVE_GCODE_STATE NAME=_ff_dock_retract", "M83",
             "G1 E-%.3f F1800" % distance]
    if dwell:
        steps.append("G4 P%d" % dwell)
    return steps + ["RESTORE_GCODE_STATE NAME=_ff_dock_retract"]


# ---------------------------------------------------------------------------
# A pickup that a purge line follows
# ---------------------------------------------------------------------------

def test_an_armed_first_pickup_retracts_harder_and_stays_raised(rig):
    rig.mounted = -1
    rig.cmd_TOOLCHANGE_PREPARE_PICKUP(Gcmd())

    log = change(rig)

    assert log == [
        "ff_toolchange: initial T2 pickup is followed by a purge line;"
        " skipping return to the final mesh point",
        "grab T2 in_dock=True"] + retract(0.9, dwell=250)
    assert rig.purge_pickup_armed is False       # used up by that pickup


def test_the_hint_can_be_cleared_before_it_is_used(rig):
    rig.cmd_TOOLCHANGE_PREPARE_PICKUP(Gcmd())
    rig.cmd_TOOLCHANGE_PREPARE_PICKUP(Gcmd(ENABLE=0))

    assert rig.purge_pickup_armed is False


def test_an_armed_swap_takes_the_harder_retract_and_returns_as_usual(rig):
    rig.purge_pickup_armed = True

    log = change(rig)

    assert log == HOP + ["release T0", "grab T2 in_dock=True"]         + retract(0.9, dwell=250) + [
        "SAVE_GCODE_STATE NAME=_ff_restore_axis", "G90",
        "G1 Z5.200 F1200", "G1 X100.000 Y50.000 F9000", "G1 Z3.200 F1200",
        "M83", "G1 E0.400 F200",
        "RESTORE_GCODE_STATE NAME=_ff_restore_axis"]


def test_the_dwell_is_skipped_when_it_is_not_configured(rig):
    rig.purge_pickup_armed = True
    rig.mounted = -1
    rig.purge_retract_dwell_ms = 0

    log = change(rig)

    assert "G1 E-0.900 F1800" in log
    assert not any(command.startswith("G4") for command in log)


# ---------------------------------------------------------------------------
# The registered prime tower
# ---------------------------------------------------------------------------

def test_a_change_over_the_model_is_not_returned_to_and_gets_the_full_retract(rig):
    register_tower(rig)

    log = change(rig)                     # POS is far from the tower

    assert log[0].startswith("ff_toolchange: T2 issued outside the registered"
                             " prime tower")
    assert log[1:5] == HOP and log[5] == "release T0"
    assert log[6:] == ["grab T2 in_dock=True"] + retract(0.9, dwell=250)
    assert not any(command.startswith("G1 X100") for command in log)


def test_a_change_on_the_tower_returns_to_the_tower_and_gives_back_the_retract(rig):
    register_tower(rig)
    rig.position = list(ON_THE_TOWER)

    log = change(rig)

    assert log == HOP + ["release T0", "grab T2 in_dock=True"] \
        + retract(0.9, dwell=250) + [
        "SAVE_GCODE_STATE NAME=_ff_restore_axis", "G90",
        "G1 Z5.200 F1200", "G1 X20.000 Y200.000 F9000", "G1 Z3.200 F1200",
        "M83", "G1 E0.400 F200",                 # never more than restore_unretract
        "RESTORE_GCODE_STATE NAME=_ff_restore_axis"]


def test_a_tool_the_slicer_already_unloaded_is_not_retracted_a_second_time(rig):
    register_tower(rig)
    change(rig, tool=2)                              # first use of T2
    rig.mounted = 2
    change(rig, tool=1)                              # first use of T1
    rig.mounted = 1
    del rig.log[:]

    log = change(rig, tool=2)                        # T2 comes back

    assert "G1 E-0.900 F1800" not in log
    assert not any(command.startswith("G1 E") for command in log)
    assert "grab T2 in_dock=True" in log             # still lifted and lowered


def test_the_job_start_command_forgets_which_tools_were_used(rig):
    register_tower(rig)
    change(rig, tool=2)
    assert rig.job_tool_mask == 1 << 2

    rig.cmd_TOOLCHANGE_BEGIN_JOB(Gcmd())

    assert rig.job_tool_mask == 0


def test_a_failed_change_does_not_count_as_a_used_tool(rig, monkeypatch):
    def broken_grab(tool, retract_in_dock=False):
        raise rig.module.FFToolchangeError("grab failed")

    monkeypatch.setattr(rig, "_grab", broken_grab)

    with pytest.raises(CommandError):
        change(rig)

    assert rig.job_tool_mask == 0
    assert rig._dock_retract is None            # the plan does not leak


def test_the_tower_can_be_cleared_and_must_be_given_whole(rig):
    register_tower(rig)
    assert rig.prime_tower_geometry is not None

    rig.cmd_TOOLCHANGE_SET_PRIME_TOWER(Gcmd(CLEAR=1))
    assert rig.prime_tower_geometry is None

    with pytest.raises(CommandError, match="CENTER_X and CENTER_Y"):
        rig.cmd_TOOLCHANGE_SET_PRIME_TOWER(Gcmd(CENTER_X=1, WIDTH=20))


def test_the_tower_can_be_given_by_its_corner(rig):
    rig.cmd_TOOLCHANGE_SET_PRIME_TOWER(Gcmd(X=10, Y=20, WIDTH=20, DEPTH=10))

    cx, cy, hx, hy, cos_a, sin_a, rotation = rig.prime_tower_geometry
    assert (cx, cy) == (20., 25.)
    assert (hx, hy) == (11., 6.)                  # half size + the 1 mm safety
    assert (cos_a, sin_a, rotation) == (1., 0., 0.)


def test_the_tower_test_follows_its_rotation(rig):
    register_tower(rig)
    just_above = [20., 213.]                     # 13 mm off the centre in Y

    assert not rig._position_is_in_prime_tower(just_above)   # half depth is 10

    register_tower(rig, ROT=90)
    assert rig.prime_tower_geometry[4] == pytest.approx(math.cos(math.pi / 2))
    assert rig._position_is_in_prime_tower(just_above)       # now it is long


# ---------------------------------------------------------------------------
# No prime tower
# ---------------------------------------------------------------------------

def test_without_a_tower_the_macro_primes_and_the_model_is_not_returned_to(rig):
    rig.no_tower_prime_macro = "_NS_MARK_TOOL_PRIME"

    log = change(rig)

    assert log[0].startswith("ff_toolchange: T2 selected without a prime tower")
    assert log[1:5] == HOP and log[5] == "release T0"
    assert log[6:] == (["grab T2 in_dock=True"] + retract(0.4)
                       + ["_NS_MARK_TOOL_PRIME TOOL=2"])


def test_without_a_macro_a_change_without_a_tower_is_the_plain_one(rig):
    log = change(rig)

    assert "G1 X100.000 Y50.000 F9000" in log
    assert not any("without a prime tower" in str(line) for line in log)


def test_the_macro_is_not_run_for_the_tool_that_is_already_mounted(rig):
    rig.no_tower_prime_macro = "_NS_MARK_TOOL_PRIME"
    rig.mounted = 2

    log = change(rig, tool=2)

    assert "_NS_MARK_TOOL_PRIME TOOL=2" not in log
    assert "G1 X100.000 Y50.000 F9000" not in log


# ---------------------------------------------------------------------------
# A change that restores nothing (the pickups of the cleaning macros)
# ---------------------------------------------------------------------------

def test_a_change_that_restores_nothing_is_the_originals_by_default(rig):
    rig.restore_axis = ""

    assert change(rig) == ["release T0", "grab T2 in_dock=False"]


def test_with_the_option_it_is_raised_and_retracted_and_left_so(rig):
    rig.restore_axis = ""
    rig.protect_every_change = True

    log = change(rig)

    assert log == HOP + ["release T0", "grab T2 in_dock=True"] + retract(0.4)


def test_with_the_option_the_first_pickup_has_nothing_to_raise(rig):
    rig.restore_axis = ""
    rig.protect_every_change = True
    rig.mounted = -1

    assert change(rig) == ["grab T2 in_dock=True"] + retract(0.4)


def test_the_option_does_nothing_when_no_hop_or_retract_is_configured(rig):
    rig.restore_axis = ""
    rig.protect_every_change = True
    rig.restore_z_hop = rig.restore_retract = rig.purge_retract = 0.
    rig.restore_unretract = 0.

    assert change(rig) == ["release T0", "grab T2 in_dock=False"]


def test_with_the_option_a_z_only_restore_still_comes_back_down(rig):
    rig.restore_axis = "Z"
    rig.protect_every_change = True

    log = change(rig)

    assert log == HOP + ["release T0", "grab T2 in_dock=True"] \
        + retract(0.4) + [
        "SAVE_GCODE_STATE NAME=_ff_restore_axis", "G90",
        "G1 Z3.200 F9000", "RESTORE_GCODE_STATE NAME=_ff_restore_axis"]


def test_the_option_is_read_from_the_config():
    tc, _ = build(protect_every_change=1)
    plain, _ = build()

    assert tc.protect_every_change is True
    assert plain.protect_every_change is False


# ---------------------------------------------------------------------------
# The return to a point beyond the safe column (the purge chute, the wipe pad)
# ---------------------------------------------------------------------------
#
# The docks stand right of x_safe, one tool after the other along Y. After a
# pickup the carriage is on the safe column; one straight move from there to
# the chute cuts across the docked tools with the new tool on the carriage
# (T1 into T2, on a Creator 5). With the option the return goes along the safe
# column first, then out.

AT_THE_CHUTE = [274., 254., 3.2, 0.]


def xy_moves(log):
    return [command for command in log
            if command.startswith(("G1 X", "G1 Y"))]


def corridor(rig, position=AT_THE_CHUTE):
    rig.restore_via_corridor = True
    rig.x_safe = 250.
    rig.position = list(position)
    return rig


def test_a_return_to_the_chute_is_one_straight_move_unless_asked_otherwise(rig):
    rig.position = list(AT_THE_CHUTE)

    assert xy_moves(change(rig)) == ["G1 X274.000 Y254.000 F9000"]


def test_with_the_corridor_the_return_goes_along_the_safe_column_first(rig):
    log = change(corridor(rig))

    assert log == HOP + ["release T0", "grab T2 in_dock=True"] \
        + retract(0.4) + [
        "SAVE_GCODE_STATE NAME=_ff_restore_axis", "G90",
        "G1 Z5.200 F1200",
        "G1 Y254.000 F9000", "G1 X274.000 F9000",
        "G1 Z3.200 F1200", "M83", "G1 E0.400 F200",
        "RESTORE_GCODE_STATE NAME=_ff_restore_axis"]


def test_a_return_that_is_not_hopped_goes_along_the_column_too(rig):
    rig.restore_z_hop = rig.restore_retract = rig.purge_retract = 0.
    rig.restore_unretract = 0.

    log = change(corridor(rig))

    assert log == ["release T0", "grab T2 in_dock=False",
                   "SAVE_GCODE_STATE NAME=_ff_restore_axis", "G90",
                   "G1 Y254.000 F9000", "G1 X274.000 F9000",
                   "RESTORE_GCODE_STATE NAME=_ff_restore_axis"]


def test_a_return_onto_the_bed_is_still_one_move(rig):
    log = change(corridor(rig, position=[100., 50., 3.2, 0.]))

    assert xy_moves(log) == ["G1 X100.000 Y50.000 F9000"]


@pytest.mark.parametrize("x, via_the_column", [
    (247.9, False), (248.0, True), (250.0, True), (274.0, True)])
def test_the_corridor_starts_two_millimetres_before_the_safe_column(
        rig, x, via_the_column):
    log = change(corridor(rig, position=[x, 254., 3.2, 0.]))

    moves = xy_moves(log)
    if via_the_column:
        assert moves == ["G1 Y254.000 F9000", "G1 X%.3f F9000" % x]
    else:
        assert moves == ["G1 X%.3f Y254.000 F9000" % x]


def test_a_return_of_one_axis_only_is_not_split(rig):
    rig.restore_axis = "x"

    assert xy_moves(change(corridor(rig))) == ["G1 X274.000 F9000"]


def test_parking_with_the_corridor_goes_along_the_column_too(rig):
    corridor(rig)
    rig.printer.lookup_object = lambda name, default=None: None   # no mesh
    gcmd = Gcmd()
    gcmd.get = lambda name, default=None: default

    rig.cmd_TOOLCHANGE_PARK(gcmd)

    assert rig.log == [
        "release T0",
        "SAVE_GCODE_STATE NAME=_ff_restore_axis", "G90",
        "G1 Y254.000 F9000", "G1 X274.000 F9000",
        "RESTORE_GCODE_STATE NAME=_ff_restore_axis"]


def test_the_corridor_option_is_read_from_the_config():
    tc, _ = build(restore_via_corridor=1)
    plain, _ = build()

    assert tc.restore_via_corridor is True
    assert plain.restore_via_corridor is False


# ---------------------------------------------------------------------------
# The wait that ends every dock step
# ---------------------------------------------------------------------------

def waiting_toolchange(**options):
    """A bare FFToolchange whose real _wait_moves talks to a recording
    toolhead; returns it, the list of calls and the toolhead."""
    module = load_module()
    tc = module.FFToolchange.__new__(module.FFToolchange)
    calls = []
    toolhead = SimpleNamespace(
        flush_step_generation=lambda: calls.append("flush_step_generation"),
        wait_moves=lambda: calls.append("wait_moves"))
    tc.printer = SimpleNamespace(
        lookup_object=lambda name, default=None: toolhead)
    for name, value in options.items():
        setattr(tc, name, value)
    return tc, calls, toolhead


def test_a_wait_sends_the_last_steps_before_it_starts_waiting():
    """Klipper keeps the steps that start within ~50 ms of a move's end back
    until a later flush, and while the queue drains that flush is a timer 0.2 s
    ahead of them. A stall of the host then in the middle of the move shuts
    the MCU down (Timer too close, T3's dock, 2026-10-14). Flushing first puts
    all of the motion on the MCU; the wait itself is unchanged."""
    tc, calls, _ = waiting_toolchange(flush_steps_before_wait=True)

    tc._wait_moves()

    assert calls == ["flush_step_generation", "wait_moves"]


def test_without_the_option_a_wait_is_klippers_own():
    tc, calls, _ = waiting_toolchange()

    tc._wait_moves()

    assert calls == ["wait_moves"]


def test_a_toolhead_without_the_flush_is_waited_on_all_the_same():
    """Any non-CommandError from a gcode handler shuts the printer down, so a
    toolhead that lacks the call must not turn a wait into one."""
    tc, calls, toolhead = waiting_toolchange(flush_steps_before_wait=True)
    del toolhead.flush_step_generation

    tc._wait_moves()

    assert calls == ["wait_moves"]


def test_the_flush_option_is_read_from_the_config():
    tc, _ = build(flush_steps_before_wait=1)
    plain, _ = build()

    assert tc.flush_steps_before_wait is True
    assert plain.flush_steps_before_wait is False


# ---------------------------------------------------------------------------
# What stays as it was
# ---------------------------------------------------------------------------

def test_with_nothing_registered_the_change_is_upstreams(rig):
    rig.restore_z_hop = rig.restore_retract = rig.purge_retract = 0.
    rig.restore_unretract = 0.

    assert change(rig) == [
        "release T0", "grab T2 in_dock=False",
        "SAVE_GCODE_STATE NAME=_ff_restore_axis", "G90",
        "G1 X100.000 Y50.000 F9000",
        "RESTORE_GCODE_STATE NAME=_ff_restore_axis"]


def test_the_hint_still_pulls_filament_back_when_only_it_is_set(rig):
    rig.restore_z_hop = rig.restore_retract = rig.restore_unretract = 0.
    rig.mounted = -1
    rig.purge_pickup_armed = True

    log = change(rig)

    assert "grab T2 in_dock=True" in log
    assert "G1 E-0.900 F1800" in log


def test_a_bare_toolchange_object_sets_the_print_offset_as_the_original_does():
    module = load_module()
    bare = module.FFToolchange.__new__(module.FFToolchange)
    bare.temp_offset = 0.00045
    bare.tools = [SimpleNamespace(
        calibrated=lambda: True, nozzle=(0., 0., 3.2), z_adjust=0.)
        for _ in range(4)]
    bare._station_z = lambda: 1.0
    bare._reset_gcode_position = lambda: None
    bare._current_tool_or_none = lambda: (0, "sensors")

    bare.cmd_TOOLCHANGE_SET_PRINT_OFFSET(Gcmd(NOZZLE=220, BED=60, LAYER=0.2))

    assert bare.job_z == pytest.approx(0.045)
    bare.cmd_TOOLCHANGE_SET_PRINT_OFFSET(Gcmd(CLEAR=1))
    assert bare.job_z == 0.


def test_a_bare_toolchange_object_has_no_n4s4_state():
    module = load_module()
    bare = module.FFToolchange.__new__(module.FFToolchange)

    assert bare.prime_tower_geometry is None
    assert bare.purge_pickup_armed is False
    assert bare.job_tool_mask == 0
    assert bare.shared_stepper is False
    assert bare.protect_every_change is False
    assert bare.restore_via_corridor is False
    assert bare.flush_steps_before_wait is False
    assert bare.no_tower_prime_macro == ""


# ---------------------------------------------------------------------------
# The shared stepper
# ---------------------------------------------------------------------------

def test_a_shared_stepper_follows_a_tool_that_is_selected_again(rig):
    rig.shared_stepper = True
    rig.mounted = 2
    rig.restore_axis = ""

    log = change(rig, tool=2)

    assert log == [
        "ACTIVATE_EXTRUDER EXTRUDER=extruder2",
        "ff_toolchange: syncing shared extruder stepper -> extruder2",
        "SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=extruder2"]


def test_without_a_shared_stepper_nothing_is_synced(rig):
    rig.mounted = 2
    rig.restore_axis = ""

    assert change(rig, tool=2) == ["ACTIVATE_EXTRUDER EXTRUDER=extruder2"]


@pytest.fixture
def real_grab(rig, monkeypatch):
    """The real _grab, with the sensors and the dock motion reduced to a log."""
    module = rig.module
    monkeypatch.undo()                               # the stubs of `rig`
    rig._grab = module.FFToolchange._grab.__get__(rig)
    rig._run = lambda script: rig.log.append(script)
    rig._wait_moves = lambda: None
    rig._poll_until = lambda check, timeout: True
    rig._in_location = lambda tool, eventtime=None: True
    rig._set_tool_frame = lambda tool: None
    rig._arm_runout = lambda tool: None
    rig._dock = lambda tool: (10. + tool, 100. + tool)
    rig._sleep = lambda seconds: None

    @contextlib.contextmanager
    def snapshot():
        yield

    rig._snapshot_motion_state = snapshot
    rig.accel_move = 8000
    rig.x_safe, rig.x_approach, rig.grab_pullback = 250., 280., 20.
    rig.fast_feed, rig.slow_feed, rig.grab_retreat_feed = 30000, 5400, 4800
    rig.grab_macro, rig.grab2_macro = "MOTOR_GRAB", "MOTOR_GRAB2"
    rig._grab_sensor = lambda eventtime=None: True
    return rig


def test_the_shared_stepper_is_connected_before_the_dock_retract(real_grab):
    tc = real_grab
    tc.shared_stepper = True
    tc._dock_retract = (0.9, True)

    returned = tc._grab(2, retract_in_dock=True)

    sync = "SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=extruder2"
    log = tc.log
    assert returned == 0.9
    assert log.count(sync) == 2                       # once for the retract,
    first_sync = log.index(sync)                      # once the tool is verified
    assert log.index("MOTOR_GRAB") < log.index(
        "ACTIVATE_EXTRUDER EXTRUDER=extruder2") < first_sync
    assert first_sync < log.index("G1 E-0.900 F1800") < log.index("MOTOR_GRAB2")
    assert log.index("G4 P250") < log.index("MOTOR_GRAB2")
    assert log.index("MOTOR_GRAB2") < len(log) - 1 - log[::-1].index(sync)


def test_a_grab_without_in_dock_retract_still_syncs_once_it_is_verified(real_grab):
    tc = real_grab
    tc.shared_stepper = True

    assert tc._grab(1) == 0.

    sync = "SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=extruder1"
    assert tc.log.count(sync) == 1
    assert not any(command.startswith("G1 E") for command in tc.log)


# ---------------------------------------------------------------------------
# The print Z offset: base, build plate, material
# ---------------------------------------------------------------------------

@pytest.fixture
def offsets():
    module = load_module()
    tc = module.FFToolchange.__new__(module.FFToolchange)
    tc.log = []
    tc.print_base_z = tc.plate_z = tc.material_z = tc.job_z = 0.
    tc.temp_offset = 0.00045
    tc.restore_z_feed = 1200
    tc.mounted = 0
    tc.homed = "xyz"
    tc.tools = [SimpleNamespace(
        calibrated=lambda: True, nozzle=(0., 0., 3.2), z_adjust=0.)
        for _ in range(4)]
    tc.reactor = SimpleNamespace(monotonic=lambda: 0.)
    tc.printer = SimpleNamespace(lookup_object=lambda name, default=None: (
        SimpleNamespace(get_status=lambda *a: {
            "gcode_position": [10., 20., 0.2, 0.]})
        if name == "gcode_move" else SimpleNamespace(
            get_status=lambda eventtime: {"homed_axes": tc.homed})))
    tc._station_z = lambda: 1.0
    tc._reset_gcode_position = lambda: tc.log.append("reset position")
    tc._current_tool_or_none = lambda: (tc.mounted, "sensors")
    tc._run = tc.log.append
    return tc


def test_the_three_z_components_add_up_and_none_accumulates(offsets):
    tc = offsets

    tc.cmd_TOOLCHANGE_SET_PRINT_OFFSET(
        Gcmd(NOZZLE=220, BED=60, LAYER=0.2, PLATE=0.03))
    assert tc.print_base_z == pytest.approx(0.045)
    assert tc.job_z == pytest.approx(0.075)

    tc.cmd_TOOLCHANGE_SET_MATERIAL_OFFSET(Gcmd(VALUE=-0.02))
    assert tc.job_z == pytest.approx(0.055)

    # the same values again: replaced, not added
    tc.cmd_TOOLCHANGE_SET_PRINT_OFFSET(
        Gcmd(NOZZLE=220, BED=60, LAYER=0.2, PLATE=0.03))
    tc.cmd_TOOLCHANGE_SET_MATERIAL_OFFSET(Gcmd(VALUE=-0.02))
    assert tc.job_z == pytest.approx(0.055)


def test_a_thin_first_layer_and_a_hot_bed_keep_their_app_terms(offsets):
    offsets.cmd_TOOLCHANGE_SET_PRINT_OFFSET(
        Gcmd(NOZZLE=120, BED=100, LAYER=0.08))

    assert offsets.print_base_z == pytest.approx(0.08 - 0.06)


def test_clearing_resets_every_component(offsets):
    tc = offsets
    tc.cmd_TOOLCHANGE_SET_PRINT_OFFSET(Gcmd(NOZZLE=220, PLATE=0.03))
    tc.cmd_TOOLCHANGE_SET_MATERIAL_OFFSET(Gcmd(VALUE=0.05))

    tc.cmd_TOOLCHANGE_SET_PRINT_OFFSET(Gcmd(CLEAR=1))

    assert (tc.print_base_z, tc.plate_z, tc.material_z, tc.job_z) == (0, 0, 0, 0)


def test_the_material_term_moves_z_on_its_own_when_it_can(offsets):
    tc = offsets

    tc.cmd_TOOLCHANGE_SET_MATERIAL_OFFSET(Gcmd(VALUE=0.05))

    assert tc.log == [
        "reset position", "SAVE_GCODE_STATE NAME=_ff_material_z", "G90",
        "G1 Z0.200 F1200", "RESTORE_GCODE_STATE NAME=_ff_material_z"]


@pytest.mark.parametrize("why", ["MOVE=0", "not homed", "no tool"])
def test_the_material_term_leaves_z_alone_when_it_cannot_or_is_told_to(
        offsets, why):
    tc = offsets
    params = {"VALUE": 0.05}
    if why == "MOVE=0":
        params["MOVE"] = 0
    elif why == "not homed":
        tc.homed = "xy"
    else:
        tc.mounted = -1

    tc.cmd_TOOLCHANGE_SET_MATERIAL_OFFSET(Gcmd(**params))

    assert tc.material_z == 0.05
    assert not any(command.startswith("G1") for command in tc.log)


def test_the_same_material_value_changes_nothing(offsets):
    tc = offsets
    tc.cmd_TOOLCHANGE_SET_MATERIAL_OFFSET(Gcmd(VALUE=0.05))
    del tc.log[:]

    tc.cmd_TOOLCHANGE_SET_MATERIAL_OFFSET(Gcmd(VALUE=0.05))

    assert tc.log == []


@pytest.mark.parametrize("command, params", [
    ("cmd_TOOLCHANGE_SET_MATERIAL_OFFSET", {"VALUE": 0.6}),
    ("cmd_TOOLCHANGE_SET_PRINT_OFFSET", {"NOZZLE": 200, "PLATE": -0.6}),
])
def test_the_corrections_are_limited_to_half_a_millimetre(
        offsets, command, params):
    with pytest.raises(CommandError):
        getattr(offsets, command)(Gcmd(**params))


# ---------------------------------------------------------------------------
# Construction: option defaults and the command names
# ---------------------------------------------------------------------------

class Section:
    def __init__(self, **options):
        self.options = options

    def has_section(self, name):
        return name in self.sections

    def get_prefix_sections(self, prefix):
        return [SimpleNamespace(get_name=lambda name=name: name)
                for name in self.sections if name.startswith(prefix)]

    sections = ()

    def get_printer(self):
        return self.printer

    def get_name(self):
        return "ff_toolchange"

    def error(self, message):
        return CommandError(message)

    def _value(self, name, default, kind=str):
        return kind(self.options[name]) if name in self.options else default

    def get(self, name, default=REQUIRED):
        return self._value(name, default)

    def getfloat(self, name, default=REQUIRED, minval=None, maxval=None,
                 above=None):
        value = self._value(name, default, float)
        if maxval is not None and value > maxval:
            raise CommandError("%s above its maximum" % name)
        return value

    def getint(self, name, default=REQUIRED, minval=None, maxval=None):
        return self._value(name, default, int)

    def getboolean(self, name, default=REQUIRED):
        return bool(int(self.options[name])) if name in self.options \
            else default


def build(sections=(), **options):
    module = load_module()
    commands = []
    gcode = SimpleNamespace(
        register_command=lambda name, handler, desc=None: commands.append(name))
    objects = {"gcode": gcode}

    class Printer:
        def get_reactor(self):
            return None

        def lookup_object(self, name, default=None):
            return objects.get(name, default)

        def load_object(self, config, name):
            return SimpleNamespace(
                calibrated=lambda: False, nozzle=None, z_adjust=0.,
                index=int(name.split()[-1]), extruder_name="extruder",
                has_dock=lambda: False)

        def add_object(self, name, obj):
            objects[name] = obj

        def register_event_handler(self, event, handler):
            pass

    config = Section(**options)
    config.printer = Printer()
    config.sections = tuple(sections)
    return module.FFToolchange(config), commands


def test_the_n4s4_options_default_to_nothing_extra():
    tc, _ = build(restore_retract=0.4)

    assert tc.purge_retract == 0.4                  # follows restore_retract
    assert tc.purge_retract_dwell_ms == 0
    assert tc.tower_repeat_retract == 0.
    assert tc.no_tower_prime_macro == ""
    assert tc.shared_stepper is False
    assert tc.print_base_z == tc.plate_z == tc.material_z == tc.job_z == 0.


def test_the_options_are_read():
    tc, _ = build(purge_retract=0.9, purge_retract_dwell_ms=250,
                  tower_repeat_retract=0.1,
                  no_tower_prime_macro="  _NS_MARK_TOOL_PRIME ")

    assert (tc.purge_retract, tc.purge_retract_dwell_ms,
            tc.tower_repeat_retract) == (0.9, 250, 0.1)
    assert tc.no_tower_prime_macro == "_NS_MARK_TOOL_PRIME"


def test_ff_extruder_in_the_config_turns_the_stepper_sync_on():
    tc, _ = build(sections=["ff_extruder"])

    assert tc.shared_stepper is True


def test_the_status_command_keeps_its_plain_name():
    _, commands = build()

    assert "TOOLCHANGE_STATUS" in commands
    assert "FF_TOOLCHANGE_STATUS" in commands


def test_a_status_macro_in_the_config_takes_the_plain_name():
    _, commands = build(sections=["gcode_macro TOOLCHANGE_STATUS"])

    assert "TOOLCHANGE_STATUS" not in commands
    assert "FF_TOOLCHANGE_STATUS" in commands


def test_a_status_macro_is_found_whatever_its_case():
    _, commands = build(sections=["gcode_macro toolchange_status"])

    assert "TOOLCHANGE_STATUS" not in commands    # klippy would refuse both
    assert "FF_TOOLCHANGE_STATUS" in commands


def test_another_macro_does_not_take_the_status_name():
    _, commands = build(sections=["gcode_macro TOOLCHANGE_PARK_ALL",
                                  "ff_stats"])

    assert "TOOLCHANGE_STATUS" in commands


def test_the_new_commands_are_registered():
    _, commands = build()

    for name in ("TOOLCHANGE_SET_PRIME_TOWER", "TOOLCHANGE_PREPARE_PICKUP",
                 "TOOLCHANGE_BEGIN_JOB", "TOOLCHANGE_SET_MATERIAL_OFFSET"):
        assert name in commands

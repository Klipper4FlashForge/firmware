"""Mesh probing lifts relative to the last trigger, not to one fixed height."""

import importlib.util
from types import SimpleNamespace

import pytest

from lib.paths import ROOT


pytestmark = pytest.mark.static
MODULE = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" /
          "klippy" / "extras" / "ff_bed_mesh.py")


@pytest.fixture(scope="module")
def ff_bed_mesh():
    spec = importlib.util.spec_from_file_location("test_ff_bed_mesh", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Helper:
    speed = 300.
    lift_speed = 20.

    def __init__(self):
        self.moves = []

    def _move(self, coord, speed):
        self.moves.append((coord, speed))


class Toolhead:
    def __init__(self):
        self.z = 0.

    def get_position(self):
        return [0., 0., self.z, 0.]


class Gcode:
    def __init__(self):
        self.commands = {}

    def register_command(self, name, func, desc=None):
        self.commands[name] = func


class Printer:
    def __init__(self, helper, with_mesh=True):
        self.toolhead = Toolhead()
        self.gcode = Gcode()
        mesh = SimpleNamespace(bmc=SimpleNamespace(
            probe_mgr=SimpleNamespace(probe_helper=helper)))
        self.objects = {"toolhead": self.toolhead, "gcode": self.gcode}
        if with_mesh:
            self.objects["bed_mesh"] = mesh

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)


class Config:
    error = RuntimeError

    def __init__(self, printer, **values):
        self.printer = printer
        self.values = values

    def get_printer(self):
        return self.printer

    def getfloat(self, option, default, minval=None, above=None):
        return self.values.get(option, default)


def install(module, **values):
    helper = Helper()
    printer = Printer(helper)
    adapter = module.FFBedMesh(Config(printer, **values))
    return adapter, helper, printer


def test_the_first_move_goes_to_the_safe_height_at_full_speed(ff_bed_mesh):
    _, helper, printer = install(ff_bed_mesh)
    printer.toolhead.z = 0.5

    helper._raise_tool(True)

    assert helper.moves == [([None, None, 5.0], helper.speed)]


def test_the_first_move_never_lowers_a_head_above_the_safe_height(ff_bed_mesh):
    _, helper, printer = install(ff_bed_mesh)
    printer.toolhead.z = 8.

    helper._raise_tool(True)

    assert helper.moves[0][0] == [None, None, 8.0]


def test_later_moves_lift_the_clearance_above_the_last_trigger(ff_bed_mesh):
    _, helper, printer = install(ff_bed_mesh)
    printer.toolhead.z = 0.292

    helper._raise_tool(False)

    coord, speed = helper.moves[0]
    assert coord[2] == pytest.approx(2.292)
    assert speed == helper.lift_speed


def test_a_low_target_uses_the_recovery_height_without_latching(ff_bed_mesh):
    _, helper, printer = install(ff_bed_mesh)
    printer.toolhead.z = -1.5

    helper._raise_tool(False)
    printer.toolhead.z = 0.3
    helper._raise_tool(False)

    assert helper.moves[0][0] == [None, None, 3.0]
    assert helper.moves[1][0][2] == pytest.approx(2.3)


def test_only_the_mesh_helper_instance_is_patched(ff_bed_mesh):
    adapter, helper, printer = install(ff_bed_mesh)
    other = Helper()

    assert "_raise_tool" in vars(helper)
    assert "_raise_tool" not in vars(other)
    assert "FF_BED_MESH_STATUS" in printer.gcode.commands
    assert adapter.get_status(0)["dynamic_z"] is True


def test_heights_that_could_not_clear_the_threshold_are_refused(ff_bed_mesh):
    printer = Printer(Helper())

    with pytest.raises(RuntimeError, match="first_move_z"):
        ff_bed_mesh.FFBedMesh(Config(printer, first_move_z=1.0))
    with pytest.raises(RuntimeError, match="recovery_move_z"):
        ff_bed_mesh.FFBedMesh(Config(printer, recovery_move_z=1.0))


def test_it_needs_bed_mesh(ff_bed_mesh):
    printer = Printer(Helper(), with_mesh=False)

    with pytest.raises(RuntimeError, match="after \\[bed_mesh\\]"):
        ff_bed_mesh.FFBedMesh(Config(printer))

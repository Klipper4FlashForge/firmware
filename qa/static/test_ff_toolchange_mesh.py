"""An active mesh must not turn an unhomed-Z dock move into Z travel."""

import configparser
import importlib.util

import pytest

from lib.paths import ROOT


pytestmark = pytest.mark.static
MODULE = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" /
          "klippy" / "extras" / "ff_toolchange.py")
PRINT_CONFIG = (ROOT / "pkgs" / "klipper-config" / "payload" / "config" /
                "ff-print-macros.cfg")
MESH_CLEAR_MESSAGE = ("TOOLCHANGE_PARK: cleared active bed mesh"
                      " because Z is unhomed")


@pytest.fixture(scope="module")
def ff_toolchange():
    spec = importlib.util.spec_from_file_location("test_ff_toolchange_mesh", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Mesh:
    # Deliberately non-flat: crossing the bed changes physical Z.
    def calc_z(self, x, y):
        return 0.292361 + 0.001 * x + 0.002 * y


class BedMesh:
    def __init__(self, active):
        self.mesh = Mesh() if active else None

    def get_mesh(self):
        return self.mesh


class Toolhead:
    def __init__(self, homed_axes):
        self.homed_axes = homed_axes

    def get_status(self, eventtime):
        return {"homed_axes": self.homed_axes}


class Printer:
    def __init__(self, toolhead, bed_mesh):
        self.objects = {"toolhead": toolhead, "bed_mesh": bed_mesh}

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)


class Gcmd:
    def __init__(self):
        self.responses = []

    def error(self, message):
        return RuntimeError(message)

    def respond_info(self, message):
        self.responses.append(message)


class Park:
    def __init__(self, module, mounted=1, homed_axes="xy", mesh=True,
                 fail=False):
        self.module = module
        self.mounted = mounted
        self.fail = fail
        self.changing = False
        self.events = []
        self.bed_mesh = BedMesh(mesh)
        self.toolhead = Toolhead(homed_axes)
        self.printer = Printer(self.toolhead, self.bed_mesh)
        self.reactor = self

    def monotonic(self):
        return 0.

    def _restore_axis_arg(self, gcmd):
        return ""

    def _wait_moves(self):
        self.events.append("wait")

    def _derive_current_tool(self):
        return self.mounted, "sensor state"

    def _ensure_homed(self, axes):
        assert axes == "xy"
        assert "x" in self.toolhead.homed_axes
        assert "y" in self.toolhead.homed_axes
        self.events.append("ensure_xy")

    def _run(self, script):
        assert script == "BED_MESH_CLEAR"
        self.events.append("clear_mesh")
        self.bed_mesh.mesh = None

    def _release(self, tool):
        assert tool == self.mounted
        self.events.append("first_dock_xy_move")
        mesh = self.bed_mesh.get_mesh()
        if (mesh is not None and "z" not in self.toolhead.homed_axes
                and mesh.calc_z(0, 0) != mesh.calc_z(250, 0)):
            raise self.module.FFToolchangeError("Must home axis first")
        if self.fail:
            raise self.module.FFToolchangeError("parking failed")


def test_active_nonflat_mesh_cleared_before_first_dock_move(ff_toolchange):
    park = Park(ff_toolchange)
    gcmd = Gcmd()
    assert park.bed_mesh.get_mesh().calc_z(0, 0) != (
        park.bed_mesh.get_mesh().calc_z(250, 0))

    ff_toolchange.FFToolchange.cmd_TOOLCHANGE_PARK(park, gcmd)

    assert park.events == ["wait", "ensure_xy", "clear_mesh",
                           "first_dock_xy_move"]
    assert park.bed_mesh.get_mesh() is None
    assert gcmd.responses == [MESH_CLEAR_MESSAGE]


@pytest.mark.parametrize("homed_axes,mesh", [
    ("xy", False),
    ("xyz", True),
])
def test_park_preserves_mesh_when_no_guard_needed(
        ff_toolchange, homed_axes, mesh):
    park = Park(ff_toolchange, homed_axes=homed_axes, mesh=mesh)
    gcmd = Gcmd()

    ff_toolchange.FFToolchange.cmd_TOOLCHANGE_PARK(park, gcmd)

    assert park.events == ["wait", "ensure_xy", "first_dock_xy_move"]
    assert (park.bed_mesh.get_mesh() is not None) == mesh
    assert gcmd.responses == []


def test_no_mounted_tool_does_not_clear_mesh(ff_toolchange):
    park = Park(ff_toolchange, mounted=-1)
    gcmd = Gcmd()

    ff_toolchange.FFToolchange.cmd_TOOLCHANGE_PARK(park, gcmd)

    assert park.events == ["wait"]
    assert park.bed_mesh.get_mesh() is not None
    assert gcmd.responses == ["no tool mounted (sensor state)"]


def test_parking_failure_leaves_mesh_cleared(ff_toolchange):
    park = Park(ff_toolchange, fail=True)
    gcmd = Gcmd()

    with pytest.raises(RuntimeError, match="parking failed"):
        ff_toolchange.FFToolchange.cmd_TOOLCHANGE_PARK(park, gcmd)

    assert park.events == ["wait", "ensure_xy", "clear_mesh",
                           "first_dock_xy_move"]
    assert park.bed_mesh.get_mesh() is None
    assert gcmd.responses == [MESH_CLEAR_MESSAGE]


def test_print_start_loads_or_probes_mesh_after_homing():
    config = configparser.RawConfigParser(
        strict=False, inline_comment_prefixes=(";", "#"))
    config.read(PRINT_CONFIG)
    gcode = config.get("gcode_macro START_PRINT", "gcode")

    assert gcode.index("BED_MESH_CLEAR") < gcode.index("G28")
    assert gcode.index("G28") < gcode.index("BED_MESH_CALIBRATE")
    assert gcode.index("G28") < gcode.index("BED_MESH_PROFILE LOAD=default")
    assert gcode.index("G28") < gcode.index("BED_MESH_PROFILE LOAD=MESH_DATA")

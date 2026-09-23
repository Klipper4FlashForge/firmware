"""Creator 5 toolchanger status regression tests."""

import importlib.util
import types

import pytest

from lib.paths import ROOT


MODULE = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" /
          "klippy" / "extras" / "ff_toolchange.py")


@pytest.fixture(scope="module")
def ff_toolchange():
    spec = importlib.util.spec_from_file_location("test_ff_toolchange", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGcmd:
    responses = []

    @staticmethod
    def error(message):
        return RuntimeError(message)

    def respond_info(self, message):
        self.responses.append(message)


class FakeToolchanger:
    def __init__(self, module, fail=False):
        self.module = module
        self.fail = fail
        self.changing = False
        self.status_seen_during_release = None
        self.job_z = 0.0
        self.gcode_transform = types.SimpleNamespace(tool=None)

    @staticmethod
    def _restore_axis_arg(gcmd):
        return ""

    @staticmethod
    def _wait_moves():
        pass

    @staticmethod
    def _derive_current_tool():
        return 0, "T0 out of its dock and grabbed"

    def _ensure_homed(self, axes):
        assert axes == "xy"
        assert self.changing

    def _release(self, tool):
        assert tool == 0
        view = self.module._ToolchangerView(self)
        self.status_seen_during_release = view.get_status(0)["status"]
        if self.fail:
            raise self.module.FFToolchangeError("release failed")

    @staticmethod
    def get_status(eventtime):
        # Model the brief contradictory sensor state seen while seating.
        return {"current_tool": -1, "state_ok": False,
                "state_reason": "transient", "print_offset_ready": True}


def test_park_reports_changing_during_release(ff_toolchange):
    toolchanger = FakeToolchanger(ff_toolchange)
    ff_toolchange.FFToolchange.cmd_TOOLCHANGE_PARK(toolchanger, FakeGcmd())

    assert toolchanger.status_seen_during_release == "changing"
    assert toolchanger.changing is False


def test_park_clears_changing_after_failure(ff_toolchange):
    toolchanger = FakeToolchanger(ff_toolchange, fail=True)

    with pytest.raises(RuntimeError, match="release failed"):
        ff_toolchange.FFToolchange.cmd_TOOLCHANGE_PARK(
            toolchanger, FakeGcmd())

    assert toolchanger.status_seen_during_release == "changing"
    assert toolchanger.changing is False

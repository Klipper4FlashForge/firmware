"""Creator 5 toolchanger status regression tests."""

import importlib.util
import types

import pytest

from lib.paths import ROOT


MODULE = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" /
          "klippy" / "extras" / "ff_toolchange.py")
CONFIG = (ROOT / "pkgs" / "klipper-config" / "payload" / "config" /
          "printer_n4s4.cfg")


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


def test_status_command_is_exposed_as_a_mainsail_macro():
    module_source = MODULE.read_text(encoding="utf-8")
    config_source = CONFIG.read_text(encoding="utf-8")

    assert "'FF_TOOLCHANGE_STATUS', self.cmd_TOOLCHANGE_STATUS" in module_source
    assert "[gcode_macro TOOLCHANGE_STATUS]" in config_source
    assert "    FF_TOOLCHANGE_STATUS" in config_source


def test_chute_purges_use_the_front_right_lip_wipe():
    config_source = CONFIG.read_text(encoding="utf-8")

    assert "variable_lip_wipe_enabled: 1" in config_source
    assert "variable_lip_wipe_z: 0.0" in config_source
    assert "variable_lip_wipe_x_left: 263.0" in config_source
    assert "variable_lip_wipe_x_right: 271.0" in config_source
    assert "variable_lip_wipe_feed: 12000" in config_source
    assert "[gcode_macro _NS_CHUTE_LIP_WIPE]" in config_source
    assert "{% set wipe_z = ff.lip_wipe_z|float %}" in config_source
    assert "G1 X{xr} F{ff.clean_wipe_feed}" in config_source
    assert "G1 X{xl} Y{y0 + 1.0} F{ff.lip_wipe_feed}" in config_source
    assert "G1 X{xl} Y{y0 + 7.0} F{ff.lip_wipe_feed}" in config_source
    assert config_source.count("_NS_CHUTE_LIP_WIPE TOOL=") == 2
    assert "variable_exit_x:" not in config_source
    assert "variable_exit_y:" not in config_source

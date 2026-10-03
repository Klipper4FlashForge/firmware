"""Parking preserves an enclosing toolchange's status ownership.

G28 can park a mounted tool inside an auto-homing T<n> operation. Clearing the
outer operation's flag then exposes normal pickup sensor overlap as an error.
These tests run the real park command, sensor derivation and status views;
hardware actions are substituted, so they do not validate release mechanics.
"""
import importlib.util
from types import SimpleNamespace

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.static

MODULE = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" / "klippy" /
          "extras" / "ff_toolchange.py")


class CommandError(Exception):
    pass


@pytest.fixture
def parking(monkeypatch):
    spec = importlib.util.spec_from_file_location("anvil_ff_toolchange", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tc = module.FFToolchange.__new__(module.FFToolchange)
    tc.changing = False
    # No bed mesh is loaded, so the park's unhomed-Z mesh guard stays idle.
    tc.printer = SimpleNamespace(lookup_object=lambda name, default=None: None)
    tc.restore_axis = ""
    tc.release_macro = "MOTOR_RELEASE"
    tc.dock_sensors = ["dock%d" % i for i in range(module.EXTRUDER_COUNT)]
    tc.grab_sensors = ["grab%d" % i for i in range(module.EXTRUDER_COUNT)]
    # T0 is mounted: its dock is empty and its grab sensor is active.
    sensors = {name: i != 0 for i, name in enumerate(tc.dock_sensors)}
    sensors.update({name: i == 0 for i, name in enumerate(tc.grab_sensors)})
    monkeypatch.setattr(tc, "_sensor", lambda name, eventtime=None: sensors[name])
    monkeypatch.setattr(tc, "_wait_moves", lambda: None)
    monkeypatch.setattr(tc, "_station_z", lambda: None)
    tc.tools = []
    tc.armed_tool = -1
    tc.job_z = 0.0
    tc.gcode_transform = SimpleNamespace(tool=0)
    view = module._ToolchangerView(tc)
    gcmd = SimpleNamespace(get=lambda name, default=None: default,
                           error=CommandError)
    return module, tc, view, sensors, gcmd


@pytest.mark.parametrize("incoming", [False, True], ids=["standalone", "nested"])
def test_park_reports_changing_and_preserves_callers_state(parking, monkeypatch,
                                                          incoming):
    _, tc, view, sensors, gcmd = parking
    tc.changing = incoming
    stages = []

    def ensure_homed(axes):
        assert axes == "xy"
        assert tc.changing is True
        assert view.get_status(0)["status"] == "changing"
        stages.append("home")

    def release(tool):
        assert tool == 0
        assert tc.changing is True
        # Tool seats in its dock before the grab sensor clears.
        sensors["dock0"] = True
        raw = tc.get_status(0)
        compat = view.get_status(0)
        assert raw["state_ok"] is False
        assert "sensor state is impossible" in raw["state_reason"]
        assert "changing" not in raw
        assert compat["status"] == "changing"
        assert compat["state_reason"] == raw["state_reason"]
        sensors["grab0"] = False
        assert tc.get_status(0)["state_ok"] is True
        assert tc.changing is True
        assert view.get_status(0)["status"] == "changing"
        stages.append("release")

    monkeypatch.setattr(tc, "_ensure_homed", ensure_homed)
    monkeypatch.setattr(tc, "_release", release)
    tc.cmd_TOOLCHANGE_PARK(gcmd)

    assert stages == ["home", "release"]
    assert tc.changing is incoming
    assert tc.get_status(0)["current_tool"] == -1
    assert view.get_status(0)["status"] == ("changing" if incoming else "ready")


@pytest.mark.parametrize("incoming", [False, True], ids=["standalone", "nested"])
@pytest.mark.parametrize("failure", ["sensor", "unexpected"])
def test_park_restores_callers_state_on_exception(parking, monkeypatch,
                                                 incoming, failure):
    module, tc, view, sensors, gcmd = parking
    tc.changing = incoming

    def ensure_homed(axes):
        assert tc.changing is True

    def release(tool):
        assert tc.changing is True
        sensors["dock0"] = True
        error = module.FFToolchangeError if failure == "sensor" else RuntimeError
        raise error("injected parking failure")

    monkeypatch.setattr(tc, "_ensure_homed", ensure_homed)
    monkeypatch.setattr(tc, "_release", release)
    expected = CommandError if failure == "sensor" else RuntimeError
    with pytest.raises(expected, match="injected parking failure"):
        tc.cmd_TOOLCHANGE_PARK(gcmd)

    assert tc.changing is incoming
    assert tc.get_status(0)["state_ok"] is False
    assert view.get_status(0)["status"] == ("changing" if incoming else "error")

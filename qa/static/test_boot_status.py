"""Normal boot tells the person at the panel what it is waiting for."""
import importlib.util
import sys
from types import SimpleNamespace

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.static

SCRIPT = (ROOT / "pkgs" / "anvil-core" / "payload" / "bin" /
          "ff-startup.py")


@pytest.fixture(scope="module")
def startup():
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec = importlib.util.spec_from_file_location("anvil_ff_startup", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


class Panel:
    def __init__(self):
        self.frames = []

    def say(self, status, progress, **kwargs):
        self.frames.append((status, progress, kwargs))


class Moonraker:
    def __init__(self, states):
        self.states = iter(states)

    def klippy_state(self):
        return next(self.states)


def test_wait_names_moonraker_then_klipper_and_advances_the_bar(startup,
                                                                 monkeypatch):
    panel = Panel()
    moonraker = Moonraker([None, "startup", "ready"])
    clock = iter([10.0, 10.0, 40.0, 40.0, 60.0])
    monkeypatch.setattr(startup.time, "time", lambda: next(clock))
    monkeypatch.setattr(startup.time, "sleep", lambda _seconds: None)

    assert startup.wait_for_stack(moonraker, panel, 100.0, 0.0)
    assert [frame[0] for frame in panel.frames] == [
        "STARTING MOONRAKER", "STARTING KLIPPER"]
    assert "PRINTER API" in panel.frames[0][2]["detail"]
    assert "PRINTER BOARDS" in panel.frames[1][2]["detail"]
    assert 0.35 < panel.frames[0][1] < panel.frames[1][1] < 0.75


def test_an_ordinary_boot_finishes_as_startup_not_setup(startup, monkeypatch):
    panel = Panel()
    monkeypatch.setattr(startup, "bring_up_printer",
                        lambda *_args, **_kwargs: True)
    args = SimpleNamespace(no_import=True)

    assert startup.startup(args, object(), panel, 0.0, 1.0) == 0
    assert panel.frames[-1][:2] == ("STARTUP COMPLETE", 1.0)
    assert panel.frames[-1][2]["note"] == ""
    assert startup.TITLE == "REFORGE IS STARTING"
    assert "SETUP" not in startup.RETRY

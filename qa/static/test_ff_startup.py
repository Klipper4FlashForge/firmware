"""ff-startup.py stamps the install only after a verified import.

The stamp is what stops the factory-calibration import from running again,
so every path short of "imported, saved, and still there after the restart"
has to leave no stamp and let the next boot retry. These run the migration
against a fake moonraker; nothing here needs a printer.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.paths import ROOT


pytestmark = pytest.mark.static
MODULE = ROOT / "pkgs" / "anvil-core" / "payload" / "bin" / "ff-startup.py"

CALIBRATED = [{"calibrated": True}, None, None, None]
UNCALIBRATED = [{"calibrated": False}, None, None, None]


@pytest.fixture(scope="module")
def ff_startup():
    spec = importlib.util.spec_from_file_location("test_ff_startup", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Moonraker:
    """Answers tools() from a script, one reading per call, and records the
    gcode it is asked to run."""

    def __init__(self, tools, pending=False, refuse=(), state_message=""):
        self.readings = list(tools)
        self.pending = pending
        self.refuse = set(refuse)
        self.state_message = state_message
        self.scripts = []

    def tools(self):
        return self.readings.pop(0)

    def save_pending(self):
        return self.pending

    def gcode(self, script, timeout=30.0):
        self.scripts.append(script)
        if script in self.refuse:
            return False, "refused"
        return True, "ok"

    def get(self, path, timeout=5.0):
        if path == "/printer/info":
            return {"state_message": self.state_message}
        return None


@pytest.fixture
def args(tmp_path):
    json_dir = tmp_path / "config"
    json_dir.mkdir()
    (json_dir / "extruder.json").write_text("{}")
    return SimpleNamespace(stamp=str(tmp_path / "anvil" / "stamp"),
                           dir=str(json_dir), timeout=1.0, dry_run=False,
                           no_import=False)


@pytest.fixture
def panel(ff_startup):
    return ff_startup.Panel(enabled=False)


@pytest.fixture
def ready(ff_startup, monkeypatch):
    monkeypatch.setattr(ff_startup, "wait_for_ready", lambda *a: True)


def stamped(args):
    return Path(args.stamp).exists()


def test_a_verified_import_is_stamped(ff_startup, args, panel, ready):
    moonraker = Moonraker([UNCALIBRATED, CALIBRATED, CALIBRATED])
    assert ff_startup.migrate(args, moonraker, panel) == 0
    assert moonraker.scripts == ["FF_IMPORT_FIRMWARE_CONFIG", "SAVE_CONFIG"]
    assert Path(args.stamp).read_text().startswith(
        "imported %s\n" % args.dir)


def test_a_save_that_does_not_answer_is_still_stamped(ff_startup, args, panel,
                                                      ready):
    # SAVE_CONFIG restarts klippy under the open request, so no answer is the
    # normal shape of success. The verdict is the reading after the restart.
    moonraker = Moonraker([UNCALIBRATED, CALIBRATED, CALIBRATED],
                          refuse={"SAVE_CONFIG"})
    assert ff_startup.migrate(args, moonraker, panel) == 0
    assert stamped(args)


def test_a_tool_already_calibrated_is_stamped_without_importing(
        ff_startup, args, panel):
    moonraker = Moonraker([CALIBRATED])
    assert ff_startup.migrate(args, moonraker, panel) == 0
    assert moonraker.scripts == []
    assert Path(args.stamp).read_text().startswith(
        "already calibrated; nothing imported\n")


@pytest.mark.parametrize("moonraker", [
    pytest.param(Moonraker([None]), id="tools-unreadable"),
    pytest.param(Moonraker([UNCALIBRATED], pending=None),
                 id="pending-unreadable"),
    pytest.param(Moonraker([UNCALIBRATED], pending=True),
                 id="another-save-pending"),
    pytest.param(Moonraker([UNCALIBRATED],
                           refuse={"FF_IMPORT_FIRMWARE_CONFIG"}),
                 id="import-refused"),
    pytest.param(Moonraker([UNCALIBRATED, UNCALIBRATED]),
                 id="import-landed-nothing"),
    pytest.param(Moonraker([UNCALIBRATED, CALIBRATED, UNCALIBRATED]),
                 id="save-did-not-take"),
])
def test_anything_short_of_a_verified_save_leaves_no_stamp(
        ff_startup, args, panel, ready, moonraker):
    assert ff_startup.migrate(args, moonraker, panel) == 1
    assert not stamped(args)


def test_klipper_not_coming_back_leaves_no_stamp(ff_startup, args, panel,
                                                 monkeypatch):
    monkeypatch.setattr(ff_startup, "wait_for_ready", lambda *a: False)
    moonraker = Moonraker([UNCALIBRATED, CALIBRATED])
    assert ff_startup.migrate(args, moonraker, panel) == 1
    assert not stamped(args)


def test_another_pending_save_is_never_committed(ff_startup, args, panel):
    # SAVE_CONFIG commits everything staged, so a save someone else staged is
    # not ours to make.
    moonraker = Moonraker([UNCALIBRATED], pending=True)
    ff_startup.migrate(args, moonraker, panel)
    assert moonraker.scripts == []


def test_a_dry_run_sends_nothing_and_stamps_nothing(ff_startup, args, panel):
    args.dry_run = True
    moonraker = Moonraker([UNCALIBRATED])
    assert ff_startup.migrate(args, moonraker, panel) == 0
    assert moonraker.scripts == []
    assert not stamped(args)


@pytest.fixture
def printer_up(ff_startup, monkeypatch):
    monkeypatch.setattr(ff_startup, "bring_up_printer", lambda *a: True)


@pytest.fixture
def no_migrate(ff_startup, monkeypatch):
    def migrate(*a):
        raise AssertionError("migrate must not run")
    monkeypatch.setattr(ff_startup, "migrate", migrate)


def test_a_stamped_install_does_not_migrate(ff_startup, args, panel,
                                            printer_up, no_migrate):
    ff_startup.stamp(args.stamp, "earlier boot")
    assert ff_startup.startup(args, None, panel, 0.0, 1.0) == 0


def test_no_factory_json_means_nothing_to_import(ff_startup, args, panel,
                                                 printer_up, no_migrate,
                                                 tmp_path):
    args.dir = str(tmp_path / "empty")
    assert ff_startup.startup(args, None, panel, 0.0, 1.0) == 0
    assert not stamped(args)


def test_a_printer_that_never_came_up_does_not_migrate(
        ff_startup, args, panel, no_migrate, monkeypatch):
    monkeypatch.setattr(ff_startup, "bring_up_printer", lambda *a: False)
    assert ff_startup.startup(args, None, panel, 0.0, 1.0) == 1
    assert not stamped(args)


def test_an_unwritable_stamp_is_reported_not_raised(ff_startup, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert ff_startup.stamp(str(blocker / "stamp"), "note") is False


@pytest.mark.parametrize("message, board", [
    ("MCU 'eheaterboard' error during connect", "THE HEATER BOARD"),
    ("MCU 'eboard' error during connect", "THE EXTRUDER BOARD"),
    ("MCU 'levelboard' error during connect", "THE LEVEL BOARD"),
    ("MCU 'mcu' error during connect", "THE MAIN BOARD"),
    ("Lost communication with MCU", None),
])
def test_klippy_fault_names_the_board(ff_startup, message, board):
    moonraker = Moonraker([], state_message=message)
    assert ff_startup.klippy_fault(moonraker) == board

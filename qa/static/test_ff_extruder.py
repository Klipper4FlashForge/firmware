"""Creator 5 shared-extruder-stepper adapter tests."""

import importlib.util
import sys
import types

import pytest

from lib.paths import ROOT


MODULE = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" /
          "klippy" / "extras" / "ff_extruder.py")


class FakePrinterExtruder:
    original_m109_calls = 0

    def __init__(self, config, extruder_num):
        self.printer = config.get_printer()
        self.name = config.get_name()
        self.extruder_stepper = None
        if (config.get("step_pin", None) is not None
                or config.get("dir_pin", None) is not None
                or config.get("rotation_distance", None) is not None):
            self.extruder_stepper = object()

    def get_name(self):
        return self.name

    def cmd_M109(self, gcmd):
        type(self).original_m109_calls += 1


class FakeExtruderStepper:
    original_calls = 0

    def cmd_default_SET_PRESSURE_ADVANCE(self, gcmd):
        type(self).original_calls += 1


class FakeConfig:
    error = RuntimeError

    def __init__(self, printer, name, values=None):
        self.printer = printer
        self.name = name
        self.values = values or {}
        self.accessed = set()

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.name

    def get(self, option, default=None):
        self.accessed.add(option)
        return self.values.get(option, default)


class FakePrinter:
    def __init__(self):
        self.gcode = types.SimpleNamespace(
            scripts=[],
            run_script_from_command=lambda script: self.gcode.scripts.append(
                script))
        self.objects = {"gcode": self.gcode}

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)


@pytest.fixture
def adapter(monkeypatch):
    original_init = FakePrinterExtruder.__init__
    original_m109 = FakePrinterExtruder.cmd_M109
    original_pressure_advance = (
        FakeExtruderStepper.cmd_default_SET_PRESSURE_ADVANCE)
    FakeExtruderStepper.original_calls = 0
    FakePrinterExtruder.original_m109_calls = 0
    fake_extruder = types.ModuleType("kinematics.extruder")
    fake_extruder.PrinterExtruder = FakePrinterExtruder
    fake_extruder.ExtruderStepper = FakeExtruderStepper
    fake_kinematics = types.ModuleType("kinematics")
    fake_kinematics.extruder = fake_extruder
    monkeypatch.setitem(sys.modules, "kinematics", fake_kinematics)
    monkeypatch.setitem(sys.modules, "kinematics.extruder", fake_extruder)

    spec = importlib.util.spec_from_file_location("test_ff_extruder", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    printer = FakePrinter()
    instance = module.load_config(FakeConfig(
        printer, "ff_extruder", {
            "shared_extruder": "extruder",
            "post_m109_macro": "_NS_TOOLCHANGE_PRIME",
        }))
    try:
        yield module, printer, instance
    finally:
        FakePrinterExtruder.__init__ = original_init
        FakePrinterExtruder.cmd_M109 = original_m109
        FakeExtruderStepper.cmd_default_SET_PRESSURE_ADVANCE = (
            original_pressure_advance)


def test_only_base_extruder_creates_a_physical_stepper(adapter):
    module, printer, _instance = adapter
    values = {
        "step_pin": "PB14",
        "dir_pin": "PB15",
        "enable_pin": "!PB12",
        "microsteps": "16",
        "rotation_distance": "19.15",
        "pressure_advance": "0.02",
    }

    base_config = FakeConfig(printer, "extruder", values)
    base = module.klipper_extruder.PrinterExtruder(base_config, 0)
    assert base.extruder_stepper is not None

    logical_config = FakeConfig(printer, "extruder1", values)
    logical = module.klipper_extruder.PrinterExtruder(logical_config, 1)
    assert logical.extruder_stepper is None
    assert set(module._LOGICAL_STEPPER_OPTIONS) <= logical_config.accessed


def test_pressure_advance_uses_synced_shared_stepper(adapter):
    module, printer, _instance = adapter

    class SharedStepper:
        motion_queue = "extruder1"

        def __init__(self):
            self.calls = 0

        def cmd_SET_PRESSURE_ADVANCE(self, gcmd):
            self.calls += 1

    shared_stepper = SharedStepper()
    shared = types.SimpleNamespace(extruder_stepper=shared_stepper)
    active = types.SimpleNamespace(
        extruder_stepper=None, get_name=lambda: "extruder1")
    printer.objects.update({
        "extruder": shared,
        "toolhead": types.SimpleNamespace(get_extruder=lambda: active),
    })

    handler = module.klipper_extruder.ExtruderStepper()
    handler.cmd_default_SET_PRESSURE_ADVANCE(types.SimpleNamespace())
    assert shared_stepper.calls == 1


def test_pressure_advance_refuses_wrong_motion_queue(adapter):
    module, printer, _instance = adapter
    shared_stepper = types.SimpleNamespace(
        motion_queue="extruder2",
        cmd_SET_PRESSURE_ADVANCE=lambda gcmd: None)
    active = types.SimpleNamespace(
        extruder_stepper=None, get_name=lambda: "extruder1")
    printer.objects.update({
        "extruder": types.SimpleNamespace(extruder_stepper=shared_stepper),
        "toolhead": types.SimpleNamespace(get_extruder=lambda: active),
    })

    class Gcmd:
        @staticmethod
        def error(message):
            return RuntimeError(message)

    handler = module.klipper_extruder.ExtruderStepper()
    with pytest.raises(RuntimeError, match="not synced"):
        handler.cmd_default_SET_PRESSURE_ADVANCE(Gcmd())


def test_post_m109_macro_runs_after_native_wait(adapter):
    module, printer, _instance = adapter
    extruder = module.klipper_extruder.PrinterExtruder(
        FakeConfig(printer, "extruder"), 0)

    extruder.cmd_M109(types.SimpleNamespace())

    assert FakePrinterExtruder.original_m109_calls == 1
    assert printer.gcode.scripts == ["_NS_TOOLCHANGE_PRIME"]


def test_restart_rebinds_adapter_to_new_printer(adapter):
    module, _old_printer, _instance = adapter
    new_printer = FakePrinter()

    restarted = module.load_config(FakeConfig(
        new_printer, "ff_extruder", {"shared_extruder": "extruder"}))

    assert restarted.printer is new_printer
    assert module._active_printer is new_printer

    class SharedStepper:
        motion_queue = "extruder2"

        def __init__(self):
            self.calls = 0

        def cmd_SET_PRESSURE_ADVANCE(self, gcmd):
            self.calls += 1

    shared_stepper = SharedStepper()
    active = types.SimpleNamespace(
        extruder_stepper=None, get_name=lambda: "extruder2")
    new_printer.objects.update({
        "extruder": types.SimpleNamespace(extruder_stepper=shared_stepper),
        "toolhead": types.SimpleNamespace(get_extruder=lambda: active),
    })

    handler = module.klipper_extruder.ExtruderStepper()
    handler.cmd_default_SET_PRESSURE_ADVANCE(types.SimpleNamespace())
    assert shared_stepper.calls == 1


def test_duplicate_section_in_same_printer_is_rejected(adapter):
    module, printer, _instance = adapter

    with pytest.raises(RuntimeError, match="only one"):
        module.load_config(FakeConfig(
            printer, "ff_extruder", {"shared_extruder": "extruder"}))

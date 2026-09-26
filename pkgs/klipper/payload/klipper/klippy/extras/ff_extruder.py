# Creator 5 shared-extruder-stepper adapter
#
# Copyright (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""Keep Creator 5 shared-stepper policy out of Klipper's extruder.py.

The Creator 5 exposes four logical hotends but only one physical filament
stepper.  Its generated printer.cfg repeats the physical stepper options in
all four [extruderN] sections.  Unmodified Klipper consequently tries to
create the same stepper four times.

Klipper loads configured extras before toolhead.add_printer_objects(), which
is where kinematics.extruder creates the extruders.  This module installs two
small adapters during that window:

* logical extruders consume, but hide, their repeated stepper options so only
  [extruder] creates the physical ExtruderStepper;
* default SET_PRESSURE_ADVANCE falls back to that shared stepper after
  verifying it is synchronized to the active logical extruder;
* an optional macro can run after the native M109 wait completes, without
  replacing Klipper's built-in M109 command from printer.cfg.

All generic motion, heater, trapq, and Pressure Advance implementation remains
in the Klipper version shipped by Reforge.
"""

import logging

import kinematics.extruder as klipper_extruder


# Options normally consumed by stepper.PrinterStepper or ExtruderStepper.
# They must still be marked as accessed in the generated Creator 5 config,
# even though logical extruders deliberately do not instantiate either.
_LOGICAL_STEPPER_OPTIONS = (
    'step_pin',
    'dir_pin',
    'enable_pin',
    'microsteps',
    'gear_ratio',
    'rotation_distance',
    'full_steps_per_rotation',
    'step_pulse_duration',
    'pressure_advance',
    'pressure_advance_smooth_time',
)

# These are the options PrinterExtruder checks to decide whether it should
# construct an ExtruderStepper.  Returning None for all three keeps a logical
# hotend heater and motion queue without creating another physical stepper.
_STEPPER_TRIGGER_OPTIONS = frozenset((
    'step_pin', 'dir_pin', 'rotation_distance'))

_installed = False
_active_printer = None
_shared_extruder_name = None
_post_m109_macro = None


class _LogicalExtruderConfig:
    """Config proxy which suppresses only physical-stepper creation."""

    def __init__(self, config):
        self._config = config

    def get(self, option, *args, **kwargs):
        if option in _STEPPER_TRIGGER_OPTIONS:
            # Read through the real wrapper first so check_unused_options()
            # records the generated setting as intentionally consumed.
            self._config.get(option, None)
            return None
        return self._config.get(option, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._config, name)


class FFExtruder:
    def __init__(self, config):
        global _installed, _active_printer, _shared_extruder_name
        global _post_m109_macro

        self.printer = config.get_printer()
        self.shared_extruder = config.get('shared_extruder', 'extruder')
        self.post_m109_macro = config.get('post_m109_macro', '').strip()

        # This adapter must run before toolhead creates PrinterExtruder
        # instances.  Refuse a partial late installation instead of leaving a
        # mixture of shared and per-tool steppers.
        if self.printer.lookup_object('toolhead', None) is not None:
            raise config.error(
                "[ff_extruder] loaded after toolhead; the shared-stepper "
                "adapter must be configured before extruders are created")

        if _installed:
            # Klipper's RESTART/FIRMWARE_RESTART rebuilds the Printer object
            # without unloading imported Python modules.  The class adapters
            # therefore remain installed, but must be rebound to the new
            # Printer rather than mistaken for a duplicate config section.
            if _active_printer is self.printer:
                raise config.error(
                    "only one [ff_extruder] section is allowed")
            _active_printer = self.printer
            _shared_extruder_name = self.shared_extruder
            _post_m109_macro = self.post_m109_macro
            logging.info(
                "ff_extruder: rebound shared-stepper adapter after restart; "
                "owner=%s", self.shared_extruder)
            return
        if not hasattr(klipper_extruder, 'PrinterExtruder'):
            raise config.error(
                "[ff_extruder] incompatible Klipper: PrinterExtruder missing")
        if not hasattr(klipper_extruder, 'ExtruderStepper'):
            raise config.error(
                "[ff_extruder] incompatible Klipper: ExtruderStepper missing")

        printer_extruder = klipper_extruder.PrinterExtruder
        extruder_stepper = klipper_extruder.ExtruderStepper
        if not hasattr(extruder_stepper,
                       'cmd_default_SET_PRESSURE_ADVANCE'):
            raise config.error(
                "[ff_extruder] incompatible Klipper: default Pressure "
                "Advance handler missing")
        if not hasattr(printer_extruder, 'cmd_M109'):
            raise config.error(
                "[ff_extruder] incompatible Klipper: M109 handler missing")

        original_printer_extruder_init = printer_extruder.__init__
        original_m109 = printer_extruder.cmd_M109
        original_default_pressure_advance = (
            extruder_stepper.cmd_default_SET_PRESSURE_ADVANCE)
        def shared_printer_extruder_init(self, config, extruder_num):
            if extruder_num:
                # Mark every repeated legacy option as consumed before the
                # normal constructor sees the proxy and creates only the
                # logical heater/trapq side of this extruder.
                for option in _LOGICAL_STEPPER_OPTIONS:
                    config.get(option, None)
                config = _LogicalExtruderConfig(config)
            original_printer_extruder_init(
                self, config, extruder_num)

        def shared_default_pressure_advance(self, gcmd):
            printer = _active_printer
            shared_name = _shared_extruder_name
            active = printer.lookup_object('toolhead').get_extruder()

            # Retain ordinary Klipper behavior for an extruder that owns its
            # physical stepper (the Creator 5's [extruder], and compatibility
            # with any future non-shared configuration).
            if active.extruder_stepper is not None:
                return original_default_pressure_advance(
                    self, gcmd)

            shared = printer.lookup_object(shared_name, None)
            if shared is None or shared.extruder_stepper is None:
                raise gcmd.error(
                    "Active extruder does not have a stepper and shared "
                    "extruder '%s' is unavailable" % shared_name)

            shared_stepper = shared.extruder_stepper
            if shared_stepper.motion_queue != active.get_name():
                raise gcmd.error(
                    "Shared extruder stepper is not synced to active "
                    "extruder '%s'" % active.get_name())
            shared_stepper.cmd_SET_PRESSURE_ADVANCE(gcmd)

        def shared_m109(self, gcmd):
            # Preserve the firmware's native temperature selection and wait
            # semantics.  The optional hook runs only after that wait has
            # completed; its macro is responsible for being a no-op unless a
            # tool change actually needs recovery priming.
            original_m109(self, gcmd)
            if _post_m109_macro:
                self.printer.lookup_object('gcode').run_script_from_command(
                    _post_m109_macro)

        printer_extruder.__init__ = shared_printer_extruder_init
        printer_extruder.cmd_M109 = shared_m109
        extruder_stepper.cmd_default_SET_PRESSURE_ADVANCE = (
            shared_default_pressure_advance)
        _active_printer = self.printer
        _shared_extruder_name = self.shared_extruder
        _post_m109_macro = self.post_m109_macro
        _installed = True
        logging.info(
            "ff_extruder: installed shared-stepper adapter; owner=%s",
            self.shared_extruder)

    def get_status(self, eventtime):
        return {
            'installed': True,
            'shared_extruder': self.shared_extruder,
        }


def load_config(config):
    return FFExtruder(config)

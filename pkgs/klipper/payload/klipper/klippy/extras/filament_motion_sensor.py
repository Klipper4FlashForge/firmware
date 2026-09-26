# Filament Motion Sensor Module
#
# Copyright (C) 2021 Joshua Wherrett <thejoshw.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging
from . import filament_switch_sensor

CHECK_RUNOUT_TIMEOUT = .250

class EncoderSensor:
    def __init__(self, config):
        # Read config
        self.printer = config.get_printer()
        self.name = config.get_name().split()[-1]  # chenhe
        switch_pin = config.get('switch_pin')
        self.extruder_name = config.get('extruder')
        self.detection_length = config.getfloat(
                'detection_length', 7., above=0.)
        # Configure pins
        buttons = self.printer.load_object(config, 'buttons')
        buttons.register_buttons([switch_pin], self.encoder_event)
        # Get printer objects
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode') # chenhe
        self.runout_helper = filament_switch_sensor.RunoutHelper(config)
        self.get_status = self.runout_helper.get_status
        self.extruder = None
        self.estimated_print_time = None
        # Initialise internal state
        self.filament_runout_pos = None
        # Register commands and event handlers
        self.printer.register_event_handler('klippy:ready',
                self._handle_ready)
        self.printer.register_event_handler('idle_timeout:printing',
                self._handle_printing)
        self.printer.register_event_handler('idle_timeout:ready',
                self._handle_not_printing)
        self.printer.register_event_handler('idle_timeout:idle',
                self._handle_not_printing)
        self.gcode.register_mux_command(  # chenhe
            "RESET_FILAMENT_SENSOR", "SENSOR", self.name,
            self.cmd_RESET_FILAMENT_SENSOR,
            desc=self.cmd_RESET_FILAMENT_SENSOR_help)        
    def _update_filament_runout_pos(self, eventtime=None):
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        self.filament_runout_pos = (
                self._get_extruder_pos(eventtime) +
                self.detection_length)
    def _handle_ready(self):
        self.extruder = self.printer.lookup_object(self.extruder_name)
        self.estimated_print_time = (
                self.printer.lookup_object('mcu').estimated_print_time)
        self._update_filament_runout_pos()
        self._extruder_pos_update_timer = self.reactor.register_timer(
                self._extruder_pos_update_event)
    def _handle_printing(self, print_time):
        self.reactor.update_timer(self._extruder_pos_update_timer,
                self.reactor.NOW)
    def _handle_not_printing(self, print_time):
        self.reactor.update_timer(self._extruder_pos_update_timer,
                self.reactor.NEVER)
    def _get_extruder_pos(self, eventtime=None):
        if eventtime is None:
            eventtime = self.reactor.monotonic()
        print_time = self.estimated_print_time(eventtime)
        return self.extruder.find_past_position(print_time)
    def _extruder_pos_update_event(self, eventtime):
        extruder_pos = self._get_extruder_pos(eventtime)
        # Check for filament runout
        is_present = extruder_pos < self.filament_runout_pos
        self.runout_helper.note_runout_position(extruder_pos,
                self.filament_runout_pos)
        self.runout_helper.note_filament_present(eventtime, is_present)
        return eventtime + CHECK_RUNOUT_TIMEOUT
    def encoder_event(self, eventtime, state):
        if self.extruder is not None:
            self._update_filament_runout_pos(eventtime)
            # Check for filament insertion
            # Filament is always assumed to be present on an encoder event
            self.runout_helper.note_filament_present(eventtime, True)
    cmd_RESET_FILAMENT_SENSOR_help = "Reset the filament motion sensor detection length"
    def cmd_RESET_FILAMENT_SENSOR(self, gcmd): # chenhe
        eventtime = self.reactor.monotonic()
        current_pos = self._get_extruder_pos(eventtime)
        self._update_filament_runout_pos(eventtime)
        gcmd.respond_info(
            "Reset Motion Sensor %s: reset , Current pos: %.2f mm , Runout pos: %.2f mm (current + %.2f mm)" % 
            (self.name, current_pos, self.filament_runout_pos, self.detection_length))
def load_config_prefix(config):
    return EncoderSensor(config)

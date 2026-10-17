# Creator 5 nozzle diameter configuration
#
# Copyright (C) 2026
# This file may be distributed under the terms of the GNU GPLv3 license.
"""Stage per-tool nozzle sizes and persist them using Klipper's SAVE_CONFIG.

SET deliberately changes neither live extruders nor Klipper's autosave data.
APPLY hands the staged values to SAVE_CONFIG and its normal backup/restart
path, so extrusion limits and macro-visible config agree after the restart.
"""

import copy
import math


class FFNozzleSize:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.extruders = {}
        self.diameters = {}
        self.pending = {}
        for tool in range(4):
            name = 'extruder' if tool == 0 else 'extruder%d' % tool
            if config.has_section(name):
                self.extruders[tool] = name
                self.diameters[tool] = config.getsection(name).getfloat(
                    'nozzle_diameter', above=0.)
        for suffix, handler, description in (
                ('STATUS', self.cmd_STATUS,
                 'Show active and staged nozzle diameters for every tool'),
                ('SET', self.cmd_SET,
                 'Stage a nozzle diameter: TOOL=0..3 DIAMETER=0.25'),
                ('APPLY', self.cmd_APPLY,
                 'Save staged nozzle diameters and restart Klipper')):
            self.gcode.register_command(
                'FF_NOZZLE_SIZE_' + suffix, handler, desc=description)

    def _require_idle(self, gcmd):
        eventtime = self.printer.get_reactor().monotonic()
        sd = self.printer.lookup_object('virtual_sdcard', None)
        paused = self.printer.lookup_object('pause_resume', None)
        stats = self.printer.lookup_object('print_stats', None)
        if ((sd is not None and sd.is_active())
                or (paused is not None and paused.is_paused)
                or (stats is not None and stats.get_status(eventtime).get(
                    'state') in ('printing', 'paused'))):
            raise gcmd.error(
                'Nozzle size changes are not allowed during a print or pause')
        toolhead = self.printer.lookup_object('toolhead')
        print_time, estimated, empty = toolhead.check_busy(eventtime)
        if not empty or print_time > estimated:
            raise gcmd.error(
                'Motion is still in progress; wait for it to finish and retry')

    def cmd_STATUS(self, gcmd):
        lines = ['Nozzle diameters (active Klipper configuration):']
        for tool in sorted(self.extruders):
            line = 'T%d [%s]: %.3f mm' % (
                tool, self.extruders[tool], self.diameters[tool])
            if tool in self.pending:
                line += ' -> %.3f mm staged' % self.pending[tool]
            lines.append(line)
        if self.pending:
            lines.append('Run NOZZLE_SIZE_APPLY to save and restart Klipper; '
                         'RESTART discards staged values.')
        else:
            lines.append('No nozzle size changes staged.')
        gcmd.respond_info('\n'.join(lines))

    def cmd_SET(self, gcmd):
        self._require_idle(gcmd)
        tool = gcmd.get_int('TOOL', minval=0, maxval=3)
        if tool not in self.extruders:
            raise gcmd.error('T%d has no configured extruder' % tool)
        diameter = gcmd.get_float('DIAMETER', minval=0.10, maxval=1.20)
        if not math.isfinite(diameter):
            raise gcmd.error('DIAMETER must be a finite number')
        if math.isclose(diameter, self.diameters[tool], abs_tol=1.e-9):
            self.pending.pop(tool, None)
            gcmd.respond_info(
                'T%d remains at %.3f mm; no change staged for this tool.'
                % (tool, self.diameters[tool]))
            return
        self.pending[tool] = diameter
        gcmd.respond_info(
            'T%d nozzle size staged: %.3f -> %.3f mm. '
            'Run NOZZLE_SIZE_APPLY to save and restart Klipper.'
            % (tool, self.diameters[tool], diameter))

    def cmd_APPLY(self, gcmd):
        self._require_idle(gcmd)
        if not self.pending:
            gcmd.respond_info('No nozzle size changes staged; no restart needed.')
            return
        configfile = self.printer.lookup_object('configfile')
        eventtime = self.printer.get_reactor().monotonic()
        if configfile.get_status(eventtime).get('save_config_pending'):
            raise gcmd.error(
                'Other calibration/config changes await SAVE_CONFIG. '
                'Save or discard them first, then stage nozzle sizes again.')

        # SAVE_CONFIG refuses values also defined in included files, and can
        # fail on an I/O error. Restore its in-memory state on failure so a
        # later unrelated SAVE_CONFIG cannot accidentally save nozzle edits.
        # v0.13 keeps this state in configfile.autosave; v0.12 kept it directly
        # in configfile. Use the native saver in either case, never edit files.
        autosave = getattr(configfile, 'autosave', configfile)
        fields = ('fileconfig', 'save_config_pending', 'status_save_pending')
        if not all(hasattr(autosave, field) for field in fields):
            raise gcmd.error('Unsupported Klipper SAVE_CONFIG implementation')
        before = {field: copy.deepcopy(getattr(autosave, field))
                  for field in fields}
        try:
            for tool, diameter in sorted(self.pending.items()):
                configfile.set(self.extruders[tool], 'nozzle_diameter',
                               '%.6f' % diameter)
            gcmd.respond_info(
                'Saving nozzle diameters with SAVE_CONFIG; '
                'Klipper will restart and heater targets will be cleared.')
            self.gcode.run_script_from_command('SAVE_CONFIG')
        except Exception:
            for field, value in before.items():
                setattr(autosave, field, value)
            raise
        self.pending.clear()

    def get_status(self, eventtime):
        return {
            'diameters': {'T%d' % tool: value
                          for tool, value in self.diameters.items()},
            'pending': {'T%d' % tool: value
                        for tool, value in self.pending.items()},
        }


def load_config(config):
    return FFNozzleSize(config)

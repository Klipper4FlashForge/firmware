# Print-start / print-end entry points for the FlashForge Creator 5 Pro.
#
# The stock slicer profile's start G-code carries no toolchanger information
# and no G28 -- its only motion is `G1 Z5 F2400`, which on an unhomed machine
# raises "Must home axis first". So the file cannot be what prepares the
# machine: something must home, clean and grab a tool BEFORE its first line,
# which is exactly what the touchscreen app did before sending M23/M24. This
# module restores that ordering for prints started from Moonraker/Mainsail by
# wrapping the commands that begin a print, so the stock OrcaSlicer profile
# needs no modification.
#
# NOT via idle_timeout:printing: that is emitted synchronously inside motion
# scheduling, which is no place to run a G-code script, and it fires only once
# the toolhead syncs -- which for a stock file is the very `G1 Z5` that fails.
#
# It contains no print policy. It resolves the file, reads the slicer
# metadata, and calls two ordinary macros defined in ff-print-macros.cfg:
#
#       FF_BEFORE_PRINT_START ORIGIN=<cmd> [BED=] [TOOL=] [NOZZLE=] [LAYER=]
#       FF_AFTER_PRINT_END    STATE=<complete|cancelled|error|...>
#
# Everything derived is also published in get_status as printer.ff_print.*.
#
# The command metadata comes from the head of the file. A bounded tail read is
# additionally used for Orca's resolved prime-tower settings:
#   bed          the first `M140`/`M190 S<t>`
#   nozzle       the first `M104`/`M109 S<t>`
#   first tool   the first bare `Tn` -- the file's initial extruder, NOT the
#                lowest-numbered one it uses
#   tools        all tools named by Orca's `; filament:` header (1-based in
#                that header), with a full-file bare-Tn scan as fallback
#   layer        the first `;HEIGHT:` -- the FIRST layer's height, which is
#                what the print Z offset's thin-layer term wants
#   prime tower  Orca's single resolved wipe_tower_x/y plus width/brim/rotation
#                values. In a multi-plate project the start-G-code placeholder
#                may incorrectly expand to the first value of a comma-separated
#                list instead of the active plate's single resolved value.
#
# Temperatures and tools are not read from the slicer's config block: those
# values can differ from the commands actually emitted. The sole exception is
# Orca's resolved prime-tower geometry. Its exact single-value entries are
# needed because a custom start-G-code placeholder can incorrectly expand to
# another plate's entry from a comma-separated multi-plate value.
#
# Per-tool clean temperatures are NOT taken from the file; the app's material
# table is ported as _FF_FILAMENT.temps, with the material per tool alongside.

import logging
import math
import os
import re

EXTRUDER_COUNT = 4

# Bounded read: everything parsed sits within ~8 KB of the start on real
# files, so this is a wide margin rather than a guess.
HEAD_BYTES = 256 * 1024
TAIL_BYTES = 256 * 1024

# print_stats states that mean the job is over (as opposed to paused mid-print).
FINISHED_STATES = ('complete', 'cancelled', 'error')


def _parse_metadata(path):
    """Read what the file says about itself, from its own commands.

    Returns a dict with whatever could be derived; missing keys simply are
    not present.  Never raises -- a file we cannot read just yields {}, and
    the macro then runs with no derived parameters."""
    try:
        with open(path, 'rb') as fh:
            head = fh.read(HEAD_BYTES).decode('utf-8', 'replace')
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - TAIL_BYTES), os.SEEK_SET)
            tail = fh.read(TAIL_BYTES).decode('utf-8', 'replace')
    except Exception:
        logging.exception("ff_print: cannot read '%s'", path)
        return {}

    metadata = {}

    # Bed and nozzle: the file's own first heat command, the way the app's
    # parser did it (it scanned M104/M109/M140/M190/M141/M191 and reported
    # nozzleTemp / bedTemp / chamberTemp).
    bed_match = re.search(r'^M1[49]0 S([0-9]+(?:\.[0-9]+)?)', head, re.M)
    if bed_match is not None and float(bed_match.group(1)) > 0:
        metadata['bed'] = int(float(bed_match.group(1)))
    nozzle_match = re.search(r'^M10[49] S([0-9]+(?:\.[0-9]+)?)', head, re.M)
    if nozzle_match is not None and float(nozzle_match.group(1)) > 0:
        metadata['nozzle'] = int(float(nozzle_match.group(1)))

    # The file's initial extruder (the app's "fisrNozzleIndex", sic).
    tool_match = re.search(r'^T([0-%d])\b' % (EXTRUDER_COUNT - 1),
                           head, re.M)
    if tool_match is not None:
        metadata['tool'] = int(tool_match.group(1))

    # Orca lists every used filament/tool in its compact header.  Values are
    # one-based there (`; filament: 1,3` means T0 and T2).  This lets the
    # print-start macro offer an "all used colours" purge without loading the
    # complete G-code into memory.  Older/non-Orca files fall back to a
    # streaming scan of bare Tn commands across the whole file.
    tools = []
    filament_match = re.search(r'^;\s*filament:\s*([0-9, ]+)\s*$',
                               head, re.M | re.I)
    if filament_match is not None:
        for value in filament_match.group(1).split(','):
            try:
                tool = int(value.strip()) - 1
            except ValueError:
                continue
            if 0 <= tool < EXTRUDER_COUNT and tool not in tools:
                tools.append(tool)
    if not tools:
        try:
            with open(path, 'rb') as fh:
                for line in fh:
                    match = re.match(br'^T([0-%d])\b'
                                     % (EXTRUDER_COUNT - 1), line)
                    if match is not None:
                        tool = int(match.group(1))
                        if tool not in tools:
                            tools.append(tool)
        except Exception:
            logging.exception("ff_print: cannot scan tools in '%s'", path)
    if metadata.get('tool') is not None and metadata['tool'] not in tools:
        tools.insert(0, metadata['tool'])
    if tools:
        metadata['tools'] = tools

    # First-layer height, from the per-layer marker the slicer emits. This
    # feeds the print Z offset's thin-layer term, which is a FIRST-layer
    # correction.
    layer_match = re.search(r'^;HEIGHT:([0-9.]+)', head, re.M)
    if layer_match is not None:
        try:
            metadata['layer'] = float(layer_match.group(1))
        except ValueError:
            pass

    # Orca writes both a single resolved value and, for multi-plate projects,
    # a comma-separated list for wipe_tower_x/y. Match ONLY the single-value
    # lines: those are the coordinates actually used by the generated moves.
    # Reading the tail is bounded, so this does not load a large G-code file.
    number = r'([-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))'

    def resolved_float(option):
        match = re.search(r'^;\s*%s\s*=\s*%s\s*$'
                          % (re.escape(option), number), tail, re.M)
        if match is None:
            return None
        try:
            return float(match.group(1))
        except ValueError:
            return None

    tower_options = (
        ('prime_tower_x', 'wipe_tower_x'),
        ('prime_tower_y', 'wipe_tower_y'),
        ('prime_tower_width', 'prime_tower_width'),
        ('prime_tower_brim', 'prime_tower_brim_width'),
        ('prime_tower_rotation', 'wipe_tower_rotation_angle'),
    )
    for key, option in tower_options:
        value = resolved_float(option)
        if value is not None:
            metadata[key] = value

    # Orca exposes only prime_tower_width as a setting; the other dimension
    # is generated from the required purge volume. Recover that real depth
    # from the first core outline immediately preceding WIPE_TOWER_BRIM_START.
    # Rotating every emitted point back into the tower's local frame keeps the
    # calculation valid when wipe_tower_rotation_angle is non-zero.
    brim_marker = head.find('; WIPE_TOWER_BRIM_START')
    if brim_marker >= 0:
        type_marker = head.rfind(';TYPE:Prime tower', 0, brim_marker)
    else:
        type_marker = -1
    block = head[type_marker:brim_marker] if type_marker >= 0 else None
    if block is None and metadata.get('prime_tower_x') is not None:
        # A large first object can push the first tower outline beyond the
        # bounded head buffer. Stream until the first brim instead of loading
        # the whole file; each new TYPE marker replaces the candidate block.
        try:
            candidate = None
            with open(path, 'rb') as fh:
                for raw_line in fh:
                    line = raw_line.decode('utf-8', 'replace')
                    if ';TYPE:Prime tower' in line:
                        candidate = [line]
                    elif candidate is not None:
                        if '; WIPE_TOWER_BRIM_START' in line:
                            block = ''.join(candidate)
                            break
                        candidate.append(line)
        except Exception:
            logging.exception(
                "ff_print: cannot scan prime-tower outline in '%s'", path)
    if block is not None:
        points = []
        current_x = current_y = None
        for line in block.splitlines():
            if re.match(r'^G[01]\b', line) is None:
                continue
            x_match = re.search(r'\bX([-+0-9.]+)', line)
            y_match = re.search(r'\bY([-+0-9.]+)', line)
            if x_match is not None:
                current_x = float(x_match.group(1))
            if y_match is not None:
                current_y = float(y_match.group(1))
            if (x_match is not None or y_match is not None) and \
                    current_x is not None and current_y is not None:
                points.append((current_x, current_y))
        if len(points) >= 4:
            rotation = metadata.get('prime_tower_rotation', 0.)
            angle = math.radians(rotation)
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            local = [(cos_a * x + sin_a * y,
                      -sin_a * x + cos_a * y) for x, y in points]
            local_x = [point[0] for point in local]
            local_y = [point[1] for point in local]
            extent_x = max(local_x) - min(local_x)
            extent_y = max(local_y) - min(local_y)
            expected = metadata.get('prime_tower_width')
            if expected is not None and \
                    abs(extent_y - expected) < abs(extent_x - expected):
                extent_x, extent_y = extent_y, extent_x
            world_x = [point[0] for point in points]
            world_y = [point[1] for point in points]
            metadata['prime_tower_width'] = extent_x
            metadata['prime_tower_depth'] = extent_y
            metadata['prime_tower_center_x'] = (
                min(world_x) + max(world_x)) / 2.
            metadata['prime_tower_center_y'] = (
                min(world_y) + max(world_y)) / 2.
            metadata['prime_tower_core_min_x'] = min(world_x)
            metadata['prime_tower_core_max_x'] = max(world_x)
            metadata['prime_tower_core_min_y'] = min(world_y)
            metadata['prime_tower_core_max_y'] = max(world_y)

    return metadata


class FFPrint:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.before_macro = config.get('before_macro', 'FF_BEFORE_PRINT_START')
        self.after_macro = config.get('after_macro', 'FF_AFTER_PRINT_END')
        self.hook_commands = [c.strip().upper() for c
                              in config.getlist('hook_commands',
                                                ['SDCARD_PRINT_FILE', 'M23'])
                              if c.strip()]
        self.previous_handlers = {}
        self.metadata = {}
        self.filename = None
        self.origin = None
        # Latch: only a print WE announced may fire the end macro.
        self.active = False
        self.end_state = 'complete'
        self.end_timer = None
        self.printer.register_event_handler('klippy:connect',
                                            self._handle_connect)
        self.printer.register_event_handler('idle_timeout:ready',
                                            self._handle_ready)

    def _handle_connect(self):
        # Same rename dance gcode_macro uses for rename_existing: take the
        # command over and keep the previous handler to chain to.
        for cmd in self.hook_commands:
            previous = self.gcode.register_command(cmd, None)
            if previous is None:
                raise self.printer.config_error(
                    "ff_print: command '%s' is not registered -- is there a"
                    " [virtual_sdcard] section in the config? (Section ORDER"
                    " does not matter: this runs at klippy:connect, after"
                    " every section is loaded.)" % (cmd,))
            self.previous_handlers[cmd] = previous
            self.gcode.register_command(
                cmd, self._make_handler(cmd),
                desc="%s (ff_print: runs %s first)"
                     % (cmd, self.before_macro))
        self.end_timer = self.reactor.register_timer(self._run_end_macro)

    def _make_handler(self, cmd):
        def handler(gcmd):
            self._cmd_start(cmd, gcmd)
        return handler

    def get_status(self, eventtime):
        return {
            'filename': self.filename,
            'origin': self.origin,
            'active': self.active,
            'tool': self.metadata.get('tool'),
            'tools': self.metadata.get('tools', []),
            'nozzle': self.metadata.get('nozzle'),
            'bed': self.metadata.get('bed'),
            'layer': self.metadata.get('layer'),
            'prime_tower_x': self.metadata.get('prime_tower_x'),
            'prime_tower_y': self.metadata.get('prime_tower_y'),
            'prime_tower_width': self.metadata.get('prime_tower_width'),
            'prime_tower_depth': self.metadata.get('prime_tower_depth'),
            'prime_tower_brim': self.metadata.get('prime_tower_brim'),
            'prime_tower_rotation': self.metadata.get(
                'prime_tower_rotation'),
            'prime_tower_center_x': self.metadata.get(
                'prime_tower_center_x'),
            'prime_tower_center_y': self.metadata.get(
                'prime_tower_center_y'),
            'prime_tower_core_min_x': self.metadata.get(
                'prime_tower_core_min_x'),
            'prime_tower_core_max_x': self.metadata.get(
                'prime_tower_core_max_x'),
            'prime_tower_core_min_y': self.metadata.get(
                'prime_tower_core_min_y'),
            'prime_tower_core_max_y': self.metadata.get(
                'prime_tower_core_max_y'),
        }

    def _resolve(self, gcmd, cmd):
        """Path of the file this command is about, mirroring virtual_sdcard's
        own resolution (a plain join under sdcard_dirname)."""
        if cmd == 'M23':
            name = gcmd.get_raw_command_parameters().strip()
        else:
            name = gcmd.get('FILENAME', '')
        name = name.strip()
        if name.startswith('/'):
            name = name[1:]
        if not name:
            return None
        virtual_sdcard = self.printer.lookup_object('virtual_sdcard', None)
        if virtual_sdcard is None:
            return None
        return os.path.join(virtual_sdcard.sdcard_dirname, name)

    def _cmd_start(self, cmd, gcmd):
        path = self._resolve(gcmd, cmd)
        self.filename = path
        self.origin = cmd
        self.metadata = _parse_metadata(path) if path else {}
        if self.metadata:
            logging.info("ff_print: %s -> %s", path, self.metadata)
        else:
            logging.info("ff_print: %s -> no slicer metadata found", path)

        params = ['ORIGIN=%s' % (cmd,)]
        for key, name, fmt in (('bed', 'BED', '%d'), ('tool', 'TOOL', '%d'),
                               ('nozzle', 'NOZZLE', '%d'),
                               ('layer', 'LAYER', '%s')):
            if self.metadata.get(key) is not None:
                params.append('%s=%s' % (name, fmt % (self.metadata[key],)))
        if self.metadata.get('tools'):
            params.append('TOOLS=%s' % ','.join(
                str(tool) for tool in self.metadata['tools']))

        # Let the macro raise: a refusal here must stop the print BEFORE the
        # base command loads and resumes the file.
        self.gcode.run_script_from_command(
            '%s %s' % (self.before_macro, ' '.join(params)))
        # Arm the end latch only once prepare succeeded.
        self.active = True
        self.previous_handlers[cmd](gcmd)

    def _handle_ready(self, print_time):
        """idle_timeout:ready fires whenever the queue drains -- including a
        long M190 mid-print and any manual jog.  Only a job we announced, and
        only one print_stats calls finished, is a real end of print."""
        if not self.active:
            return
        stats = self.printer.lookup_object('print_stats', None)
        if stats is None:
            return
        state = stats.get_status(self.reactor.monotonic()).get('state')
        if state not in FINISHED_STATES:
            return
        self.active = False
        self.end_state = state
        # Run the macro from a timer, not from this event: the handler runs in
        # the idle_timeout timeout path and should not block on a G-code script.
        self.reactor.update_timer(self.end_timer, self.reactor.NOW)

    def _run_end_macro(self, eventtime):
        state = self.end_state
        try:
            self.gcode.run_script('%s STATE=%s' % (self.after_macro, state))
        except Exception:
            logging.exception("ff_print: %s failed", self.after_macro)
        return self.reactor.NEVER


def load_config(config):
    return FFPrint(config)

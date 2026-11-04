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
#   next tool    the second distinct bare `Tn`, plus its first `M109` target,
#                within the head of the file; this covers a preheat window
#                which begins inside start G-code
#   tools        all tools named by Orca's `; filament:` header (1-based in
#                that header), with a bare-Tn scan as fallback that gives up
#                after SCAN_BUDGET seconds
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
#
# N4S4: reading the file runs in a thread while the reactor goes on (see
# _run_off_thread), and with check_bounds the print is refused up front when
# the file moves beyond what the machine reaches (see _scan_extents).

import logging
import math
import os
import re
import threading
import time

EXTRUDER_COUNT = 4

# Bounded read: everything parsed sits within ~8 KB of the start on real
# files, so this is a wide margin rather than a guess.
HEAD_BYTES = 256 * 1024
TAIL_BYTES = 256 * 1024

# The two line-by-line scans below used to run on the thread that also keeps
# the heaters alive, over a file that can be tens of megabytes; a stall of a
# few seconds with a heater on makes the MCU shut down. The search for the
# second tool therefore looks at the head of the file only, as the original's
# first version of it did: a second colour further in is not needed from the
# start (Orca's own preheat lands in the object body), and heating it that
# early would only leave a hot nozzle idle. The fallback scan for the tools of
# a file without Orca's header gives up after SCAN_BUDGET seconds. (N4S4: all
# of it now runs in a thread, see _run_off_thread; the bounds are kept, they
# keep the start from taking long.)
NEXT_TOOL_SCAN_BYTES = HEAD_BYTES
SCAN_BUDGET = 2.0

# N4S4: how _cmd_start waits for the thread that reads the file. The reactor
# runs timers (clock sync, heaters) every SCAN_POLL seconds meanwhile.
SCAN_POLL = 0.05

# N4S4: the extents scan reads the whole file in pieces of this size and lets
# go of the interpreter between pieces, so that no other thread waits longer
# than a piece takes (about 30 ms of regex per 16 KiB on the Creator 5's CPU,
# 2.2 s for a 3 MB file). A file that takes longer than the budget is not
# checked at all, rather than refused on a guess.
EXTENTS_CHUNK = 16 * 1024
EXTENTS_YIELD = 0.001
EXTENTS_BUDGET = 40.0

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
    # streaming scan of bare Tn commands, which stops after SCAN_BUDGET.
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
            deadline = time.monotonic() + SCAN_BUDGET
            with open(path, 'rb') as fh:
                for count, line in enumerate(fh):
                    if count % 2000 == 0 and time.monotonic() > deadline:
                        logging.warning(
                            "ff_print: gave up scanning '%s' for its tools"
                            " after %.1f s; the list may be incomplete",
                            path, SCAN_BUDGET)
                        break
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

    # Orca normally inserts an M104 `preheat_time` seconds before a tool is
    # needed. If the first colour is shorter than that window, the requested
    # start lies inside custom machine-start G-code and no early M104 can be
    # placed in the object body. Find the second distinct tool and the M109
    # which establishes its actual first-use target; ADAPTIVE_MESH can start
    # that one heater after probing, just before first-layer printing begins.
    initial_tool = metadata.get('tool')
    if initial_tool is not None:
        active_tool = None
        next_tool = None
        scanned = 0
        try:
            with open(path, 'rb') as fh:
                for raw_line in fh:
                    scanned += len(raw_line)
                    if scanned > NEXT_TOOL_SCAN_BYTES:
                        break
                    line = raw_line.decode('utf-8', 'replace')
                    select = re.match(r'^T([0-%d])\b'
                                      % (EXTRUDER_COUNT - 1), line)
                    if select is not None:
                        active_tool = int(select.group(1))
                        if active_tool != initial_tool and next_tool is None:
                            next_tool = active_tool
                            metadata['next_tool'] = next_tool
                    if next_tool is None or re.match(r'^M109\b', line) is None:
                        continue
                    explicit = re.search(r'\bT([0-%d])\b'
                                         % (EXTRUDER_COUNT - 1), line)
                    target_tool = (int(explicit.group(1))
                                   if explicit is not None else active_tool)
                    target = re.search(r'\bS([0-9]+(?:\.[0-9]+)?)', line)
                    if target_tool == next_tool and target is not None:
                        temperature = float(target.group(1))
                        if temperature > 0.:
                            metadata['next_nozzle'] = int(temperature)
                            break
        except Exception:
            logging.exception(
                "ff_print: cannot scan next-tool preheat in '%s'", path)

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
    brim_end_marker = head.find('; WIPE_TOWER_BRIM_END', brim_marker)
    brim_block = (head[brim_marker:brim_end_marker]
                  if brim_marker >= 0 and brim_end_marker >= 0 else None)
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
    if brim_block is None and metadata.get('prime_tower_x') is not None:
        # The automatic-brim sentinel is resolved only in the generated
        # moves. If those moves lie beyond the bounded head buffer, collect
        # the first brim block with a small streaming scan.
        try:
            candidate = None
            with open(path, 'rb') as fh:
                for raw_line in fh:
                    line = raw_line.decode('utf-8', 'replace')
                    if '; WIPE_TOWER_BRIM_START' in line:
                        candidate = [line]
                    elif candidate is not None:
                        if '; WIPE_TOWER_BRIM_END' in line:
                            brim_block = ''.join(candidate)
                            break
                        candidate.append(line)
        except Exception:
            logging.exception(
                "ff_print: cannot scan prime-tower brim in '%s'", path)
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

            # Orca uses -1 for "automatic" prime-tower brim. Resolve that
            # sentinel from the actual emitted brim paths. The scalar value
            # is the largest local expansion, making toolchange avoidance
            # conservative; exact world bounds keep adaptive mesh tight.
            if metadata.get('prime_tower_brim', 0.) < 0.:
                brim_points = []
                current_x = current_y = None
                for line in (brim_block or '').splitlines():
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
                        brim_points.append((current_x, current_y))
                if brim_points:
                    brim_local = [(cos_a * x + sin_a * y,
                                   -sin_a * x + cos_a * y)
                                  for x, y in brim_points]
                    brim_local_x = [point[0] for point in brim_local]
                    brim_local_y = [point[1] for point in brim_local]
                    expansions = (
                        min(local_x) - min(brim_local_x),
                        max(brim_local_x) - max(local_x),
                        min(local_y) - min(brim_local_y),
                        max(brim_local_y) - max(local_y),
                    )
                    metadata['prime_tower_brim'] = max(
                        0., max(expansions))
                    brim_world_x = [point[0] for point in brim_points]
                    brim_world_y = [point[1] for point in brim_points]
                    metadata['prime_tower_outer_min_x'] = min(brim_world_x)
                    metadata['prime_tower_outer_max_x'] = max(brim_world_x)
                    metadata['prime_tower_outer_min_y'] = min(brim_world_y)
                    metadata['prime_tower_outer_max_y'] = max(brim_world_y)
                else:
                    # Never pass Orca's negative sentinel to Klipper.
                    metadata['prime_tower_brim'] = 0.

    return metadata


# ----------------------------------------------------------------- extents
#
# N4S4. On 2026-10-14 a job was started whose prime tower reached Y 260.018
# while the printer reaches Y 260.0 for T0 and 259.6 for T1 (T1's nozzle sits
# 0.414 mm further out): Klipper refused the move ("Move out of range") two
# minutes into the job, after the nozzles were hot and the mesh was probed.
# The file says where it goes, so it is read once, up front.

def _axis_regex(axis):
    return re.compile(
        (r'^G[0-3](?![0-9])[^;\n]*?[ \t]%s([-+]?[0-9]*\.?[0-9]+)'
         % axis.upper()).encode(), re.M)


_AXIS_RE = {axis: _axis_regex(axis) for axis in 'xyz'}
# The two things that change what a coordinate means: G90/G91 (relative moves
# are not positions) and a bare Tn (the tool's offset is added).
_MODE_RE = re.compile(rb'^(?:(G9[01])|T([0-3]))(?![0-9])', re.M)


class _Extents:
    """The smallest and largest X, Y and Z a file moves to, in machine
    coordinates (the mounted tool's offset added, which is what Klipper
    checks), each with the G-code value and the tool it came from."""

    def __init__(self):
        self.low = {}       # axis -> (machine, gcode, tool)
        self.high = {}

    def add(self, axis, values, offset, tool):
        low, high = min(values), max(values)
        if axis not in self.low or low + offset < self.low[axis][0]:
            self.low[axis] = (low + offset, low, tool)
        if axis not in self.high or high + offset > self.high[axis][0]:
            self.high[axis] = (high + offset, high, tool)

    def as_status(self):
        out = {}
        for axis in 'xyz':
            if axis in self.low:
                out['%s_min' % axis] = round(self.low[axis][0], 3)
            if axis in self.high:
                out['%s_max' % axis] = round(self.high[axis][0], 3)
        return out


def _scan_extents(path, offsets, initial_tool=None, budget=EXTENTS_BUDGET,
                  chunk=EXTENTS_CHUNK, pause=time.sleep):
    """Read the whole file for the positions it moves to.

    `offsets` maps a tool to its (x, y, z) offset, `initial_tool` is the tool
    that is mounted before the file's first `Tn`. Relative stretches (G91 to
    the next G90) are skipped. Arcs count by their end points. Returns an
    _Extents, or None when the file cannot be read or the budget ran out. It
    works in pieces of `chunk` bytes and calls `pause` between them, so that
    a thread that scans does not hold the interpreter for long."""
    extents = _Extents()
    state = {'tool': initial_tool, 'relative': False}

    def segment(text):
        if state['relative'] or not text:
            return
        offset = offsets.get(state['tool'], (0., 0., 0.))
        for index, axis in enumerate('xyz'):
            found = _AXIS_RE[axis].findall(text)
            if found:
                extents.add(axis, [float(value) for value in found],
                            offset[index], state['tool'])

    def process(text):
        position = 0
        for marker in _MODE_RE.finditer(text):
            segment(text[position:marker.start()])
            position = marker.end()
            if marker.group(1) is not None:
                state['relative'] = marker.group(1) == b'G91'
            else:
                state['tool'] = int(marker.group(2))
        segment(text[position:])

    deadline = time.monotonic() + budget
    try:
        carry = b''
        with open(path, 'rb') as fh:
            while True:
                block = fh.read(chunk)
                data = carry + block
                # whole lines only: a word cut in two would be read wrongly
                cut = len(data) if not block else data.rfind(b'\n') + 1
                carry = data[cut:]
                if cut:
                    process(data[:cut])
                if not block:
                    return extents
                if time.monotonic() > deadline:
                    logging.warning(
                        "ff_print: gave up reading '%s' for its extents after"
                        " %.0f s; it is not checked against the machine's"
                        " range", path, budget)
                    return None
                pause(EXTENTS_YIELD)
    except Exception:
        logging.exception("ff_print: cannot read the extents of '%s'", path)
        return None


def _bounds_problems(extents, low, high):
    """What of the file lies outside [low, high], the toolhead's axis range.
    Z is checked at the top only: the bottom of a Z range is not where a
    print goes."""
    problems = []
    for index, axis in enumerate('xyz'):
        if axis in extents.high and extents.high[axis][0] > high[index] + 1e-6:
            problems.append((axis, 'above', extents.high[axis], high[index]))
        if (axis != 'z' and axis in extents.low
                and extents.low[axis][0] < low[index] - 1e-6):
            problems.append((axis, 'below', extents.low[axis], low[index]))
    return problems


def _describe_problem(problem):
    axis, side, (machine, gcode, tool), limit = problem
    shift = machine - gcode
    origin = "G-code %s%.3f" % (axis.upper(), gcode)
    if tool is not None and abs(shift) >= 0.0005:
        origin += " and T%d's offset %+.3f" % (tool, shift)
    return ("%s %s %.1f mm (%s) -- the machine reaches %.1f mm"
            % (axis.upper(), "up to" if side == 'above' else "down to",
               machine, origin, limit))


def _analyse_file(path, offsets, want_extents):
    """Everything ff_print reads from the file; what runs in the thread."""
    metadata = _parse_metadata(path)
    extents = (_scan_extents(path, offsets, metadata.get('tool'))
               if want_extents else None)
    return metadata, extents


class FFPrint:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.before_macro = config.get('before_macro', 'FF_BEFORE_PRINT_START')
        self.after_macro = config.get('after_macro', 'FF_AFTER_PRINT_END')
        # N4S4: refuse a file that moves beyond the machine's range before
        # anything heats or moves. On unless turned off.
        self.check_bounds = config.getboolean('check_bounds', True)
        self.extents = None
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
            'next_tool': self.metadata.get('next_tool'),
            'next_nozzle': self.metadata.get('next_nozzle'),
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
            'prime_tower_outer_min_x': self.metadata.get(
                'prime_tower_outer_min_x'),
            'prime_tower_outer_max_x': self.metadata.get(
                'prime_tower_outer_max_x'),
            'prime_tower_outer_min_y': self.metadata.get(
                'prime_tower_outer_min_y'),
            'prime_tower_outer_max_y': self.metadata.get(
                'prime_tower_outer_max_y'),
            # N4S4: the machine-coordinate extents of the file, as read at
            # the start (None when not checked or not readable)
            'extents': self.extents,
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

    # N4S4: the reading of the file (a few hundred KiB line by line, and with
    # check_bounds all of it) takes a second or more on this CPU. It runs in a
    # thread, and the reactor goes on meanwhile: the clock-sync pings, the
    # heaters and whatever motion is still queued do not wait for it.
    def _run_off_thread(self, func, *args):
        box = {}
        done = threading.Event()

        def work():
            try:
                box['value'] = func(*args)
            except BaseException as err:    # handed to the caller, below
                box['error'] = err
            finally:
                done.set()

        thread = threading.Thread(target=work, name='ff_print-scan',
                                  daemon=True)
        thread.start()
        if self.reactor is None:
            done.wait()
        else:
            while not done.is_set():
                self.reactor.pause(self.reactor.monotonic() + SCAN_POLL)
        thread.join()
        if 'error' in box:
            raise box['error']
        return box['value']

    def _tool_offsets(self):
        """{tool: (x, y, z)}: what the mounted tool adds to a G-code
        position. Empty without [ff_toolchange]: no tool frame, no offset."""
        toolchange = self.printer.lookup_object('ff_toolchange', None)
        try:
            return {tool: (toolchange.offset_x[tool],
                           toolchange.offset_y[tool],
                           toolchange.offset_z[tool])
                    for tool in range(EXTRUDER_COUNT)}
        except (AttributeError, IndexError, TypeError):
            return {}

    def _axis_limits(self):
        """The toolhead's axis range as (low, high), X Y Z, or None."""
        toolhead = self.printer.lookup_object('toolhead', None)
        try:
            status = toolhead.get_status(self.reactor.monotonic())
            return (list(status['axis_minimum'])[:3],
                    list(status['axis_maximum'])[:3])
        except (AttributeError, KeyError, TypeError):
            return None

    def _cmd_start(self, cmd, gcmd):
        path = self._resolve(gcmd, cmd)
        self.filename = path
        self.origin = cmd
        limits = self._axis_limits() if self.check_bounds and path else None
        if path:
            self.metadata, extents = self._run_off_thread(
                _analyse_file, path,
                self._tool_offsets() if limits is not None else {},
                limits is not None)
        else:
            self.metadata, extents = {}, None
        self.extents = extents.as_status() if extents is not None else None
        if self.metadata:
            logging.info("ff_print: %s -> %s", path, self.metadata)
        else:
            logging.info("ff_print: %s -> no slicer metadata found", path)
        if extents is not None:
            logging.info("ff_print: %s moves within %s (machine range %s..%s)",
                         path, self.extents, limits[0], limits[1])
            problems = _bounds_problems(extents, limits[0], limits[1])
            if problems:
                # Before the statistics, the macro and the base command: a
                # refused file has no job and nothing has been touched.
                message = (
                    "ff_print: not starting %s: the file moves beyond what "
                    "the printer reaches: %s. Move the object or the prime "
                    "tower in the slicer and slice again (check_bounds: "
                    "False in [ff_print] turns this check off)."
                    % (os.path.basename(path),
                       "; ".join(_describe_problem(p) for p in problems)))
                logging.warning("%s", message)
                raise gcmd.error(message)

        params = ['ORIGIN=%s' % (cmd,)]
        for key, name, fmt in (('bed', 'BED', '%d'), ('tool', 'TOOL', '%d'),
                               ('nozzle', 'NOZZLE', '%d'),
                               ('layer', 'LAYER', '%s')):
            if self.metadata.get(key) is not None:
                params.append('%s=%s' % (name, fmt % (self.metadata[key],)))
        # The tools the file uses are NOT passed on: the original's START_PRINT
        # would then clean every one of them. A start macro that wants them
        # reads printer.ff_print.tools.

        self._stats('note_job_prepare', path, cmd)
        try:
            # Let the macro raise: a refusal here must stop the print BEFORE
            # the base command loads and resumes the file.
            self.gcode.run_script_from_command(
                '%s %s' % (self.before_macro, ' '.join(params)))
            # Arm the end latch only once prepare succeeded.
            self.active = True
            self.previous_handlers[cmd](gcmd)
        except Exception as err:
            self._stats('note_job_aborted', str(err))
            raise

    def _stats(self, method, *args):
        """Tell [ff_stats], if configured, where a job starts and ends.  The
        statistics must never decide whether a print runs, so a failure in
        there only reaches the log."""
        stats = self.printer.lookup_object('ff_stats', None)
        if stats is None:
            return
        try:
            getattr(stats, method)(*args)
        except Exception:
            logging.exception("ff_print: ff_stats.%s failed", method)

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
        self._stats('note_job_ending', state)
        # Run the macro from a timer, not from this event: the handler runs in
        # the idle_timeout timeout path and should not block on a G-code script.
        self.reactor.update_timer(self.end_timer, self.reactor.NOW)

    def _run_end_macro(self, eventtime):
        state = self.end_state
        try:
            self.gcode.run_script('%s STATE=%s' % (self.after_macro, state))
        except Exception:
            logging.exception("ff_print: %s failed", self.after_macro)
        self._stats('note_job_finalize')
        return self.reactor.NEVER


def load_config(config):
    return FFPrint(config)

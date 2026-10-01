# Creator 5 statistics: tool changes, print time, measured filament, phases
#
# Copyright (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""Lifetime and per-job statistics that Klipper and Moonraker do not keep.

What is recorded (see docs/statistics.md for the user-facing description):

* tool changes -- how many (swaps and first pickups, per tool), how long each
  took, how often a grab or release had to be retried, and which stage failed;
* print time -- per job and over the machine's life, split into the phases a
  job passes through (prepare, homing, heating, mesh, purge, toolchange,
  print, paused, end) so that a startup or preheat optimisation can be
  measured instead of guessed;
* filament -- MEASURED per tool, not taken from print_stats.

WHY THE FILAMENT IS MEASURED HERE

print_stats derives filament_used from the G-code E position.  The Creator 5
has four logical extruders that each keep their own absolute E coordinate, and
gcode_move re-bases the G-code E position on every ACTIVATE_EXTRUDER
(gcode_move._handle_activate_extruder).  The re-base is subtracted from the
running total, so a job with many tool changes reports a fraction of what was
extruded (about a quarter on the multi-colour jobs measured on this machine).
Each PrinterExtruder, however, keeps `last_position`, its own cumulative E
coordinate in filament millimetres, which G92 and tool switches do not touch.
The net movement of that coordinate between two points in time is the
filament that tool consumed in between.  Net means extruded minus retracted:
the unload/load pair of a tool change cancels, purges and prime-tower lines
count.

Optionally (correct_print_stats, default on) print_stats.filament_used is
replaced by this measurement while a tracked job prints, which also corrects
Mainsail's dashboard and Moonraker's job history for new jobs.

HOW TIMES ARE TAKEN

Phases and tool-change durations are wall-clock times of the moment the
G-code is processed.  Klipper processes G-code up to about two seconds ahead
of the motion, so a phase boundary can be that much early; the error does
not accumulate, because every boundary has a similar lead.  A tool-change
duration starts after the in-flight motion has finished (the change waits for
it first) and ends when the last command of the change is queued, so the
short return travel to the print position is not included.

Nothing here may break a print: every entry point called from other modules
is exception-safe on the caller's side, and a failed write only logs.
"""

import functools
import json
import logging
import math
import os
import time

STATS_VERSION = 1
DEFAULT_PATH = '/usr/data/anvil-data/stats/ff_stats.json'

# The phases a job's time is divided into.  Exclusive: at any moment exactly
# one is current, so they add up to the job's total time.
PHASES = ('prepare', 'homing', 'heating', 'mesh', 'purge', 'toolchange',
          'print', 'paused', 'end')

# How a job can end.  `aborted` never reached the printing state (refused or
# failed while preparing); `interrupted` is found on disk after a power loss
# or crash; `shutdown` is a Klipper shutdown while the job was open.
OUTCOMES = ('completed', 'cancelled', 'error', 'aborted', 'interrupted',
            'shutdown')
PRINT_STATS_OUTCOME = {'complete': 'completed', 'cancelled': 'cancelled',
                       'error': 'error'}
FINISHED_STATES = tuple(PRINT_STATS_OUTCOME)

# Commands whose execution time is attributed to a phase.  Wrapped at
# klippy:connect, so macros and built-ins alike are covered without editing
# any macro.  Override with phase_commands: in [ff_stats].  Commands that are
# not registered are skipped.
DEFAULT_PHASE_COMMANDS = (
    'M109=heating, M190=heating, M191=heating, TEMPERATURE_WAIT=heating, '
    'BED_MESH_CALIBRATE=mesh, '
    '_FF_NOZZLE_CLEAN=purge, _PURGE_NEAR_OBJECT=purge, PURGE=purge, '
    '_NS_CHUTE_LIP_WIPE=purge, _FF_NOZZLE_WIPE=purge, '
    'TOOLCHANGE_PARK=toolchange, FF_AFTER_PRINT_END=end')

POLL_INTERVAL = 1.0
# A job that announced its end but was never finalised (END_PRINT killed by an
# error, say) is closed after this long, so it cannot block the next one.
ENDING_TIMEOUT = 900.
MAX_FAILURE_TEXT = 160


def _guarded(method):
    """Klipper turns an exception in an event handler into a failed start and
    one in a G-code command into a printer shutdown.  Statistics are never
    worth either: log the bug and carry on."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:
            logging.exception("ff_stats: %s failed", method.__name__)
            return None
    return wrapper


# ---------------------------------------------------------------- formatting

def fmt_duration(seconds):
    seconds = max(0, int(round(seconds or 0)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return '%dh %02dm %02ds' % (hours, minutes, secs)
    if minutes:
        return '%dm %02ds' % (minutes, secs)
    return '%ds' % secs


def fmt_short(seconds):
    """Seconds with a decimal while that is the interesting part (a tool
    change takes tens of seconds), the h/m/s form beyond two minutes."""
    seconds = max(0., seconds or 0.)
    return '%.1f s' % seconds if seconds < 120. else fmt_duration(seconds)


def fmt_length(mm):
    return '%.2f m' % ((mm or 0.) / 1000.)


def filament_grams(mm, diameter, density):
    """Mass of `mm` of filament; density in g/cm3, diameter in mm."""
    area = math.pi * (diameter / 2.) ** 2          # mm2
    return (mm or 0.) * area / 1000. * density     # mm3 -> cm3 -> g


def _parse_phase_commands(text):
    mapping = {}
    for item in text.replace('\n', ',').split(','):
        item = item.strip()
        if not item:
            continue
        cmd, sep, phase = item.partition('=')
        cmd, phase = cmd.strip().upper(), phase.strip().lower()
        if not sep or not cmd or phase not in PHASES:
            raise ValueError(
                "phase_commands entry '%s' must be COMMAND=<%s>"
                % (item, '|'.join(PHASES)))
        mapping[cmd] = phase
    return mapping


# --------------------------------------------------------------- data layout

def _zero_tools(count):
    return [0.] * count


def _new_toolchange_totals(count):
    return {
        'swaps': 0, 'pickups': 0, 'reselects': 0, 'failed': 0,
        'grab_attempts_failed': 0, 'release_attempts_failed': 0,
        'time_s': 0., 'max_s': 0.,
        'failures_by_stage': {},
        'tools': [{'picked': 0, 'time_s': 0., 'failed': 0}
                  for _ in range(count)],
    }


def _new_lifetime(count):
    return {
        'jobs': {name: 0 for name in ('started',) + OUTCOMES},
        'print_time_s': 0., 'job_time_s': 0.,
        'filament_mm': _zero_tools(count),
        'phases_s': {name: 0. for name in PHASES},
        'toolchange': _new_toolchange_totals(count),
    }


def _new_data(count):
    return {'version': STATS_VERSION, 'created': time.time(), 'next_job': 1,
            'lifetime': _new_lifetime(count), 'jobs': [], 'open_job': None}


def _fill(target, template):
    """Add every key `template` has and `target` lacks (forward compatible
    loading: a file from an older release gains the newer counters)."""
    for key, value in template.items():
        if key not in target:
            target[key] = json.loads(json.dumps(value))
        elif isinstance(value, dict) and isinstance(target[key], dict):
            _fill(target[key], value)
    return target


def _fit_list(values, count, fill):
    values = list(values or [])[:count]
    while len(values) < count:
        values.append(fill() if callable(fill) else fill)
    return values


def _new_job_toolchange():
    return {'swaps': 0, 'pickups': 0, 'failed': 0, 'attempts_failed': 0,
            'time_s': 0., 'max_s': 0.}


class FFStats:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.path = config.get('path', DEFAULT_PATH)
        self.tool_count = config.getint('tools', 4, minval=1, maxval=8)
        self.max_jobs = config.getint('max_jobs', 100, minval=0, maxval=1000)
        self.save_interval = config.getfloat('save_interval', 300.,
                                             minval=5.)
        self.job_save_interval = config.getfloat('job_save_interval', 60.,
                                                 minval=5.)
        self.default_density = config.getfloat('filament_density', 1.24,
                                               above=0.)
        self.default_diameter = config.getfloat('filament_diameter', 1.75,
                                                above=0.)
        self.correct_print_stats = config.getboolean('correct_print_stats',
                                                     True)
        try:
            self.phase_commands = _parse_phase_commands(
                config.get('phase_commands', DEFAULT_PHASE_COMMANDS))
        except ValueError as err:
            raise config.error("ff_stats: %s" % (err,))

        self.extruder_names = ['extruder' if tool == 0 else 'extruder%d' % tool
                               for tool in range(self.tool_count)]
        self.data = self._load()
        self.job = None
        self.last_job = (self.data['jobs'][-1]
                         if self.data['jobs'] else None)
        self._current_change = None
        self._dirty = False
        self._last_save = self.reactor.monotonic()
        self._timer = None
        self._wrapped = []
        self._print_stats_original = None

        self.printer.register_event_handler('klippy:connect',
                                            self._handle_connect)
        self.printer.register_event_handler('klippy:ready',
                                            self._handle_ready)
        self.printer.register_event_handler('klippy:shutdown',
                                            self._handle_shutdown)
        self.printer.register_event_handler('klippy:disconnect',
                                            self._handle_disconnect)
        self.printer.register_event_handler('homing:home_rails_begin',
                                            self._handle_homing_begin)
        self.printer.register_event_handler('homing:home_rails_end',
                                            self._handle_homing_end)
        self.gcode.register_command(
            'FF_STATS_SHOW', self.cmd_FF_STATS_SHOW,
            desc=self.cmd_FF_STATS_SHOW_help)
        self.gcode.register_command(
            'FF_STATS_PHASE', self.cmd_FF_STATS_PHASE,
            desc=self.cmd_FF_STATS_PHASE_help)
        self.gcode.register_command(
            'FF_STATS_RESET', self.cmd_FF_STATS_RESET,
            desc=self.cmd_FF_STATS_RESET_help)

    # ------------------------------------------------------------ persistence

    def _load(self):
        count = self.tool_count
        data = None
        try:
            with open(self.path, 'r') as handle:
                data = json.load(handle)
            if (not isinstance(data, dict)
                    or data.get('version') != STATS_VERSION):
                raise ValueError("unsupported statistics file layout")
        except FileNotFoundError:
            data = None
        except Exception as err:
            logging.exception("ff_stats: cannot read %s", self.path)
            self._set_aside(err)
            data = None
        if data is None:
            return _new_data(count)
        _fill(data, _new_data(count))
        life = data['lifetime']
        life['filament_mm'] = _fit_list(life['filament_mm'], count, 0.)
        tools = life['toolchange']['tools']
        life['toolchange']['tools'] = _fit_list(
            tools, count, lambda: {'picked': 0, 'time_s': 0., 'failed': 0})
        if not isinstance(data['jobs'], list):
            data['jobs'] = []
        return data

    def _set_aside(self, err):
        """Keep an unreadable file for inspection instead of overwriting it."""
        try:
            aside = '%s.corrupt-%d' % (self.path, int(time.time()))
            os.replace(self.path, aside)
            logging.error("ff_stats: moved unreadable %s to %s (%s)",
                          self.path, aside, err)
        except OSError:
            logging.exception("ff_stats: cannot move %s aside", self.path)

    def save(self, now=None):
        """Write the statistics atomically.  Never raises."""
        self._dirty = False
        self._last_save = self.reactor.monotonic() if now is None else now
        if not self.path:
            return False
        try:
            self.data['saved'] = time.time()
            self.data['open_job'] = (self._job_record('open', self._last_save)
                                     if self.job is not None else None)
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp = self.path + '.tmp'
            with open(tmp, 'w') as handle:
                json.dump(self.data, handle, indent=1, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
            return True
        except Exception:
            logging.exception("ff_stats: cannot write %s", self.path)
            return False

    def _recover_open_job(self):
        """A job still marked open on disk was cut short by a power loss or a
        crash: keep what was recorded, as `interrupted`."""
        record = self.data.get('open_job')
        if not record:
            return
        self.data['open_job'] = None
        record['status'] = 'interrupted'
        record['end'] = self.data.get('saved', record.get('start'))
        self._fold(record)
        logging.warning("ff_stats: job %s was interrupted (power loss or "
                        "crash); recorded as such", record.get('id'))

    # ------------------------------------------------------------------ setup

    @_guarded
    def _handle_connect(self):
        for cmd, phase in sorted(self.phase_commands.items()):
            self._wrap(cmd, phase)
        if self.correct_print_stats:
            self._install_print_stats_fix()
        names = self._extruder_names_from_toolchanger()
        if names:
            self.extruder_names = names

    @_guarded
    def _handle_ready(self):
        self._recover_open_job()
        self._dirty = True
        if self._timer is None:
            self._timer = self.reactor.register_timer(
                self._tick, self.reactor.NOW)

    @_guarded
    def _handle_shutdown(self):
        self._close_job('shutdown', 'klippy shutdown')
        self.save()

    @_guarded
    def _handle_disconnect(self):
        self._close_job('interrupted', 'klippy stopped')
        self.save()

    def _extruder_names_from_toolchanger(self):
        toolchanger = self.printer.lookup_object('ff_toolchange', None)
        tools = getattr(toolchanger, 'tools', None)
        try:
            names = [tool.extruder_name for tool in tools][:self.tool_count]
        except (TypeError, AttributeError):
            return None
        return names if len(names) == self.tool_count else None

    def _wrap(self, cmd, phase):
        """Run `cmd` inside `phase`.  Same take-over/chain pattern ff_print
        uses for SDCARD_PRINT_FILE: unregister, keep the old handler, register
        a wrapper that calls it."""
        gcode = self.gcode
        when_not_ready = cmd in getattr(gcode, 'base_gcode_handlers', {})
        help_text = getattr(gcode, 'gcode_help', {}).get(cmd)
        previous = gcode.register_command(cmd, None)
        if previous is None:
            return False
        stats = self

        def handler(gcmd, _previous=previous, _phase=phase):
            stats.push_phase(_phase)
            try:
                return _previous(gcmd)
            finally:
                stats.pop_phase(_phase)

        gcode.register_command(cmd, handler, when_not_ready=when_not_ready,
                               desc=help_text)
        self._wrapped.append(cmd)
        return True

    def _install_print_stats_fix(self):
        stats_object = self.printer.lookup_object('print_stats', None)
        original = getattr(stats_object, '_update_filament_usage', None)
        if original is None:
            return False
        stats = self

        def corrected(eventtime):
            try:
                value = stats.print_filament_mm()
            except Exception:
                logging.exception("ff_stats: filament correction failed")
                value = None
            if value is None:
                return original(eventtime)
            stats_object.filament_used = value

        stats_object._update_filament_usage = corrected
        self._print_stats_original = original
        return True

    # ----------------------------------------------------------------- phases

    def _current_phase(self):
        job = self.job
        if job is None:
            return None
        return job['stack'][-1] if job['stack'] else job['base']

    def _account(self, now):
        job = self.job
        if job is None:
            return
        delta = now - job['last']
        if delta > 0.:
            phase = self._current_phase()
            job['phases'][phase] = job['phases'].get(phase, 0.) + delta
        job['last'] = now

    @_guarded
    def push_phase(self, name):
        if self.job is None:
            return
        self._account(self.reactor.monotonic())
        self.job['stack'].append(name)

    @_guarded
    def pop_phase(self, name):
        job = self.job
        if job is None:
            return
        self._account(self.reactor.monotonic())
        for index in range(len(job['stack']) - 1, -1, -1):
            if job['stack'][index] == name:
                del job['stack'][index]
                return

    def _set_base(self, name):
        job = self.job
        if job is None or job['base'] == name:
            return
        self._account(self.reactor.monotonic())
        job['base'] = name

    @_guarded
    def _handle_homing_begin(self, *args):
        self.push_phase('homing')

    @_guarded
    def _handle_homing_end(self, *args):
        self.pop_phase('homing')

    # --------------------------------------------------------------- filament

    def _sample_e(self):
        """Every tool's cumulative E coordinate (None where unavailable)."""
        out = []
        for name in self.extruder_names[:self.tool_count]:
            extruder = self.printer.lookup_object(name, None)
            position = getattr(extruder, 'last_position', None)
            out.append(float(position) if position is not None else None)
        return out

    @staticmethod
    def _net(now, base):
        """Net filament per tool between two samples, never negative (a final
        retract on a tool that printed nothing is not consumption)."""
        out = []
        for current, start in zip(now, base):
            if current is None or start is None:
                out.append(0.)
            else:
                out.append(max(0., current - start))
        return out

    def print_filament_mm(self):
        """Filament since the printing state began, for print_stats.  None
        when no tracked job is printing (the original logic then applies)."""
        job = self.job
        if job is None or job.get('e_print_base') is None:
            return None
        return sum(self._net(self._sample_e(), job['e_print_base']))

    # -------------------------------------------------------------------- jobs

    def _slicer_header(self, path):
        """Filament density/diameter per tool from the file's first lines."""
        out = {}
        if not path:
            return out
        try:
            with open(path, 'r', errors='replace') as handle:
                head = handle.read(8192)
        except OSError:
            return out
        for key in ('filament_density', 'filament_diameter'):
            marker = '; %s:' % key
            at = head.find(marker)
            if at < 0:
                continue
            line = head[at + len(marker):].split('\n', 1)[0]
            try:
                values = [float(item) for item in line.split(',')
                          if item.strip()]
            except ValueError:
                continue
            if values:
                out[key.split('_', 1)[1]] = values
        return out

    def _start_job(self, path, origin, managed, now):
        if self.job is not None:
            self._close_job('interrupted', 'superseded by a new job')
        number = self.data['next_job']
        self.data['next_job'] = number + 1
        self.data['lifetime']['jobs']['started'] += 1
        header = self._slicer_header(path)
        self.job = {
            'id': number,
            'file': os.path.basename(path) if path else '',
            'origin': origin or '',
            'managed': managed,
            'start_wall': time.time(),
            'start': now, 'last': now,
            'base': 'prepare', 'stack': [],
            'phases': {name: 0. for name in PHASES},
            'e_base': self._sample_e(),
            'e_print_base': None,
            'prepare_mm': None,
            'toolchange': _new_job_toolchange(),
            'slicer': header,
            'ending': None, 'ending_at': None,
            'print_s': 0.,
        }
        self._dirty = True
        return self.job

    def note_job_prepare(self, path, origin):
        """ff_print: a print was requested; the start macro is about to run."""
        self._start_job(path, origin, True, self.reactor.monotonic())

    def note_job_aborted(self, reason):
        """ff_print: the start macro or the file load refused the print."""
        self._close_job('aborted', reason)

    def note_job_ending(self, state):
        """ff_print: print_stats left the printing state; the end macro runs."""
        job = self.job
        if job is None or job['ending']:
            return
        self._begin_ending(PRINT_STATS_OUTCOME.get(state, 'error'))

    def note_job_finalize(self):
        """ff_print: the end macro has finished."""
        job = self.job
        if job is None:
            return
        self._close_job(job['ending'] or 'completed')

    def _begin_ending(self, outcome):
        job = self.job
        self._account(self.reactor.monotonic())
        status = self._print_stats_status()
        if status is not None:
            job['print_s'] = float(status.get('print_duration') or 0.)
        job['ending'] = outcome
        job['ending_at'] = self.reactor.monotonic()
        job['base'] = 'end'

    def _print_stats_status(self):
        stats_object = self.printer.lookup_object('print_stats', None)
        if stats_object is None:
            return None
        try:
            return stats_object.get_status(self.reactor.monotonic())
        except Exception:
            logging.exception("ff_stats: cannot read print_stats")
            return None

    def _job_record(self, status, now, message=''):
        """The job as a JSON-ready record; does not close the job."""
        job = self.job
        self._account(now)
        filament = self._net(self._sample_e(), job['e_base'])
        prepare = job['prepare_mm']
        if prepare is None:
            prepare = list(filament)
        return {
            'id': job['id'], 'file': job['file'], 'origin': job['origin'],
            'status': status, 'message': message[:MAX_FAILURE_TEXT],
            'start': job['start_wall'],
            'end': time.time(),
            'total_s': now - job['start'],
            'print_s': job['print_s'],
            'phases_s': {name: round(job['phases'].get(name, 0.), 3)
                         for name in PHASES},
            'filament_mm': [round(value, 3) for value in filament],
            'prepare_mm': [round(value, 3) for value in prepare],
            'toolchange': dict(job['toolchange']),
            'slicer': job['slicer'],
        }

    def _close_job(self, status, message=''):
        job = self.job
        if job is None:
            return None
        now = self.reactor.monotonic()
        if job['ending'] is None and status in ('completed', 'cancelled',
                                                'error'):
            job['ending'] = status
        if not job['print_s']:
            reading = self._print_stats_status()
            if reading is not None and reading.get('state') in (
                    FINISHED_STATES + ('printing', 'paused')):
                job['print_s'] = float(reading.get('print_duration') or 0.)
        record = self._job_record(status, now, message)
        self.job = None
        self._fold(record)
        self.save(now)
        return record

    def _fold(self, record):
        """Add a finished job to the lifetime totals and the job list."""
        life = self.data['lifetime']
        status = record.get('status', 'error')
        if status not in OUTCOMES:
            status = 'error'
        life['jobs'][status] += 1
        life['print_time_s'] += record.get('print_s', 0.)
        life['job_time_s'] += record.get('total_s', 0.)
        life['filament_mm'] = [
            total + value for total, value in zip(
                life['filament_mm'],
                _fit_list(record.get('filament_mm'), self.tool_count, 0.))]
        for name in PHASES:
            life['phases_s'][name] += record.get('phases_s', {}).get(name, 0.)
        self.data['jobs'].append(record)
        if self.max_jobs <= 0:
            self.data['jobs'] = []
        elif len(self.data['jobs']) > self.max_jobs:
            del self.data['jobs'][:-self.max_jobs]
        self.last_job = record
        self._dirty = True

    # ---------------------------------------------------------- tool changes

    def toolchange_begin(self, tool):
        """ff_toolchange: a change to `tool` starts (the previous motion has
        finished).  Returns the token to hand to toolchange_end()."""
        self.push_phase('toolchange')
        token = {'tool': tool, 'start': self.reactor.monotonic(),
                 'attempts_failed': 0}
        self._current_change = token
        return token

    def note_failed_attempt(self, kind, tool):
        """ff_toolchange: one grab or release attempt did not succeed (the last
        one of a failed change counts too)."""
        totals = self.data['lifetime']['toolchange']
        key = ('release_attempts_failed' if kind == 'release'
               else 'grab_attempts_failed')
        totals[key] += 1
        if self._current_change is not None:
            self._current_change['attempts_failed'] += 1
        if self.job is not None:
            self.job['toolchange']['attempts_failed'] += 1
        self._dirty = True

    def toolchange_end(self, token, from_tool, to_tool, ok, stage=None,
                       error=None):
        self.pop_phase('toolchange')
        if token is None:
            return
        if self._current_change is token:
            self._current_change = None
        duration = max(0., self.reactor.monotonic() - token['start'])
        totals = self.data['lifetime']['toolchange']
        job_totals = self.job['toolchange'] if self.job is not None else None
        index = to_tool if 0 <= to_tool < self.tool_count else None
        if not ok:
            totals['failed'] += 1
            where = stage or 'other'
            totals['failures_by_stage'][where] = \
                totals['failures_by_stage'].get(where, 0) + 1
            if index is not None:
                totals['tools'][index]['failed'] += 1
            if job_totals is not None:
                job_totals['failed'] += 1
            totals['last_failure'] = {
                'tool': to_tool, 'stage': where, 'when': time.time(),
                'message': (error or '')[:MAX_FAILURE_TEXT]}
            self._dirty = True
            return
        if from_tool == to_tool:
            totals['reselects'] += 1
            self._dirty = True
            return
        kind = 'pickups' if from_tool is None or from_tool < 0 else 'swaps'
        totals[kind] += 1
        totals['time_s'] += duration
        totals['max_s'] = max(totals['max_s'], duration)
        if index is not None:
            totals['tools'][index]['picked'] += 1
            totals['tools'][index]['time_s'] += duration
        if job_totals is not None:
            job_totals[kind] += 1
            job_totals['time_s'] += duration
            job_totals['max_s'] = max(job_totals['max_s'], duration)
        self._dirty = True

    # ------------------------------------------------------------------ timer

    def _tick(self, eventtime):
        try:
            self._poll(eventtime)
        except Exception:
            logging.exception("ff_stats: poll failed")
        return eventtime + POLL_INTERVAL

    def _poll(self, eventtime):
        now = self.reactor.monotonic()
        # The attributes, not get_status(): that call also runs print_stats'
        # own filament update, and polling must not have side effects.
        stats_object = self.printer.lookup_object('print_stats', None)
        state = getattr(stats_object, 'state', None)
        job = self.job
        if job is None and state == 'printing':
            # A print that did not come through ff_print: track it from here.
            path = getattr(stats_object, 'filename', '') or ''
            job = self._start_job(path, 'print_stats', False, now)
            job['base'] = 'print'
            job['prepare_mm'] = _zero_tools(self.tool_count)
            job['e_print_base'] = list(job['e_base'])
        if job is not None:
            self._follow_print_state(job, state, now)
        # While a job runs the open-job snapshot goes out on its own schedule:
        # nothing marks the data dirty during a long print, and a power loss
        # must find a snapshot that is a minute old, not one from job start.
        interval = (self.job_save_interval if self.job is not None
                    else self.save_interval)
        if ((self._dirty or self.job is not None)
                and now - self._last_save >= interval):
            self.save(now)

    def _follow_print_state(self, job, state, now):
        if job['ending']:
            if (not job['managed']
                    or now - (job['ending_at'] or now) > ENDING_TIMEOUT):
                self._close_job(job['ending'])
            return
        if state == 'printing':
            if job['e_print_base'] is None:
                self._account(now)
                sample = self._sample_e()
                job['e_print_base'] = sample
                job['prepare_mm'] = self._net(sample, job['e_base'])
            self._set_base('print')
        elif state == 'paused':
            self._set_base('paused')
        elif state in FINISHED_STATES and job['e_print_base'] is not None:
            self._begin_ending(PRINT_STATS_OUTCOME[state])
            if not job['managed']:
                self._close_job(job['ending'])

    # ------------------------------------------------------------------- status

    def get_status(self, eventtime):
        try:
            return self._status(eventtime)
        except Exception:
            logging.exception("ff_stats: status failed")
            return {'error': 'see klippy.log'}

    def _status(self, eventtime):
        life = self.data['lifetime']
        totals = life['toolchange']
        changes = totals['swaps'] + totals['pickups']
        job = self.job
        live = None
        if job is not None:
            live = {
                'id': job['id'], 'file': job['file'],
                'phase': self._current_phase(),
                'elapsed_s': round(self.reactor.monotonic() - job['start'], 1),
                'toolchanges': job['toolchange']['swaps']
                + job['toolchange']['pickups'],
                'filament_mm': [
                    round(v, 1) for v in self._net(self._sample_e(),
                                                   job['e_base'])],
            }
        last = self.last_job
        return {
            'jobs': dict(life['jobs']),
            'print_time_s': round(life['print_time_s'], 1),
            'job_time_s': round(life['job_time_s'], 1),
            'filament_mm': [round(v, 1) for v in life['filament_mm']],
            'toolchange': {
                'count': changes, 'swaps': totals['swaps'],
                'pickups': totals['pickups'], 'failed': totals['failed'],
                'attempts_failed': totals['grab_attempts_failed']
            + totals['release_attempts_failed'],
                'avg_s': round(totals['time_s'] / changes, 2)
                if changes else 0.,
                'max_s': round(totals['max_s'], 2),
            },
            'phases_s': {k: round(v, 1) for k, v in life['phases_s'].items()},
            'job': live,
            'last_job': None if last is None else {
                'id': last.get('id'), 'file': last.get('file'),
                'status': last.get('status'),
                'total_s': round(last.get('total_s', 0.), 1),
                'print_s': round(last.get('print_s', 0.), 1),
                'filament_mm': last.get('filament_mm'),
            },
            'corrects_print_stats': self._print_stats_original is not None,
        }

    # ----------------------------------------------------------------- reports

    def _filament_totals(self, values, slicer=None):
        """(metres-as-mm total, estimated grams) for per-tool lengths.  The
        mass uses the sliced file's own density/diameter when it names them,
        otherwise the extruder's diameter and the configured density."""
        diameters = self._diameters(slicer)
        densities = (slicer or {}).get('density') or []
        grams = 0.
        for tool, mm in enumerate(values):
            density = (densities[tool] if tool < len(densities)
                       else self.default_density)
            diameter = (diameters[tool] if tool < len(diameters)
                        else diameters[-1])
            grams += filament_grams(mm, diameter, density)
        return sum(values), grams

    def _diameters(self, slicer=None):
        listed = (slicer or {}).get('diameter')
        if listed:
            return listed
        values = []
        for name in self.extruder_names[:self.tool_count]:
            extruder = self.printer.lookup_object(name, None)
            area = getattr(extruder, 'filament_area', None)
            values.append(math.sqrt(area / math.pi) * 2.
                          if area else self.default_diameter)
        return values or [self.default_diameter]

    def summary_text(self):
        life = self.data['lifetime']
        jobs = life['jobs']
        totals = life['toolchange']
        changes = totals['swaps'] + totals['pickups']
        total_mm, grams = self._filament_totals(life['filament_mm'])
        since = time.strftime('%Y-%m-%d', time.localtime(
            self.data.get('created', time.time())))
        lines = ["Statistics since %s (counted by this printer)" % since]
        outcome = ', '.join('%d %s' % (jobs[name], name)
                            for name in OUTCOMES if jobs[name])
        lines.append("Jobs: %d started%s" % (
            jobs['started'], (' | ' + outcome) if outcome else ''))
        lines.append("Print time: %s (jobs in total %s)" % (
            fmt_duration(life['print_time_s']),
            fmt_duration(life['job_time_s'])))
        lines.append("Filament: %s (about %.0f g)" % (
            fmt_length(total_mm), grams))
        lines.append("Per tool: " + ' | '.join(
            'T%d %s' % (tool, fmt_length(mm))
            for tool, mm in enumerate(life['filament_mm'])))
        avg = totals['time_s'] / changes if changes else 0.
        lines.append(
            "Tool changes: %d (%d swaps, %d first pickups) | avg %s, "
            "slowest %s" % (changes, totals['swaps'], totals['pickups'],
                            fmt_short(avg), fmt_short(totals['max_s'])))
        lines.append("Failed changes: %d | failed attempts: %d grab, %d release%s" % (
            totals['failed'], totals['grab_attempts_failed'],
            totals['release_attempts_failed'],
            (' | by stage: ' + ', '.join(
                '%s %d' % item for item in sorted(
                    totals['failures_by_stage'].items())))
            if totals['failures_by_stage'] else ''))
        lines.append("Changes into: " + ' | '.join(
            'T%d %d' % (tool, entry['picked'])
            for tool, entry in enumerate(totals['tools'])))
        lines.append(self._phase_line(life['phases_s'], life['job_time_s']))
        lines.append("Details: FF_STATS_JOB, FF_STATS_JOBS COUNT=10")
        return '\n'.join(lines)

    @staticmethod
    def _phase_line(phases, total):
        parts = []
        for name in PHASES:
            seconds = phases.get(name, 0.)
            if seconds < 0.5:
                continue
            share = (100. * seconds / total) if total else 0.
            parts.append('%s %s (%.0f%%)' % (name, fmt_duration(seconds),
                                             share))
        if not parts:
            return "Time by phase: none yet"
        # three to a line: Mainsail wraps long lines, but badly
        rows = [' | '.join(parts[start:start + 3])
                for start in range(0, len(parts), 3)]
        return "Time by phase: " + '\n'.join(rows)

    def job_text(self, record=None, live=False):
        if record is None:
            if self.job is not None:
                record = self._job_record('running', self.reactor.monotonic())
                live = True
            elif self.last_job is not None:
                record = self.last_job
            else:
                return "No job has been recorded yet."
        slicer = record.get('slicer') or {}
        mm = _fit_list(record.get('filament_mm'), self.tool_count, 0.)
        prep = _fit_list(record.get('prepare_mm'), self.tool_count, 0.)
        total_mm, grams = self._filament_totals(mm, slicer)
        changes = record['toolchange']['swaps'] + record['toolchange']['pickups']
        started = time.strftime('%Y-%m-%d %H:%M',
                                time.localtime(record.get('start', 0)))
        head = "Job %s%s: %s" % (record.get('id'),
                                 ' (running)' if live else '',
                                 record.get('file') or '(unknown file)')
        lines = [head,
                 "Started %s | %s | total %s | printing %s" % (
                     started, record.get('status'),
                     fmt_duration(record.get('total_s')),
                     fmt_duration(record.get('print_s')))]
        if record.get('message'):
            lines.append("Note: %s" % record['message'])
        lines.append("Filament: %s (about %.0f g) | before print start %s" % (
            fmt_length(total_mm), grams, fmt_length(sum(prep))))
        used = ' | '.join('T%d %s' % (tool, fmt_length(value))
                          for tool, value in enumerate(mm) if value > 0.)
        lines.append("Per tool: " + (used or "none"))
        tc = record['toolchange']
        lines.append(
            "Tool changes: %d (%d swaps, %d first pickups) | time %s, slowest "
            "%s | "
            "failed %d, failed attempts %d" % (
                changes, tc['swaps'], tc['pickups'], fmt_duration(tc['time_s']),
                fmt_short(tc['max_s']), tc['failed'], tc['attempts_failed']))
        lines.append(self._phase_line(record.get('phases_s', {}),
                                      record.get('total_s', 0.)))
        return '\n'.join(lines)

    def jobs_text(self, count):
        jobs = self.data['jobs'][-count:] if count > 0 else []
        if not jobs:
            return "No jobs recorded yet."
        lines = ["Last %d of %d recorded jobs (newest last):" % (
            len(jobs), len(self.data['jobs']))]
        for record in jobs:
            total_mm = sum(record.get('filament_mm') or [0.])
            tc = record['toolchange']
            lines.append("#%s %s | %s | %s | %s | %d changes | %s" % (
                record.get('id'),
                time.strftime('%m-%d %H:%M',
                              time.localtime(record.get('start', 0))),
                record.get('status'), fmt_duration(record.get('total_s')),
                fmt_length(total_mm), tc['swaps'] + tc['pickups'],
                (record.get('file') or '')[:32]))
        return '\n'.join(lines)

    # ------------------------------------------------------------------ commands

    cmd_FF_STATS_SHOW_help = ("Print statistics: WHAT=SUMMARY|JOB|JOBS "
                              "[COUNT=10]")

    def cmd_FF_STATS_SHOW(self, gcmd):
        what = gcmd.get('WHAT', 'SUMMARY').upper()
        count = gcmd.get_int('COUNT', 10, minval=1, maxval=100)
        try:
            text = self._report(what, count)
        except ValueError as err:
            raise gcmd.error("FF_STATS_SHOW: %s" % (err,))
        except Exception:
            # An exception that is not a command error is a printer
            # shutdown in Klipper; a bug in a report must not be one.
            logging.exception("ff_stats: FF_STATS_SHOW failed")
            raise gcmd.error("FF_STATS_SHOW: internal error, see klippy.log")
        gcmd.respond_info(text)

    def _report(self, what, count):
        if what == 'SUMMARY':
            return self.summary_text()
        if what == 'JOB':
            return self.job_text()
        if what == 'JOBS':
            return self.jobs_text(count)
        raise ValueError("WHAT must be SUMMARY, JOB or JOBS, got '%s'" % what)

    cmd_FF_STATS_RESET_help = ("Erase ALL statistics and start counting "
                               "again: FF_STATS_RESET CONFIRM=1 (the old "
                               "file is kept)")

    def cmd_FF_STATS_RESET(self, gcmd):
        if not gcmd.get_int('CONFIRM', 0, minval=0, maxval=1):
            raise gcmd.error(
                "FF_STATS_RESET: this erases every counter and the job list. "
                "Run FF_STATS_RESET CONFIRM=1 to do it; the old file is kept "
                "next to the new one.")
        if self.job is not None:
            raise gcmd.error("FF_STATS_RESET: not while a job is running")
        try:
            kept = self._reset()
        except Exception:
            logging.exception("ff_stats: reset failed")
            raise gcmd.error("FF_STATS_RESET: failed, see klippy.log")
        gcmd.respond_info("Statistics reset." + (
            " The previous file was kept as %s" % os.path.basename(kept)
            if kept else ""))

    def _reset(self):
        """Start from zero.  The old file is renamed, never deleted: a running
        Klipper would write its in-memory totals straight back, so deleting
        the file by hand does not reset anything."""
        kept = None
        if self.path and os.path.exists(self.path):
            kept = '%s.reset-%d' % (self.path, int(time.time()))
            os.replace(self.path, kept)
        self.data = _new_data(self.tool_count)
        self.last_job = None
        self._dirty = True
        self.save()
        return kept

    cmd_FF_STATS_PHASE_help = ("Mark a phase of the running job: NAME=<phase> "
                               "starts it, END=1 ends it (for custom macros)")

    def cmd_FF_STATS_PHASE(self, gcmd):
        name = gcmd.get('NAME').lower()
        if name not in PHASES:
            raise gcmd.error("FF_STATS_PHASE: NAME must be one of %s"
                             % ', '.join(PHASES))
        if gcmd.get_int('END', 0, minval=0, maxval=1):
            self.pop_phase(name)
        else:
            self.push_phase(name)


def load_config(config):
    return FFStats(config)

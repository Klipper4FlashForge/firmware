"""Statistics module (ff_stats): bookkeeping, persistence, phases, hooks."""

import configparser
import importlib.util
import json
import re
import types

import jinja2
import pytest

from lib.paths import ROOT


EXTRAS = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" /
          "klippy" / "extras")
STATS = EXTRAS / "ff_stats.py"
TOOLCHANGE = EXTRAS / "ff_toolchange.py"
PRINT = EXTRAS / "ff_print.py"
N4S4 = (ROOT / "pkgs" / "klipper-config" / "payload" / "config" /
        "printer_n4s4.cfg")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load("test_ff_stats_module", STATS)


@pytest.fixture(scope="module")
def ff_toolchange():
    return _load("test_ff_stats_toolchange", TOOLCHANGE)


# ------------------------------------------------------------------- fakes

class FakeReactor:
    NOW = 0.
    NEVER = 9999999999.

    def __init__(self):
        self.now = 1000.
        self.timers = []

    def monotonic(self):
        return self.now

    def register_timer(self, callback, when):
        self.timers.append(callback)
        return callback

    def advance(self, seconds):
        self.now += seconds


class FakeGcmd:
    def __init__(self, **params):
        self.params = {key.upper(): value for key, value in params.items()}
        self.responses = []

    @staticmethod
    def error(message):
        return RuntimeError(message)

    def get(self, name, default=None):
        value = self.params.get(name, default)
        if value is None:
            raise RuntimeError("missing %s" % name)
        return value

    # The real GCodeCommand spells it get_int (getint belongs to the config
    # object).  Only the real name exists here, so a slip is caught.
    def get_int(self, name, default=None, minval=None, maxval=None):
        return int(self.params.get(name, default))

    def respond_info(self, message):
        self.responses.append(message)


class FakeGcode:
    def __init__(self):
        self.ready_handlers = {}
        self.base_gcode_handlers = {}
        self.gcode_help = {}

    def register_command(self, cmd, func, when_not_ready=False, desc=None):
        if func is None:
            self.base_gcode_handlers.pop(cmd, None)
            return self.ready_handlers.pop(cmd, None)
        assert cmd not in self.ready_handlers, "%s registered twice" % cmd
        self.ready_handlers[cmd] = func
        if when_not_ready:
            self.base_gcode_handlers[cmd] = func
        if desc is not None:
            self.gcode_help[cmd] = desc

    def run(self, cmd, **params):
        gcmd = FakeGcmd(**params)
        self.ready_handlers[cmd](gcmd)
        return gcmd


class FakeExtruder:
    def __init__(self, diameter=1.75):
        self.last_position = 0.
        self.filament_area = 3.14159265 * (diameter / 2.) ** 2


class FakePrintStats:
    """Klipper's print_stats, reduced to what ff_stats touches."""

    def __init__(self):
        self.state = 'standby'
        self.filename = ''
        self.print_duration = 0.
        self.filament_used = 0.
        self.original_calls = 0

    def _update_filament_usage(self, eventtime):
        self.original_calls += 1
        self.filament_used += 1.

    def get_status(self, eventtime):
        if self.state == 'printing':
            self._update_filament_usage(eventtime)
        return {'state': self.state, 'filename': self.filename,
                'print_duration': self.print_duration,
                'filament_used': self.filament_used}


class FakeConfig:
    class error(Exception):
        pass

    def __init__(self, printer, options):
        self.printer = printer
        self.options = options

    def get_printer(self):
        return self.printer

    def get(self, name, default=None):
        return self.options.get(name, default)

    def getint(self, name, default=None, minval=None, maxval=None):
        return int(self.options.get(name, default))

    def getfloat(self, name, default=None, minval=None, above=None):
        return float(self.options.get(name, default))

    def getboolean(self, name, default=None):
        return bool(self.options.get(name, default))


class FakePrinter:
    def __init__(self):
        self.reactor = FakeReactor()
        self.objects = {'gcode': FakeGcode()}
        self.handlers = {}

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def register_event_handler(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)

    def send_event(self, event, *args):
        for callback in self.handlers.get(event, []):
            callback(*args)


@pytest.fixture
def rig(mod, tmp_path):
    """An FFStats on a fake printer with four extruders and a print_stats."""
    printer = FakePrinter()
    printer.objects['print_stats'] = FakePrintStats()
    for tool in range(4):
        printer.objects['extruder' if tool == 0 else 'extruder%d' % tool] = \
            FakeExtruder()
    path = tmp_path / "stats" / "ff_stats.json"
    config = FakeConfig(printer, {'path': str(path)})
    stats = mod.FFStats(config)
    printer.objects['ff_stats'] = stats
    rig = types.SimpleNamespace(
        printer=printer, reactor=printer.reactor, stats=stats, path=path,
        gcode=printer.objects['gcode'], ps=printer.objects['print_stats'],
        mod=mod, config=config)
    rig.extruder = lambda tool: printer.objects[
        'extruder' if tool == 0 else 'extruder%d' % tool]
    rig.send = printer.send_event
    return rig


def _run_job(rig, tool_moves, end='complete'):
    """A representative job; returns the finished record."""
    stats, reactor, ps = rig.stats, rig.reactor, rig.ps
    stats.note_job_prepare("/gcodes/cubes.gcode", "SDCARD_PRINT_FILE")
    reactor.advance(10.)                                  # prepare
    rig.extruder(0).last_position += 50.                  # start-up clean
    stats.push_phase('heating')
    reactor.advance(30.)
    stats.pop_phase('heating')
    ps.state = 'printing'
    stats._poll(reactor.monotonic())
    for tool, mm, seconds in tool_moves:
        reactor.advance(seconds)
        rig.extruder(tool).last_position += mm
    ps.print_duration = 99.
    ps.state = end
    stats.note_job_ending(end)
    reactor.advance(5.)                                   # END_PRINT
    stats.note_job_finalize()
    return stats.last_job


# ------------------------------------------------------------ configuration

def test_default_phase_commands_are_valid(mod):
    mapping = mod._parse_phase_commands(mod.DEFAULT_PHASE_COMMANDS)
    assert mapping['M109'] == 'heating'
    assert mapping['BED_MESH_CALIBRATE'] == 'mesh'
    assert mapping['FF_AFTER_PRINT_END'] == 'end'
    assert set(mapping.values()) <= set(mod.PHASES)


@pytest.mark.parametrize("text", ["M109", "M109=warming", "=heating"])
def test_a_bad_phase_command_is_a_config_error(mod, text):
    printer = FakePrinter()
    config = FakeConfig(printer, {'phase_commands': text})
    with pytest.raises(FakeConfig.error, match="phase_commands"):
        mod.FFStats(config)


# ----------------------------------------------------------------- the job

def test_job_records_phases_filament_and_outcome(rig):
    record = _run_job(rig, [(0, 100., 60.), (1, 200., 60.)])

    assert record['status'] == 'completed'
    assert record['file'] == 'cubes.gcode'
    assert record['filament_mm'] == [150., 200., 0., 0.]
    assert record['prepare_mm'] == [50., 0., 0., 0.]
    assert record['print_s'] == 99.
    phases = record['phases_s']
    assert phases['heating'] == 30.
    assert phases['prepare'] == 10.
    assert phases['print'] == 120.
    assert phases['end'] == 5.
    assert sum(phases.values()) == pytest.approx(record['total_s'])

    life = rig.stats.data['lifetime']
    assert life['jobs']['started'] == 1
    assert life['jobs']['completed'] == 1
    assert life['filament_mm'] == [150., 200., 0., 0.]
    assert life['print_time_s'] == 99.
    assert life['phases_s']['print'] == 120.


def test_an_unused_tool_that_only_retracted_counts_zero(rig):
    record = _run_job(rig, [(2, -1., 5.)])
    assert record['filament_mm'][2] == 0.


def test_cancelled_and_failed_jobs_are_counted_as_such(rig):
    _run_job(rig, [(0, 10., 5.)], end='cancelled')
    _run_job(rig, [(0, 10., 5.)], end='error')
    jobs = rig.stats.data['lifetime']['jobs']
    assert (jobs['started'], jobs['cancelled'], jobs['error']) == (2, 1, 1)


def test_a_refused_print_is_recorded_as_aborted(rig):
    rig.stats.note_job_prepare("/gcodes/x.gcode", "SDCARD_PRINT_FILE")
    rig.reactor.advance(3.)
    rig.stats.note_job_aborted("tool T2 is not calibrated")
    jobs = rig.stats.data['lifetime']['jobs']
    assert jobs['aborted'] == 1
    assert rig.stats.job is None
    assert rig.stats.last_job['message'] == "tool T2 is not calibrated"


def test_pause_time_is_its_own_phase(rig):
    stats, reactor, ps = rig.stats, rig.reactor, rig.ps
    stats.note_job_prepare("/gcodes/p.gcode", "SDCARD_PRINT_FILE")
    ps.state = 'printing'
    stats._poll(reactor.monotonic())
    reactor.advance(20.)
    ps.state = 'paused'
    stats._poll(reactor.monotonic())
    reactor.advance(300.)
    ps.state = 'printing'
    stats._poll(reactor.monotonic())
    reactor.advance(10.)
    ps.state = 'complete'
    stats.note_job_ending('complete')
    stats.note_job_finalize()
    phases = stats.last_job['phases_s']
    assert (phases['print'], phases['paused']) == (30., 300.)


def test_a_new_job_closes_a_forgotten_one_as_interrupted(rig):
    rig.stats.note_job_prepare("/gcodes/a.gcode", "M23")
    rig.stats.note_job_prepare("/gcodes/b.gcode", "SDCARD_PRINT_FILE")
    assert rig.stats.data['lifetime']['jobs']['interrupted'] == 1
    assert rig.stats.job['file'] == 'b.gcode'


def test_a_print_that_bypassed_ff_print_is_still_tracked(rig):
    rig.ps.state = 'printing'
    rig.ps.filename = 'manual.gcode'
    rig.stats._poll(rig.reactor.monotonic())
    assert rig.stats.job is not None and not rig.stats.job['managed']
    rig.extruder(1).last_position += 40.
    rig.reactor.advance(60.)
    rig.ps.print_duration = 60.
    rig.ps.state = 'complete'
    rig.stats._poll(rig.reactor.monotonic())
    assert rig.stats.job is None
    assert rig.stats.last_job['filament_mm'][1] == 40.
    assert rig.stats.last_job['status'] == 'completed'


def test_a_job_that_never_gets_its_end_call_is_closed(rig):
    rig.stats.note_job_prepare("/gcodes/c.gcode", "SDCARD_PRINT_FILE")
    rig.ps.state = 'printing'
    rig.stats._poll(rig.reactor.monotonic())
    rig.ps.state = 'complete'
    rig.stats.note_job_ending('complete')
    rig.reactor.advance(rig.mod.ENDING_TIMEOUT + 1.)
    rig.stats._poll(rig.reactor.monotonic())
    assert rig.stats.job is None
    assert rig.stats.last_job['status'] == 'completed'


def test_klipper_shutdown_closes_the_open_job(rig):
    rig.stats.note_job_prepare("/gcodes/d.gcode", "SDCARD_PRINT_FILE")
    rig.send('klippy:shutdown')
    assert rig.stats.job is None
    assert rig.stats.data['lifetime']['jobs']['shutdown'] == 1
    assert rig.path.exists()


# -------------------------------------------------------------- phases

def test_overlays_take_time_from_the_base_phase(rig):
    stats, reactor = rig.stats, rig.reactor
    stats.note_job_prepare("/gcodes/e.gcode", "SDCARD_PRINT_FILE")
    reactor.advance(1.)
    stats.push_phase('purge')
    reactor.advance(2.)
    stats.push_phase('heating')       # innermost wins
    reactor.advance(4.)
    stats.pop_phase('heating')
    reactor.advance(8.)
    stats.pop_phase('purge')
    reactor.advance(16.)
    phases = stats._job_record('open', reactor.monotonic())['phases_s']
    assert (phases['prepare'], phases['purge'], phases['heating']) == \
        (17., 10., 4.)


def test_homing_events_are_attributed(rig):
    rig.stats.note_job_prepare("/gcodes/f.gcode", "SDCARD_PRINT_FILE")
    rig.send('homing:home_rails_begin', object(), [])
    rig.reactor.advance(12.)
    rig.send('homing:home_rails_end', object(), [])
    record = rig.stats._job_record('open', rig.reactor.monotonic())
    assert record['phases_s']['homing'] == 12.


def test_marks_outside_a_job_are_ignored(rig):
    rig.stats.push_phase('mesh')
    rig.stats.pop_phase('mesh')
    assert rig.stats.job is None


def test_configured_commands_are_wrapped_and_keep_working(rig):
    calls = []

    def m109(gcmd):
        calls.append(gcmd.params)
        rig.reactor.advance(7.)

    rig.gcode.register_command('M109', m109, desc="wait for nozzle")
    rig.send('klippy:connect')
    assert 'M109' in rig.stats._wrapped
    assert rig.gcode.gcode_help['M109'] == "wait for nozzle"
    assert 'BED_MESH_CALIBRATE' not in rig.stats._wrapped   # not registered

    rig.stats.note_job_prepare("/gcodes/g.gcode", "SDCARD_PRINT_FILE")
    rig.gcode.run('M109', S='205')
    assert calls == [{'S': '205'}]
    assert rig.stats._job_record('open', rig.reactor.monotonic())[
        'phases_s']['heating'] == 7.


def test_a_wrapped_command_that_raises_still_closes_its_phase(rig):
    def failing(gcmd):
        rig.reactor.advance(3.)
        raise RuntimeError("probe failed")

    rig.gcode.register_command('BED_MESH_CALIBRATE', failing)
    rig.send('klippy:connect')
    rig.stats.note_job_prepare("/gcodes/h.gcode", "SDCARD_PRINT_FILE")
    with pytest.raises(RuntimeError, match="probe failed"):
        rig.gcode.run('BED_MESH_CALIBRATE')
    assert rig.stats.job['stack'] == []
    assert rig.stats.job['phases']['mesh'] == 3.


# -------------------------------------------------------- print_stats fix

def test_print_stats_reports_the_measured_filament_while_printing(rig):
    rig.send('klippy:connect')
    assert rig.stats.get_status(0)['corrects_print_stats'] is True
    stats = rig.stats
    stats.note_job_prepare("/gcodes/i.gcode", "SDCARD_PRINT_FILE")
    rig.extruder(0).last_position += 50.          # clean: not part of print
    rig.ps.state = 'printing'
    stats._poll(rig.reactor.monotonic())
    rig.extruder(0).last_position += 100.
    rig.extruder(1).last_position += 30.
    reading = rig.ps.get_status(rig.reactor.monotonic())
    assert reading['filament_used'] == 130.
    assert rig.ps.original_calls == 0


def test_print_stats_keeps_its_own_logic_without_a_tracked_job(rig):
    rig.send('klippy:connect')
    rig.ps.state = 'printing'
    rig.ps._update_filament_usage(0)
    assert rig.ps.original_calls == 1


def test_the_print_stats_fix_can_be_switched_off(mod, tmp_path):
    printer = FakePrinter()
    printer.objects['print_stats'] = FakePrintStats()
    config = FakeConfig(printer, {'path': str(tmp_path / 's.json'),
                                  'correct_print_stats': False})
    mod.FFStats(config)
    printer.send_event('klippy:connect')
    printer.objects['print_stats']._update_filament_usage(0)
    assert printer.objects['print_stats'].original_calls == 1


# ----------------------------------------------------------- tool changes

def _change(rig, from_tool, to_tool, seconds, ok=True, stage=None, error=None):
    token = rig.stats.toolchange_begin(to_tool)
    rig.reactor.advance(seconds)
    rig.stats.toolchange_end(token, from_tool, to_tool, ok, stage, error)


def test_swaps_pickups_and_reselects_are_told_apart(rig):
    _change(rig, -1, 0, 10.)          # first pickup
    _change(rig, 0, 1, 20.)           # swap
    _change(rig, 1, 1, 1.)            # same tool again
    totals = rig.stats.data['lifetime']['toolchange']
    assert (totals['pickups'], totals['swaps'], totals['reselects']) == \
        (1, 1, 1)
    assert totals['time_s'] == 30.
    assert totals['max_s'] == 20.
    assert totals['tools'][1] == {'picked': 1, 'time_s': 20., 'failed': 0}
    status = rig.stats.get_status(0)['toolchange']
    assert status['count'] == 2 and status['avg_s'] == 15.


def test_failures_are_filed_under_their_stage(rig):
    _change(rig, 0, 2, 4., ok=False, stage='grab',
            error="grab sensor never activated for T2")
    _change(rig, 0, 3, 2., ok=False, stage='release', error="x")
    totals = rig.stats.data['lifetime']['toolchange']
    assert totals['failed'] == 2
    assert totals['failures_by_stage'] == {'grab': 1, 'release': 1}
    assert totals['tools'][2]['failed'] == 1
    assert totals['swaps'] == 0
    assert totals['last_failure']['stage'] == 'release'


def test_failed_attempts_are_counted_per_kind_and_per_job(rig):
    rig.stats.note_job_prepare("/gcodes/j.gcode", "SDCARD_PRINT_FILE")
    token = rig.stats.toolchange_begin(1)
    rig.stats.note_failed_attempt('grab', 1)
    rig.stats.note_failed_attempt('release', 0)
    rig.stats.note_failed_attempt('release', 0)
    assert token['attempts_failed'] == 3
    rig.stats.toolchange_end(token, 0, 1, True)
    totals = rig.stats.data['lifetime']['toolchange']
    assert (totals['grab_attempts_failed'],
            totals['release_attempts_failed']) == (1, 2)
    assert rig.stats.job['toolchange']['attempts_failed'] == 3


def test_a_toolchange_inside_a_job_counts_for_the_job_and_its_phase(rig):
    rig.stats.note_job_prepare("/gcodes/k.gcode", "SDCARD_PRINT_FILE")
    _change(rig, 0, 1, 25.)
    assert rig.stats.job['toolchange']['swaps'] == 1
    assert rig.stats.job['phases']['toolchange'] == 25.


def test_toolchanger_hooks_report_the_stage_of_a_failure(ff_toolchange, rig):
    module = ff_toolchange

    class Toolchanger:
        changing = False
        restore_z_hop = restore_retract = purge_retract = 0.
        purge_pickup_armed = False
        prime_tower_geometry = None
        no_tower_prime_macro = None
        job_tool_mask = 0
        gcode = types.SimpleNamespace(respond_info=lambda message: None)

        def __init__(self, fail_in=None, current=0):
            self.printer = rig.printer
            self.printer.command_error = RuntimeError
            self.fail_in = fail_in
            self.current = current

        @staticmethod
        def _restore_axis_arg(gcmd):
            return ''

        @staticmethod
        def _capture_position():
            return None

        @staticmethod
        def _wait_moves():
            pass

        @staticmethod
        def _ensure_homed():
            pass

        def _derive_current_tool(self):
            return self.current, "ok"

        @staticmethod
        def _pickup_retract(tool, explicit_purge=False):
            return 0., False

        def _release(self, tool):
            rig.reactor.advance(3.)
            if self.fail_in == 'release':
                raise module.FFToolchangeError("release failed")

        def _grab(self, tool, **kwargs):
            rig.reactor.advance(5.)
            if self.fail_in == 'grab':
                raise module.FFToolchangeError("grab failed")
            return 0.

        @staticmethod
        def _extruder_name(tool):
            return 'extruder%d' % tool

        @staticmethod
        def _run(script):
            pass

        _sync_shared_extruder_stepper = _set_tool_frame = _arm_runout = \
            staticmethod(lambda tool: None)

        def _stats_call(self, method, *args):
            return module.FFToolchange._stats_call(self, method, *args)

    def toolchange(fake, tool):
        module.FFToolchange._toolchange(fake, FakeGcmd(), tool)

    toolchange(Toolchanger(current=0), 1)                 # a swap
    toolchange(Toolchanger(current=-1), 2)                # a first pickup
    toolchange(Toolchanger(current=3), 3)                 # same tool again
    with pytest.raises(RuntimeError, match="release failed"):
        toolchange(Toolchanger(fail_in='release', current=0), 1)
    with pytest.raises(RuntimeError, match="grab failed"):
        toolchange(Toolchanger(fail_in='grab', current=0), 1)

    totals = rig.stats.data['lifetime']['toolchange']
    assert (totals['swaps'], totals['pickups'], totals['reselects']) == \
        (1, 1, 1)
    assert totals['time_s'] == 8. + 5.            # swap 3+5, pickup 5
    assert totals['failures_by_stage'] == {'release': 1, 'grab': 1}


def test_a_failing_stats_module_cannot_break_a_toolchange(ff_toolchange):
    class Broken:
        def toolchange_begin(self, tool):
            raise RuntimeError("disk full")

    fake = types.SimpleNamespace(printer=types.SimpleNamespace(
        lookup_object=lambda name, default=None: Broken()))
    assert ff_toolchange.FFToolchange._stats_call(
        fake, 'toolchange_begin', 1) is None


# ------------------------------------------------------------- persistence

def test_statistics_survive_a_restart(rig, mod):
    _run_job(rig, [(0, 100., 60.)])
    assert rig.path.exists()

    # a restart is a new printer object reading the same file
    again = mod.FFStats(FakeConfig(FakePrinter(), {'path': str(rig.path)}))
    assert again.data['lifetime']['jobs']['completed'] == 1
    assert again.data['lifetime']['filament_mm'][0] == 150.
    assert again.last_job['file'] == 'cubes.gcode'


def test_a_job_open_when_power_failed_is_recovered_as_interrupted(rig, mod):
    rig.stats.note_job_prepare("/gcodes/l.gcode", "SDCARD_PRINT_FILE")
    rig.extruder(0).last_position += 25.
    rig.reactor.advance(40.)
    rig.stats.save()                      # what the periodic flush writes
    on_disk = json.loads(rig.path.read_text())
    assert on_disk['open_job']['status'] == 'open'

    again = mod.FFStats(FakeConfig(FakePrinter(), {'path': str(rig.path)}))
    again._recover_open_job()
    assert again.data['lifetime']['jobs']['interrupted'] == 1
    assert again.last_job['filament_mm'][0] == 25.
    assert again.data['open_job'] is None


def test_an_open_job_is_saved_every_minute_even_when_nothing_happens(rig):
    stats, reactor = rig.stats, rig.reactor
    stats.note_job_prepare("/gcodes/p.gcode", "SDCARD_PRINT_FILE")
    rig.ps.state = 'printing'
    stats._poll(reactor.monotonic())
    stats.save()                                    # the start of the job
    reactor.advance(30.)
    rig.extruder(0).last_position += 10.
    stats._poll(reactor.monotonic())                # too early: no write
    assert json.loads(rig.path.read_text())['open_job']['filament_mm'][0] == 0.

    reactor.advance(40.)                            # 70 s since the save
    rig.extruder(0).last_position += 5.
    stats._poll(reactor.monotonic())
    on_disk = json.loads(rig.path.read_text())
    assert on_disk['open_job']['filament_mm'][0] == 15.


def test_an_unreadable_file_is_kept_aside_not_overwritten(mod, tmp_path):
    path = tmp_path / "ff_stats.json"
    path.write_text("{ this is not json")
    stats = mod.FFStats(FakeConfig(FakePrinter(), {'path': str(path)}))
    assert stats.data['lifetime']['jobs']['started'] == 0
    assert list(tmp_path.glob("ff_stats.json.corrupt-*"))


def test_a_file_from_an_older_layout_gains_new_counters(mod, tmp_path):
    path = tmp_path / "ff_stats.json"
    path.write_text(json.dumps({'version': 1, 'lifetime': {
        'jobs': {'started': 3}, 'filament_mm': [1.0]}}))
    stats = mod.FFStats(FakeConfig(FakePrinter(), {'path': str(path)}))
    life = stats.data['lifetime']
    assert life['jobs']['started'] == 3 and life['jobs']['completed'] == 0
    assert life['filament_mm'] == [1.0, 0., 0., 0.]
    assert life['toolchange']['swaps'] == 0


def test_a_write_failure_is_logged_not_raised(rig, tmp_path):
    rig.stats.path = str(tmp_path / "missing" / "dir" / "x.json")
    # parent can be created, so make the path itself unwritable
    (tmp_path / "missing").write_text("a file where a directory is needed")
    assert rig.stats.save() is False


def test_old_jobs_are_trimmed_to_max_jobs(mod, tmp_path):
    printer = FakePrinter()
    printer.objects['print_stats'] = FakePrintStats()
    stats = mod.FFStats(FakeConfig(printer, {
        'path': str(tmp_path / "s.json"), 'max_jobs': 3}))
    printer.objects['ff_stats'] = stats
    for number in range(5):
        stats.note_job_prepare("/gcodes/%d.gcode" % number, "M23")
        stats.note_job_aborted("x")
    assert [job['file'] for job in stats.data['jobs']] == \
        ['2.gcode', '3.gcode', '4.gcode']
    assert stats.data['lifetime']['jobs']['aborted'] == 5


# ------------------------------------------------------------------ reports

def test_status_is_json_serialisable_and_complete(rig):
    _run_job(rig, [(0, 100., 60.)])
    status = rig.stats.get_status(0)
    json.dumps(status)
    assert status['jobs']['completed'] == 1
    assert status['filament_mm'][0] == 150.
    assert status['last_job']['status'] == 'completed'
    assert status['job'] is None
    rig.stats.note_job_prepare("/gcodes/m.gcode", "M23")
    assert rig.stats.get_status(0)['job']['phase'] == 'prepare'


def test_reports_mention_the_numbers_and_survive_respond_info(rig):
    _run_job(rig, [(0, 1000., 60.), (1, 500., 60.)])
    _change(rig, 0, 1, 18.)
    for text in (rig.stats.summary_text(), rig.stats.job_text(),
                 rig.stats.jobs_text(5)):
        assert text
        # respond_info strips leading blanks, so nothing may depend on them
        assert all(line == line.lstrip() for line in text.split('\n'))
    summary = rig.stats.summary_text()
    assert "1 completed" in summary
    assert "T0 1.05 m" in summary and "T1 0.50 m" in summary
    assert "1 swaps" in summary and "Time by phase:" in summary
    assert "cubes.gcode" in rig.stats.job_text()
    assert "#1 " in rig.stats.jobs_text(5)


def test_reports_work_before_anything_was_recorded(rig):
    assert "No job has been recorded" in rig.stats.job_text()
    assert "No jobs recorded" in rig.stats.jobs_text(10)
    assert "Statistics since" in rig.stats.summary_text()


def test_grams_use_the_files_own_density_when_it_has_one(rig, tmp_path):
    gcode = tmp_path / "dens.gcode"
    gcode.write_text("; total layer number: 3\n"
                     "; filament_density: 2.0,1.0,1.0,1.0\n"
                     "; filament_diameter: 2.0,2.0,2.0,2.0\n")
    header = rig.stats._slicer_header(str(gcode))
    assert header == {'density': [2.0, 1.0, 1.0, 1.0],
                      'diameter': [2.0, 2.0, 2.0, 2.0]}
    mm, grams = rig.stats._filament_totals([1000., 0., 0., 0.], header)
    assert mm == 1000.
    assert grams == pytest.approx(3.14159265 * 1.0 ** 2 * 1000 / 1000 * 2.0)


def test_show_command_dispatches_and_rejects_nonsense(rig):
    gcmd = rig.gcode.run('FF_STATS_SHOW', WHAT='summary')
    assert "Statistics since" in gcmd.responses[0]
    gcmd = rig.gcode.run('FF_STATS_SHOW', WHAT='JOBS', COUNT='3')
    assert "No jobs recorded" in gcmd.responses[0]
    with pytest.raises(RuntimeError, match="WHAT must be"):
        rig.gcode.run('FF_STATS_SHOW', WHAT='everything')


def test_a_bug_in_a_report_is_a_command_error_not_a_crash(rig):
    # In Klipper any exception that is not a command error shuts the printer
    # down, so a report that breaks must surface as one.
    def boom():
        raise KeyError('broken')

    rig.stats.summary_text = boom
    with pytest.raises(RuntimeError, match='internal error'):
        rig.gcode.run('FF_STATS_SHOW', WHAT='SUMMARY')


def test_entry_points_klipper_calls_never_raise(rig):
    def boom(*args):
        raise KeyError('broken')

    rig.stats._account = boom
    rig.stats.note_job_prepare('/gcodes/o.gcode', 'M23')
    assert rig.stats.push_phase('mesh') is None
    assert rig.stats.pop_phase('mesh') is None
    rig.send('homing:home_rails_begin', object(), [])
    rig.send('homing:home_rails_end', object(), [])
    rig.send('klippy:connect')
    rig.send('klippy:ready')


def test_a_status_that_breaks_reports_instead_of_raising(rig):
    def boom(eventtime):
        raise KeyError('broken')

    rig.stats._status = boom
    assert rig.stats.get_status(0) == {'error': 'see klippy.log'}


def test_reset_needs_confirmation_and_keeps_the_old_file(rig):
    _run_job(rig, [(0, 100., 60.)])
    with pytest.raises(RuntimeError, match="CONFIRM=1"):
        rig.gcode.run('FF_STATS_RESET')
    assert rig.stats.data['lifetime']['jobs']['completed'] == 1

    gcmd = rig.gcode.run('FF_STATS_RESET', CONFIRM='1')
    assert "Statistics reset" in gcmd.responses[0]
    life = rig.stats.data['lifetime']
    assert life['jobs']['started'] == 0 and life['filament_mm'] == [0.] * 4
    assert rig.stats.data['jobs'] == [] and rig.stats.last_job is None
    kept = list(rig.path.parent.glob("ff_stats.json.reset-*"))
    assert len(kept) == 1
    assert json.loads(kept[0].read_text())['lifetime']['jobs']['completed'] == 1
    # the file on disk is the empty one: a restart must not bring the old
    # numbers back
    assert json.loads(rig.path.read_text())['lifetime']['jobs']['started'] == 0


def test_reset_is_refused_while_a_job_runs(rig):
    rig.stats.note_job_prepare("/gcodes/q.gcode", "SDCARD_PRINT_FILE")
    with pytest.raises(RuntimeError, match="not while a job is running"):
        rig.gcode.run('FF_STATS_RESET', CONFIRM='1')
    assert rig.stats.job is not None


def test_manual_phase_marks(rig):
    rig.stats.note_job_prepare("/gcodes/n.gcode", "SDCARD_PRINT_FILE")
    rig.gcode.run('FF_STATS_PHASE', NAME='purge')
    rig.reactor.advance(6.)
    rig.gcode.run('FF_STATS_PHASE', NAME='purge', END='1')
    assert rig.stats.job['phases']['purge'] == 6.
    with pytest.raises(RuntimeError, match="NAME must be"):
        rig.gcode.run('FF_STATS_PHASE', NAME='coffee')


# ---------------------------------------------------- hooks and the config

def test_ff_print_reports_the_job_boundaries():
    source = PRINT.read_text(encoding="utf-8")
    assert "self._stats('note_job_prepare', path, cmd)" in source
    assert "self._stats('note_job_aborted', str(err))" in source
    assert "self._stats('note_job_ending', state)" in source
    assert "self._stats('note_job_finalize')" in source
    # the end call comes after the end macro, so the 'end' phase includes it
    run_end = source.split("def _run_end_macro", 1)[1]
    assert run_end.index("self.after_macro") < run_end.index("note_job_finalize")


def test_ff_toolchange_reports_every_change_and_failed_attempt():
    source = TOOLCHANGE.read_text(encoding="utf-8")
    assert "self._stats_call('toolchange_begin', tool)" in source
    assert "'toolchange_end', stats_token, current, tool, completed" in source
    assert source.count("self._stats_call('note_failed_attempt'") == 3
    assert "stats_stage = 'release'" in source
    assert "stats_stage = 'grab'" in source
    assert "stats_stage = 'restore'" in source


def _n4s4_config():
    cp = configparser.RawConfigParser(
        strict=False, inline_comment_prefixes=(";", "#"))
    cp.read_string(N4S4.read_text(encoding="utf-8"))
    return cp


def test_the_n4s4_config_loads_the_module_and_offers_the_macros():
    cp = _n4s4_config()
    assert cp.has_section("ff_stats")
    expected = {
        "gcode_macro FF_STATS": "FF_STATS_SHOW WHAT=SUMMARY",
        "gcode_macro FF_STATS_JOB": "FF_STATS_SHOW WHAT=JOB",
    }
    for section, command in expected.items():
        assert cp.get(section, "gcode").strip() == command
        assert cp.get(section, "description").strip()
    jobs = cp.get("gcode_macro FF_STATS_JOBS", "gcode")
    assert jobs.strip().startswith("FF_STATS_SHOW WHAT=JOBS")


def test_the_jobs_macro_passes_count_through():
    env = jinja2.Environment("{%", "%}", "{", "}")
    body = _n4s4_config().get("gcode_macro FF_STATS_JOBS", "gcode")
    template = env.from_string(body)
    assert template.render(params={}).strip() == "FF_STATS_SHOW WHAT=JOBS"
    assert template.render(params={"COUNT": "25"}).strip() == \
        "FF_STATS_SHOW WHAT=JOBS COUNT=25"


def test_statistics_macros_are_not_hidden_from_mainsail():
    # Mainsail lists only macros that do not start with an underscore.
    for name in ("FF_STATS", "FF_STATS_JOB", "FF_STATS_JOBS"):
        assert re.search(r"^\[gcode_macro %s\]$" % name,
                         N4S4.read_text(encoding="utf-8"), re.M)


def test_the_extra_registers_no_name_a_macro_also_uses():
    source = STATS.read_text(encoding="utf-8")
    registered = set(re.findall(r"register_command\(\s*'([A-Z_]+)'", source))
    macros = set(re.findall(r"^\[gcode_macro ([A-Z_]+)\]$",
                            N4S4.read_text(encoding="utf-8"), re.M))
    assert registered == {'FF_STATS_SHOW', 'FF_STATS_PHASE',
                          'FF_STATS_RESET'}
    assert not registered & macros

"""The lag watch (ff_lagwatch): what it reports when Klipper's main thread
stops, and that it can never be the thing that stops it."""

import gc
import importlib.util
import logging
import re
import threading
import types

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.static

EXTRAS = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" /
          "klippy" / "extras")
MODULE = EXTRAS / "ff_lagwatch.py"
N4S4 = (ROOT / "pkgs" / "klipper-config" / "payload" / "config" /
        "printer_n4s4.cfg")


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("test_ff_lagwatch_mod",
                                                  MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


class FakeGcode:
    def __init__(self):
        self.commands = {}

    def register_command(self, name, func, when_not_ready=False, desc=None):
        self.commands[name] = func


class FakeGcmd:
    def __init__(self):
        self.responses = []

    def respond_info(self, message):
        self.responses.append(message)


class FakeConfig:
    def __init__(self, printer, **options):
        self.printer = printer
        self.options = options

    def get_printer(self):
        return self.printer

    def _value(self, kind, name, default, minval, maxval):
        value = kind(self.options.get(name, default))
        if minval is not None and value < minval:
            raise ValueError("%s below %s" % (name, minval))
        if maxval is not None and value > maxval:
            raise ValueError("%s above %s" % (name, maxval))
        return value

    def getfloat(self, name, default=None, minval=None, maxval=None):
        return self._value(float, name, default, minval, maxval)

    def getint(self, name, default=None, minval=None, maxval=None):
        return self._value(int, name, default, minval, maxval)


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


class FakeToolhead:
    def __init__(self, ahead):
        self.ahead = ahead

    def get_status(self, eventtime):
        return {'print_time': 500. + self.ahead,
                'estimated_print_time': 500.}


@pytest.fixture
def rig(mod, caplog):
    caplog.set_level(logging.INFO)
    printer = FakePrinter()
    watch = mod.FFLagWatch(FakeConfig(printer))
    yield types.SimpleNamespace(
        watch=watch, printer=printer, reactor=printer.reactor, mod=mod,
        gcode=printer.objects['gcode'], log=caplog)
    watch._handle_disconnect()          # no thread or gc hook outlives a test


def lines(rig, text):
    return [r.getMessage() for r in rig.log.records if text in r.getMessage()]


def beat(rig, seconds_late=0.):
    """One heartbeat, `seconds_late` after it was due."""
    watch = rig.watch
    if watch._expected is None:
        watch._expected = rig.reactor.now + watch.period
    rig.reactor.now = watch._expected + seconds_late
    return watch._beat(rig.reactor.now)


class Stuck:
    """A thread that stands in for the main thread, blocked in a call."""

    def __init__(self):
        self.release = threading.Event()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.blocked_in_fsync,
                                       daemon=True)
        self.thread.start()
        assert self.ready.wait(5.)

    def blocked_in_fsync(self):
        self.ready.set()
        self.release.wait(10.)

    def stop(self):
        self.release.set()
        self.thread.join(5.)


# ---------------------------------------------------------------- the beat

def test_a_beat_on_time_reports_nothing(rig):
    beat(rig)
    beat(rig, 0.05)

    assert rig.watch.stalls == 0
    assert not lines(rig, "ff_lagwatch:")


def test_the_next_beat_is_one_period_ahead(rig):
    assert beat(rig) == pytest.approx(rig.reactor.now + rig.watch.period)


def test_a_late_beat_is_reported_with_how_late_it_was(rig):
    beat(rig)
    beat(rig, 1.2)

    assert rig.watch.stalls == 1
    assert rig.watch.worst == pytest.approx(1.2)
    (line,) = lines(rig, "was away for")
    assert "1.20 s" in line and "stall 1" in line


def test_the_report_says_what_the_printer_was_doing(rig):
    rig.printer.objects['ff_toolchange'] = types.SimpleNamespace(changing=True)
    rig.printer.objects['toolhead'] = FakeToolhead(ahead=0.4)
    beat(rig)

    beat(rig, 1.0)

    (line,) = lines(rig, "was away for")
    assert "a tool change was running" in line
    assert "0.40 s of motion queued" in line


def test_a_drained_queue_is_said_to_be_empty(rig):
    rig.printer.objects['toolhead'] = FakeToolhead(ahead=-3.)
    beat(rig)

    beat(rig, 1.0)

    assert "no motion was queued" in lines(rig, "was away for")[0]


def test_a_report_still_works_without_a_toolhead(rig):
    beat(rig)

    beat(rig, 1.0)

    assert rig.watch.stalls == 1


def test_a_failure_inside_the_beat_is_logged_not_raised(rig, monkeypatch):
    """A reactor timer that raises shuts the printer down."""
    def broken(now, late):
        raise RuntimeError("boom")

    monkeypatch.setattr(rig.watch, "_report", broken)
    beat(rig)

    following = beat(rig, 1.0)

    assert following == pytest.approx(rig.reactor.now + rig.watch.period)
    assert lines(rig, "heartbeat failed")


def test_no_more_than_max_reports_lines_are_written_but_all_are_counted(mod):
    printer = FakePrinter()
    watch = mod.FFLagWatch(FakeConfig(printer, max_reports=2))
    rig = types.SimpleNamespace(watch=watch, reactor=printer.reactor)
    beat(rig)
    for _ in range(5):
        beat(rig, 1.0)

    assert watch.stalls == 5
    assert watch._reports == 2


# ------------------------------------------------------- the watchdog thread

def test_the_watchdog_names_the_call_the_main_thread_is_stuck_in(rig):
    stuck = Stuck()
    try:
        rig.watch.main_ident = stuck.thread.ident
        beat(rig)
        rig.reactor.advance(0.1 + 0.6)          # a beat due 0.6 s ago

        rig.watch._look()
    finally:
        stuck.stop()

    (line,) = lines(rig, "has not run for")
    assert "blocked_in_fsync" in line
    assert "test_ff_lagwatch.py" in line


def test_the_stack_the_watchdog_saw_is_in_the_report_when_the_beat_returns(rig):
    stuck = Stuck()
    try:
        rig.watch.main_ident = stuck.thread.ident
        beat(rig)
        rig.reactor.advance(0.1 + 0.6)
        rig.watch._look()
    finally:
        stuck.stop()

    rig.reactor.advance(0.5)
    rig.watch._beat(rig.reactor.now)

    (line,) = lines(rig, "was away for")
    assert "it was in:" in line and "blocked_in_fsync" in line
    assert rig.watch.recent[-1][2].count("blocked_in_fsync") == 1


def test_a_stall_the_watchdog_missed_is_reported_without_a_stack(rig):
    beat(rig)

    beat(rig, 0.8)

    assert "no sample taken" in lines(rig, "was away for")[0]


def test_one_stall_is_sampled_at_intervals_and_at_most_a_few_times(rig):
    stuck = Stuck()
    try:
        rig.watch.main_ident = stuck.thread.ident
        beat(rig)
        rig.reactor.advance(0.1 + 0.3)
        rig.watch._look()
        rig.watch._look()                       # too soon after the first
        for _ in range(10):
            rig.reactor.advance(rig.mod.SAMPLE_INTERVAL + 0.01)
            rig.watch._look()
    finally:
        stuck.stop()

    assert len(rig.watch._samples) == rig.mod.MAX_SAMPLES
    assert len(lines(rig, "has not run for")) == rig.mod.MAX_SAMPLES


def test_the_watchdog_is_quiet_while_the_beat_is_on_time(rig):
    beat(rig)
    rig.reactor.advance(0.1 + rig.watch.threshold / 2.)

    rig.watch._look()

    assert not lines(rig, "has not run for")


def test_the_watchdog_does_nothing_before_the_first_beat(rig):
    rig.reactor.advance(100.)

    rig.watch._look()

    assert not lines(rig, "has not run for")


def test_the_watchdog_leaves_the_timer_alone_while_it_runs(rig):
    """The beat disarms itself, so the watchdog cannot sample the timer on
    its way out of a stall and call that the culprit."""
    seen = []
    rig.watch._report = lambda now, late: seen.append(rig.watch._expected)
    beat(rig)

    beat(rig, 1.0)

    assert seen == [None]


def test_the_stack_is_innermost_first_and_short(mod):
    stuck = Stuck()
    try:
        import sys
        frame = sys._current_frames()[stuck.thread.ident]
        text = mod.describe_stack(frame, depth=3)
    finally:
        stuck.stop()

    parts = text.split(" <- ")
    assert len(parts) == 3
    assert "wait" in parts[0]                   # threading.Event.wait
    assert any("blocked_in_fsync" in part for part in parts)
    assert mod.describe_stack(None) == "(no frame)"


# ------------------------------------------------------ garbage collection

def test_a_slow_collection_is_logged_by_the_next_beat(rig):
    watch = rig.watch
    watch._gc_callback('start', {'generation': 2})
    rig.reactor.advance(0.3)
    watch._gc_callback('stop', {'generation': 2, 'collected': 5})
    beat(rig)

    beat(rig)

    (line,) = lines(rig, "garbage collection")
    assert "generation 2" in line and "0.30 s" in line


def test_a_quick_collection_is_not_worth_a_line(rig):
    rig.watch._gc_callback('start', {'generation': 0})
    rig.reactor.advance(0.001)
    rig.watch._gc_callback('stop', {'generation': 0})

    beat(rig)
    beat(rig)

    assert not lines(rig, "garbage collection")


def test_the_gc_hook_is_in_the_interpreter_only_while_watching(rig):
    watch = rig.watch

    watch._handle_ready()
    assert watch._gc_callback in gc.callbacks
    watch._handle_disconnect()

    assert watch._gc_callback not in gc.callbacks


# ---------------------------------------------------------------- lifecycle

def test_ready_starts_the_timer_and_the_thread_and_disconnect_stops_them(rig):
    rig.printer.send_event('klippy:ready')
    assert rig.reactor.timers == [rig.watch._beat]
    thread = rig.watch._thread
    assert thread.is_alive() and thread.daemon

    rig.printer.send_event('klippy:disconnect')

    assert not thread.is_alive()


def test_a_second_ready_does_not_start_a_second_watcher(rig):
    rig.printer.send_event('klippy:ready')
    rig.printer.send_event('klippy:ready')

    assert len(rig.reactor.timers) == 1


def test_gc_hook_can_be_left_off(mod):
    printer = FakePrinter()
    watch = mod.FFLagWatch(FakeConfig(printer, gc_threshold=0))
    watch._handle_ready()
    try:
        assert watch._gc_callback not in gc.callbacks
    finally:
        watch._handle_disconnect()


def test_the_options_have_sensible_limits(mod):
    printer = FakePrinter()
    with pytest.raises(ValueError):
        mod.FFLagWatch(FakeConfig(printer, threshold=0.01))
    with pytest.raises(ValueError):
        mod.FFLagWatch(FakeConfig(printer, period=5))


# ------------------------------------------------------------ what it shows

def test_ff_lagwatch_says_so_when_nothing_happened(rig):
    gcmd = FakeGcmd()

    rig.gcode.commands['FF_LAGWATCH'](gcmd)

    assert "0 (worst 0.00 s)" in gcmd.responses[0]
    assert "printer.log" in gcmd.responses[0]


def test_ff_lagwatch_lists_the_recent_stalls(rig):
    beat(rig)
    beat(rig, 1.5)
    gcmd = FakeGcmd()

    rig.gcode.commands['FF_LAGWATCH'](gcmd)

    text = gcmd.responses[0]
    assert ": 1 (worst 1.50 s)" in text
    assert re.search(r"\d\d:\d\d:\d\d  1\.50 s  no sample", text)


def test_the_status_is_plain_data(rig):
    beat(rig)
    beat(rig, 0.7)

    assert rig.watch.get_status(0.) == {
        'stalls': 1, 'worst_s': 0.7, 'threshold_s': 0.25}


# ------------------------------------------------------------ the config

def test_the_n4s4_config_loads_the_lag_watch():
    config = N4S4.read_text(encoding="utf-8")

    assert re.search(r"^\[ff_lagwatch\]$", config, re.M)


def test_the_module_has_a_load_config(mod):
    printer = FakePrinter()

    assert isinstance(mod.load_config(FakeConfig(printer)), mod.FFLagWatch)

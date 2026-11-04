# Creator 5 lag watch: what keeps Klipper's main thread from running
#
# Copyright (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""Finds out why Klipper's main thread stopped for a moment.

Klipper does everything in one thread: it reads the G-code, plans the moves,
generates the steps and sends them to the MCUs, and it answers the clock-sync
pings that keep the host's idea of the MCU clocks right.  When that thread is
blocked -- by a file write on the slow eMMC, by a long garbage collection, by
anything that takes a second -- no steps reach the MCU in that time.  If a
move was running and its last steps were still on the host, the stepper timer
on the MCU finds itself late and the MCU shuts down ("Timer too close").  The
MCU's message blames "the host computer is overloaded"; the cause is usually
a single stall, and nothing in a normal log says what the stall was.

This extra measures it.  A timer in the reactor wakes up every `period`
seconds.  A second thread watches that timer: when it is more than
`threshold` seconds late, the main thread is stuck, and the watchdog writes
down where it is stuck (the Python stack of the main thread, taken while it
is blocked -- a file write or sleep shows up as the call that is blocking).
When the main thread runs again, the timer reports how long it was away and
what the printer was doing.  Garbage collections slower than `gc_threshold`
are logged as well, because a collection holds the interpreter and no other
thread can sample it.

Everything goes to klippy's log (printer.log) as `ff_lagwatch:` lines, and
`FF_LAGWATCH` summarises them.  Nothing here moves the machine or changes a
print.  A stall that this module reports was already there; it only names it.
"""

import collections
import gc
import logging
import os
import sys
import threading
import time
import traceback

DEFAULT_THRESHOLD = 0.25    # seconds late before the main thread counts as stuck
DEFAULT_PERIOD = 0.1        # heartbeat of the timer
DEFAULT_GC_THRESHOLD = 0.05
DEFAULT_MAX_REPORTS = 50    # log lines about stalls per Klipper run
SAMPLE_INTERVAL = 0.5       # between two stack samples of one stall
MAX_SAMPLES = 4             # per stall
STACK_DEPTH = 6             # frames shown, innermost first


def describe_stack(frame, depth=STACK_DEPTH):
    """`file:line function <- ...`, innermost call first."""
    if frame is None:
        return "(no frame)"
    summary = traceback.StackSummary.extract(
        traceback.walk_stack(frame), limit=depth, lookup_lines=False)
    return " <- ".join("%s:%d %s" % (os.path.basename(entry.filename),
                                     entry.lineno, entry.name)
                       for entry in summary)


class FFLagWatch:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        self.threshold = config.getfloat('threshold', DEFAULT_THRESHOLD,
                                         minval=0.05)
        self.period = config.getfloat('period', DEFAULT_PERIOD,
                                      minval=0.02, maxval=1.)
        self.gc_threshold = config.getfloat('gc_threshold',
                                            DEFAULT_GC_THRESHOLD, minval=0.)
        self.max_reports = config.getint('max_reports', DEFAULT_MAX_REPORTS,
                                         minval=1)
        # Klipper builds its modules in the main thread; that is the thread
        # whose stack the watchdog samples.
        self.main_ident = threading.get_ident()

        self.stalls = 0
        self.worst = 0.
        self.recent = collections.deque(maxlen=10)  # (wall time, s, where)
        self._reports = 0
        self._expected = None       # when the timer is next due
        self._samples = []          # (seconds late, where) of the open stall
        self._sample_key = None
        self._sample_time = 0.
        self._gc_start = None
        self._gc_events = collections.deque(maxlen=50)  # (generation, s)
        self._stop = threading.Event()
        self._thread = None
        self._timer = None

        self.printer.register_event_handler('klippy:ready',
                                            self._handle_ready)
        self.printer.register_event_handler('klippy:disconnect',
                                            self._handle_disconnect)
        self.gcode.register_command(
            'FF_LAGWATCH', self.cmd_FF_LAGWATCH,
            desc=self.cmd_FF_LAGWATCH_help)

    # ------------------------------------------------------------ lifecycle

    def _handle_ready(self):
        if self._timer is not None:
            return
        if self.gc_threshold > 0.:
            gc.callbacks.append(self._gc_callback)
        self._timer = self.reactor.register_timer(self._beat,
                                                  self.reactor.NOW)
        self._thread = threading.Thread(target=self._watch,
                                        name='ff_lagwatch', daemon=True)
        self._thread.start()
        logging.info("ff_lagwatch: watching the main thread (stuck after "
                     "%.2f s, beat every %.2f s)", self.threshold, self.period)

    def _handle_disconnect(self):
        self._stop.set()
        try:
            gc.callbacks.remove(self._gc_callback)
        except ValueError:
            pass
        thread = self._thread
        if thread is not None:
            thread.join(1.)

    # ------------------------------------------------- the timer (main thread)

    def _beat(self, eventtime):
        # A reactor timer that raises shuts the printer down, and this one
        # only watches: whatever goes wrong in here is logged and dropped.
        try:
            now = self.reactor.monotonic()
            expected = self._expected
            # Disarmed while this runs, so the watchdog does not sample the
            # timer itself on its way out of a stall.
            self._expected = None
            if expected is not None and now - expected >= self.threshold:
                self._report(now, now - expected)
            self._log_gc()
        except Exception:
            logging.exception("ff_lagwatch: heartbeat failed")
        self._expected = self.reactor.monotonic() + self.period
        return self._expected

    def _report(self, now, late):
        self.stalls += 1
        self.worst = max(self.worst, late)
        samples, self._samples = self._samples, []
        self._sample_key = None
        where = None
        if samples:
            where = samples[-1][1]
            if samples[0][1] != where:
                where = "%s (first seen in %s)" % (where, samples[0][1])
        self.recent.append((time.time(), late, where))
        if self._reports >= self.max_reports:
            return
        self._reports += 1
        logging.warning(
            "ff_lagwatch: the main thread was away for %.2f s (stall %d%s)%s;"
            " %s", late, self.stalls,
            ", last report" if self._reports >= self.max_reports else "",
            self._context(),
            ("it was in: %s" % where) if where
            else "no sample taken (a garbage collection or a long call "
                 "holds the interpreter; see the gc lines)")

    def _context(self):
        """What the printer was doing, in a few words."""
        parts = []
        toolchange = self.printer.lookup_object('ff_toolchange', None)
        if getattr(toolchange, 'changing', False):
            parts.append("a tool change was running")
        toolhead = self.printer.lookup_object('toolhead', None)
        try:
            status = toolhead.get_status(self.reactor.monotonic())
            ahead = status['print_time'] - status['estimated_print_time']
            parts.append("the MCUs had %.2f s of motion queued" % ahead
                         if ahead > 0. else "no motion was queued")
        except Exception:
            pass
        return " [%s]" % ", ".join(parts) if parts else ""

    def _log_gc(self):
        while self._gc_events:
            generation, took = self._gc_events.popleft()
            logging.info("ff_lagwatch: garbage collection of generation %s "
                         "took %.2f s", generation, took)

    # ------------------------------------------------ garbage collection hook

    def _gc_callback(self, phase, info):
        # Runs inside the collector, in whichever thread triggered it: keep it
        # to arithmetic and a list append, and log from the timer instead.
        if phase == 'start':
            self._gc_start = self.reactor.monotonic()
        elif self._gc_start is not None:
            took = self.reactor.monotonic() - self._gc_start
            self._gc_start = None
            if took >= self.gc_threshold:
                self._gc_events.append((info.get('generation'), took))

    # ------------------------------------------------- the watchdog (thread)

    def _watch(self):
        wait = max(0.02, min(self.period, self.threshold / 2.))
        while not self._stop.wait(wait):
            try:
                self._look()
            except Exception:
                logging.exception("ff_lagwatch: watchdog failed")

    def _look(self):
        expected = self._expected
        if expected is None:
            return
        now = self.reactor.monotonic()
        late = now - expected
        if late < self.threshold:
            return
        if self._sample_key != expected:
            self._sample_key = expected
            self._samples = []
        elif (len(self._samples) >= MAX_SAMPLES
              or now - self._sample_time < SAMPLE_INTERVAL):
            return
        self._sample_time = now
        frame = sys._current_frames().get(self.main_ident)
        where = describe_stack(frame)
        self._samples.append((late, where))
        if self._reports < self.max_reports:
            logging.warning("ff_lagwatch: the main thread has not run for "
                            "%.2f s and is in: %s", late, where)

    # ------------------------------------------------------------ the status

    def get_status(self, eventtime):
        return {'stalls': self.stalls, 'worst_s': round(self.worst, 3),
                'threshold_s': self.threshold}

    cmd_FF_LAGWATCH_help = ("Show the stalls of Klipper's main thread that "
                            "were seen since it started")

    def cmd_FF_LAGWATCH(self, gcmd):
        lines = ["Main-thread stalls of %.2f s or more since Klipper started: "
                 "%d (worst %.2f s)" % (self.threshold, self.stalls,
                                        self.worst)]
        for stamp, late, where in self.recent:
            lines.append("%s  %.2f s  %s" % (
                time.strftime('%H:%M:%S', time.localtime(stamp)), late,
                where or "no sample"))
        if not self.stalls:
            lines.append("None. The details of a stall are in printer.log "
                         "(search for ff_lagwatch).")
        gcmd.respond_info("\n".join(lines))


def load_config(config):
    return FFLagWatch(config)

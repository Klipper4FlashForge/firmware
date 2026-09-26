# Glue between AFC (anvil-afc) and this printer: AFC's Moonraker writes,
# moved off klippy's main thread.
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# AFC records statistics, slot data (lane_data) and their clean-up with
# blocking HTTP requests to Moonraker (AFC_utils.AFC_moonraker: urlopen, no
# timeout) made from inside klippy's reactor. Several of them run straight
# after a toolchange, when AFC's restore_pos() has just queued the moves back
# to the print and not waited for them: CHANGE_TOOL's
# average_toolchange_time and increase_toolcount_change. While a request is
# in flight the reactor cannot generate steps for those moves, and on this
# printer, where Moonraker shares a slow CPU with klippy, that is long enough
# for the MCU to shut down with "Timer too close".
#
# So the three write methods are replaced, on AFC's class, with versions
# that hand the call to one background thread and return at once. The thread
# runs AFC's own method unchanged, against a view of the AFC_moonraker object
# whose logger posts every call back onto the reactor
# (register_async_callback), because AFC's logger writes to the console and
# nothing but the reactor thread may do that. One thread, one queue: writes
# land in the order AFC made them. Reads stay synchronous -- AFC makes them
# at startup and for commands typed at the console, where nothing is moving.

import logging
import queue
import threading

WRITES = ('update_afc_stats', 'send_lane_data', 'remove_database_entry')

# The live instance. A RESTART builds a new one, and the patched methods,
# installed once per process, must reach it rather than a retired one.
_current = None


class _ReactorLogger:
    """AFC_logger's methods, each forwarded to the real logger on the
    reactor thread."""
    def __init__(self, reactor, target):
        self._reactor = reactor
        self._target = target

    def __getattr__(self, name):
        method = getattr(self._target, name)

        def forward(*args, **kwargs):
            self._reactor.register_async_callback(
                lambda eventtime: method(*args, **kwargs))
        return forward


class _ThreadView:
    """The AFC_moonraker object as seen from the worker: every attribute is
    the object's own, except the logger, and methods are bound to this view
    so that _get_results() and friends log through it too."""
    def __init__(self, target, logger):
        self._target = target
        self.logger = logger

    def __getattr__(self, name):
        attr = getattr(type(self._target), name, None)
        if callable(attr):
            return attr.__get__(self)
        return getattr(self._target, name)


class FFAfc:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        try:
            from . import AFC_utils
        except ImportError:
            raise config.error("[ff_afc] needs AFC (the anvil-afc package):"
                               " extras/AFC_utils.py does not import")
        self.queue = queue.Queue()
        self.thread = threading.Thread(target=self._worker,
                                       name="ff_afc-moonraker", daemon=True)
        self.thread.start()
        _install(AFC_utils.AFC_moonraker)
        global _current
        _current = self
        self.printer.register_event_handler('klippy:disconnect',
                                            self._handle_disconnect)

    def _handle_disconnect(self):
        # Retire this worker; a RESTART's new instance starts its own.
        self.queue.put(None)

    def submit(self, original, moonraker, args, kwargs):
        view = _ThreadView(moonraker,
                           _ReactorLogger(self.reactor, moonraker.logger))
        self.queue.put((original, view, args, kwargs))

    def _worker(self):
        while True:
            job = self.queue.get()
            if job is None:
                return
            original, view, args, kwargs = job
            try:
                original(view, *args, **kwargs)
            except Exception:
                # AFC's methods catch their own HTTP errors; anything else is
                # logged here rather than killing the thread for good.
                logging.exception("ff_afc: AFC Moonraker write %s failed",
                                  original.__name__)


def _install(cls):
    """Patch the write methods once per process, keeping the originals."""
    if getattr(cls, '_ff_afc_originals', None) is not None:
        return
    originals = {name: getattr(cls, name) for name in WRITES}
    cls._ff_afc_originals = originals
    for name, original in originals.items():
        setattr(cls, name, _deferred(original))


def _deferred(original):
    def method(self, *args, **kwargs):
        if _current is None:
            return original(self, *args, **kwargs)
        _current.submit(original, self, args, kwargs)
    method.__name__ = original.__name__
    method.__doc__ = original.__doc__
    return method


def load_config(config):
    return FFAfc(config)

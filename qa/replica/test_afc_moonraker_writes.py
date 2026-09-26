"""AFC's Moonraker writes leave klippy's main thread (ff_afc.py).

THE FAILURE THIS EXISTS FOR

On hardware, every AFC toolchange (`T0`..`T3`) shut the printer down with
"Timer too close" right after the grab, while `SELECT_TOOL` -- the same
grab without AFC -- did not. AFC's CHANGE_TOOL queues the moves back to the
print (restore_pos, no wait) and then records two statistics with blocking
HTTP POSTs to Moonraker (AFC_utils.AFC_moonraker.update_afc_stats: urlopen,
no timeout) from the reactor. AFC.log put 174 ms between the last queued
move and the next line; the reactor generated no steps for that long.

ff_afc.py replaces AFC's three write methods with versions that queue the
call to one background thread. This runs AFC's real class against a
deliberately slow HTTP server on the printer's own interpreter:

  * negative control -- before ff_afc is loaded, a write blocks the caller
    for the server's full delay, which is the bug;
  * with ff_afc loaded, three writes return at once and still arrive, in
    the order they were made;
  * AFC's logger is never called from the worker: its calls are handed to
    the reactor (register_async_callback), because AFC's logger writes to
    the console;
  * klippy:disconnect retires the worker, so a RESTART does not leave one
    behind per restart.
"""
import pytest

pytestmark = pytest.mark.replica

MODDIR = "/usr/data/anvil"
PY = MODDIR + "/bin/python3.13"
ENV = MODDIR + "/anvil-env.sh"
KLIPPY = MODDIR + "/klipper/klippy"

DELAY = 1.0

SCRIPT = r"""
. %(env)s
cd %(klippy)s
exec %(py)s - <<'PYEOF'
import sys, threading, time
sys.path.insert(0, ".")
from http.server import BaseHTTPRequestHandler, HTTPServer

got = []

class Slow(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        time.sleep(%(delay)s)
        got.append(body.decode())
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"result": {}}')
    def log_message(self, *args):
        pass

server = HTTPServer(("127.0.0.1", 0), Slow)
threading.Thread(target=server.serve_forever, daemon=True).start()
port = server.server_address[1]

from extras import AFC_utils

class Log:
    def __init__(self):
        self.calls = []
    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, threading.current_thread().name))
        return record

log = Log()
moon = AFC_utils.AFC_moonraker("http://127.0.0.1", port, log)

start = time.monotonic()
moon.update_afc_stats("sync", 1)
print("SYNC %%.2f" %% (time.monotonic() - start))

class Reactor:
    def __init__(self):
        self.callbacks = []
    def register_async_callback(self, callback, waketime=0):
        self.callbacks.append(callback)
    def monotonic(self):
        return time.monotonic()

class Printer:
    def __init__(self):
        self.reactor = Reactor()
        self.handlers = {}
    def get_reactor(self):
        return self.reactor
    def register_event_handler(self, event, callback):
        self.handlers[event] = callback

class Config:
    def __init__(self, printer):
        self.printer = printer
    def get_printer(self):
        return self.printer
    def error(self, msg):
        return Exception(msg)

from extras import ff_afc
printer = Printer()
glue = ff_afc.load_config(Config(printer))

start = time.monotonic()
for i in range(3):
    moon.update_afc_stats("async%%d" %% i, i)
print("ASYNC %%.2f" %% (time.monotonic() - start))

deadline = time.monotonic() + 10 * %(delay)s
while len(got) < 4 and time.monotonic() < deadline:
    time.sleep(0.05)
print("ORDER %%s" %% ",".join(b.split("key=")[1].split("&")[0] for b in got))

dead = AFC_utils.AFC_moonraker("http://127.0.0.1", 1, log)
log.calls.clear()
dead.update_afc_stats("unreachable", 1)
time.sleep(1.0)
print("LOGGED_FROM_WORKER %%d" %% len(log.calls))
for callback in printer.reactor.callbacks:
    callback(0.)
print("LOGGED_ON_REACTOR %%s" %% ",".join(
    "%%s@%%s" %% (name, thread) for name, thread in log.calls))

printer.handlers["klippy:disconnect"]()
glue.thread.join(2.0)
print("WORKER_ALIVE %%s" %% glue.thread.is_alive())
PYEOF
""" % {"env": ENV, "klippy": KLIPPY, "py": PY, "delay": DELAY}


@pytest.fixture(scope="module")
def result(printer):
    for path in (PY, KLIPPY + "/extras/AFC_utils.py", KLIPPY + "/extras/ff_afc.py"):
        if not printer.file(path).exists:
            pytest.fail("there is no %s -- `make build` first" % path)
    res = printer.sh(SCRIPT, timeout=300)
    lines = dict(ln.strip().split(" ", 1) for ln in res.out.splitlines()
                 if ln.strip() and " " in ln.strip())
    assert "WORKER_ALIVE" in lines, (
        "the script did not finish:\nexit=%s\n%s\n%s"
        % (res.code, res.out[-2000:], res.err[-2000:]))
    return lines


def test_without_the_glue_a_write_blocks_the_caller(result):
    """Negative control: this is the stall that shut the printer down."""
    assert float(result["SYNC"]) >= DELAY * 0.9, result


def test_with_the_glue_writes_return_at_once(result):
    assert float(result["ASYNC"]) < 0.3, (
        "three AFC statistics writes took %ss to return with ff_afc loaded "
        "-- the reactor would still stall on them" % result["ASYNC"])


def test_writes_still_arrive_in_order(result):
    assert result["ORDER"] == "sync,async0,async1,async2", result["ORDER"]


def test_afcs_logger_only_runs_on_the_reactor(result):
    assert result["LOGGED_FROM_WORKER"] == "0", result
    calls = result["LOGGED_ON_REACTOR"].split(",")
    assert calls and all(c.endswith("@MainThread") for c in calls), calls
    assert any(c.startswith("error@") for c in calls), (
        "an unreachable Moonraker should still reach AFC's error log: %s"
        % calls)


def test_disconnect_retires_the_worker(result):
    assert result["WORKER_ALIVE"] == "False", result

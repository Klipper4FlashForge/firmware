"""ff_stats against Klipper's REAL gcode dispatcher and print_stats, on the printer's interpreter.

WHY THIS IS NOT JUST qa/static/test_ff_stats.py

The static tests run ff_stats against fakes of the reactor, the gcode
dispatcher and print_stats. Fakes agree with the code that was written beside
them. Three things ff_stats does only work if they agree with Klipper:

  * it TAKES OVER registered commands (M109, BED_MESH_CALIBRATE, ...) the way
    ff_print takes over SDCARD_PRINT_FILE -- unregister, keep the old handler,
    register a wrapper -- and the old handler of an "extended" command is a
    lambda that parses parameters into the command object it is handed;
  * it REPLACES print_stats._update_filament_usage on the live object, so the
    attribute has to exist, be called the way Klipper calls it, and its result
    has to come out through get_status();
  * it answers FF_STATS_SHOW through gcmd.get/getint with the real signatures.

This loads the real gcode.py and extras/print_stats.py from the INSTALLED
klippy tree, and the ff_stats.py that was installed, on the interpreter that
will run all of them. It needs no MCU: nothing here touches hardware.
"""
import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.replica

MODDIR = "/usr/data/anvil"
PY = MODDIR + "/bin/python3.13"
ENV = MODDIR + "/anvil-env.sh"
KLIPPY = MODDIR + "/klipper/klippy"
EXTRAS = ROOT / "pkgs" / "klipper" / "payload" / "klipper" / "klippy" / "extras"
N4S4 = (ROOT / "pkgs" / "klipper-config" / "payload" / "config" /
        "printer_n4s4.cfg")


@pytest.fixture(scope="module")
def box(printer):
    for path in (PY, KLIPPY + "/gcode.py"):
        if not printer.file(path).exists:
            pytest.fail("there is no %s, so there is nothing to ask this of "
                        "-- `make build` first." % path)
    return printer


def _py(box, body, timeout=300):
    return box.sh(". %s\nexec %s - <<'PYEOF'\n%s\nPYEOF\n" % (ENV, PY, body),
                  timeout=timeout)


@pytest.mark.parametrize("name", ["ff_stats.py", "ff_print.py",
                                  "ff_toolchange.py"])
def test_the_installed_extras_are_the_ones_in_the_checkout(box, name):
    installed = box.file(KLIPPY + "/extras/" + name)
    assert installed.exists, "%s was not installed" % name
    assert installed.text == (EXTRAS / name).read_text(encoding="utf-8")


def test_the_shipped_config_loads_the_extra_and_offers_the_macros(box):
    config = box.file(MODDIR + "/config/printer_n4s4.cfg")
    assert config.exists
    assert config.text == N4S4.read_text(encoding="utf-8")
    for section in ("[ff_stats]", "[gcode_macro FF_STATS]",
                    "[gcode_macro FF_STATS_JOB]",
                    "[gcode_macro FF_STATS_JOBS]"):
        assert section in config.text


def test_the_extra_imports_and_works_against_real_klipper(box):
    body = r'''
import json, sys, tempfile, os
sys.path.insert(0, "%(klippy)s")
import gcode as gcode_mod
from extras import print_stats as ps_mod
from extras import ff_stats

fails = []
def check(name, ok, detail=""):
    if not ok:
        fails.append("%%s %%s" %% (name, detail))

class Mutex:
    def __enter__(self): return self
    def __exit__(self, *a): return False

class Reactor:
    NOW = 0.
    NEVER = 9999999999.
    def __init__(self): self.t = 100.
    def monotonic(self): return self.t
    def mutex(self, is_locked=False): return Mutex()
    def register_timer(self, cb, when): return cb

class Printer:
    config_error = Exception
    def __init__(self):
        self.reactor, self.objects, self.handlers = Reactor(), {}, {}
    def get_start_args(self): return {}
    def get_reactor(self): return self.reactor
    def register_event_handler(self, ev, cb):
        self.handlers.setdefault(ev, []).append(cb)
    def send_event(self, ev, *args):
        for cb in self.handlers.get(ev, []): cb(*args)
    def lookup_object(self, name, default=None): return self.objects.get(name, default)
    def load_object(self, config, name, default=None): return self.objects[name]

class Config:
    error = Exception
    def __init__(self, printer, **opts):
        self.printer, self.opts = printer, opts
    def get_printer(self): return self.printer
    def get(self, n, d=None): return self.opts.get(n, d)
    def getint(self, n, d=None, minval=None, maxval=None): return int(self.opts.get(n, d))
    def getfloat(self, n, d=None, minval=None, above=None): return float(self.opts.get(n, d))
    def getboolean(self, n, d=None): return bool(self.opts.get(n, d))

class Pos:
    e = 0.
class GcodeMove:                       # only the ORIGINAL print_stats logic reads it
    def get_status(self, eventtime=None):
        return {"position": Pos(), "extrude_factor": 1.0}

class Extruder:
    def __init__(self): self.last_position = 0.; self.filament_area = 2.405

printer = Printer()
gc = gcode_mod.GCodeDispatch(printer)
printer.objects["gcode"] = gc
printer.objects["gcode_move"] = GcodeMove()
gc._handle_ready()
output = []
gc.register_output_handler(output.append)

calls = []
def m109(gcmd):
    calls.append(("M109", gcmd.get_float("S")))
    printer.reactor.t += 7.
def macro(gcmd):
    calls.append(("MACRO", gcmd.get("FOO"), gcmd.get_int("BAR")))
    printer.reactor.t += 4.
gc.register_command("M109", m109, desc="wait for the nozzle")
gc.register_command("FF_TEST_MACRO", macro, desc="an extended command")

ps = ps_mod.PrintStats(Config(printer))
printer.objects["print_stats"] = ps
for tool in range(4):
    printer.objects["extruder" if tool == 0 else "extruder%%d" %% tool] = Extruder()
ex = lambda t: printer.objects["extruder" if t == 0 else "extruder%%d" %% t]

path = os.path.join(tempfile.mkdtemp(), "ff_stats.json")
stats = ff_stats.FFStats(Config(
    printer, path=path,
    phase_commands="M109=heating, FF_TEST_MACRO=purge, NOT_REGISTERED=mesh"))
printer.objects["ff_stats"] = stats
printer.send_event("klippy:connect")

check("wrapped", stats._wrapped == ["FF_TEST_MACRO", "M109"], stats._wrapped)
check("help kept", gc.gcode_help.get("M109") == "wait for the nozzle", gc.gcode_help.get("M109"))
check("ps fixed", stats.get_status(0)["corrects_print_stats"] is True)

stats.note_job_prepare("/tmp/cubes.gcode", "SDCARD_PRINT_FILE")
gc._process_commands(["M109 S205"])
gc._process_commands(["FF_TEST_MACRO FOO=hello BAR=3"])
check("handlers got real params", calls == [("M109", 205.0), ("MACRO", "hello", 3)], calls)
phases = stats.job["phases"]
check("heating attributed", phases["heating"] == 7., phases)
check("purge attributed", phases["purge"] == 4., phases)
check("stack empty", stats.job["stack"] == [], stats.job["stack"])

# the real print_stats: start it, extrude on two tools, read its status
ex(0).last_position += 50.            # before the print: not counted by print_stats
ps.set_current_file("cubes.gcode")
ps.note_start()
stats._poll(printer.reactor.monotonic())
ex(0).last_position += 100.
ex(1).last_position += 30.
printer.reactor.t += 60.
status = ps.get_status(printer.reactor.monotonic())
check("print_stats measured", status["filament_used"] == 130., status["filament_used"])
check("print_stats state", status["state"] == "printing", status["state"])

ps.note_complete()
stats.note_job_ending("complete")
printer.reactor.t += 3.
stats.note_job_finalize()
check("job closed", stats.job is None)
rec = stats.last_job
check("record filament", rec["filament_mm"][:2] == [150., 30.], rec["filament_mm"])
check("record status", rec["status"] == "completed", rec["status"])

# the console command, through the real dispatcher and the real gcmd API
del output[:]
gc._process_commands(["FF_STATS_SHOW WHAT=SUMMARY"])
text = "\n".join(output)
check("summary printed", "Statistics since" in text and "1 completed" in text, text[:300])
del output[:]
gc._process_commands(["FF_STATS_SHOW WHAT=JOBS COUNT=5"])
check("jobs printed", "cubes.gcode" in "\n".join(output), output)
del output[:]
gc._process_commands(["FF_STATS_SHOW WHAT=nonsense"])
check("bad WHAT rejected", any("WHAT must be" in line for line in output), output)

# the file it wrote reads back
saved = json.load(open(path))
check("saved", saved["lifetime"]["jobs"]["completed"] == 1 and saved["version"] == 1)
json.dumps(stats.get_status(0))

if fails:
    print("FAILURES")
    for line in fails:
        print(line)
else:
    print("ALL OK")
''' % {"klippy": KLIPPY}
    res = _py(box, body)
    assert res.ok and "ALL OK" in res.out, (
        "ff_stats misbehaves against the real Klipper code on %s\nexit=%s\n%s\n%s"
        % (PY, res.code, res.out[-3000:], res.err[-3000:]))

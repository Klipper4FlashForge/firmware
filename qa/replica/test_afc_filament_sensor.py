"""AFC's gate sensors, on the klippy tree and interpreter the printer runs.

THE FAILURE THIS EXISTS FOR

ff-afc.cfg gives each head a `pin_tool_start`, and AFC builds a stock
`filament_switch_sensor` on that pin and wraps its RunoutHelper with its own
DebounceButton (AFC_utils.py). DebounceButton picks how to call the helper
by reading `inspect.signature(runout_helper.note_filament_present)`:

  * (eventtime, is_filament_present)                     -> Klipper's
  * (eventtime, is_filament_present, force, immediate)   -> newer Klipper's
  * anything else with more than two parameters          -> Kalico's

The Klipper4FlashForge fork's helper was
(eventtime, is_filament_present, extruder_pos=None, runout_pos=None): four
parameters, so AFC took it for Kalico and replaced the method with a
one-argument handler. The switch sensor's own button callback still calls it
with two, and the first edge on any gate sensor raised TypeError inside a
button callback -- a klippy shutdown the moment filament moved.

pkgs/klipper overlays filament_switch_sensor.py and filament_motion_sensor.py
to keep upstream's two-parameter shape (the positions go through
note_runout_position). This runs the real pieces against each other: the
installed RunoutHelper, AFC's installed DebounceButton, and the switch
sensor's installed button callback, with only the reactor and printer
stubbed. The negative control puts the fork's four-parameter shape back and
must fail the old way, or the test is not testing anything.
"""
import pytest

pytestmark = pytest.mark.replica

MODDIR = "/usr/data/anvil"
PY = MODDIR + "/bin/python3.13"
ENV = MODDIR + "/anvil-env.sh"
KLIPPY = MODDIR + "/klipper/klippy"

SCRIPT = r"""
. %(env)s
cd %(klippy)s
exec %(py)s - <<'PYEOF'
import sys, types
sys.path.insert(0, ".")
from extras import filament_switch_sensor as fss
from extras import AFC_utils


class Reactor:
    NEVER = 9e99
    def __init__(self):
        self.callbacks = []
    def monotonic(self):
        return 100.0
    def register_callback(self, cb, waketime=None):
        self.callbacks.append((cb, waketime))


class Printer:
    def __init__(self):
        self.reactor = Reactor()
    def get_reactor(self):
        return self.reactor
    def lookup_object(self, name, default=None):
        return object()


class Config:
    def __init__(self, printer):
        self.printer = printer
    def get_printer(self):
        return self.printer
    def getfloat(self, name, default=None, **kw):
        return default


def helper(cls, printer):
    # The installed class, past its constructor: only the state that
    # note_filament_present reads. min_event_systime NEVER keeps it from
    # running any runout/insert gcode -- the question is the call shape.
    h = cls.__new__(cls)
    h.name = "e0_tool_start"
    h.printer = printer
    h.reactor = printer.reactor
    h.filament_present = False
    h.sensor_enabled = True
    h.min_event_systime = Reactor.NEVER
    h.runout_gcode = h.insert_gcode = None
    h.last_extruder_pos = h.last_runout_pos = None
    return h


def edge(cls):
    # AFC_utils.add_filament_switch, minus the config plumbing: a switch
    # sensor, AFC's DebounceButton over its helper, then one button edge
    # through the switch sensor's own callback, then the reactor.
    printer = Printer()
    sensor = types.SimpleNamespace(runout_helper=helper(cls, printer))
    AFC_utils.DebounceButton(Config(printer), sensor)
    fss.SwitchSensor._button_handler(sensor, 100.0, True)
    for cb, waketime in printer.reactor.callbacks:
        cb(waketime)
    return sensor.runout_helper.filament_present


try:
    print("SHIPPED present=%%s" %% edge(fss.RunoutHelper))
except Exception as exc:
    print("SHIPPED raised %%s: %%s" %% (type(exc).__name__, exc))


class ForkShape(fss.RunoutHelper):
    def note_filament_present(self, eventtime, is_filament_present,
                              extruder_pos=None, runout_pos=None):
        return fss.RunoutHelper.note_filament_present(
            self, eventtime, is_filament_present)

try:
    print("FORK present=%%s" %% edge(ForkShape))
except Exception as exc:
    print("FORK raised %%s" %% type(exc).__name__)


h = helper(fss.RunoutHelper, Printer())
h.note_runout_position(12.5, 62.5)
st = h.get_status(0.)
print("STATUS %%s %%s" %% (st["filament_position"], st["runout_position"]))
PYEOF
""" % {"env": ENV, "klippy": KLIPPY, "py": PY}


@pytest.fixture(scope="module")
def result(printer):
    for path in (PY, KLIPPY + "/extras/AFC_utils.py",
                 KLIPPY + "/extras/filament_switch_sensor.py"):
        if not printer.file(path).exists:
            pytest.fail("there is no %s -- `make build` first" % path)
    res = printer.sh(SCRIPT, timeout=300)
    lines = [ln.strip() for ln in res.out.splitlines() if ln.strip()]
    assert lines, "the script printed nothing:\nexit=%s\n%s" % (
        res.code, res.err[-2000:])
    return lines


def test_a_gate_sensor_edge_reaches_afc_and_the_sensor(result):
    assert "SHIPPED present=True" in result, (
        "a filament edge on an AFC gate sensor did not land -- with the "
        "shipped filament_switch_sensor.py, AFC's DebounceButton must pass "
        "it through to the helper:\n  %s" % "\n  ".join(result))


def test_the_forks_four_parameter_shape_still_breaks(result):
    """Negative control: put the fork's signature back and the same edge
    must raise, or the test above proves nothing."""
    assert "FORK raised TypeError" in result, (
        "the fork's (eventtime, is_filament_present, extruder_pos, "
        "runout_pos) shape no longer trips AFC's DebounceButton, so the "
        "overlay is either unnecessary now or this test is not exercising "
        "it:\n  %s" % "\n  ".join(result))


def test_the_motion_sensor_positions_still_report(result):
    """The overlay moved them out of note_filament_present, not away."""
    assert "STATUS 12.5 62.5" in result, "\n  ".join(result)

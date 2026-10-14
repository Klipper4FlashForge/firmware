"""A file that moves beyond the machine is refused at the start, and reading
the file does not stop Klipper (N4S4).

On 2026-10-14 a job whose prime tower reached Y 260.018 was started on a
printer that reaches Y 260.0 (T1's nozzle sits 0.414 mm further out): Klipper
refused the move two minutes in, with the nozzles hot and the mesh probed.
The scan that reads the file for this also used to run in Klipper's own
thread, which the lag watch caught holding it for 0.7 s.
"""

import importlib.util
import threading
from types import SimpleNamespace

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.static

MODULE = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" / "klippy" /
          "extras" / "ff_print.py")

LOW = [-5., -5., -10.]
HIGH = [310., 260., 270.]
# T1 as calibrated on the printer in question: x, y and z against T0
OFFSETS = {0: (0., 0., 2.9156), 1: (-0.0615, 0.4139, 2.9694),
           2: (-0.0243, 0.1168, 2.8833), 3: (0.7048, -0.2725, 2.9069)}


def _module():
    spec = importlib.util.spec_from_file_location("ff_print_bounds", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MOD = _module()


def _scan(tmp_path, text, offsets=None, initial_tool=None, **options):
    path = tmp_path / "job.gcode"
    path.write_bytes(text if isinstance(text, bytes) else text.encode())
    return MOD._scan_extents(str(path), offsets or {}, initial_tool, **options)


def _status(extents):
    return extents.as_status()


# ----------------------------------------------------------------- scanning

def test_the_extents_are_the_smallest_and_largest_positions(tmp_path):
    extents = _scan(tmp_path, "G1 X10 Y20 Z0.2\nG1 X150.5 Y-3\nG1 Z0.4 Y99\n")

    assert _status(extents) == {
        "x_min": 10.0, "x_max": 150.5, "y_min": -3.0, "y_max": 99.0,
        "z_min": 0.2, "z_max": 0.4}


def test_comments_and_other_commands_are_not_positions(tmp_path):
    extents = _scan(tmp_path, (
        "; G1 X999 Y999 Z999\n"
        ";TYPE:Wall outer X500\n"
        "G1 X10 Y10 ; X700 Y700\n"
        "G28 X900\n"
        "G10 X800\n"
        "G92 X600 Y600\n"
        "M104 S220 X77\n"
        "G1 F6000\n"
        "G1 X20 Y20\n"))

    assert _status(extents) == {"x_min": 10.0, "x_max": 20.0,
                                "y_min": 10.0, "y_max": 20.0}


def test_the_end_points_of_arcs_count(tmp_path):
    extents = _scan(tmp_path, "G2 X50 Y60 I5 J5\nG3 X70 Y10 I1 J1\n")

    assert extents.high["x"][0] == 70.0 and extents.high["y"][0] == 60.0


def test_relative_moves_are_not_positions(tmp_path):
    extents = _scan(tmp_path, (
        "G1 X10 Y10\nG91\nG1 X-500 Y-500 Z-50\nG90\nG1 X20 Y20\n"))

    assert _status(extents) == {"x_min": 10.0, "x_max": 20.0,
                                "y_min": 10.0, "y_max": 20.0}


def test_a_file_that_ends_in_relative_mode_ignores_the_rest(tmp_path):
    extents = _scan(tmp_path, "G1 X10 Y10\nG91\nG1 X900 Y900\n")

    assert extents.high["x"][0] == 10.0


def test_a_mounted_tools_offset_is_added(tmp_path):
    extents = _scan(tmp_path, "T1\nG1 X195.232 Y260.018 Z5\n", OFFSETS)

    machine, gcode, tool = extents.high["y"]
    assert machine == pytest.approx(260.4319)
    assert gcode == 260.018 and tool == 1
    assert extents.high["x"][0] == pytest.approx(195.1705)
    assert extents.high["z"][0] == pytest.approx(5 + 2.9694)


def test_the_initial_tool_is_in_force_until_the_first_select(tmp_path):
    extents = _scan(tmp_path, "G1 Y100\nT3\nG1 Y100\n", OFFSETS,
                    initial_tool=1)

    assert extents.high["y"][0] == pytest.approx(100.4139)  # T1, then T3
    assert extents.low["y"][0] == pytest.approx(100 - 0.2725)


def test_the_tool_is_per_stretch_not_per_file(tmp_path):
    """Y 259.8 on T0 and nothing near the edge on T1 is fine; the offset of
    a tool that is not mounted there does not count."""
    extents = _scan(tmp_path, "T0\nG1 Y259.8\nT1\nG1 Y100\n", OFFSETS)

    assert extents.high["y"][0] == pytest.approx(259.8)
    assert extents.high["y"][2] == 0


def test_a_select_with_more_digits_is_not_a_tool(tmp_path):
    extents = _scan(tmp_path, "T10\nG1 Y100\n", OFFSETS, initial_tool=0)

    assert extents.high["y"][2] == 0


@pytest.mark.parametrize("chunk", [1, 3, 7, 16, 4096])
def test_pieces_of_any_size_give_the_same_extents(tmp_path, chunk):
    text = ("T1\r\nG1 X10.5 Y20 Z0.2 ; a\r\nG1 X-4.5 Y259.9\r\nG91\r\n"
            "G1 X900\r\nG90\r\nT0\r\nG1 X300 Y-1.25\r\n")

    reference = _scan(tmp_path, text, OFFSETS, chunk=1 << 20)
    pieces = _scan(tmp_path, text, OFFSETS, chunk=chunk)

    assert _status(pieces) == _status(reference)
    assert pieces.high["y"] == reference.high["y"]


def test_a_last_line_without_a_newline_is_read(tmp_path):
    extents = _scan(tmp_path, "G1 X10\nG1 X77 Y5", chunk=4)

    assert extents.high["x"][0] == 77.0


def test_a_file_without_moves_has_no_extents(tmp_path):
    extents = _scan(tmp_path, "; nothing\nM104 S200\n")

    assert _status(extents) == {}


def test_the_scan_lets_go_of_the_interpreter_between_pieces(tmp_path):
    pauses = []

    _scan(tmp_path, "G1 X1\n" * 50, chunk=32, pause=pauses.append)

    assert len(pauses) >= 5 and set(pauses) == {MOD.EXTENTS_YIELD}


def test_a_file_that_takes_too_long_is_not_checked(tmp_path):
    assert _scan(tmp_path, "G1 X1\n" * 100, chunk=8, budget=-1.0) is None


def test_a_file_that_cannot_be_read_is_not_checked(tmp_path):
    assert MOD._scan_extents(str(tmp_path / "gone.gcode"), {}) is None


# ------------------------------------------------------------ what is wrong

def test_a_file_inside_the_range_has_no_problems(tmp_path):
    extents = _scan(tmp_path, "G1 X-5 Y260 Z269.9\nG1 X310 Y-5 Z0\n")

    assert MOD._bounds_problems(extents, LOW, HIGH) == []


def test_each_side_of_x_and_y_and_the_top_of_z_are_checked(tmp_path):
    extents = _scan(tmp_path, "G1 X-5.5 Y260.5 Z271\nG1 X311 Y-6 Z-20\n")

    problems = MOD._bounds_problems(extents, LOW, HIGH)

    assert sorted((p[0], p[1]) for p in problems) == [
        ("x", "above"), ("x", "below"), ("y", "above"), ("y", "below"),
        ("z", "above")]                  # Z below is not a problem


def test_the_message_names_the_axis_the_value_and_the_tool(tmp_path):
    extents = _scan(tmp_path, "T1\nG1 Y260.018\n", OFFSETS)
    (problem,) = MOD._bounds_problems(extents, LOW, HIGH)

    text = MOD._describe_problem(problem)

    assert text == ("Y up to 260.4 mm (G-code Y260.018 and T1's offset "
                    "+0.414) -- the machine reaches 260.0 mm")


def test_no_offset_is_mentioned_when_there_is_none(tmp_path):
    extents = _scan(tmp_path, "T0\nG1 Y262.7\n", {0: (0., 0., 0.)})
    (problem,) = MOD._bounds_problems(extents, LOW, HIGH)

    assert "offset" not in MOD._describe_problem(problem)


# ------------------------------------------------------- the print start

class Reactor:
    def __init__(self):
        self.now = 0.
        self.pauses = 0
        self.on_pause = None

    def monotonic(self):
        return self.now

    def pause(self, waketime):
        self.pauses += 1
        self.now = max(self.now, waketime)
        if self.on_pause is not None:
            self.on_pause()
        return self.now


class CommandError(Exception):
    pass


class Gcmd:
    error = CommandError

    def get(self, name, default=""):
        return "job.gcode"


class Rig:
    pass


def _rig(tmp_path, text, limits=(LOW, HIGH), offsets=OFFSETS, options=None,
         reactor=True):
    (tmp_path / "job.gcode").write_text(text, encoding="utf-8")
    rig = Rig()
    rig.scripts, rig.base_calls, rig.stats = [], [], []
    rig.reactor = Reactor() if reactor else None
    gcode = SimpleNamespace(
        run_script_from_command=rig.scripts.append)
    stats = SimpleNamespace(
        note_job_prepare=lambda *a: rig.stats.append(("prepare",) + a),
        note_job_aborted=lambda *a: rig.stats.append(("aborted",) + a))
    objects = {
        "gcode": gcode, "ff_stats": stats,
        "virtual_sdcard": SimpleNamespace(sdcard_dirname=str(tmp_path))}
    if limits is not None:
        objects["toolhead"] = SimpleNamespace(
            get_status=lambda eventtime: {
                "axis_minimum": limits[0] + [0.], "axis_maximum":
                limits[1] + [0.]})
    if offsets is not None:
        objects["ff_toolchange"] = SimpleNamespace(
            offset_x=[offsets[t][0] for t in range(4)],
            offset_y=[offsets[t][1] for t in range(4)],
            offset_z=[offsets[t][2] for t in range(4)])
    printer = SimpleNamespace(
        get_reactor=lambda: rig.reactor,
        lookup_object=lambda name, default=None: objects.get(name, default),
        register_event_handler=lambda event, handler: None)
    options = options or {}
    config = SimpleNamespace(
        get_printer=lambda: printer,
        get=lambda name, default=None: default,
        getlist=lambda name, default=None: default,
        getboolean=lambda name, default=None: options.get(name, default))
    rig.ff = MOD.FFPrint(config)
    rig.ff.previous_handlers["SDCARD_PRINT_FILE"] = rig.base_calls.append
    return rig


def _start(rig):
    rig.ff._cmd_start("SDCARD_PRINT_FILE", Gcmd())


def test_a_file_beyond_the_range_is_refused_before_anything_happens(tmp_path):
    """The file of 2026-10-14, in small: T1's prime tower at Y260.018."""
    rig = _rig(tmp_path, "T1\nM109 S220\nG1 X195.232 Y260.018 Z0.2\n")

    with pytest.raises(CommandError) as refused:
        _start(rig)

    text = str(refused.value)
    assert "not starting job.gcode" in text
    assert "Y up to 260.4 mm" in text and "T1's offset +0.414" in text
    assert "check_bounds" in text
    assert rig.scripts == [] and rig.base_calls == []   # no macro, no file
    assert rig.stats == []                              # and no job


def test_the_same_file_on_t0_alone_is_fine(tmp_path):
    rig = _rig(tmp_path, "T0\nM109 S220\nG1 X195.232 Y259.99 Z0.2\n")

    _start(rig)

    assert len(rig.scripts) == 1 and len(rig.base_calls) == 1


def test_a_file_that_exceeds_even_without_an_offset_is_refused(tmp_path):
    rig = _rig(tmp_path, "T0\nG1 X1 Y262.696\n", offsets=None)

    with pytest.raises(CommandError, match="Y up to 262.7"):
        _start(rig)


def test_every_problem_is_named(tmp_path):
    rig = _rig(tmp_path, "T0\nG1 X-9 Y270\n", offsets=None)

    with pytest.raises(CommandError) as refused:
        _start(rig)

    assert "X down to -9.0" in str(refused.value)
    assert "Y up to 270.0" in str(refused.value)


def test_a_file_inside_the_range_starts_and_reports_its_extents(tmp_path):
    rig = _rig(tmp_path, "T0\nM104 S220\nG1 X10 Y20 Z0.2\nG1 X200 Y250\n")

    _start(rig)

    assert rig.base_calls != [] and len(rig.scripts) == 1
    assert rig.ff.get_status(0.)["extents"] == {
        "x_min": 10.0, "x_max": 200.0, "y_min": 20.0, "y_max": 250.0,
        "z_min": 3.116, "z_max": 3.116}      # 0.2 + T0's gap 2.9156, to 3 places


def test_with_the_check_off_the_file_is_not_even_read_for_it(tmp_path):
    rig = _rig(tmp_path, "T1\nG1 Y999\n", options={"check_bounds": False})

    _start(rig)

    assert rig.base_calls != []
    assert rig.ff.get_status(0.)["extents"] is None


def test_the_check_is_on_unless_turned_off(tmp_path):
    assert _rig(tmp_path, "T0\n").ff.check_bounds is True


def test_without_a_toolhead_there_is_nothing_to_check_against(tmp_path):
    rig = _rig(tmp_path, "T1\nG1 Y999\n", limits=None)

    _start(rig)

    assert rig.base_calls != []


def test_without_a_toolchanger_no_offset_is_added(tmp_path):
    rig = _rig(tmp_path, "T1\nG1 Y259.99\n", offsets=None)

    _start(rig)

    assert rig.base_calls != []


def test_a_file_that_cannot_be_scanned_is_started_anyway(tmp_path, monkeypatch):
    rig = _rig(tmp_path, "T1\nG1 Y999\n")
    monkeypatch.setattr(MOD, "_scan_extents", lambda *a, **k: None)

    _start(rig)

    assert rig.base_calls != []
    assert rig.ff.get_status(0.)["extents"] is None


def test_the_job_is_started_as_before_with_the_same_parameters(tmp_path):
    rig = _rig(tmp_path, "; filament: 1,3\nM104 S220\nT0\nT2\nM109 S240\n"
                         "G1 X10 Y10\n")

    _start(rig)

    assert rig.scripts == [
        "FF_BEFORE_PRINT_START ORIGIN=SDCARD_PRINT_FILE TOOL=0 NOZZLE=220"]
    assert [entry[0] for entry in rig.stats] == ["prepare"]


# ---------------------------------------------------------- the thread

def test_the_file_is_read_in_a_thread_and_the_reactor_goes_on(
        tmp_path, monkeypatch):
    """While the thread reads, the start waits with reactor.pause, which is
    what keeps clock sync, the heaters and queued motion running. The
    reading is held until the reactor has paused, so this fails if the start
    simply called it."""
    rig = _rig(tmp_path, "T0\nG1 X10 Y10\n")
    seen = []
    released = threading.Event()
    real = MOD._analyse_file

    def reading(path, offsets, want_extents):
        seen.append(threading.current_thread())
        assert released.wait(5.), "the reactor never got a turn"
        return real(path, offsets, want_extents)

    monkeypatch.setattr(MOD, "_analyse_file", reading)
    rig.reactor.on_pause = released.set

    _start(rig)

    assert seen and seen[0] is not threading.main_thread()
    assert seen[0].name == "ff_print-scan"
    assert rig.reactor.pauses >= 1
    assert rig.base_calls != []


def test_a_start_without_a_reactor_still_works(tmp_path):
    rig = _rig(tmp_path, "T0\nG1 X10 Y10\n", reactor=False, limits=None)

    _start(rig)

    assert rig.base_calls != []


def test_an_error_in_the_thread_reaches_the_start(tmp_path, monkeypatch):
    rig = _rig(tmp_path, "T0\n")

    def broken(*args):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(MOD, "_analyse_file", broken)

    with pytest.raises(RuntimeError, match="disk gone"):
        _start(rig)

    assert rig.base_calls == []


def test_the_thread_is_finished_when_the_start_returns(tmp_path):
    rig = _rig(tmp_path, "T0\nG1 X10 Y10\n")

    _start(rig)

    assert not [t for t in threading.enumerate()
                if t.name == "ff_print-scan"]

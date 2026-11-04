"""Hardware-free smoke check using the printer's real gcode/configfile code."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types

sys.path.insert(0, sys.argv[1])
import configfile  # noqa: E402
import gcode  # noqa: E402

spec = importlib.util.spec_from_file_location('nozzle_extra', sys.argv[2])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Mutex:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Printer:
    config_error = configfile.error
    command_error = gcode.CommandError

    def __init__(self, path):
        self.path, self.objects, self.exits = path, {}, []
        self.objects['toolhead'] = types.SimpleNamespace(
            check_busy=lambda now: (0., 1., True),
            get_last_move_time=lambda: 0., dwell=lambda seconds: None,
            wait_moves=lambda: None)
        self.gc = gcode.GCodeDispatch(self)
        self.objects['gcode'] = self.gc
        self.cf = configfile.PrinterConfig(self)
        self.objects['configfile'] = self.cf
        cfg = self.cf.read_main_config()
        self.extra = module.load_config(cfg.getsection('ff_nozzle_size'))
        self.output = []
        self.gc.register_output_handler(self.output.append)
        self.gc._handle_ready()

    def get_start_args(self):
        return {'config_file': str(self.path)}

    def get_reactor(self):
        return types.SimpleNamespace(monotonic=lambda: 1., mutex=lambda: Mutex())

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def register_event_handler(self, *args):
        pass

    def send_event(self, *args):
        pass

    def request_exit(self, result):
        self.exits.append(result)

    def run(self, command):
        self.gc.run_script_from_command(command)


with tempfile.TemporaryDirectory(prefix='nozzle-native-') as temp:
    root = Path(temp)
    path = root / 'printer.cfg'
    include = root / 'custom.cfg'
    include.write_text('# empty custom include\n')
    original = '[include custom.cfg]\n[ff_nozzle_size]\n'
    for tool in range(4):
        name = 'extruder' if tool == 0 else 'extruder%d' % tool
        original += '[%s]\nnozzle_diameter: 0.400\n' % name
    original += (configfile.AUTOSAVE_HEADER
                 + '#*# [probe]\n#*# z_offset = 1.234\n')
    path.write_text(original)
    printer = Printer(path)
    printer.run('FF_NOZZLE_SIZE_SET TOOL=0 DIAMETER=.6')
    printer.run('FF_NOZZLE_SIZE_SET TOOL=3 DIAMETER=.25')
    assert path.read_text() == original
    assert not printer.cf.get_status(1.)['save_config_pending']
    printer.run('FF_NOZZLE_SIZE_APPLY')
    assert printer.exits, 'SAVE_CONFIG did not request a restart'
    backups = list(root.glob('printer-*.cfg'))
    assert len(backups) == 1 and backups[0].read_text() == original
    saved = path.read_text()
    assert '#nozzle_diameter: 0.400' in saved
    assert '#*# nozzle_diameter = 0.600000' in saved
    assert '#*# nozzle_diameter = 0.250000' in saved
    assert '#*# z_offset = 1.234' in saved
    restarted = Printer(path)
    assert restarted.extra.diameters == {0: .6, 1: .4, 2: .4, 3: .25}
    assert restarted.extra.pending == {}
    restarted.run('FF_NOZZLE_SIZE_APPLY')
    assert not restarted.exits
    print('PASS: native SAVE_CONFIG, backup, calibration preserved, reload, no-op')

    # An include wins at load; native SAVE_CONFIG must refuse to overwrite it.
    include.write_text('[extruder3]\nnozzle_diameter: .8\n')
    conflicting = Printer(path)
    assert conflicting.extra.diameters[3] == .8
    old_autosave = configfile.ConfigFileReader().build_config_string(
        conflicting.cf.autosave.fileconfig)
    conflicting.run('FF_NOZZLE_SIZE_SET TOOL=3 DIAMETER=.6')
    try:
        conflicting.run('FF_NOZZLE_SIZE_APPLY')
    except gcode.CommandError as exc:
        assert 'conflicts' in str(exc)
    else:
        raise AssertionError('Included nozzle override was not rejected')
    assert path.read_text() == saved
    assert not conflicting.exits
    assert not conflicting.cf.get_status(1.)['save_config_pending']
    assert configfile.ConfigFileReader().build_config_string(
        conflicting.cf.autosave.fileconfig) == old_autosave
    assert conflicting.extra.pending == {3: .6}
    print('PASS: native include conflict, autosave rollback, staged edits retained')

    include.write_text('# no nozzle overrides\n')
    calibrating = Printer(path)
    calibrating.run('FF_NOZZLE_SIZE_SET TOOL=1 DIAMETER=.8')
    calibrating.cf.set('probe', 'z_offset', '1.5')
    try:
        calibrating.run('FF_NOZZLE_SIZE_APPLY')
    except gcode.CommandError as exc:
        assert 'Other calibration' in str(exc)
    else:
        raise AssertionError('Pending calibration was not protected')
    assert path.read_text() == saved
    assert calibrating.cf.autosave.status_save_pending == {
        'probe': {'z_offset': '1.5'}}
    assert not calibrating.exits
    print('PASS: native pending calibration protected')

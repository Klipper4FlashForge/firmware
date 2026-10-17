"""Nozzle edits must be staged, idle-only, and saved through native Klipper."""

import configparser
import importlib.util
import types

import jinja2
import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.static

MODULE = (ROOT / 'pkgs/klipper/payload/klipper/klippy/extras/ff_nozzle_size.py')
CONFIG = ROOT / 'pkgs/klipper-config/payload/config/printer_n4s4.cfg'


class Gcmd:
    error = RuntimeError

    def __init__(self, **params):
        self.params = params
        self.responses = []

    def _get(self, name, cast, minval, maxval):
        try:
            value = cast(self.params[name])
        except (KeyError, ValueError):
            raise self.error('Invalid or missing ' + name)
        if value < minval or value > maxval:
            raise self.error('Out of range ' + name)
        return value

    def get_int(self, name, minval, maxval):
        return self._get(name, int, minval, maxval)

    def get_float(self, name, minval, maxval):
        return self._get(name, float, minval, maxval)

    def respond_info(self, message):
        self.responses.append(message)


class Config:
    def __init__(self, printer):
        self.printer = printer
        self.values = {'extruder' if t == 0 else 'extruder%d' % t: 0.4
                       for t in range(4)}

    def get_printer(self):
        return self.printer

    def has_section(self, name):
        return name in self.values

    def getsection(self, name):
        return types.SimpleNamespace(getfloat=lambda *a, **kw: self.values[name])


class Saver:
    def __init__(self):
        self.fileconfig = configparser.RawConfigParser()
        self.fileconfig.read_string('[probe]\nz_offset = 1.234\n')
        self.save_config_pending = False
        self.status_save_pending = {}

    def get_status(self, eventtime):
        return {'save_config_pending': self.save_config_pending}

    def set(self, section, option, value):
        if not self.fileconfig.has_section(section):
            self.fileconfig.add_section(section)
        self.fileconfig.set(section, option, value)
        self.save_config_pending = True
        self.status_save_pending.setdefault(section, {})[option] = value


class Printer:
    def __init__(self):
        self.commands = {}
        self.scripts = []
        self.motion = (0., 1., True)
        self.objects = {
            'gcode': types.SimpleNamespace(
                register_command=lambda name, handler, **kw:
                    self.commands.setdefault(name, handler),
                run_script_from_command=self.run_script),
            'toolhead': types.SimpleNamespace(check_busy=lambda now: self.motion),
            'configfile': Saver(),
        }

    def run_script(self, script):
        self.scripts.append(script)

    def lookup_object(self, name, default=None):
        return self.objects.get(name, default)

    def get_reactor(self):
        return types.SimpleNamespace(monotonic=lambda: 1.)


@pytest.fixture
def setup():
    spec = importlib.util.spec_from_file_location('ff_nozzle_test', MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    printer = Printer()
    config = Config(printer)
    return module.load_config(config), printer, config


def test_staging_never_changes_live_sizes_or_native_autosave(setup):
    extra, printer, _ = setup
    extra.cmd_SET(Gcmd(TOOL='3', DIAMETER='0.25'))
    assert extra.get_status(0) == {
        'diameters': {'T0': .4, 'T1': .4, 'T2': .4, 'T3': .4},
        'pending': {'T3': .25}}
    assert not printer.objects['configfile'].save_config_pending
    assert not printer.objects['configfile'].fileconfig.has_section('extruder3')
    assert printer.scripts == []


def test_apply_saves_multiple_tools_once_and_preserves_calibration(setup):
    extra, printer, _ = setup
    extra.cmd_SET(Gcmd(TOOL='0', DIAMETER='0.6'))
    extra.cmd_SET(Gcmd(TOOL='3', DIAMETER='0.25'))
    extra.cmd_APPLY(Gcmd())
    saved = printer.objects['configfile'].fileconfig
    assert saved.getfloat('extruder', 'nozzle_diameter') == .6
    assert saved.getfloat('extruder3', 'nozzle_diameter') == .25
    assert saved.getfloat('probe', 'z_offset') == 1.234
    assert not saved.has_section('extruder1')
    assert printer.scripts == ['SAVE_CONFIG']
    assert extra.pending == {}
    assert extra.diameters[0] == .4  # new limits take effect only on restart


def test_returning_to_active_value_cancels_staged_edit_without_restart(setup):
    extra, printer, _ = setup
    extra.cmd_SET(Gcmd(TOOL=1, DIAMETER='.8'))
    extra.cmd_SET(Gcmd(TOOL=1, DIAMETER='.4'))
    extra.cmd_APPLY(Gcmd())
    assert extra.pending == {}
    assert printer.scripts == []


@pytest.mark.parametrize('params', [
    {}, {'TOOL': 0}, {'DIAMETER': .25}, {'TOOL': -1, 'DIAMETER': .25},
    {'TOOL': 4, 'DIAMETER': .25}, {'TOOL': '1.5', 'DIAMETER': .25},
    *({'TOOL': 0, 'DIAMETER': size}
      for size in ('nan', 'inf', '-inf', 'oops', '0', '.09', '1.21')),
])
def test_invalid_input_never_stages_a_change(setup, params):
    extra, printer, _ = setup
    with pytest.raises(RuntimeError):
        extra.cmd_SET(Gcmd(**params))
    assert extra.pending == {}
    assert not printer.objects['configfile'].save_config_pending


@pytest.mark.parametrize('busy', ['sd', 'pause', 'printing', 'paused',
                                  'motion', 'lookahead'])
def test_set_and_apply_are_blocked_while_printing_paused_or_moving(setup, busy):
    extra, printer, _ = setup
    extra.cmd_SET(Gcmd(TOOL=2, DIAMETER=.6))
    if busy == 'sd':
        printer.objects['virtual_sdcard'] = types.SimpleNamespace(is_active=lambda: True)
    elif busy == 'pause':
        printer.objects['pause_resume'] = types.SimpleNamespace(is_paused=True)
    elif busy in ('printing', 'paused'):
        printer.objects['print_stats'] = types.SimpleNamespace(
            get_status=lambda now: {'state': busy})
    else:
        printer.motion = (2., 1., True) if busy == 'motion' else (0., 1., False)
    for command, gcmd in ((extra.cmd_SET, Gcmd(TOOL=3, DIAMETER=.8)),
                          (extra.cmd_APPLY, Gcmd())):
        with pytest.raises(RuntimeError):
            command(gcmd)
    assert extra.pending == {2: .6}
    assert printer.scripts == []
    status = Gcmd()
    extra.cmd_STATUS(status)  # reading is always permitted
    assert 'T2 [extruder2]: 0.400 mm -> 0.600 mm staged' in status.responses[0]


def test_pending_calibration_is_not_silently_saved_by_apply(setup):
    extra, printer, _ = setup
    extra.cmd_SET(Gcmd(TOOL=2, DIAMETER=.6))
    saver = printer.objects['configfile']
    saver.set('probe', 'z_offset', '1.5')
    with pytest.raises(RuntimeError, match='Other calibration'):
        extra.cmd_APPLY(Gcmd())
    assert saver.status_save_pending == {'probe': {'z_offset': '1.5'}}
    assert not saver.fileconfig.has_section('extruder2')
    assert printer.scripts == []


@pytest.mark.parametrize('split_autosave', [False, True])
def test_failed_native_save_rolls_back_only_its_autosave_changes(setup, split_autosave):
    extra, printer, _ = setup
    extra.cmd_SET(Gcmd(TOOL=2, DIAMETER=.6))
    saver = printer.objects['configfile']
    if split_autosave:
        printer.objects['configfile'] = types.SimpleNamespace(
            autosave=saver, get_status=saver.get_status, set=saver.set)

    def fail(script):
        raise RuntimeError('SAVE_CONFIG conflicts with included value')

    printer.objects['gcode'].run_script_from_command = fail
    with pytest.raises(RuntimeError, match='conflicts with included'):
        extra.cmd_APPLY(Gcmd())
    assert extra.pending == {2: .6}
    assert not saver.save_config_pending
    assert saver.status_save_pending == {}
    assert saver.fileconfig.getfloat('probe', 'z_offset') == 1.234
    assert not saver.fileconfig.has_section('extruder2')


def test_restart_reads_saved_sizes_and_discards_unapplied_staging(setup):
    extra, printer, config = setup
    extra.cmd_SET(Gcmd(TOOL=0, DIAMETER=.6))
    config.values['extruder3'] = .25
    restarted = type(extra)(config)
    assert restarted.diameters == {0: .4, 1: .4, 2: .4, 3: .25}
    assert restarted.pending == {}


def test_macro_tiles_forward_parameters_without_truncation():
    cp = configparser.RawConfigParser()
    cp.read(CONFIG, encoding='utf-8')
    assert cp.has_section('ff_nozzle_size')
    env = jinja2.Environment('{%', '%}', '{', '}', undefined=jinja2.StrictUndefined)
    for suffix in ('STATUS', 'SET', 'APPLY'):
        code = cp.get('gcode_macro NOZZLE_SIZE_' + suffix, 'gcode')
        out = env.from_string(code).render(params={'TOOL': '1.5', 'DIAMETER': '.25'})
        expected = 'FF_NOZZLE_SIZE_' + suffix
        if suffix == 'SET':
            expected += ' TOOL=1.5 DIAMETER=.25'
        assert out.strip() == expected


@pytest.mark.parametrize('params', [{}, {'TOOL': '3'}, {'DIAMETER': '.25'}])
def test_macro_reports_usage_instead_of_an_undefined_parameter(params):
    cp = configparser.RawConfigParser()
    cp.read(CONFIG, encoding='utf-8')
    env = jinja2.Environment('{%', '%}', '{', '}', undefined=jinja2.StrictUndefined)

    def fail(message):
        raise RuntimeError(message)

    code = cp.get('gcode_macro NOZZLE_SIZE_SET', 'gcode')
    with pytest.raises(RuntimeError, match='Usage: NOZZLE_SIZE_SET'):
        env.from_string(code).render(params=params, action_raise_error=fail)

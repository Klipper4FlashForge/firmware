#!/usr/bin/env python3
"""Run a G-code scenario through the real Klipper/AFC host stack."""
import argparse
import configparser
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import tempfile
import sys

from scenarios import SCENARIOS
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from setup import ROOT, WORK, prepare


class Database(BaseHTTPRequestHandler):
    """In-memory stand-in for Moonraker persistence, outside motion control."""
    def log_message(self, *args):
        pass

    def respond(self):
        path = urlparse(self.path).path
        body = {}
        if path == '/server/config':
            body = {'config': {}, 'orig': {}}
        elif path == '/server/database/item':
            body = {'value': {}}
        elif path == '/server/info':
            body = {'klippy_connected': True, 'klippy_state': 'ready'}
        data = json.dumps({'result': body}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_DELETE = respond


def config(output, port, args):
    base = configparser.RawConfigParser(strict=False, inline_comment_prefixes=('#', ';'))
    base.read(ROOT / 'pkgs/klipper-config/payload/config/printer.base.cfg')
    limits = '\n'.join(f'{key}: {base.get("printer", key)}' for key in
                       ['kinematics', 'max_velocity', 'max_accel', 'max_z_velocity', 'max_z_accel'])
    lines = [f'''[mcu]
serial: /dev/null
[printer]
{limits}
[creator5_sim]
output: {output / 'trace.json'}
sensor_delay: {args.sensor_delay}
lock_time: {args.lock_time if args.lock_time is not None else 0.25}
heating_rate: {args.heating_rate}
initial_temperature: {args.temperature}
fail_grab: {args.fail_grab}
fail_unlock: {args.fail_unlock}
async_unlock: {bool(args.async_unlock)}
fail_seat: {args.fail_seat}
{f'unlock_duration: {args.unlock_duration}' if args.unlock_duration is not None else ''}
motor_mode: {'production' if args.lock_time is None else 'fixed'}
geometry_source: {'override calibration' if args.overrides else 'synthetic calibration fixture'}
[force_move]
enable_force_move: True
[virtual_sdcard]
path: {output}
[pause_resume]
[display_status]
[include {Path(__file__).with_name('brush.cfg').resolve()}]
''']
    pin = 0
    for axis in 'xyz':
        geometry = '\n'.join(f'{key}: {base.get("stepper_" + axis, key)}' for key in
                             ['microsteps', 'rotation_distance', 'position_min', 'position_max'])
        lines.append(f'''[stepper_{axis}]
step_pin: gpio{pin}
dir_pin: gpio{pin + 1}
endstop_pin: gpio{pin + 2}
{geometry}
position_endstop: 0
''')
        pin += 3
    for i in range(4):
        name = 'extruder' if i == 0 else f'extruder{i}'
        lines.append(f'''[{name}]
step_pin: gpio{pin}
dir_pin: gpio{pin + 1}
heater_pin: gpio{pin + 2}
sensor_pin: analog{i}
sensor_type: EPCOS 100K B57560G104F
microsteps: 16
rotation_distance: 7
nozzle_diameter: 0.4
filament_diameter: 1.75
control: watermark
min_temp: 0
max_temp: 320
[ff_tool {i}]
dock_x: 297
dock_y: {20 + i * 80}
nozzle_x: {i * 0.1}
nozzle_y: {i * -0.1}
nozzle_z: {i * 0.02}
''')
        pin += 3
        for sensor in [f'extruder_pos{i + 1}', f'extruder_grab{i + 1}']:
            lines.append(f'[gcode_button {sensor}]\npin: gpio{pin}\npress_gcode:\n')
            pin += 1
    if args.lock_time is None:
        lines.append(f'[include {Path(__file__).with_name("motor.cfg").resolve()}]')
    else:
        for motor in ['GRAB', 'GRAB2', 'RELEASE', 'STOP']:
            lines.append(f'[gcode_macro MOTOR_{motor}]\ngcode:\n    SIM_LOCK ACTION={motor}\n')
    lines.append(f'''[ff_toolchange]
[include {ROOT / 'pkgs/klipper-config/payload/config/ff-afc.cfg'}]
[AFC]
VarFile: {output / 'AFC/AFC.var'}
moonraker_host: http://127.0.0.1
moonraker_port: {port}
[delayed_gcode _FF_AFC_PREP]
initial_duration: 0
[ff_toolchange]
contact_speed: {args.contact_speed}
withdrawal_speed: {args.withdrawal_speed}
contact_accel: {args.contact_accel}
[gcode_macro LOAD_FILAMENT]
gcode:
    SIM_LOAD TOOL={{params.TOOL}}
[gcode_macro UNLOAD_FILAMENT]
gcode:
    SIM_UNLOAD TOOL={{params.TOOL}}
''')
    # Reuse shipped native motor macros; full ff-toolchange.cfg also wraps
    # vendor resonance commands absent from the desktop fixture.
    native_motor = configparser.RawConfigParser(strict=False)
    native_motor.read(ROOT / 'pkgs/klipper-config/payload/config/ff-toolchange.cfg')
    lines.append('[ff_toolchange]')
    for option, action in [('grab_macro', 'GRAB'), ('grab2_macro', 'GRAB2'), ('release_macro', 'RELEASE')]:
        macro = native_motor.get('ff_toolchange', option)
        lines.append(f'{option}: {macro}')
    for action in ['GRAB', 'GRAB2', 'RELEASE']:
        option = {'GRAB': 'grab_macro', 'GRAB2': 'grab2_macro', 'RELEASE': 'release_macro'}[action]
        macro = native_motor.get('ff_toolchange', option)
        body = (native_motor.get('gcode_macro ' + macro, 'gcode') if args.lock_time is None
                else f'SIM_LOCK ACTION={action}')
        lines.append(f'[gcode_macro {macro}]\ngcode:\n' +
                     '\n'.join('    ' + line for line in body.strip().splitlines()))
    for i in range(4):
        lines.append(f'[AFC_extruder e{i}]\npin_tool_start: gpio{pin}\n')
        pin += 1
    if args.overrides:
        (output / 'overrides.cfg').write_text(args.overrides.read_text())
        lines.append(f'[include {output / "overrides.cfg"}]')
    (output / 'printer.cfg').write_text('\n'.join(lines))
    state = {'system': {'extruders': {f'e{i}': {'lane_loaded': f'e{i}'} for i in range(4)}}}
    state['Tools'] = {f'e{i}': {'map': [f'T{i}'], 'tool_loaded': True,
                               'material': 'PLA', 'extruder_temp': 220} for i in range(4)}
    (output / 'AFC').mkdir(exist_ok=True)
    (output / 'AFC/AFC.var.unit').write_text(json.dumps(state))


DEFAULTS = dict(sensor_delay=.05, lock_time=None, heating_rate=3., temperature=220.,
                fail_grab=-1, fail_unlock=False, contact_speed=20.,
                withdrawal_speed=20., contact_accel=50., async_unlock=0, unlock_duration=None, fail_seat=-1)


def run_one(name, args, klipper, port):
    scene = SCENARIOS[name]
    settings = dict(DEFAULTS, **scene.get('settings', {}))
    settings.update({key: getattr(args, key) for key in DEFAULTS
                     if getattr(args, key) is not None})
    if settings['lock_time'] is not None:
        if args.async_unlock is None:
            settings['async_unlock'] = 0
        if args.unlock_duration is None:
            settings['unlock_duration'] = None
    selected = argparse.Namespace(**vars(args))
    for key, value in settings.items():
        setattr(selected, key, value)
    if settings['sensor_delay'] < 0 or (settings['lock_time'] is not None and settings['lock_time'] < 0) or settings['heating_rate'] <= 0:
        raise ValueError('delays must be nonnegative and heating rate must be positive')
    # Runtime input files and logs are temporary. All reviewable results are
    # collected into one catalog and one replay, rather than scenario folders.
    with tempfile.TemporaryDirectory(prefix='creator5-sim-') as directory:
        output = Path(directory)
        config(output, port, selected)
        (output / 'scenario.gcode').write_text(scene['gcode'])
        command = [sys.executable, str(Path(__file__).with_name('host.py')), str(klipper),
                   str(output / 'printer.cfg'), '-i', str(output / 'scenario.gcode'),
                   '-o', str(output / 'mcu.commands'), '-d', str(klipper / 'out/klipper.dict'),
                   '-l', str(output / 'klippy.log')]
        result = subprocess.run(command, timeout=60, capture_output=True, text=True)
        if not (output / 'trace.json').exists():
            raise RuntimeError((output / 'klippy.log').read_text()[-6000:] + result.stderr)
        trace = json.loads((output / 'trace.json').read_text())
        trace['scenario'] = name
        trace['label'] = scene['label']
        trace['inputs'] = dict(gcode=scene['gcode'], settings=settings,
                               printer_config=(output / 'printer.cfg').read_text(),
                               overrides=args.overrides.read_text() if args.overrides else None)
        trace['logs'] = {'klippy': (output / 'klippy.log').read_text(),
                          'afc': (output / 'AFC.log').read_text() if (output / 'AFC.log').exists() else ''}
        failed = bool(result.returncode or trace['summary']['errors'] or not trace['summary']['checks'])
        expected = scene.get('expected_error')
        trace['expected_error'] = expected
        trace['verified'] = (failed and any(expected in e for e in trace['summary']['errors'])
                             if expected else not failed)
        if trace['summary']['brush_warnings'] and not expected:
            trace['verified'] = False
        return trace


def replay(traces):
    template = Path(__file__).with_name('viewer.html').read_text()
    visible = {name: traces[name] for name, scene in SCENARIOS.items()
               if scene.get('visible', True) and name in traces}
    # Explicitly running an isolated reference fixture still yields a usable
    # page; references never appear alongside the public replay catalog.
    payload = json.dumps({'traces': visible or traces, 'template': template}).replace('<', '\\u003c')
    return """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Creator 5 simulator</title>
<style>body{margin:0;background:#101722;color:#e7edf7;font:15px system-ui}nav{max-width:1052px;margin:24px auto 0;padding:0 24px;display:flex;gap:14px;align-items:center}select{background:#2b4059;color:white;border:1px solid #415b79;padding:10px;border-radius:7px}iframe{border:0;width:100%;display:block}#result{color:#9eafc6}</style>
<nav><label for="scenario">Scenario</label><select id="scenario"></select><span id="result"></span></nav>
<iframe id="replay" title="Selected scenario movement replay"></iframe>
<script id="catalog" type="application/json">""" + payload + """</script>
<script>
const catalog=JSON.parse(document.getElementById('catalog').textContent);
const select=document.getElementById('scenario'),frame=document.getElementById('replay');
for(const [id,trace] of Object.entries(catalog.traces)){const option=document.createElement('option');option.value=id;option.textContent=trace.label;select.append(option)}
function show(){const trace=catalog.traces[select.value];document.getElementById('result').textContent=trace.verified?(trace.expected_error?'Expected failure verified':'Verified'):'Failed';frame.srcdoc=catalog.template.replace('__TRACE_JSON__',JSON.stringify(trace).replaceAll('<',String.fromCharCode(92)+'u003c'));history.replaceState(null,'','#'+select.value)}
frame.onload=()=>{frame.style.height=frame.contentDocument.documentElement.scrollHeight+'px'};
addEventListener('resize',()=>{if(frame.contentDocument)frame.style.height=frame.contentDocument.documentElement.scrollHeight+'px'});
const requested=location.hash.slice(1);if(catalog.traces[requested])select.value=requested;
select.onchange=show;show();
</script></html>"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=['all', *SCENARIOS], default='all',
                        help='Run the whole catalog (default), or update one selected scenario')
    parser.add_argument('--list', action='store_true', help='List available scenarios')
    parser.add_argument('--output', type=Path, default=WORK)
    for key, value in DEFAULTS.items():
        if key == 'fail_unlock':
            parser.add_argument('--fail-unlock', action='store_true', default=None)
        else:
            parser.add_argument('--' + key.replace('_', '-'), type=int if key in ['fail_grab', 'async_unlock', 'fail_seat'] else float,
                                default=None)
    parser.add_argument('--overrides', type=Path,
                        help='Config with measured tool calibration or motion setting overrides')
    args = parser.parse_args()
    if args.list:
        for name, scene in SCENARIOS.items():
            if not scene.get('visible', True):
                continue
            print(f'{name:24} {scene["label"]}')
        return
    klipper = prepare()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    traces = (json.loads((output / 'traces.json').read_text())
              if (output / 'traces.json').exists() and args.scenario != 'all' else {})
    traces = {name: trace for name, trace in traces.items()
              if name in SCENARIOS and SCENARIOS[name].get('visible', True)}
    sources = [ROOT / 'versions.env',
               ROOT / 'pkgs/klipper-config/payload/config/ff-toolchange.cfg',
               ROOT / 'pkgs/klipper-config/payload/config/ff-afc.cfg',
               ROOT / 'pkgs/klipper-config/payload/config/printer.base.cfg',
               Path(__file__), Path(__file__).with_name('host.py'),
               Path(__file__).with_name('creator5_sim.py'), Path(__file__).with_name('motor.cfg'),
               Path(__file__).with_name('brush.cfg'), Path(__file__).with_name('scenarios.py')]
    sources.extend((ROOT / 'pkgs/klipper/payload/klipper/klippy/extras').glob('*.py'))
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    hashes['klipper_tree'] = klipper.name
    server = ThreadingHTTPServer(('127.0.0.1', 0), Database)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    names = [name for name, scene in SCENARIOS.items() if scene.get('visible', True)] if args.scenario == 'all' else [args.scenario]
    try:
        for name in names:
            trace = run_one(name, args, klipper, server.server_port)
            trace['sources'] = hashes
            traces[name] = trace
            summary = trace['summary']
            mean = summary['mean_swap_seconds']
            print(f'{name:24} {"verified" if trace["verified"] else "FAILED":8} '
                  f'{summary["duration"]:.3f}s total; {summary["swap_count"]} swaps'
                  + (f'; {mean:.3f}s/swap' if mean is not None else ''))
    finally:
        server.shutdown()
        server.server_close()
    (output / 'traces.json').write_text(json.dumps(traces, indent=2))
    (output / 'replay.html').write_text(replay(traces))
    columns = ['scenario', 'kind', 'from_tool', 'to_tool', 'duration', 'afc_tool_swap_seconds',
               'return_complete_at', 'motor_complete_at', 'all_motion_complete_at',
               'all_motion_seconds', 'motor_tail_seconds',
               'release_seconds', 'grab_seconds', 'heating_seconds', 'other_seconds', 'passed']
    with (output / 'selections.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        for name, trace in traces.items():
            writer.writerows(dict(op, scenario=name) for op in trace['operations'])
    print(f'Replay: {output / "replay.html"}')
    if not all(traces[name]['verified'] for name in names):
        raise SystemExit(1)


if __name__ == '__main__':
    main()

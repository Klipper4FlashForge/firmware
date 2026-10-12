"""Integration scenarios using real pinned Klipper, AFC and shipped macros.

Run explicitly with the simulator environment prepared; hardware observations
are modeled, so these checks prove software sequencing, not physical safety.
"""
import json
import os
import subprocess
import tempfile

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.simulator
SIM = ROOT / 'tools/creator5-sim'
PYTHON = os.environ.get('CREATOR5_SIM_PYTHON',
                        str(ROOT / 'work/creator5-sim/venv/bin/python3.12'))


def run(name, *options, scenario='swap', success=True):
    with tempfile.TemporaryDirectory(prefix='creator5-sim-qa-') as output:
        result = subprocess.run([PYTHON, str(SIM / 'run.py'), '--scenario', scenario,
                                 '--output', output, *options],
                                capture_output=True, text=True, timeout=90)
        assert (result.returncode == 0) == success, result.stdout + result.stderr
        from pathlib import Path
        traces = json.loads((Path(output) / 'traces.json').read_text())
        assert (Path(output) / 'replay.html').exists()
        return traces[scenario]


def test_all_heads_swap_and_restore_nozzle_position():
    trace = run('warm')
    assert [check['tool'] for check in trace['checks']] == [0, 1, 2, 3, 0]
    assert all(check['passed'] for check in trace['checks'])
    assert trace['summary']['mounted'] == 0
    assert trace['summary']['errors'] == []
    locks = [event for event in trace['events'] if event['kind'] == 'lock']
    assert sum(event['action'] == 'GRAB' for event in locks) == 5
    assert sum(event['action'] == 'RELEASE' for event in locks) == 4
    # Every head change executes actual planned travel and modeled actuation.
    assert len(trace['moves']) > 30


def test_every_directed_head_pair_restores_position():
    trace = run('all-pairs', scenario='all-pairs')
    tools = [c['tool'] for c in trace['checks']]
    assert set(zip(tools, tools[1:])) == {(a, b) for a in range(4)
                                       for b in range(4) if a != b}
    assert all(check['passed'] for check in trace['checks'])
    assert trace['summary']['checks'] == 13
    swaps = [op for op in trace['operations'] if op['kind'] == 'swap']
    assert len(swaps) == trace['summary']['swap_count'] == 12
    assert set((op['from_tool'], op['to_tool']) for op in swaps) == set(zip(tools, tools[1:]))
    for op in swaps:
        assert op['duration'] == pytest.approx(op['end'] - op['start'])
        assert 0 < op['afc_tool_swap_seconds'] < op['duration']
        assert op['duration'] == pytest.approx(op['release_seconds'] +
                                              op['grab_seconds'] + op['other_seconds'])
    assert trace['summary']['mean_swap_seconds'] == pytest.approx(
        sum(op['duration'] for op in swaps) / 12)


def test_production_motor_timing_and_empty_e2_pickup():
    trace = run('pickup-e2', scenario='pickup-e2')
    assert trace['model']['motor_mode'] == 'production'
    assert trace['summary']['swap_count'] == 0
    assert trace['summary']['mean_swap_seconds'] is None
    op, = trace['operations']
    assert (op['kind'], op['from_tool'], op['to_tool']) == ('pickup', None, 2)
    assert op['release_seconds'] == 0
    assert 0 < op['afc_tool_swap_seconds'] < op['duration']
    lock, lock2 = trace['motor_moves']
    assert lock['distance'] == pytest.approx(.07)
    assert lock['duration'] == pytest.approx(.07 / .25 + .25 / 1.875)
    assert lock['accel'] == 1.875
    assert lock2['accel'] == 1.25
    assert lock2['distance'] == pytest.approx(.03)
    assert lock2['duration'] == pytest.approx(.03 / .02 + .02 / 1.25)
    # Second lock movement runs concurrently with the carriage retreat.
    assert any(m['time'] < lock2['time'] + lock2['duration'] and
               m['time'] >= lock2['time'] for m in trace['moves'])


def test_faster_retreat_reduces_planned_time_without_changing_return():
    baseline = run('baseline', '--soft-docking', '0')
    faster = run('faster', '--retreat-feed', '4800', '--soft-docking', '0')
    assert baseline['summary']['duration'] - faster['summary']['duration'] > 2
    assert [c['position'] for c in baseline['checks']] == [c['position'] for c in faster['checks']]


def test_cold_head_waits_at_dock_exit_before_return():
    trace = run('cold', '--temperature', '25', scenario='heating')
    waits = [event for event in trace['events']
             if event['kind'] == 'wait' and event['reason'].startswith('heating')]
    assert waits and sum(event['duration'] for event in waits) > 50
    for wait in waits:
        preceding = [m for m in trace['moves'] if m['time'] < wait['start']]
        move = preceding[-1]
        length = (move['start_v'] + move['cruise_v']) * move['accel_t'] / 2
        length += move['cruise_v'] * move['cruise_t']
        end_v = move['cruise_v'] - move['accel'] * move['decel_t']
        length += (move['cruise_v'] + end_v) * move['decel_t'] / 2
        endpoint = [v + r * length for v, r in zip(move['start'], move['direction'])]
        assert endpoint[0] == pytest.approx(250)
    assert all(c['passed'] for c in trace['checks'])


def test_afc_mapping_and_same_head_selection():
    trace = run('mapping', scenario='mapping')
    assert [check['tool'] for check in trace['checks']] == [2, 2, 1]
    locks = [e for e in trace['events'] if e['kind'] == 'lock' and e['action'] == 'GRAB']
    assert len(locks) == 2


def test_missing_grab_sensor_prevents_lock_and_return():
    trace = run('missing-grab', '--fail-grab', '1', success=False)
    assert trace['summary']['errors']
    assert any('grab sensor' in error for error in trace['summary']['errors'])
    assert trace['summary']['mounted'] is None
    assert not any(e['kind'] == 'lock' and e.get('action') == 'GRAB'
                   and e.get('mounted') == 1 for e in trace['events'])
    assert [check['tool'] for check in trace['checks']] == [0]


def test_failed_unlock_leaves_head_at_dock():
    trace = run('unlock-failure', '--fail-unlock', success=False)
    assert trace['summary']['mounted'] == 0
    assert trace['summary']['errors']
    assert any('release failed' in error for error in trace['summary']['errors'])
    assert not any(e['kind'] == 'lock' and e.get('action') == 'GRAB'
                   and e.get('mounted') == 1 for e in trace['events'])


@pytest.mark.parametrize('x', [240, 245, 280])
@pytest.mark.parametrize('relative', [False, True])
def test_brush_leaves_negative_z_and_restores_print_state(x, relative):
    trace = run('brush', scenario=f'brush-{x}-{relative}')
    brush, = trace['brush_runs']
    commands = brush['commands']
    assert min(c['end'][2] for c in commands) == pytest.approx(-.5)
    assert trace['model']['travel_limits'][2] == [-10., 270.]
    assert trace['brush_warnings'] == []
    # Once on the rubber, all four exits are vertical; XY resumes at Z5.
    low = next(i for i, c in enumerate(commands) if c['end'][2] == -.5)
    assert [c['end'][0] for c in commands[low+1:low+5]] == [271, 264, 271, 264]
    exits = commands[low+5:low+9]
    assert [c['end'][2] for c in exits] == [0, 1, 3, 5]
    assert all(c['end'][:2] == c['start'][:2] for c in exits)
    assert commands[-1]['end'][:3] == [x, 150, 10]
    for key in ['absolute_coordinates', 'absolute_extrude', 'speed',
                'speed_factor', 'extrude_factor', 'homing_origin']:
        assert brush['after'][key] == brush['before'][key]
    # Explicit staged return restores XYZ before RESTORE restores modes.
    assert brush['after']['gcode_position'][:3] == [x, 150, 10]
    assert all(c['passed'] for c in trace['checks'])


def test_brush_clearance_accounts_for_active_tool_offset():
    trace = run('offset-brush', scenario='brush-offset')
    assert trace['brush_warnings'] == []
    y_moves = [c for c in trace['brush_runs'][0]['commands'] if c['start'][1] != c['end'][1]]
    assert y_moves
    assert all(max(c['start'][0], c['end'][0]) <= 245 + 1e-6 for c in y_moves)
    assert min(c['end'][2] for c in trace['brush_runs'][0]['commands']) == pytest.approx(-.44)
    assert all(c['passed'] for c in trace['checks'])


def test_brush_preserves_hardware_upper_limit():
    trace = run('brush-too-high', scenario='brush-too-high')
    assert trace['verified'] and trace['expected_error'] == 'Move out of range'
    assert any('Move out of range' in e for e in trace['summary']['errors'])
    assert trace['brush_runs'][0]['commands'] == []


def test_whole_catalog_is_published_in_one_replay():
    with tempfile.TemporaryDirectory(prefix='creator5-sim-catalog-') as output:
        result = subprocess.run([PYTHON, str(SIM / 'run.py'), '--output', output],
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        from pathlib import Path
        folder = Path(output)
        traces = json.loads((folder / 'traces.json').read_text())
        assert len(traces) == 16
        assert not any('baseline' in name or name == 'faster' for name in traces)
        assert 'all-pairs' not in traces
        assert 'all-pairs-trajectory' in traces
        assert all(t['inputs']['settings']['dock_shortcuts'] == 1 for t in traces.values())
        assert all(trace['verified'] for trace in traces.values())
        assert not traces['brush-offset']['brush_warnings']
        assert traces['heating']['model']['initial_temperature'] == 25
        assert all(traces[name]['expected_error'] for name in
                   ['missing-grab', 'failed-unlock', 'brush-too-high'])
        assert set(p.name for p in folder.iterdir()) == {'traces.json', 'selections.csv', 'replay.html'}
        cached = dict(traces, faster=traces['swap'], obsolete_replay=traces['swap'])
        (folder / 'traces.json').write_text(json.dumps(cached))
        # Updating a scene preserves the catalog in the same replay file.
        result = subprocess.run([PYTHON, str(SIM / 'run.py'), '--output', output,
                                 '--scenario', 'swap', '--dock-shortcuts', '0'],
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        updated = json.loads((folder / 'traces.json').read_text())
        assert updated.keys() == traces.keys()
        assert updated['brush'] == traces['brush']
        assert updated['swap']['summary']['duration'] > traces['swap']['summary']['duration']


@pytest.mark.parametrize('z', [1, 100])
def test_brush_returns_xy_at_clearance_then_lowers_z(z):
    trace = run('brush-height', scenario=f'brush-z{z}')
    brush, = trace['brush_runs']
    commands = brush['commands']
    assert all(not (abs(c['end'][2] - c['start'][2]) > 1e-6 and
                    any(abs(c['end'][i] - c['start'][i]) > 1e-6 for i in (0, 1)))
               for c in commands)
    raise_z, return_y, return_x, lower_z = commands[-4:]
    assert raise_z['start'][:2] == raise_z['end'][:2]
    assert raise_z['end'][2] == pytest.approx(z + 5)
    assert return_y['start'][2] == return_y['end'][2] == z + 5
    assert return_y['start'][0] == return_y['end'][0] == 245
    assert return_y['end'][1] == 150
    assert return_x['start'][2] == return_x['end'][2] == z + 5
    assert return_x['start'][1] == return_x['end'][1] == 150
    assert return_x['end'][0] == 280
    assert lower_z['start'][:2] == lower_z['end'][:2] == [280, 150]
    assert lower_z['end'][2] == z
    # Verify the planned trajectory too, not just individual G1 commands:
    # no brush trajectory segment has both a Z and an XY component.
    movements = [m for m in trace['moves'] if m['phase'] == 'brush']
    assert movements
    assert all(not (abs(m['direction'][2]) > 1e-6 and
                    any(abs(m['direction'][i]) > 1e-6 for i in (0, 1)))
               for m in movements)
    xy, descent = movements[-2:]
    assert descent['time'] >= xy['time'] + xy['accel_t'] + xy['cruise_t'] + xy['decel_t'] - 1e-6
    assert xy['cruise_v'] - xy['accel'] * xy['decel_t'] == pytest.approx(0)
    assert descent['start_v'] == pytest.approx(0)
    assert brush['after']['gcode_position'][:3] == [280, 150, z]
    assert all(c['passed'] for c in trace['checks'])


@pytest.mark.parametrize('x', [150, 280])
def test_optimized_brush_shortens_clear_travel_and_preserves_state(x):
    baseline = run('baseline', scenario=f'brush-{x}-baseline')
    optimized = run('optimized', scenario=f'brush-{x}-optimized')
    before, = baseline['brush_runs']
    after, = optimized['brush_runs']
    assert after['end'] - after['start'] < before['end'] - before['start'] - 1
    assert after['after'] == before['after']
    assert optimized['brush_warnings'] == []
    diagonals = [c for c in after['commands'] if c['start'][0] != c['end'][0]
                 and c['start'][1] != c['end'][1]]
    assert bool(diagonals) == (x <= 245)
    assert all(max(c['start'][0], c['end'][0]) <= 245 + 1e-6 and
               c['start'][2] == c['end'][2] for c in diagonals)
    # The rubber-contact strokes retain their original 100 mm/s feed.
    wipes = [c for c in after['commands'] if c['start'][2] == c['end'][2] == -.5]
    assert len(wipes) == 4
    assert all(c['speed'] == 100 for c in wipes)
    assert any(c['speed'] == 200 for c in after['commands'])


def test_demo_center_travel_has_explicit_feed_after_brush():
    trace = run('demo', scenario='brush-280-optimized')
    brush, = trace['brush_runs']
    assert brush['after']['speed'] == brush['before']['speed'] == 1234
    center = next(c for c in trace['motion_commands']
                  if c['start'][:3] == [280, 150, 10] and c['end'][:3] == [150, 150, 10])
    assert center['speed'] == 200


def test_t0_t3_shorter_trajectory_preserves_speeds_and_return():
    baseline = run('baseline', '--soft-docking', '0', scenario='t0-t3-baseline')
    optimized = run('optimized', '--soft-docking', '0', scenario='t0-t3-trajectory')
    get_op = lambda t: next(op for op in t['operations'] if op['from_tool'] == 0 and op['to_tool'] == 3)
    assert get_op(optimized)['duration'] < get_op(baseline)['duration'] - .1
    assert optimized['inputs']['settings']['retreat_feed'] == baseline['inputs']['settings']['retreat_feed'] == 1500
    assert optimized['dock_warnings'] == []
    assert optimized['checks'][-1]['position'] == baseline['checks'][-1]['position']
    first = next(c for c in optimized['motion_commands'] if c['phase'] == 'release T0')
    assert first['start'][:2] == [150, 150]
    assert first['end'][:2] == [250, 20]
    assert first['speed'] == 500
    assert first['start'][2] == first['end'][2]
    # Dock engagement, pullback and retreat feeds remain the factory values.
    for trace in [baseline, optimized]:
        commands = [c for c in trace['motion_commands'] if c['phase'] == 'grab T3']
        assert next(c for c in commands if c['end'][:2] == [297, 260])['speed'] == 90
        assert next(c for c in commands if c['start'][:2] == [297, 260] and c['end'][0] == 277)['speed'] == 80
        assert next(c for c in commands if c['start'][0] == 277 and c['end'][0] == 250)['speed'] == 25


def test_shorter_trajectory_handles_every_tool_pair():
    trace = run('all-pairs', scenario='all-pairs-trajectory')
    tools = [c['tool'] for c in trace['checks']]
    assert set(zip(tools, tools[1:])) == {(a,b) for a in range(4) for b in range(4) if a != b}
    assert all(c['passed'] for c in trace['checks'])
    assert trace['dock_warnings'] == []
    for c in trace['motion_commands']:
        if c['phase'].startswith(('release ', 'grab ')) and c['start'][1] != c['end'][1]:
            assert max(c['start'][0], c['end'][0]) <= 250 + 1e-6
            assert c['start'][2] == c['end'][2]


def test_t0_t3_outside_start_retracts_x_before_any_y():
    trace = run('outside-dock', scenario='t0-t3-outside')
    commands = [c for c in trace['motion_commands'] if c['phase'] == 'release T0']
    first = commands[0]
    assert first['start'][:2] == [280, 150]
    assert first['end'][:2] == [250, 150]
    assert trace['dock_warnings'] == []
    assert trace['checks'][-1]['position'][:3] == [280, 150, 10]


def test_guard_rejects_afc_return_through_occupied_t2_dock(tmp_path):
    overrides = tmp_path / 'overrides.cfg'
    overrides.write_text('''[gcode_macro MOTOR_STOP]
gcode:
    {% if printer.ff_toolchange.current_tool == 3 %}
        SIM_RAW_MOVE X=280.3 Y=149.7 SPEED=500
    {% endif %}
''')
    trace = run('outside-return', '--overrides', str(overrides),
                scenario='t0-t3-outside', success=False)
    # AFC may retry from its cached commanded coordinates while handling
    # the first rejection. Every attempt must be rejected before planning.
    assert trace['dock_warnings']
    violation = trace['dock_warnings'][0]
    assert violation['phase'] == 'toolchange / return'
    assert violation['start'][:2] == [250, 260]
    assert violation['end'][:2] == pytest.approx([280.3, 149.7])
    dock, = violation['crossed_docks']
    assert dock['tool'] == 2
    assert dock['y'] == 180
    assert dock['x'] == pytest.approx(271.97642792384405)
    assert not trace['verified']
    assert trace['checks'] == []
    assert not any(c['start'] == violation['start'] and c['end'] == violation['end']
                   for c in trace['motion_commands'])
    assert all(v['crossed_docks'][0]['tool'] == 2 for v in trace['dock_warnings'])


def test_outside_t0_t3_must_return_without_crossing_t2():
    trace = run('outside-return-regression', scenario='t0-t3-outside')
    assert trace['dock_warnings'] == []
    assert trace['checks'][-1]['position'][:3] == [280, 150, 10]
    commands = [c for c in trace['motion_commands'] if c['phase'] == 'toolchange / return'
                and c['start'][1] != c['end'][1]]
    assert commands
    assert all(max(c['start'][0], c['end'][0]) <= 250 + 1e-6 for c in commands)
    assert any(c['end'][:2] == pytest.approx([250, 149.7]) for c in commands)


def test_dock_shortcut_uses_machine_position_with_tool_offset():
    trace = run('boundary', scenario='dock-offset-boundary')
    commands = [c for c in trace['motion_commands'] if c['phase'] == 'release T3']
    first = commands[0]
    assert first['start'][0] == pytest.approx(250.2)
    assert first['end'][0] == 250
    assert first['start'][1] == first['end'][1]
    assert trace['dock_warnings'] == []
    assert trace['checks'][-1]['position'][:3] == pytest.approx([249.9, 150, 10])


def test_unsafe_dock_shortcut_is_rejected(tmp_path):
    overrides = tmp_path / 'overrides.cfg'
    overrides.write_text('[gcode_macro FF_MOTOR_RELEASE]\ngcode:\n    G1 X280 Y260 F30000\n')
    trace = run('unsafe-dock', '--async-unlock', '0', '--overrides', str(overrides),
                scenario='t0-t3-outside', success=False)
    assert not trace['verified']
    assert any('Unsafe dock move' in e for e in trace['summary']['errors'])
    assert trace['dock_warnings']
    assert not any(c['phase'] == 'release T0' and c['end'][:2] == [280, 260]
                   for c in trace['motion_commands'])


@pytest.mark.parametrize('flags,reason', [
    (['--fail-grab', '3'], 'grab sensor'), (['--fail-unlock'], 'release failed'),
])
def test_shorter_t0_t3_does_not_resume_after_hardware_failure(flags, reason):
    trace = run('fast-failure', *flags, scenario='t0-t3-trajectory', success=False)
    assert any(reason in e for e in trace['summary']['errors'])
    assert trace['checks'] == []
    assert not any(op['from_tool'] == 0 and op['to_tool'] == 3 and op['passed']
                   for op in trace['operations'])


def test_brush_below_zero_start_lifts_before_xy():
    trace = run('below-zero', scenario='brush-below-zero')
    brush, = trace['brush_runs']
    first = brush['commands'][0]
    assert first['start'][2] == -1
    assert first['end'][2] == 5
    assert first['start'][:2] == first['end'][:2]
    assert trace['brush_warnings'] == []


@pytest.mark.parametrize('scenario,override,reason', [
    ('brush', '[gcode_macro AFC_BRUSH]\ngcode:\n    G90\n    G1 X245 Y7 F12000\n',
     'unsafe_y_travel'),
    ('brush-240-False', '[gcode_macro AFC_BRUSH]\ngcode:\n    G90\n    G1 X265 Y7 F12000\n',
     'unsafe_y_travel'),
    ('brush-offset', '[gcode_macro AFC_BRUSH]\ngcode:\n    G90\n    G1 X245 F6000\n    G1 Y7 F12000\n',
     'unsafe_y_travel'),
    ('brush', '[gcode_macro AFC_BRUSH]\ngcode:\n    G90\n    G1 X200 Z5 F12000\n',
     'mixed_xy_z_travel'),
    ('brush', '[gcode_macro _AFC_BRUSH_RETURN]\ngcode:\n    G90\n    G1 X{params.X} Y{params.Y} F12000\n',
     'unsafe_y_travel'),
    ('brush-z1', '[gcode_macro _AFC_BRUSH_RETURN]\ngcode:\n    G90\n    G1 X240 Y{params.Y} Z{params.Z} F12000\n',
     'mixed_xy_z_travel'),
    ('brush', '[gcode_macro AFC_BRUSH]\ngcode:\n    G90\n    G1 Z-0.5\n    G1 X245 F12000\n',
     'low_clearance_xy_travel'),
])
def test_unsafe_brush_shortcut_fails_before_motion(tmp_path, scenario, override, reason):
    overrides = tmp_path / 'overrides.cfg'
    overrides.write_text(override)
    trace = run('unsafe', '--overrides', str(overrides), scenario=scenario, success=False)
    assert not trace['verified']
    assert any('Unsafe brush move: ' + reason in e for e in trace['summary']['errors'])
    rejected, = trace['brush_warnings']
    assert rejected['kind'] == reason
    # The simulator rejects the bad move before handing it to the planner.
    assert not any(c['start'] == rejected['start'] and c['end'] == rejected['end']
                   for c in trace['motion_commands'])


def planned_endpoint(move):
    end_v = move['cruise_v'] - move['accel'] * move['decel_t']
    distance = (move['start_v'] + move['cruise_v']) * move['accel_t'] / 2
    distance += move['cruise_v'] * move['cruise_t']
    distance += (move['cruise_v'] + end_v) * move['decel_t'] / 2
    return [v + r * distance for v, r in zip(move['start'], move['direction'])]


def assert_rubber_speed_limit(trace):
    # Conservatively cap a whole segment if any part intersects rubber.
    # The proposed profile deliberately finishes fast braking before entry.
    count = 0
    for move in trace['moves']:
        if not move['phase'].startswith(('grab ', 'release ')):
            continue
        if move['direction'][0] < 0 and move['mounted'] is None:
            continue  # empty-carriage withdrawal does not move a nozzle
        tool = int(move['phase'].split('T')[-1])
        dock_x, dock_y = trace['model']['docks'][tool]
        endpoint = planned_endpoint(move)
        if abs(move['start'][1] - dock_y) > 1e-6 or abs(endpoint[1] - dock_y) > 1e-6:
            continue
        lo, hi = sorted([move['start'][0], endpoint[0]])
        if hi > dock_x - trace['model']['contact_depth'] + 1e-6 and lo < dock_x - 1e-6:
            count += 1
            assert max(move['start_v'], move['cruise_v']) <= trace['model']['contact_speed'] + 1e-4, move
    assert count > 0


def test_soft_seating_limits_planned_speed_for_every_head():
    trace = run('soft-all', scenario='all-pairs-trajectory')
    assert trace['model']['soft_docking']
    assert trace['model']['contact_speed'] == 70
    assert trace['model']['withdrawal_speed'] == 35
    assert trace['model']['contact_accel'] == 150
    assert not trace['model']['async_unlock']
    for motor in trace['motor_moves']:
        assert motor['accel'] == (1.875 if motor['distance'] < 0 or motor['distance'] > .05 else 1.25)
    assert_rubber_speed_limit(trace)
    assert all(c['passed'] for c in trace['checks'])
    # Seat before actuating either lock or unlock, at zero carriage velocity.
    locks = [e for e in trace['events'] if e['kind'] == 'lock' and e.get('action') in ('GRAB', 'RELEASE')]
    for event in locks:
        tool = int(event['phase'].split('T')[-1])
        dock_x, _ = trace['model']['docks'][tool]
        seats = [m for m in trace['moves'] if m['phase'] == event['phase']
                 and m['time'] <= event['time']
                 and m['direction'][0] > 0
                 and abs(planned_endpoint(m)[0] - dock_x) < 1e-4]
        seat = seats[-1]
        assert seat['cruise_v'] <= trace['model']['contact_speed'] + 1e-6
        assert seat['accel'] <= trace['model']['contact_accel'] + 1e-6
        assert seat['cruise_v'] - seat['accel'] * seat['decel_t'] == pytest.approx(0)
        assert event['time'] >= seat['time'] + seat['accel_t'] + seat['cruise_t'] + seat['decel_t']


def test_deceleration_in_rubber_is_gentle_for_all_heads():
    trace = run('gentle-rubber', scenario='all-pairs-trajectory')
    for m in trace['moves']:
        if not m['phase'].startswith(('grab ', 'release ')) or m['direction'][0] <= 0 or m['decel_t'] <= 1e-9:
            continue
        tool = int(m['phase'].split('T')[-1])
        dock_x, _ = trace['model']['docks'][tool]
        if planned_endpoint(m)[0] > dock_x - trace['model']['contact_depth'] + 1e-5:
            assert m['accel'] <= trace['model']['contact_accel']


def test_async_motor_completion_is_reported_without_extending_return_time():
    trace = run('motor-timing', scenario='all-pairs-trajectory')
    assert any(op['motor_tail_seconds'] > 0 for op in trace['operations'])
    for op in trace['operations']:
        scheduled = [m['time'] + m['duration'] for m in trace['motor_moves'] if m['time'] < op['end']]
        expected = max(scheduled, default=op['end'])
        assert op['motor_complete_at'] == pytest.approx(expected)
        assert op['return_complete_at'] == op['end']
        assert op['all_motion_complete_at'] == pytest.approx(max(op['end'], expected))
        assert op['all_motion_seconds'] == pytest.approx(op['duration'] + op['motor_tail_seconds'])
    # Repeated changes must not overwrite or overlap an unfinished motor move.
    for previous, following in zip(trace['motor_moves'], trace['motor_moves'][1:]):
        assert following['time'] >= previous['time'] + previous['duration'] - 1e-6


def test_normal_approach_then_single_low_acceleration_seat():
    trace = run('soft-ease', scenario='t0-t3-trajectory')
    dock_x, _ = trace['model']['docks'][3]
    exit_x = dock_x - trace['model']['contact_depth'] - trace['model']['contact_margin']
    ease_x = dock_x - trace['model']['ease_distance']
    moves = [m for m in trace['moves'] if m['phase'] == 'grab T3']
    approach = [m for m in moves if m['direction'][0] > 0
                and planned_endpoint(m)[0] <= ease_x + 1e-5]
    assert len(approach) == 2
    assert all(m['accel'] == 8000 for m in approach)
    assert approach[-1]['decel_t'] == pytest.approx(0, abs=1e-8)
    assert planned_endpoint(approach[0])[0] == pytest.approx(exit_x)
    assert approach[1]['cruise_v'] == pytest.approx(trace['model']['contact_speed'])
    assert planned_endpoint(approach[1])[0] == pytest.approx(ease_x)
    seating = [m for m in moves if m['direction'][0] > 0 and m['start'][0] >= ease_x - 1e-5]
    assert len(seating) == 1
    final = seating[0]
    assert final['start'][0] == pytest.approx(ease_x)
    assert planned_endpoint(final)[0] == pytest.approx(dock_x)
    assert final['accel'] == trace['model']['contact_accel']
    entry_speed = min(trace['model']['contact_speed'],
                      (2 * trace['model']['contact_accel'] * trace['model']['ease_distance']) ** .5)
    assert final['start_v'] == pytest.approx(entry_speed, abs=1e-4)
    assert final['cruise_v'] == pytest.approx(entry_speed)
    end_v = approach[-1]['cruise_v'] - approach[-1]['accel'] * approach[-1]['decel_t']
    assert end_v == pytest.approx(final['start_v'], abs=1e-4)
    assert final['cruise_v'] - final['accel'] * final['decel_t'] == pytest.approx(0)
    fast_exit = [m for m in moves if m['direction'][0] < 0 and m['cruise_v'] > trace['model']['withdrawal_speed'] + 1e-5]
    assert fast_exit
    assert all(max(m['start'][0], planned_endpoint(m)[0]) <= exit_x + 1e-5 for m in fast_exit)
    free_exit = [m for m in moves if m['direction'][0] < 0
                 and m['start'][0] <= exit_x + 1e-5]
    assert len(free_exit) == 1
    assert free_exit[0]['accel'] == 8000
    assert free_exit[0]['cruise_v'] == pytest.approx(trace['model']['rail_speed'])


def test_faster_contact_reduces_swap_time_without_hardening_final_stop():
    baseline = run('contact-20', '--contact-speed', '20', '--withdrawal-speed', '20',
                   '--contact-accel', '50', '--async-unlock', '0', scenario='t0-t3-trajectory')
    faster = run('contact-40', '--contact-speed', '40', '--withdrawal-speed', '20',
                 '--contact-accel', '50', '--async-unlock', '0', scenario='t0-t3-trajectory')
    assert baseline['model']['contact_speed'] == 20
    assert faster['model']['contact_speed'] == 40
    assert faster['summary']['mean_swap_seconds'] < baseline['summary']['mean_swap_seconds'] - .7
    assert_rubber_speed_limit(faster)
    assert faster['dock_warnings'] == []
    assert faster['model']['contact_accel'] == baseline['model']['contact_accel'] == 50
    assert faster['model']['withdrawal_speed'] == baseline['model']['withdrawal_speed'] == 20


def test_carried_exit_is_gentle_and_verified_empty_exit_is_fast():
    trace = run('exit-payload', scenario='t0-t3-trajectory')
    slow = [m for m in trace['moves'] if m['phase'] == 'grab T3' and m['direction'][0] < 0
            and m['start'][0] > 277]
    assert slow and all(m['mounted'] == 3 for m in slow)
    speed, accel = trace['model']['withdrawal_speed'], trace['model']['contact_accel']
    assert all(m['cruise_v'] <= speed and m['accel'] <= accel for m in slow)
    # Acceleration and contact speed must stay gentle until the nozzle
    # clears both rubber and margin, including the second-lock pullback.
    dock_x = trace['model']['docks'][3][0]
    exit_x = dock_x - trace['model']['contact_depth'] - trace['model']['contact_margin']
    carried = [m for m in trace['moves'] if m['phase'] == 'grab T3'
               and m['direction'][0] < 0]
    for m in carried:
        if max(m['start'][0], planned_endpoint(m)[0]) > exit_x + 1e-5:
            assert m['accel'] <= accel
            assert max(m['start_v'], m['cruise_v']) <= speed + 1e-5
    free = [m for m in carried if m['accel'] > accel]
    assert free
    assert all(m['start'][0] <= exit_x + 1e-5 for m in free)
    boundary = next(m for m in carried if abs(planned_endpoint(m)[0] - exit_x) < 1e-5)
    end_v = boundary['cruise_v'] - boundary['accel'] * boundary['decel_t']
    assert end_v == pytest.approx(speed)
    assert free[0]['start_v'] == pytest.approx(end_v)
    empty = [m for m in trace['moves'] if m['phase'] == 'release T0' and m['direction'][0] < 0]
    assert len(empty) == 1
    assert empty[0]['mounted'] is None
    assert empty[0]['cruise_v'] > 200
    unlock = next(e for e in trace['events'] if e['phase'] == 'release T0'
                  and e['kind'] == 'lock' and e.get('action') == 'RELEASE')
    assert empty[0]['time'] >= unlock['time']
    assert planned_endpoint(empty[0])[0] == 250


def test_failed_unlock_never_uses_fast_empty_withdrawal():
    trace = run('held-on-error', '--fail-unlock', scenario='t0-t3-trajectory', success=False)
    retreats = [m for m in trace['moves'] if m['phase'] == 'release T0' and m['direction'][0] < 0]
    assert all(m['mounted'] == 0 and m['cruise_v'] <= trace['model']['withdrawal_speed']
               and m['accel'] <= trace['model']['contact_accel'] for m in retreats)


def test_contact_speed_assertion_detects_old_fast_entry():
    trace = run('factory', '--soft-docking', '0', scenario='t0-t3-baseline')
    with pytest.raises(AssertionError):
        assert_rubber_speed_limit(trace)


@pytest.mark.parametrize('flags', [['--fail-grab', '3'], ['--fail-unlock']])
def test_contact_retries_remain_slow_and_restore_motion_limits(flags):
    trace = run('soft-error', *flags, scenario='t0-t3-trajectory', success=False)
    assert_rubber_speed_limit(trace)
    assert trace['final_motion_limits']['max_accel'] == 30000
    assert trace['final_motion_limits']['minimum_cruise_ratio'] == .5
    assert trace['checks'] == []


def test_invalid_contact_geometry_fails_before_moving(tmp_path):
    overrides = tmp_path / 'overrides.cfg'
    overrides.write_text('[ff_toolchange]\ncontact_depth: 45\n')
    trace = run('overlap', '--overrides', str(overrides), scenario='t0-t3-trajectory', success=False)
    assert any('overlaps the dock clearance corridor' in e for e in trace['summary']['errors'])
    assert not any(c['phase'].startswith(('grab ', 'release ')) for c in trace['motion_commands'])


def test_shorter_rubber_region_preserves_mandatory_pickup_pullback(tmp_path):
    overrides = tmp_path / 'overrides.cfg'
    overrides.write_text('[ff_toolchange]\ncontact_depth: 10\n')
    trace = run('short-rubber', '--overrides', str(overrides), scenario='t0-t3-trajectory')
    assert_rubber_speed_limit(trace)
    second_lock = next(e for e in trace['events'] if e['phase'] == 'grab T3'
                       and e['kind'] == 'lock' and e.get('action') == 'GRAB2')
    preceding = [m for m in trace['moves'] if m['phase'] == 'grab T3'
                 and m['time'] < second_lock['time']]
    assert planned_endpoint(preceding[-1])[0] == pytest.approx(277)
    assert trace['checks'][-1]['passed']


def test_contact_failure_restores_precise_cruise_ratio(tmp_path):
    overrides = tmp_path / 'overrides.cfg'
    ratio = .987654321123
    overrides.write_text(f'[printer]\nminimum_cruise_ratio: {ratio}\n')
    trace = run('ratio', '--overrides', str(overrides), '--fail-grab', '3',
                scenario='t0-t3-trajectory', success=False)
    assert trace['final_motion_limits']['minimum_cruise_ratio'] == ratio
    assert_rubber_speed_limit(trace)


def test_async_unlock_overlaps_braking_and_keeps_both_withdrawal_gates():
    sync = run('sync-unlock', '--async-unlock', '0', '--unlock-duration', '1', scenario='t0-t3-trajectory')
    overlap = run('async-unlock', '--async-unlock', '1', '--unlock-duration', '1', scenario='t0-t3-trajectory')
    assert sync['model']['unlock_duration_estimate'] == overlap['model']['unlock_duration_estimate'] == 1
    assert overlap['summary']['mean_swap_seconds'] < sync['summary']['mean_swap_seconds'] - .35
    start = next(e for e in overlap['events'] if e['kind'] == 'async_unlock_start')
    final = next(m for m in overlap['moves'] if m['phase'] == start['phase'] and m['direction'][0] > 0
                 and m['accel'] == overlap['model']['contact_accel'])
    finish = final['time'] + final['accel_t'] + final['cruise_t'] + final['decel_t']
    assert start['requested_time'] == pytest.approx(final['time'])
    assert final['start_v'] == pytest.approx(70, abs=1e-4)
    assert final['time'] <= start['time'] < finish
    assert start['pulse_end'] <= start['completion_estimate']
    seated = next(e for e in overlap['events'] if e['kind'] == 'sensor'
                  and e.get('sensor') == 'extruder_pos1' and e['value'])
    release = next(e for e in overlap['events'] if e['kind'] == 'lock' and e.get('action') == 'RELEASE')
    retreat = next(m for m in overlap['moves'] if m['phase'] == start['phase'] and m['direction'][0] < 0)
    assert retreat['time'] >= max(finish, seated['time'], release['time'], start['completion_estimate'])
    assert not any(e['kind'] == 'async_unlock_start' for e in sync['events'])
    for a, b in zip(overlap['motor_moves'], overlap['motor_moves'][1:]):
        assert b['time'] >= a['time'] + a['duration'] - 1e-6
    assert start['motor_serialization_delay'] > 0  # previous GRAB2 is respected


@pytest.mark.parametrize('flags', [['--fail-unlock'], ['--fail-seat', '0']])
def test_async_unlock_fault_never_permits_empty_withdrawal(flags):
    trace = run('async-fault', '--async-unlock', '1', '--unlock-duration', '1', *flags, scenario='t0-t3-trajectory', success=False)
    assert any(e['kind'] == 'async_unlock_failed' and e['latch_state'] == 'unknown'
               for e in trace['events'])
    assert not any(m['phase'] == 'release T0' and m['direction'][0] < 0 for m in trace['moves'])
    assert not any(e['kind'] == 'phase' and e['phase'] == 'grab T3' for e in trace['events'])
    assert not trace['operations'][-1]['passed']
    assert trace['final_motion_limits']['max_accel'] == 30000
    assert trace['final_motion_limits']['minimum_cruise_ratio'] == .5


def test_async_unlock_rejects_unsupported_start(tmp_path):
    overrides = tmp_path / 'unsupported.cfg'
    overrides.write_text('[creator5_sim]\ndock_support_depth: 10\n')
    trace = run('unsupported', '--async-unlock', '1', '--unlock-duration', '1', '--overrides', str(overrides), scenario='t0-t3-trajectory', success=False)
    assert any('outside supported rail' in error for error in trace['summary']['errors'])
    assert not any(e['kind'] == 'async_unlock_start' for e in trace['events'])
    assert not any(m['phase'] == 'release T0' and m['direction'][0] < 0 for m in trace['moves'])


def test_async_unlock_waits_for_delayed_endpoint_sensor():
    trace = run('delayed-seating', '--async-unlock', '1', '--unlock-duration', '1', '--sensor-delay', '.8', scenario='t0-t3-trajectory')
    start = next(e for e in trace['events'] if e['kind'] == 'async_unlock_start')
    seated = next(e for e in trace['events'] if e['kind'] == 'sensor'
                  and e.get('sensor') == 'extruder_pos1' and e['value'])
    release = next(e for e in trace['events'] if e['kind'] == 'lock' and e.get('action') == 'RELEASE')
    assert seated['time'] > start['completion_estimate']
    retreat = next(m for m in trace['moves'] if m['phase'] == 'release T0' and m['direction'][0] < 0)
    assert retreat['time'] >= max(seated['time'], release['time'])


def test_async_unlock_partial_scheduling_failure_finishes_docking_but_never_withdraws(tmp_path):
    overrides = tmp_path / 'partial-schedule.cfg'
    overrides.write_text('[creator5_sim]\nfail_async_schedule: True\n')
    trace = run('partial-schedule', '--async-unlock', '1', '--unlock-duration', '1', '--overrides', str(overrides), scenario='t0-t3-trajectory', success=False)
    assert any('scheduling failed' in error for error in trace['summary']['errors'])
    assert trace['summary']['latch_state'] == 'unknown'
    assert any(m['phase'] == 'release T0' and m['distance'] < 0 for m in trace['motor_moves'])
    seats = [m for m in trace['moves'] if m['phase'] == 'release T0' and m['direction'][0] > 0]
    assert planned_endpoint(seats[-1])[0] == pytest.approx(trace['model']['docks'][0][0])
    assert not any(m['phase'] == 'release T0' and m['direction'][0] < 0 for m in trace['moves'])
    assert any(e['kind'] == 'async_unlock_failed' for e in trace['events'])

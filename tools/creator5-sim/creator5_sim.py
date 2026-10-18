"""Creator 5 hardware model for Klipper's desktop file-output backend.

The motion planner, transforms, macros, AFC and ff_toolchange are unmodified.
Only physical I/O, lock actuation and thermal response are supplied here.
"""
import json
import re
from pathlib import Path


class Sensor:
    def __init__(self, sim, name):
        self.sim, self.name = sim, name

    def get_status(self, eventtime=None):
        return {'state': 'PRESSED' if self.sim.sensor(self.name) else 'RELEASED'}


class Clock:
    """The toolchange sensor state machine waits in planned printer time."""
    def __init__(self, sim):
        self.sim = sim

    def monotonic(self):
        return self.sim.time()

    def pause(self, until):
        delay = max(0., until - self.monotonic())
        self.sim.delay(delay, 'sensor wait')
        return self.monotonic()


class Creator5Sim:
    def __init__(self, config):
        self.printer = config.get_printer()
        if self.printer.get_start_args().get('debugoutput') is None:
            raise config.error('creator5_sim requires file-output mode; never use on hardware')
        self.output = Path(config.get('output'))
        self.sensor_delay = config.getfloat('sensor_delay', .05, minval=0.)
        # User estimate of rail support; distinct from seated sensor state.
        self.dock_support_depth = config.getfloat('dock_support_depth', 50., above=0.)
        self.unlock_preview_seconds = config.getfloat('unlock_preview_seconds', 1., above=0.)
        self.lock_time = config.getfloat('lock_time', .25, minval=0.)
        self.motor_mode = config.get('motor_mode', 'production')
        self.heating_rate = config.getfloat('heating_rate', 3., above=0.)
        self.initial_temp = config.getfloat('initial_temperature', 220.)
        self.fail_grab = config.getint('fail_grab', -1, minval=-1, maxval=3)
        self.fail_unlock = config.getboolean('fail_unlock', False)
        self.async_unlock = config.getboolean('async_unlock', False)
        self.unlock_duration = config.getfloat('unlock_duration', None, above=0.)
        self.fail_seat = config.getint('fail_seat', -1, minval=-1, maxval=3)
        self.fail_async_schedule = config.getboolean('fail_async_schedule', False)
        self.pending_unlock = None
        self.latch_uncertain = False
        self.geometry_source = config.get('geometry_source', 'synthetic calibration fixture')
        self.mounted = None
        self.docked = [True] * 4
        self.locked = False
        self.events, self.moves, self.checks, self.errors = [], [], [], []
        self.operations, self.motor_moves = [], []
        self.brush_runs, self.motion_commands, self.brush_warnings = [], [], []
        self.dock_warnings = []
        self.selection_active = False
        self.readings = {}
        self.heater_state = {}
        self.phase = 'setup'
        self.begin = None
        self.gcode = self.printer.lookup_object('gcode')
        for name in ['LOCK', 'LOAD', 'UNLOAD', 'BEGIN', 'CHECK', 'REPORT', 'RAW_MOVE']:
            self.gcode.register_command('SIM_' + name, getattr(self, 'cmd_' + name))
        self.gcode.register_output_handler(self.output_message)
        process = self.gcode._process_commands

        def process_scenario(commands, need_ack=True):
            if not need_ack:
                return process(commands, need_ack=False)
            # Batch G-code normally continues after an error. Scenarios stop
            # at the failing top-level command, after its finally cleanup,
            # so the recorded failure state is not overwritten by later Tn.
            for line in commands:
                if self.errors:
                    self.printer.request_exit('error_exit')
                    break
                selection = re.fullmatch(r'T(\d+)', line.split(';')[0].strip(), re.I)
                if line.split(';')[0].strip().upper() == 'AFC_BRUSH':
                    self.toolhead.wait_moves()
                    before = self.motion_state()
                    start, previous = self.time(), self.phase
                    first_command = len(self.motion_commands)
                    self.phase = 'brush'
                    self.event('phase', label='brush')
                    process([line], need_ack=True)
                    self.toolhead.wait_moves()
                    self.event('phase_end', label='brush')
                    self.brush_runs.append(dict(start=start, end=self.time(), before=before,
                                                after=self.motion_state(),
                                                commands=self.motion_commands[first_command:]))
                    self.phase = previous
                elif selection and hasattr(self, 'toolhead'):
                    self.toolhead.wait_moves()
                    start, from_tool = self.time(), self.mounted
                    event_index = len(self.events)
                    error_count = len(self.errors)
                    self.event('selection_start', command=f'T{selection[1]}')
                    self.selection_active = True
                    process([line], need_ack=True)
                    self.toolhead.wait_moves()
                    self.selection_active = False
                    operation = dict(start=start, end=self.time(),
                                     duration=self.time() - start,
                                     command=f'T{selection[1]}', from_tool=from_tool,
                                     to_tool=self.mounted, passed=len(self.errors) == error_count)
                    motor_end = max((m['time'] + m['duration'] for m in self.motor_moves),
                                    default=operation['end'])
                    operation['motor_complete_at'] = motor_end
                    operation['return_complete_at'] = operation['end']
                    operation['all_motion_complete_at'] = max(operation['end'], motor_end)
                    operation['all_motion_seconds'] = operation['all_motion_complete_at'] - start
                    operation['motor_tail_seconds'] = max(0., motor_end - operation['end'])
                    operation['kind'] = ('pickup' if from_tool is None else
                                         'reselect' if from_tool == self.mounted else 'swap')
                    # Phases are measured within this operation, including
                    # completion of AFC's return travel after the T handler.
                    phases = self.events[event_index:]
                    for name in ['release', 'grab']:
                        starts = [e['time'] for e in phases if e['kind'] == 'phase'
                                  and e['label'].startswith(name + ' ')]
                        ends = [e['time'] for e in phases if e['kind'] == 'phase_end'
                                and e['label'].startswith(name + ' ')]
                        operation[name + '_seconds'] = sum(b - a for a, b in zip(starts, ends))
                    starts = [e['time'] for e in phases if e['kind'] == 'afc_swap_start']
                    ends = [e['time'] for e in phases if e['kind'] == 'afc_swap_end']
                    operation['afc_tool_swap_seconds'] = sum(b - a for a, b in zip(starts, ends))
                    operation['heating_seconds'] = sum(e['duration'] for e in phases
                        if e['kind'] == 'wait' and e['reason'].startswith(('heating', 'cooling')))
                    operation['other_seconds'] = (operation['duration'] -
                                                  operation['release_seconds'] - operation['grab_seconds'])
                    self.operations.append(operation)
                    self.event('selection_end', command=operation['command'],
                               duration=operation['duration'])
                else:
                    process([line], need_ack=True)

        self.gcode._process_commands = process_scenario
        self.printer.register_event_handler('klippy:connect', self.connect)
        self.printer.register_event_handler('klippy:ready', self.ready)
        self.reported = False
        self.printer.register_event_handler('klippy:disconnect', self.disconnect)

    def time(self):
        # Reading a timestamp must not flush lookahead or add a queue-prime
        # interval. Explicit waits and lock commands flush the real planner.
        return self.toolhead.print_time

    def event(self, kind, **data):
        self.events.append(dict(time=self.time(), kind=kind,
                                phase=self.phase, mounted=self.mounted,
                                docked=list(self.docked), locked=self.locked, **data))

    def delay(self, seconds, reason):
        start = self.time()
        self.toolhead.dwell(seconds)
        self.event('wait', start=start, duration=seconds, reason=reason)

    def motion_state(self):
        status = self.printer.lookup_object('gcode_move').get_status(0)
        return {key: list(value) if isinstance(value, (list, tuple)) else value
                for key, value in status.items()}

    def connect(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.tc = self.printer.lookup_object('ff_toolchange')
        move = self.toolhead.move

        def observe_move(position, speed):
            start = list(self.toolhead.get_position())
            command = dict(start=start, end=list(position), speed=speed, phase=self.phase)
            # AFC's restore_pos is part of a selection too: it runs after
            # ff_toolchange clears its changing flag and returns from _grab.
            if self.selection_active or self.phase.startswith(('grab ', 'release ')):
                if (abs(position[1] - start[1]) > 1e-6 and
                        max(start[0], position[0]) > self.tc.x_safe + 1e-6):
                    crossed = []
                    for tool in range(4):
                        _, dock_y = self.tc._dock(tool)
                        fraction = (dock_y - start[1]) / (position[1] - start[1])
                        if self.docked[tool] and 0 <= fraction <= 1:
                            x = start[0] + fraction * (position[0] - start[0])
                            if x > self.tc.x_safe + 1e-6:
                                crossed.append(dict(tool=tool, x=x, y=dock_y))
                    self.dock_warnings.append(dict(kind='unsafe_dock_y_travel',
                                                   crossed_docks=crossed, **command))
                    raise self.printer.command_error('Unsafe dock move: Y travel outside dock clearance')
            if self.phase == 'brush':
                y_travel = abs(position[1] - start[1]) > 1e-6
                z_travel = abs(position[2] - start[2]) > 1e-6
                xy_travel = any(abs(position[i] - start[i]) > 1e-6 for i in (0, 1))
                frame = self.tc.gcode_transform._offsets() or (0., 0., 0.)
                base = self.printer.lookup_object('gcode_move').base_position
                a = [start[i] - frame[i] - base[i] for i in range(3)]
                b = [position[i] - frame[i] - base[i] for i in range(3)]
                wiping = (all(264 - 1e-6 <= p[0] <= 271 + 1e-6 and
                               abs(p[1] - 7) < 1e-6 and abs(p[2] + .5) < 1e-6
                               for p in (a, b)))
                problem = ('unsafe_y_travel' if y_travel and max(start[0], position[0]) > 245 + 1e-6
                           else 'mixed_xy_z_travel' if z_travel and xy_travel
                           else 'low_clearance_xy_travel' if xy_travel and min(a[2], b[2]) < 5 - 1e-6 and not wiping
                           else None)
                if problem:
                    self.brush_warnings.append(dict(kind=problem, **command))
                    raise self.printer.command_error('Unsafe brush move: ' + problem)
            # Let Klipper validate travel limits before recording a move as
            # accepted. Negative coordinates retain the real tool transform.
            result = move(position, speed)
            self.motion_commands.append(command)
            return result

        self.toolhead.move = observe_move
        # Replace only the physical input objects. Their names and the status
        # contract remain the same, including AFC's park-detector views.
        for name in self.tc.dock_sensors + self.tc.grab_sensors:
            self.printer.objects['gcode_button ' + name] = Sensor(self, name)
        self.tc.reactor = Clock(self)
        if self.async_unlock:
            if not self.tc.soft_docking or self.motor_mode != 'production':
                raise self.printer.command_error('Async unlock needs soft docking and production motor planning')
            self.tc.release_overlap = self
        append = self.toolhead.trapq_append

        def capture(trapq, time, accel_t, cruise_t, decel_t,
                    x, y, z, xr, yr, zr, start_v, cruise_v, accel):
            self.moves.append(dict(time=time, accel_t=accel_t, cruise_t=cruise_t,
                                   decel_t=decel_t, start=[x, y, z], direction=[xr, yr, zr],
                                   start_v=start_v, cruise_v=cruise_v, accel=accel,
                                   phase=self.phase, mounted=self.mounted))
            return append(trapq, time, accel_t, cruise_t, decel_t,
                          x, y, z, xr, yr, zr, start_v, cruise_v, accel)

        self.toolhead.trapq_append = capture
        wait_moves = self.toolhead.wait_moves

        def wait():
            wait_moves()
            self.update_physical()

        self.toolhead.wait_moves = wait
        if self.motor_mode == 'production':
            self.connect_motor()
        # Observe phase boundaries without replacing the shipped sequences.
        for name in ['grab', 'release']:
            original = getattr(self.tc, '_' + name)

            def phase(tool, original=original, name=name):
                previous = self.phase
                self.phase = f'{name} T{tool}'
                self.event('phase', label=self.phase)
                try:
                    return original(tool)
                finally:
                    self.event('phase_end', label=self.phase)
                    self.phase = previous

            setattr(self.tc, '_' + name, phase)
        heaters = self.printer.lookup_object('heaters')
        for i in range(4):
            name = 'extruder' if i == 0 else f'extruder{i}'
            heater = heaters.lookup_heater(name)
            self.heater_state[name] = [self.initial_temp, 0.]
            original_status = heater.get_status

            def get_temp(eventtime, name=name, heater=heater):
                self.update_heater(name, heater)
                return self.heater_state[name][0], heater.target_temp

            def get_status(eventtime, get_temp=get_temp, original=original_status):
                status = original(eventtime)
                status['temperature'], status['target'] = get_temp(eventtime)
                return status

            heater.get_temp = get_temp
            heater.get_status = get_status
            original_set = heater.set_temp

            def set_temp(target, original=original_set, name=name, heater=heater):
                self.update_heater(name, heater)
                original(target)
                self.event('heater_target', heater=name, target=target)

            heater.set_temp = set_temp
        # The upstream batch backend skips thermal waits. Supply hardware
        # temperature response so our shipped wait macro is exercised.
        self.gcode.register_command('TEMPERATURE_WAIT', None)
        self.gcode.register_command('TEMPERATURE_WAIT', self.temperature_wait)

    def connect_motor(self):
        motor = self.printer.lookup_object('manual_stepper gear_stepper')
        do_move = motor.do_move
        self.motor_do_move = do_move

        def move(position, speed, accel, sync=True):
            self.update_physical()
            contact = self.contact()
            if position > .05 and (contact is None or not self.docked[contact]
                                   or self.mounted is not None):
                raise self.printer.command_error('Lock attempted without a seated, available head')
            do_move(position, speed, accel, sync)
            if position > .05:
                self.mounted, self.locked = contact, True
                self.event('lock', action='GRAB')
            else:
                self.event('lock', action='GRAB2')

        motor.do_move = move
        append = motor.trapq_append

        def capture(trapq, time, accel_t, cruise_t, decel_t,
                    x, y, z, xr, yr, zr, start_v, cruise_v, accel):
            self.motor_moves.append(dict(time=time, duration=accel_t + cruise_t + decel_t,
                                         position=x, distance=xr * (cruise_v * cruise_t +
                                             .5 * (start_v + cruise_v) * accel_t +
                                             .5 * cruise_v * decel_t), speed=cruise_v,
                                         accel=accel, phase=self.phase))
            return append(trapq, time, accel_t, cruise_t, decel_t,
                          x, y, z, xr, yr, zr, start_v, cruise_v, accel)

        motor.trapq_append = capture

        def home(position, speed, accel, triggered, check_trigger):
            # Only the physical unlock endstop is modeled. Use the real
            # stepper planner for its full travel; the actual switch can
            # shorten this interval, which requires a measured trigger point.
            self.toolhead.wait_moves()
            contact = self.contact()
            if contact is None or contact != self.mounted:
                raise self.printer.command_error('Unlock attempted away from the mounted head dock')
            start = self.time()
            do_move(position, speed, accel, sync=True)
            if self.unlock_duration is not None and start + self.unlock_duration > self.time():
                self.delay(start + self.unlock_duration - self.time(), 'estimated unlock response')
            if self.fail_unlock:
                raise self.printer.command_error('No trigger on manual_stepper gear_stepper (simulated)')
            self.mounted, self.locked = None, False
            self.event('lock', action='RELEASE')

        motor.do_homing_move = home

    def overlap_prepare(self, tool):
        dock_x, dock_y = self.tc._dock(tool)
        distance = self.tc._seating_ease_distance()
        if distance > self.dock_support_depth or self.mounted != tool:
            raise self.printer.command_error('Async unlock start is outside supported rail or wrong tool')
        if abs(self.toolhead.get_position()[1] - dock_y) > 1e-6:
            raise self.printer.command_error('Async unlock start is not aligned with dock Y')
        if self.pending_unlock is not None:
            raise self.printer.command_error('Async unlock already pending')

        def start(requested_time):
            motor = self.printer.lookup_object('manual_stepper gear_stepper')
            start_time = max(requested_time, motor.next_cmd_time)
            original_sync = motor.sync_print_time
            schedule_error = None
            # File-output ONLY: retain actual motor planning, supply physical
            # endstop outcome below. Never use this to bypass hardware homing.
            try:
                # Queue current at the serialized motor start, not before
                # the preceding GRAB2 finishes. No G-code or planner flush.
                pin = self.printer.lookup_object('output_pin A4988_REF')
                pin.gcrq._queue_request(start_time, .35)
                motor.sync_print_time = lambda: None
                motor.next_cmd_time = start_time
                motor.do_enable(True)
                motor.do_set_position(0.)
                self.motor_do_move(-.1, .5, 1.875, sync=False)
                if self.fail_async_schedule:
                    raise self.printer.command_error('Injected failure after motor scheduling')
            except Exception as error:
                # Finish already queued docking; report the failure at the
                # withdrawal gate. Queued motor steps cannot be cancelled.
                self.latch_uncertain = True
                schedule_error = str(error)
            finally:
                motor.sync_print_time = original_sync
            pulse_end = motor.next_cmd_time
            complete = max(pulse_end, start_time + (self.unlock_duration or 0.))
            self.pending_unlock = dict(tool=tool, start=start_time, pulse_end=pulse_end,
                                       complete=complete, schedule_error=schedule_error)
            self.events.append(dict(time=start_time, kind='async_unlock_start',
                                    phase=self.phase, mounted=self.mounted,
                                    docked=list(self.docked), locked=self.locked,
                                    requested_time=requested_time,
                                    motor_serialization_delay=start_time-requested_time,
                                    requested_distance=distance, pulse_end=pulse_end,
                                    completion_estimate=complete, schedule_error=schedule_error))
        return start

    def overlap_wait(self, tool):
        pending = self.pending_unlock
        if pending is None or pending['tool'] != tool:
            raise self.printer.command_error('Async unlock was not scheduled')
        if pending['complete'] > self.time():
            self.delay(pending['complete'] - self.time(), 'async unlock completion')
        self.pending_unlock = None
        return pending

    def overlap_finish(self, tool):
        pending = self.overlap_wait(tool)
        if pending['schedule_error']:
            self.event('async_unlock_failed', latch_state='unknown',
                       error=pending['schedule_error'])
            raise self.printer.command_error('T%d release failed: async unlock scheduling failed' % tool)
        if self.fail_unlock:
            self.latch_uncertain = True
            self.event('async_unlock_failed', latch_state='unknown')
            raise self.printer.command_error('T%d release failed: async unlock endstop never triggered' % tool)
        if self.contact() != tool or not self.sensor(self.tc.dock_sensors[tool]):
            self.latch_uncertain = True
            self.event('async_unlock_failed', latch_state='unknown')
            raise self.printer.command_error('Async unlock endpoint confirmation lost')
        self.mounted, self.locked = None, False
        self.event('lock', action='RELEASE', async_unlock=True,
                   motor_start=pending['start'], pulse_end=pending['pulse_end'])
        self.tc._run('SET_PIN PIN=A4988_REF VALUE=0.05')

    def overlap_abort(self, tool):
        # Already queued pulses finish; this does not cancel or relock.
        self.overlap_wait(tool)
        self.latch_uncertain = True
        self.event('async_unlock_failed', latch_state='unknown')

    def ready(self):
        afc = self.printer.lookup_object('AFC')
        # Units populate AFC.units during their connect callbacks, after our
        # connect hook. Observe the same boundary that logs "Tool swap done".
        for unit in afc.units.values():
            tool_swap = unit.tool_swap

            def afc_swap(*args, original=tool_swap, **kwargs):
                self.event('afc_swap_start')
                try:
                    return original(*args, **kwargs)
                finally:
                    self.event('afc_swap_end')

            unit.tool_swap = afc_swap
        for extruder in afc.tools.values():
            extruder.tool_start_callback(self.printer.get_reactor().monotonic(), True)
        self.event('ready')

    def update_heater(self, name, heater):
        temp, previous = self.heater_state[name]
        now = self.time()
        target = heater.target_temp or 25.
        rate = self.heating_rate if target > temp else 0.5
        change = rate * max(0., now - previous)
        temp += max(-change, min(change, target - temp))
        self.heater_state[name] = [temp, now]
        heater.last_temp = heater.smoothed_temp = temp

    def temperature_wait(self, gcmd):
        name = gcmd.get('SENSOR')
        heater = self.printer.lookup_object('heaters').lookup_heater(name)
        minimum = gcmd.get_float('MINIMUM', float('-inf'))
        maximum = gcmd.get_float('MAXIMUM', float('inf'))
        self.update_heater(name, heater)
        temp = self.heater_state[name][0]
        if temp < minimum:
            if heater.target_temp < minimum:
                raise gcmd.error('Simulated heater target cannot reach minimum')
            self.delay((minimum - temp) / self.heating_rate, f'heating {name}')
        elif temp > maximum:
            if heater.target_temp > maximum:
                raise gcmd.error('Simulated heater target cannot reach maximum')
            self.delay((temp - maximum) / .5, f'cooling {name}')
        self.update_heater(name, heater)

    def contact(self):
        x, y, *_ = self.toolhead.get_position()
        for i, tool in enumerate(self.tc.tools):
            dx, dy = self.tc._dock(i)
            if abs(x - dx) < .05 and abs(y - dy) < .05:
                return i
        return None

    def update_physical(self):
        contact = self.contact()
        if self.mounted is not None:
            self.docked[self.mounted] = contact == self.mounted

    def sensor(self, name):
        self.update_physical()
        if name in self.tc.dock_sensors:
            tool = self.tc.dock_sensors.index(name)
            value = self.docked[tool]
            if self.phase.startswith('release ') and tool == self.fail_seat:
                value = False
        else:
            tool = self.tc.grab_sensors.index(name)
            value = self.mounted == tool or (self.contact() == tool and self.docked[tool])
            if tool == self.fail_grab:
                value = False
        now = self.time()
        old, changed = self.readings.get(name, (value, now))
        if value != old:
            # Physical transitions take a configurable time to reach the host.
            pending = self.readings.get(name + ':pending')
            if pending is None or pending[0] != value:
                pending = (value, now + self.sensor_delay)
                self.readings[name + ':pending'] = pending
            if now + 1e-9 < pending[1]:
                return old
            self.readings.pop(name + ':pending', None)
            self.events.append(dict(time=now, kind='sensor', sensor=name,
                                    value=value, phase=self.phase))
        self.readings[name] = (value, changed)
        return value

    def cmd_LOCK(self, gcmd):
        action = gcmd.get('ACTION')
        self.toolhead.wait_moves()
        contact = self.contact()
        if action == 'GRAB':
            if contact is None or not self.docked[contact] or self.mounted is not None:
                raise gcmd.error('Lock attempted without a seated, available head')
            self.delay(self.lock_time, 'lock')
            self.mounted, self.locked = contact, True
        elif action == 'RELEASE':
            if contact != self.mounted or contact is None:
                raise gcmd.error('Unlock attempted away from the mounted head dock')
            self.delay(self.lock_time, 'unlock')
            if self.fail_unlock:
                raise gcmd.error('No trigger on manual_stepper gear_stepper (simulated)')
            self.mounted, self.locked = None, False
        elif action not in ['GRAB2', 'STOP']:
            raise gcmd.error('Unknown simulated lock action')
        self.event('lock', action=action)

    def cmd_LOAD(self, gcmd):
        self.event('filament_load', tool=gcmd.get_int('TOOL'))

    def cmd_RAW_MOVE(self, gcmd):
        """Inject a raw hardware-boundary move to exercise collision guards."""
        self.toolhead.wait_moves()
        previous = self.phase
        self.phase = 'toolchange / return'
        position = list(self.toolhead.get_position())
        for i, axis in enumerate('XYZ'):
            position[i] = gcmd.get_float(axis, position[i])
        try:
            self.toolhead.move(position, gcmd.get_float('SPEED', 500.))
        finally:
            self.phase = previous

    def cmd_UNLOAD(self, gcmd):
        self.event('filament_unload', tool=gcmd.get_int('TOOL'))

    def cmd_BEGIN(self, gcmd):
        self.toolhead.wait_moves()
        self.begin = self.time()
        self.phase = 'toolchange / return'
        self.printer.lookup_object('print_stats').state = 'printing'
        self.event('begin')

    def cmd_CHECK(self, gcmd):
        self.toolhead.wait_moves()
        tool = gcmd.get_int('TOOL')
        status = self.tc.get_status(self.tc.reactor.monotonic())
        afc = self.printer.lookup_object('AFC')
        position = self.printer.lookup_object('gcode_move').get_status(0)['gcode_position']
        issues = []
        if self.mounted != tool or status['current_tool'] != tool or not status['state_ok']:
            issues.append(f'Physical/toolchange state mismatch: {self.mounted}, {status}')
        if afc.current != f'e{tool}':
            issues.append(f'AFC selected {afc.current}, expected e{tool}')
        for i, axis in enumerate('XYZ'):
            target = gcmd.get_float(axis, None)
            if target is not None and abs(position[i] - target) > .001:
                issues.append(f'{axis}={position[i]}, expected {target}')
        self.checks.append(dict(time=self.time(), tool=tool, passed=not issues,
                                position=list(position), afc_lane=afc.current))
        self.event('check', tool=tool, passed=not issues)
        if issues:
            raise gcmd.error('; '.join(issues))

    def output_message(self, message):
        if message.startswith('!!'):
            self.errors.append(message)

    def cmd_REPORT(self, gcmd):
        self.report()

    def disconnect(self):
        self.report()
        # The fork's queue listener is non-daemon, while AFC only stops it
        # at atexit. Stop it before Python waits for non-daemon threads.
        afc = self.printer.lookup_object('AFC', None)
        if afc is not None:
            afc.logger.shutdown()

    def report(self):
        if self.reported or not hasattr(self, 'toolhead'):
            return
        self.reported = True
        self.toolhead.wait_moves()
        end = self.time()
        summary = dict(duration=end - (self.begin or 0), mounted=self.mounted,
                       latch_state='unknown' if self.latch_uncertain else ('locked' if self.locked else 'unlocked'),
                       checks=len(self.checks), passed=sum(c['passed'] for c in self.checks),
                       errors=self.errors)
        swaps = [op for op in self.operations if op['kind'] == 'swap' and op['passed']]
        summary['swap_count'] = len(swaps)
        summary['mean_swap_seconds'] = (sum(op['duration'] for op in swaps) / len(swaps)
                                        if swaps else None)
        summary['mean_all_motion_seconds'] = (sum(op['all_motion_seconds'] for op in swaps) / len(swaps)
                                              if swaps else None)
        summary['all_motion_complete_at'] = max([end] + [op['all_motion_complete_at'] for op in self.operations])
        summary['brush_warnings'] = len(self.brush_warnings)
        summary['dock_warnings'] = len(self.dock_warnings)
        data = dict(summary=summary, begin=self.begin, end=end,
                    model=dict(sensor_delay=self.sensor_delay, lock_time=self.lock_time,
                               soft_docking=self.tc.soft_docking,
                               contact_depth=self.tc.contact_depth,
                               dock_support_depth=self.dock_support_depth,
                               unlock_preview_seconds=self.unlock_preview_seconds,
                               async_unlock=self.async_unlock,
                               unlock_duration_estimate=self.unlock_duration,
                               dock_support_source="user estimate; sensor transition unmeasured",
                               contact_margin=self.tc.contact_margin,
                               contact_speed=self.tc.contact_speed,
                               withdrawal_speed=self.tc.withdrawal_speed,
                               contact_accel=self.tc.contact_accel,
                               ease_distance=self.tc._seating_ease_distance(),
                               configured_ease_distance=self.tc.ease_distance,
                               empty_exit_speed=self.tc.fast_feed / 60.,
                               rail_accel=self.tc.accel_move,
                               motor_mode=self.motor_mode,
                               heating_rate=self.heating_rate, initial_temperature=self.initial_temp,
                               geometry_source=self.geometry_source,
                               travel_limits=[list(rail.get_range()) for rail in
                                              self.toolhead.get_kinematics().rails],
                               docks=[list(self.tc._dock(i)) for i in range(4)]),
                    events=self.events, moves=self.moves, checks=self.checks,
                    final_motion_limits={key: self.toolhead.get_status(0)[key] for key in
                                         ('max_accel', 'minimum_cruise_ratio')},
                    operations=self.operations, motor_moves=self.motor_moves)
        data.update(brush_runs=self.brush_runs, brush_warnings=self.brush_warnings,
                    dock_warnings=self.dock_warnings, motion_commands=self.motion_commands)
        self.output.write_text(json.dumps(data, indent=2))


def load_config(config):
    return Creator5Sim(config)

# Exclude moves toward and inside objects
#
# Copyright (C) 2019  Eric Callahan <arksine.code@gmail.com>
# Copyright (C) 2021  Troy Jacobson <troy.d.jacobson@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import logging
import json


LARGE_EXTRUDER_ADJUSTMENT = 5.0


class ExcludeObject:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.gcode_move = self.printer.load_object(config, 'gcode_move')
        self.printer.register_event_handler('klippy:connect',
                                            self._handle_connect)
        self.printer.register_event_handler("virtual_sdcard:reset_file",
                                            self._reset_file)
        self.next_transform = None

        self._reset_state()
        self.gcode.register_command(
            'EXCLUDE_OBJECT_START', self.cmd_EXCLUDE_OBJECT_START,
            desc=self.cmd_EXCLUDE_OBJECT_START_help)
        self.gcode.register_command(
            'EXCLUDE_OBJECT_END', self.cmd_EXCLUDE_OBJECT_END,
            desc=self.cmd_EXCLUDE_OBJECT_END_help)
        self.gcode.register_command(
            'EXCLUDE_OBJECT', self.cmd_EXCLUDE_OBJECT,
            desc=self.cmd_EXCLUDE_OBJECT_help)
        self.gcode.register_command(
            'EXCLUDE_OBJECT_DEFINE', self.cmd_EXCLUDE_OBJECT_DEFINE,
            desc=self.cmd_EXCLUDE_OBJECT_DEFINE_help)

    def _register_transform(self):
        if self.next_transform is None:
            tuning_tower = self.printer.lookup_object('tuning_tower')
            if tuning_tower.is_active():
                logging.info('The ExcludeObject move transform is not being '
                             'loaded due to Tuning tower being Active')
                return

            self.next_transform = self.gcode_move.set_move_transform(
                self, force=True)
            # XYZ belongs to the one physical carriage. E history belongs to
            # the active extruder; sharing its maxima and pending adjustment
            # across ACTIVATE_EXTRUDER is capable of turning an XY travel into
            # a very large extrusion.
            self.xyz_offset = [0., 0., 0.]
            self.physical_xyz = None
            self.last_excluded_xyz = [0., 0., 0.]
            self.e_states = {}
            self.active_extruder = None
            self.initial_extrusion_moves = 5
            self.last_position = [0., 0., 0., 0.]

            self.get_position()

    def _handle_connect(self):
        self.toolhead = self.printer.lookup_object('toolhead')

    def _unregister_transform(self):
        if self.next_transform:
            tuning_tower = self.printer.lookup_object('tuning_tower')
            if tuning_tower.is_active():
                logging.error('The Exclude Object move transform was not '
                              'unregistered because it is not at the head of '
                              'the transform chain.')
                return

            self.gcode_move.set_move_transform(self.next_transform, force=True)
            self.next_transform = None
            self.gcode_move.reset_last_position()

    def _reset_state(self):
        self.objects = []
        self.excluded_objects = []
        self.current_object = None

    def _reset_file(self):
        self._reset_state()
        self._unregister_transform()

    def _extruder_name(self):
        return self.toolhead.get_extruder().get_name()

    @staticmethod
    def _new_e_state(e_position):
        # Seed every value from the selected extruder's real position, not
        # zero. A non-zero or negative E origin must not manufacture a
        # retraction difference on the first excluded region.
        return {
            'offset': 0.,
            'last_virtual': e_position,
            'last_normal': e_position,
            'max_normal': e_position,
            'last_excluded': e_position,
            'max_excluded': e_position,
            'pending_adjustment': 0.,
            'was_ignored': False,
            'last_excluded_object': None,
        }

    @staticmethod
    def _state_log(state):
        return ('offset=%.6f last=%.6f normal=%.6f/%.6f '
                'excluded=%.6f/%.6f pending=%.6f ignored=%s'
                % (state['offset'], state['last_virtual'],
                   state['last_normal'], state['max_normal'],
                   state['last_excluded'], state['max_excluded'],
                   state['pending_adjustment'], state['was_ignored']))

    def _select_e_state(self, physical_e):
        ename = self._extruder_name()
        state = self.e_states.get(ename)
        new_state = state is None
        if new_state:
            state = self._new_e_state(physical_e)
            self.e_states[ename] = state
        if ename != self.active_extruder:
            previous = self.active_extruder or '<none>'
            self.active_extruder = ename
            logging.info(
                "exclude_object: active extruder %s -> %s; physical_e=%.6f;"
                " %s", previous, ename, physical_e, self._state_log(state))
            if new_state and self._test_in_excluded_region():
                logging.warning(
                    "exclude_object: first observation of %s is inside"
                    " excluded object %s; using physical E %.6f as a neutral"
                    " retraction baseline", ename, self.current_object,
                    physical_e)
        return state

    def _rebase_spatial_position(self, xyz):
        if self.physical_xyz is None:
            self.physical_xyz = list(xyz)
            self.last_excluded_xyz = [xyz[i] + self.xyz_offset[i]
                                      for i in range(3)]
            return
        # A tool-frame change below this transform changes the coordinates
        # returned by get_position() without being an exclude-object move.
        # Shift the comparison anchor with it while retaining the outstanding
        # virtual-to-physical offset.
        delta = [xyz[i] - self.physical_xyz[i] for i in range(3)]
        if any(delta):
            self.last_excluded_xyz = [self.last_excluded_xyz[i] + delta[i]
                                      for i in range(3)]
        self.physical_xyz = list(xyz)

    def get_position(self):
        pos = self.next_transform.get_position()
        self._rebase_spatial_position(pos[:3])
        state = self._select_e_state(pos[3])
        state['last_virtual'] = pos[3] + state['offset']
        self.last_position = [pos[i] + self.xyz_offset[i] for i in range(3)]
        self.last_position.append(state['last_virtual'])
        return list(self.last_position)

    def _state_for_move(self):
        ename = self._extruder_name()
        if ename == self.active_extruder:
            return self.e_states[ename]
        # Normally gcode_move calls get_position() from its activation event
        # before the next move. Select here too so correctness does not depend
        # on event-handler registration order or on callers doing that reset.
        pos = self.next_transform.get_position()
        self._rebase_spatial_position(pos[:3])
        state = self._select_e_state(pos[3])
        state['last_virtual'] = pos[3] + state['offset']
        self.last_position = [pos[i] + self.xyz_offset[i] for i in range(3)]
        self.last_position.append(state['last_virtual'])
        return state

    def _calculate_adjustment(self, state):
        adjustment = (state['max_excluded'] - state['last_excluded']
                      - (state['max_normal'] - state['last_normal']))
        state['pending_adjustment'] = adjustment
        state['was_ignored'] = False
        if abs(adjustment) >= LARGE_EXTRUDER_ADJUSTMENT:
            logging.warning(
                "exclude_object: large E correction %.6f mm for %s after"
                " excluded object %s; %s; xyz_offset=%s last_excluded_xyz=%s",
                adjustment, self.active_extruder,
                state['last_excluded_object'],
                self._state_log(state), self.xyz_offset,
                self.last_excluded_xyz)
        else:
            # Keep this at info level: it is the evidence that the skipped
            # object's E history was reconciled without borrowing another
            # tool's state.  This is emitted once when leaving an excluded
            # region, not once per move.
            logging.info(
                "exclude_object: E correction %.6f mm for %s after excluded"
                " object %s; %s; xyz_offset=%s last_excluded_xyz=%s",
                adjustment, self.active_extruder,
                state['last_excluded_object'], self._state_log(state),
                self.xyz_offset, self.last_excluded_xyz)

    def _normal_move(self, newpos, speed):
        state = self._state_for_move()
        if state['was_ignored']:
            self._calculate_adjustment(state)

        if (self.initial_extrusion_moves > 0
                and state['last_virtual'] != newpos[3]):
            # Preserve upstream's one-time grace after installing the
            # transform. It is global: restarting it for every tool would
            # print the first moves of a cancelled object after every switch.
            self.initial_extrusion_moves -= 1

        if ((self.xyz_offset[0] != 0 or self.xyz_offset[1] != 0)
                and (newpos[0] != self.last_excluded_xyz[0]
                     or newpos[1] != self.last_excluded_xyz[1])):
            self.xyz_offset = [0., 0., 0.]
            state['offset'] += state['pending_adjustment']
            state['pending_adjustment'] = 0.

        if (self.xyz_offset[2] != 0
                and newpos[2] != self.last_excluded_xyz[2]):
            self.xyz_offset[2] = 0.

        if (state['pending_adjustment'] != 0
                and newpos[3] != state['last_excluded']):
            state['offset'] += state['pending_adjustment']
            state['pending_adjustment'] = 0.

        tx_pos = [newpos[i] - self.xyz_offset[i] for i in range(3)]
        tx_pos.append(newpos[3] - state['offset'])
        self.next_transform.move(tx_pos, speed)

        self.physical_xyz = list(tx_pos[:3])
        state['last_virtual'] = newpos[3]
        state['last_normal'] = newpos[3]
        state['max_normal'] = max(state['max_normal'], newpos[3])
        self.last_position = list(newpos)

    def _ignore_move(self, newpos, speed):
        state = self._state_for_move()
        self.xyz_offset = [newpos[i] - self.physical_xyz[i]
                           for i in range(3)]
        state['offset'] += newpos[3] - state['last_virtual']
        state['last_virtual'] = newpos[3]
        state['last_excluded'] = newpos[3]
        state['max_excluded'] = max(state['max_excluded'], newpos[3])
        state['was_ignored'] = True
        state['last_excluded_object'] = self.current_object
        self.last_position = list(newpos)
        self.last_excluded_xyz = list(newpos[:3])

    def _test_in_excluded_region(self):
        return (self.current_object in self.excluded_objects
                and self.initial_extrusion_moves == 0)

    def get_status(self, eventtime=None):
        return {
            "objects": self.objects,
            "excluded_objects": self.excluded_objects,
            "current_object": self.current_object
        }

    def move(self, newpos, speed):
        self.last_speed = speed
        if self._test_in_excluded_region():
            self._ignore_move(newpos, speed)
        else:
            self._normal_move(newpos, speed)

    cmd_EXCLUDE_OBJECT_START_help = "Marks the beginning the current object" \
                                    " as labeled"
    def cmd_EXCLUDE_OBJECT_START(self, gcmd):
        name = gcmd.get('NAME').upper()
        if not any(obj["name"] == name for obj in self.objects):
            self._add_object_definition({"name": name})
        self.current_object = name
        self.was_excluded_at_start = self._test_in_excluded_region()

    cmd_EXCLUDE_OBJECT_END_help = "Marks the end the current object"
    def cmd_EXCLUDE_OBJECT_END(self, gcmd):
        if self.current_object == None and self.next_transform:
            gcmd.respond_info("EXCLUDE_OBJECT_END called, but no object is"
                              " currently active")
            return
        name = gcmd.get('NAME', default=None)
        if name != None and name.upper() != self.current_object:
            gcmd.respond_info("EXCLUDE_OBJECT_END NAME=%s does not match the"
                              " current object NAME=%s" %
                              (name.upper(), self.current_object))

        self.current_object = None

    cmd_EXCLUDE_OBJECT_help = "Cancel moves inside a specified objects"
    def cmd_EXCLUDE_OBJECT(self, gcmd):
        reset = gcmd.get('RESET', None)
        current = gcmd.get('CURRENT', None)
        name = gcmd.get('NAME', '').upper()

        if reset:
            if name:
                self._unexclude_object(name)
            else:
                self.excluded_objects = []
        elif name:
            if name.upper() not in self.excluded_objects:
                self._exclude_object(name.upper())
        elif current:
            if not self.current_object:
                raise self.gcode.error('There is no current object to cancel')
            self._exclude_object(self.current_object)
        else:
            self._list_excluded_objects(gcmd)

    cmd_EXCLUDE_OBJECT_DEFINE_help = "Provides a summary of an object"
    def cmd_EXCLUDE_OBJECT_DEFINE(self, gcmd):
        reset = gcmd.get('RESET', None)
        name = gcmd.get('NAME', '').upper()

        if reset:
            self._reset_file()
        elif name:
            parameters = gcmd.get_command_parameters().copy()
            parameters.pop('NAME')
            center = parameters.pop('CENTER', None)
            polygon = parameters.pop('POLYGON', None)

            obj = {"name": name.upper()}
            obj.update(parameters)
            if center != None:
                obj['center'] = json.loads('[%s]' % center)
            if polygon != None:
                obj['polygon'] = json.loads(polygon)
            self._add_object_definition(obj)
        else:
            self._list_objects(gcmd)

    def _add_object_definition(self, definition):
        self.objects = sorted(self.objects + [definition],
                              key=lambda o: o["name"])

    def _exclude_object(self, name):
        self._register_transform()
        self.gcode.respond_info('Excluding object {}'.format(name.upper()))
        if name not in self.excluded_objects:
            self.excluded_objects = sorted(self.excluded_objects + [name])

    def _unexclude_object(self, name):
        self.gcode.respond_info('Unexcluding object {}'.format(name.upper()))
        if name in self.excluded_objects:
            excluded_objects = list(self.excluded_objects)
            excluded_objects.remove(name)
            self.excluded_objects = sorted(excluded_objects)

    def _list_objects(self, gcmd):
        if gcmd.get('JSON', None) is not None:
            object_list = json.dumps(self.objects)
        else:
            object_list = " ".join(obj['name'] for obj in self.objects)
        gcmd.respond_info('Known objects: {}'.format(object_list))

    def _list_excluded_objects(self, gcmd):
        object_list = " ".join(self.excluded_objects)
        gcmd.respond_info('Excluded objects: {}'.format(object_list))


def load_config(config):
    return ExcludeObject(config)

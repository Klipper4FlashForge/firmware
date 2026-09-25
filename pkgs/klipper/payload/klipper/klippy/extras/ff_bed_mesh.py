# Creator 5 bed-mesh probing adapter
#
# Copyright (C) 2026
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""Reduce contact-probe Z travel for the Creator 5 bed mesh.

Klipper normally raises Z to one fixed absolute height before every bed-mesh
XY move. On the Creator 5 that repeated Z travel accounts for a substantial
part of mesh calibration time.

This adapter is attached only to bed_mesh's ProbePointsHelper. It keeps
Klipper's standard point order and leaves homing and other probe users
unchanged. The first transfer uses a safe absolute height. Every later
transfer is recalculated from the most recent trigger height, with a recovery
height used only when the calculated target would be too low.
"""

import logging


class FFBedMesh:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.first_move_z = config.getfloat('first_move_z', 5., minval=0.)
        self.travel_clearance = config.getfloat(
            'travel_clearance', 2., above=0.)
        self.low_height_threshold = config.getfloat(
            'low_height_threshold', 1.)
        self.recovery_move_z = config.getfloat(
            'recovery_move_z', 3., minval=0.)
        if self.first_move_z <= self.low_height_threshold:
            raise config.error(
                "ff_bed_mesh: first_move_z must be above "
                "low_height_threshold")
        if self.recovery_move_z <= self.low_height_threshold:
            raise config.error(
                "ff_bed_mesh: recovery_move_z must be above "
                "low_height_threshold")

        bed_mesh = self.printer.lookup_object('bed_mesh', None)
        if bed_mesh is None:
            raise config.error(
                "[ff_bed_mesh] must be configured after [bed_mesh]")
        try:
            self.probe_helper = bed_mesh.bmc.probe_mgr.probe_helper
        except AttributeError:
            raise config.error(
                "[ff_bed_mesh] is incompatible with this Klipper bed_mesh")

        # Patch only this bed-mesh helper instance. Other users of the probe
        # retain their standard Klipper behavior and point ordering.
        self.probe_helper._raise_tool = self._raise_tool
        self.printer.lookup_object('gcode').register_command(
            'FF_BED_MESH_STATUS', self.cmd_FF_BED_MESH_STATUS,
            desc='Show Creator 5 bed-mesh Z optimization status')
        logging.info(
            "ff_bed_mesh: installed standard path; first_z=%.3f "
            "clearance=%.3f threshold=%.3f recovery_z=%.3f",
            self.first_move_z, self.travel_clearance,
            self.low_height_threshold, self.recovery_move_z)

    def _raise_tool(self, is_first=False):
        helper = self.probe_helper
        toolhead = self.printer.lookup_object('toolhead')
        current_z = toolhead.get_position()[2]
        speed = helper.speed if is_first else helper.lift_speed

        if is_first:
            target_z = max(self.first_move_z, current_z)
        else:
            # Recalculate after every trigger. Recovery is therefore not
            # latched: as soon as trigger_z + clearance is safe again, the
            # following transfer automatically returns to the lower height.
            target_z = current_z + self.travel_clearance
            if target_z < self.low_height_threshold:
                target_z = self.recovery_move_z
            target_z = max(target_z, current_z + .001)
        helper._move([None, None, target_z], speed)

    def get_status(self, eventtime):
        return {
            'path': 'standard',
            'dynamic_z': True,
            'first_move_z': self.first_move_z,
            'travel_clearance': self.travel_clearance,
            'low_height_threshold': self.low_height_threshold,
            'recovery_move_z': self.recovery_move_z,
        }

    def cmd_FF_BED_MESH_STATUS(self, gcmd):
        gcmd.respond_info(
            "FF bed mesh: path=standard, dynamic_z=yes\n"
            "first_move_z=%.3f, travel_clearance=%.3f, "
            "low_height_threshold=%.3f, recovery_move_z=%.3f\n"
            "Recovery is evaluated per point and is not latched."
            % (self.first_move_z, self.travel_clearance,
               self.low_height_threshold, self.recovery_move_z))


def load_config(config):
    return FFBedMesh(config)

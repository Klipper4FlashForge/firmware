# Nozzle diameters from Mainsail

The three macros `NOZZLE_SIZE_STATUS`, `NOZZLE_SIZE_SET`, and
`NOZZLE_SIZE_APPLY` are Mainsail buttons. They configure the actual nozzle
diameter for each Creator 5 tool without editing `printer.cfg` by hand.
The helper is activated by `[ff_nozzle_size]` in `printer_n4s4.cfg`.

## Changing a nozzle size

Finish or cancel the current print and wait for all motion to stop. A paused
print is still a print: changing its nozzle configuration is refused.
After physically installing the nozzle, use the Mainsail console:

```gcode
NOZZLE_SIZE_STATUS
NOZZLE_SIZE_SET TOOL=3 DIAMETER=0.25
NOZZLE_SIZE_STATUS
```

The second status shows T3's active size and its staged replacement, for
example `T3 [extruder3]: 0.400 mm -> 0.250 mm staged`. Nothing has been written
to disk and the live extruder still uses the previous size.

Stage further tools if needed, then save all staged changes together:

```gcode
NOZZLE_SIZE_SET TOOL=0 DIAMETER=0.6
NOZZLE_SIZE_APPLY
```

`APPLY` calls Klipper's native `SAVE_CONFIG`, which creates its normal
timestamped backup, stores the changed `nozzle_diameter` options in the
managed block at the end of `printer.cfg`, and restarts Klipper. Heater
targets are cleared by the restart. No additional `FIRMWARE_RESTART` is
needed. Once Mainsail reconnects, run `NOZZLE_SIZE_STATUS` again to confirm
the active values.

| Tool | Klipper section |
| --- | --- |
| `TOOL=0` | `[extruder]` |
| `TOOL=1` | `[extruder1]` |
| `TOOL=2` | `[extruder2]` |
| `TOOL=3` | `[extruder3]` |

`DIAMETER` is in millimetres; finite values from 0.10 to 1.20 are accepted,
including 0.25, 0.40, 0.60, and 0.80. Both parameters are required by `SET`.
`APPLY` with no changes does not write anything or restart the printer.

To cancel a staged edit for one tool, set it back to the active value shown
by `STATUS`. To discard all unapplied edits, use `RESTART`. Staged edits live
only in RAM: neither a reboot nor a plain `SAVE_CONFIG` applies them. A saved
diameter, by contrast, survives a reboot. To revert a saved change, stage the
previous diameter and run `NOZZLE_SIZE_APPLY` again.

## Configuration and error handling

Keep `nozzle_diameter` definitions in the generated main `printer.cfg` or its
managed `SAVE_CONFIG` block. Do not add them to included files such as
`printer_n4s4.cfg`: Klipper cannot save an option that conflicts with a value
in an include. If an earlier manual override exists there, remove that
override and run `RESTART` before using these macros. Native `SAVE_CONFIG`
comments out the old main-file option when it saves the replacement.

If another calibration already awaits `SAVE_CONFIG`, `APPLY` refuses to save
anything. Save that calibration with `SAVE_CONFIG`, or discard it with
`RESTART`, then stage the nozzle changes again after the restart. This avoids
unintentionally saving other pending calibration work.

An include conflict or native save error is reported in the console. The
helper restores the previous in-memory autosave state and retains its staged
nozzle edits for inspection; it does not request an additional restart after
the error. File writing, backups, and reloads use Klipper's own implementation
and require no shell scripts or additional BusyBox commands.

For Moonraker or other macros, `printer.ff_nozzle_size.diameters` reports the
active values and `printer.ff_nozzle_size.pending` the staged replacements,
both keyed by `T0` through `T3`.

## Orca and extrusion limits

Also set the same nozzle diameter in the Orca printer profile, and re-slice
the job. The macros do not change the slicer profile or an existing G-code
file. The nozzle-dependent startup purge line uses Orca's
`nozzle_diameter[initial_extruder]`; printer-side filament and purge macros
read the active Klipper configuration instead.

Klipper uses the diameter to derive default extrusion limits, including the
maximum extrusion cross-section (`4 * nozzle_diameter**2`). Saving and
restarting lets Klipper recalculate those values normally. Explicitly
configured limits such as `max_extrude_cross_section` remain explicit limits;
the helper does not overwrite them. Changing nozzle size does not change
`rotation_distance`, flow calibration, tool Z calibration, or material and
build-plate offsets.

## Installing this addition on an existing printer

Copy these two files from the firmware checkout:

| Repository file | Printer destination |
| --- | --- |
| `pkgs/klipper/payload/klipper/klippy/extras/ff_nozzle_size.py` | `/usr/data/anvil/klipper/klippy/extras/ff_nozzle_size.py` |
| `pkgs/klipper-config/payload/config/printer_n4s4.cfg` | `/usr/data/anvil-data/config/printer_n4s4.cfg` |

The `[include printer_n4s4.cfg]` line must already be enabled. Run `RESTART`
after installing this brand-new extra and its activation section (or reboot
the printer). When replacing a version of the Python extra that has already
been loaded, reboot so the Python process imports the new code. The firmware
build includes the new `ff_*.py` extra through the existing package recipe.

For an initial check, run `NOZZLE_SIZE_STATUS`, stage a size, inspect
`NOZZLE_SIZE_STATUS`, then set it back to the active size and run
`NOZZLE_SIZE_APPLY`: no save or restart should occur.
After a real nozzle change, stage the installed size, apply it, and verify it
with `NOZZLE_SIZE_STATUS` after reconnection. No homing, tool pickup, or
extrusion is triggered by these macros.

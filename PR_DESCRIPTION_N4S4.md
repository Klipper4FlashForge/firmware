# Improve Creator 5 tool changes, Z-offset handling, purge control, and Orca integration

## Summary

This change set improves multi-tool printing on the FlashForge Creator 5,
especially when using OrcaSlicer and a prime tower. It addresses unsafe or
slow tool-change travel, nozzle ooze during pickup, shared-extruder-stepper
handling, incorrect prime-tower geometry in multi-plate projects, and the lack
of separate build-plate and filament Z corrections.

It also adds user-facing controls for adaptive meshing, timelapse capture, and
the pre-print purge sequence.

## Problems addressed

- After a tool change, the new tool could return to the position at which the
  previous object was last printed. This could lower the nozzle over an
  existing part and leave a blob before travelling to the prime tower.
- Tool-change travel from the dock was unnecessarily slow because the first
  slicer move could be an extruding move with a low feed rate.
- A hot tool could ooze while leaving its dock because retraction happened too
  late.
- Fast stationary pressure recovery at the prime tower could create a large
  blob.
- Dock travel did not consistently provide clearance over raised print lines.
- The Creator 5 uses one physical extruder stepper with four logical
  extruders, which required explicit motion-queue synchronization and Klipper
  API compatibility fixes.
- OrcaSlicer multi-plate placeholders could describe the first plate's prime
  tower even when another plate was sliced. The tower depth was also assumed
  to equal its configured width, although the generated tower can be
  rectangular.
- Build plates and filament materials could not have independent additive
  first-layer Z corrections.
- Pre-print purge behavior, adaptive meshing, and timelapse capture were not
  conveniently configurable from Mainsail.

## Main changes

### Shared extruder stepper

- Keep the physical extruder stepper owned by `[extruder]`; `extruder1` through
  `extruder3` remain logical hotends with independent heaters and motion
  queues.
- Synchronize the shared stepper to the selected logical extruder after every
  pickup, including selection of an already mounted tool.
- Make Pressure Advance work with the active logical extruder while validating
  that the shared stepper is connected to the correct motion queue.
- Keep the shared-stepper policy in an early-loaded `ff_extruder.py` adapter,
  leaving Klipper's generic `kinematics/extruder.py` unchanged. The adapter
  consumes the Creator 5's repeated legacy stepper options for logical
  extruders and suppresses creation of duplicate physical steppers.
- Rebind the installed adapter to Klipper's new Printer object across
  `RESTART` and `FIRMWARE_RESTART`, without accepting a genuinely duplicated
  `[ff_extruder]` section in one configuration.

### Safer and cleaner tool changes

- Report `TOOLCHANGE_PARK` as `changing` throughout the release operation so
  HelixScreen does not turn the normal dock/grab sensor transition into a
  transient `Filament System Error`; persistent sensor faults remain errors.
- Expose `TOOLCHANGE_STATUS` through a real `[gcode_macro]` wrapper so it is
  available as a Mainsail macro tile; the Python implementation remains an
  internal `FF_TOOLCHANGE_STATUS` command.
- Add configurable XY restoration speed and a 2 mm Z-hop for travel both to
  and from the docks.
- Retract filament while the newly selected hot tool is still fully seated in
  its dock, before pulling it into the build area.
- Keep retract and recovery distances and speeds independently configurable.
- Perform only a small, slow stationary recovery when the captured position is
  already inside the prime tower.
- If a tool change was requested over a printed object, suppress restoration
  to that XY position. The tool remains raised and retracted while Orca moves
  it to the prime tower, preventing a dot or blob on the previous object.
- Preserve the original restoration behavior when no prime-tower geometry has
  been registered, for compatibility with existing G-code.

New `[ff_toolchange]` options used by the local configuration:

```ini
[ff_toolchange]
restore_axis: xy
restore_feed: 30000
restore_z_hop: 2.0
restore_z_feed: 1200
restore_retract: 2.0
restore_retract_feed: 1800
restore_unretract: 0.4
restore_unretract_feed: 200
```

### Prime-tower detection and registration

- Add `TOOLCHANGE_SET_PRIME_TOWER` to register or clear the active tower's
  center, width, depth, brim, rotation, and safety margin.
- Parse the resolved single-value Orca metadata from the G-code tail instead
  of trusting potentially ambiguous multi-plate start placeholders.
- Measure the second tower dimension from the emitted tower outline before
  `WIPE_TOWER_BRIM_START`, allowing rectangular towers instead of assuming
  `DEPTH=WIDTH`.
- Expose the parsed tower geometry through `printer.ff_print`.
- Register the corrected tower both as an exclude object for adaptive mesh
  bounds and with the toolchanger for position-restoration decisions.
- Keep bounded head/tail reads and use a streaming fallback when the relevant
  tower outline is outside the initial read window.

### Build-plate and filament Z corrections

- Split the print-scoped Z correction into independent components:
  - original temperature, bed, and first-layer correction;
  - build-plate correction;
  - active-filament correction.
- Extend `TOOLCHANGE_SET_PRINT_OFFSET` with `PLATE=`.
- Add `TOOLCHANGE_SET_MATERIAL_OFFSET VALUE=<mm> [MOVE=1]`.
- Treat both values as absolute components, so repeated calls do not
  accumulate drift.
- Preserve manual babystepping and per-tool calibration when clearing the
  print-scoped correction.
- Report every component separately through the console and
  `TOOLCHANGE_STATUS`.
- Add `_BUILD_PLATE_OFFSETS` and symbolic Orca plate mappings in the local
  configuration. Actual calibration values remain printer-specific and
  default to `0.000`.

The material correction can be selected from each Orca filament profile with:

```gcode
TOOLCHANGE_SET_MATERIAL_OFFSET VALUE=0.000
```

### Adaptive mesh controls

- Add `ADAPTIVE_MESH_TOGGLE` and `ADAPTIVE_MESH_STATUS` for Mainsail.
- Enabled mode probes a fresh adaptive mesh around the real object and prime
  tower bounds.
- Disabled mode skips probing and loads the saved `MESH_DATA` profile.
- Keep tool parking, Z homing, first-tool pickup, and print Z-offset setup in a
  safe order.

### Pre-print purge and filament checks

- Add persistent `START_PURGE_SET` and `START_PURGE_STATUS` controls with
  `OFF`, `FIRST`, and `ALL` modes.
- In `ALL` mode, purge only the tools actually used by the current G-code, not
  every installed tool.
- Discover used tools from Orca metadata with a fallback scan of `Tn`
  commands.
- Check each used tool's `fd_exN` filament-presence sensor before printing.
  The `fm_exN` motion sensors remain runtime clog/motion detectors.
- Use safe non-diagonal paths for consecutive rear-chute purge operations, so
  the carriage does not cross an occupied dock or purge onto the plate.
- Raise the front-wipe target to an absolute 150 C.

### Timelapse controls

- Add a persistent `TIMELAPSE_TOGGLE` control and
  `TIMELAPSE_SETUP_STATUS` status command.
- Allow Orca to keep emitting `TIMELAPSE_TAKE_FRAME` while disabled.
- Report an ignored frame only once per disabled session instead of once per
  layer.

## Files changed

- `/usr/data/anvil/klipper/klippy/extras/ff_extruder.py`
- `/usr/data/anvil/klipper/klippy/extras/ff_toolchange.py`
- `/usr/data/anvil/klipper/klippy/extras/ff_print.py`
- `/usr/data/anvil-data/config/printer_n4s4.cfg`
- `/usr/data/anvil-data/config/timelapse.cfg`
- `/usr/data/anvil-data/config/printer.cfg`
- `CHANGELOG_N4S4.md`
- `ORCA_MACHINE_AND_FILAMENT_SETTINGS.md`
- `PR_DESCRIPTION_N4S4.md`

`printer_n4s4.cfg` is shipped from
`pkgs/klipper-config/payload/config/printer_n4s4.cfg`, so the normal
`anvil-klipper-config` build and link process installs it as
`/usr/data/anvil-data/config/printer_n4s4.cfg`. It contains the
`[ff_extruder]` activation together with the N4S4 integration macros and
calibration values. Include it in `/usr/data/anvil-data/config/printer.cfg`
immediately before the `SAVE_CONFIG` block:
```cfg
[include printer_n4s4.cfg]
```

The OrcaSlicer document contains the complete machine start G-code,
build-plate name mapping, adaptive-mesh controls, and per-filament material
offset setup used by this configuration.


## Validation

- Python modules pass syntax compilation checks.
- Configuration files pass basic parser checks.
- Prime-tower parsing was checked against multiple Orca G-code files from a
  two-plate project with different tower locations.
- The parser resolved the active plate's generated 28 x 14 mm tower instead
  of the incorrect first-plate placeholder and former 28 x 28 mm assumption.
- Manual and automatic purge paths were exercised with consecutive tools.
- Tool pickup, in-dock retract, Z-hop, prime-tower travel, material/build-plate
  Z composition, adaptive mesh selection, and timelapse suppression were
  exercised on a Creator 5 after a full firmware restart.

## Compatibility and fallback behavior

- Existing jobs without registered prime-tower geometry retain the previous
  XY restore behavior.
- Build-plate and material offsets default to zero.
- The original print Z compensation remains active independently of the new
  optional components.
- Adaptive mesh, timelapse, and purge modes can be selected without changing
  Orca's generated layer-by-layer commands.

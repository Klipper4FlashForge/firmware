# Improve Creator 5 tool changes, Z-offset handling, purge control, and Orca integration

## Summary

This change set improves multi-tool printing on the FlashForge Creator 5,
especially when using OrcaSlicer and a prime tower. It addresses unsafe or
slow tool-change travel, nozzle ooze during pickup, shared-extruder-stepper
handling, incorrect prime-tower geometry in multi-plate projects, and the lack
of separate build-plate and filament Z corrections.

It also adds user-facing controls for adaptive meshing, timelapse capture, and
the pre-print purge sequence, plus a Creator 5-specific bed-mesh travel
optimizer.

## Problems addressed

- Klipper's `print_stats` reports only about a quarter of the filament a
  multi-colour job uses, and nothing records tool changes, their duration and
  failures, or where the time of a job goes.
- After a tool change, the new tool could return to the position at which the
  previous object was last printed. This could lower the nozzle over an
  existing part and leave a blob before travelling to the prime tower.
- Tool-change travel from the dock was unnecessarily slow because the first
  slicer move could be an extruding move with a low feed rate.
- A hot tool could ooze while leaving its dock because retraction happened too
  late.
- Fast stationary pressure recovery at the prime tower could create a large
  blob.
- Every pickup in a Prime Tower job received an additional 0.9 mm firmware
  retract even when Orca had already parked that tool with a 2 mm unload
  retract. With small Prime Volume values this stacked pressure deficit could
  leave holes at the aligned wall seam after a tool change.
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
- Residual filament could remain attached to a nozzle after a rear-chute
  purge and be dragged from the purge area toward the print.
- Contact-probe mesh calibration spent unnecessary time repeatedly returning
  to one fixed absolute Z travel height.

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
- Use a stronger one-shot in-dock retract plus a short pressure-settling pause
  for an explicitly prepared pickup or a tool's first pickup in a registered
  Prime Tower job; retain the conservative retract elsewhere.
- Reset and track the successfully selected tools for every print job. Once a
  tool has already been used or prepared, subsequent Prime Tower pickups rely
  on Orca's existing unload retract instead of stacking another 0.9 mm by
  default. `TOOLCHANGE_STATUS` reports this per-job tool set.
- Make the repeated-tower retract independently configurable and cap every
  return prime to the amount actually retracted, preventing an over-prime
  when a small non-zero repeated retract is selected.
- Keep that prepared initial tool raised and retracted instead of returning it
  to the last adaptive-mesh probe point; travel directly onward to the startup
  purge line.
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
restore_retract: 0.4
restore_retract_feed: 1800
purge_retract: 0.9
purge_retract_dwell_ms: 250
tower_repeat_retract: 0.0
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
- Treat Orca's `prime_tower_brim_width = -1` as its automatic-brim sentinel,
  derive the effective brim from the emitted `WIPE_TOWER_BRIM` moves, and
  expose the exact outer bounds. Never pass the negative sentinel to Klipper.
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
- Add an `ff_bed_mesh.py` adapter for contact-probe meshes while retaining
  Klipper's standard row-by-row point order.
- Approach the first point at Z=5 mm, then lift 2 mm relative to the latest
  trigger height. If the calculated transfer height is below Z=1 mm, recover
  to Z=3 mm.
- Recalculate that decision after every point instead of latching recovery
  mode, so the next transfer returns to the normal 2 mm lift as soon as it is
  safe.
- Keep generic Klipper `bed_mesh.py` and `probe.py` unchanged and leave other
  probe users unaffected.
- `FF_BED_MESH_STATUS` reports the standard path and dynamic-Z parameters.

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
- During `START_PURGE`, start heating the next requested tool to its own target
  as soon as the current tool reaches purge temperature. Heating then overlaps
  the current purge, wipe and cooldown instead of beginning after pickup.
- After START_PURGE and one-time no-tower mini-purges, wipe the nozzle with a
  controlled zigzag across the front-right silicone lip. The rear-to-front
  travel is raised and stays at X256 outside the bed; the wipe runs at
  absolute G-code Z-1.0, is configurable, and is enabled by default. The
  following cooldown-pad position is limited to absolute G-code Z-0.9 to
  avoid pressing the nozzle unnecessarily deep into the silicone. Its local
  lip-to-pad lift is only 2 mm. Successive cooldowns rotate through a measured
  30-point grid capped at X267.5/Y13.8, inside the X268/Y14 collision limits
  next to parked T0, using a runtime-based pseudo-random starting point so
  restarts do not repeatedly begin in the centre and no persistent write is
  required.
- Raise the front-wipe target to an absolute 150 C.

### Timelapse controls

- Add a persistent `TIMELAPSE_TOGGLE` control and
  `TIMELAPSE_SETUP_STATUS` status command.
- Allow Orca to keep emitting `TIMELAPSE_TAKE_FRAME` while disabled.
- Report an ignored frame only once per disabled session instead of once per
  layer.

### Statistics

- Add `ff_stats`, a Klipper extra that keeps lifetime and per-job statistics:
  tool changes (swaps and first pickups, duration, failed changes by stage,
  failed grab/release attempts), print time, filament measured per tool, and
  the time of each job divided into phases (prepare, homing, heating, mesh,
  purge, toolchange, print, paused, end).
- Measure filament from each extruder's own running position. Klipper's
  `print_stats` re-bases the G-code E position at every `ACTIVATE_EXTRUDER`
  and reported 12% to 34% (median 25%) of the slicer's estimate on ten
  completed multi-colour jobs, against a median of 1.02 on single-tool jobs.
  `correct_print_stats` (default on) replaces `print_stats.filament_used`
  with the measurement while a tracked job prints, which corrects Mainsail's
  dashboard and Moonraker's job history for new jobs.
- Phases come from Klipper's homing events, the tool-change code and a
  configurable list of commands (`phase_commands`) that `ff_stats` wraps. No
  macro is edited.
- Show the numbers in Mainsail through the `FF_STATS`, `FF_STATS_JOB` and
  `FF_STATS_JOBS` macros, and as `printer.ff_stats` for macros and Moonraker.
  `FF_STATS_RESET CONFIRM=1` starts over and keeps the old file.
- Persist to `/usr/data/anvil-data/stats/ff_stats.json` atomically, rarely
  (the data partition is mounted `sync`), and recover a job left open by a
  power loss as `interrupted`.
- Hooks in `ff_print` and `ff_toolchange` are optional and exception-safe: a
  fault in the statistics cannot fail a print or a tool change.
- Possible follow-ups, deliberately not included: nozzle hours at
  temperature and heat cycles, and maintenance counters (axis travel, motor,
  bed and fan hours).
- Documented in `docs/statistics.md`.

## Files changed

- `/usr/data/anvil/klipper/klippy/extras/ff_stats.py`
- `/usr/data/anvil/klipper/klippy/extras/ff_extruder.py`
- `/usr/data/anvil/klipper/klippy/extras/ff_bed_mesh.py`
- `/usr/data/anvil/klipper/klippy/extras/ff_toolchange.py`
- `/usr/data/anvil/klipper/klippy/extras/ff_print.py`
- `/usr/data/anvil-data/config/printer_n4s4.cfg`
- `/usr/data/anvil-data/config/timelapse.cfg`
- `/usr/data/anvil-data/config/printer.cfg`
- `/usr/data/anvil-data/scripts/10-enable-printer-n4s4.sh`
- `pkgs/timelapse/payload/config/timelapse.cfg`
- `pkgs/timelapse/build.sh`
- `pkgs/timelapse/pkg.conf`
- `pkgs/klipper-config/build.sh`
- `pkgs/anvil-core/payload/bin/anvil-link-prog.sh`
- `qa/static/test_n4s4_include_installer.py`
- `qa/static/test_ff_print.py`
- `qa/static/test_ff_stats.py`
- `qa/static/test_ff_toolchange.py`
- `qa/static/test_timelapse_config.py`
- `qa/replica/test_custom_scripts.py`
- `qa/replica/test_ff_stats.py`
- `docs/statistics.md`
- `FEATURES_N4S4.md`
- `docs/how-a-print-runs.md`
- `mkdocs.yml`
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

The `anvil-klipper-config` package automatically installs its BusyBox-compatible
boot hook as:

```text
/usr/data/anvil-data/scripts/10-enable-printer-n4s4.sh
```

The script runs during boot, verifies that both configuration files exist,
inserts the include before Klipper's `SAVE_CONFIG` area, and creates a backup
only when it changes the file. No SSH installation step is required. Once the
exact include exists, repeated boots exit immediately without adding script
output to the log.

The modified `timelapse.cfg` is part of the `anvil-timelapse` package rather
than a manual file copied into the live configuration. It is installed under
`/usr/data/anvil/config` and linked into `/usr/data/anvil-data/config` by the
normal configuration-linking pass, so package upgrades update the macro while
the live configuration keeps the expected path.

The OrcaSlicer document contains the complete machine start G-code,
build-plate name mapping, adaptive-mesh controls, and per-filament material
offset setup used by this configuration.


## Validation

- Python modules pass syntax compilation checks.
- Configuration files pass basic parser checks.
- Prime-tower parsing was checked against multiple Orca G-code files from a
  two-plate project with different tower locations.
- Automatic Prime Tower brim parsing was checked against an Orca file using
  the `-1` sentinel; its emitted paths resolved to a 2.232 mm brim and exact
  outer bounds without aborting print start.
- Retract selection tests cover explicit purge pickups, first and repeated
  Prime Tower pickups, ordinary no-tower pickups, and return-prime capping.
- Comparing 25 and 30 mm³ Prime Volume jobs showed that the former emitted
  about 21.31 mm³ for normal T2 tower priming and the latter about 28.23 mm³.
  The previous 2.9 mm stacked filament retract corresponds to about 6.98 mm³,
  matching that observed threshold; repeated pickups now avoid the additional
  firmware retract.
- The parser resolved the active plate's generated 28 x 14 mm tower instead
  of the incorrect first-plate placeholder and former 28 x 28 mm assumption.
- Manual and automatic purge paths were exercised with consecutive tools.
- Statistics: unit tests cover jobs, phases, measured filament, the
  `print_stats` correction, tool-change counting (including the real
  `FFToolchange._toolchange` stage tracking), persistence, recovery and the
  reports. A replica test runs `ff_stats` on the printer's interpreter against
  Klipper's real `gcode.py` and `print_stats.py`. The statistics have not yet
  run through a print on a printer.
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

# Creator 5 N4S4 – Change Log

Last updated: 2026-09-23

This document describes the local changes compared with the original
FlashForge/Klipper4FlashForge implementation. All printer paths are relative
to `/usr/data`.

## `/usr/data/anvil/klipper/klippy/extras/ff_extruder.py`

### Shared physical extruder stepper

- The Creator 5 policy is implemented as an early-loaded Klipper extra, so
  the generic Reforge/Klipper `kinematics/extruder.py` remains unmodified.
- Only `[extruder]` creates and owns the physical extruder stepper.
- `extruder1` through `extruder3` remain logical hotends with their own
  heaters, temperatures, and extrusion motion queues.
- Existing stepper and Pressure Advance options belonging to the logical
  extruders are still consumed from the configuration, but they no longer
  create duplicate stepper objects.
- `SET_PRESSURE_ADVANCE` supports an active logical extruder without its own
  stepper by using the stepper owned by `[extruder]`.
- Before applying Pressure Advance, the implementation verifies that the
  shared stepper is synchronized with the active logical extruder's motion
  queue. An incorrect assignment aborts with an error.
- `[ff_extruder]` must be present in the configuration so the adapter is
  installed before Klipper creates the extruders.
- `RESTART` and `FIRMWARE_RESTART` are supported without a host reboot. The
  already-installed class adapters are rebound to Klipper's newly created
  Printer object, while a genuinely duplicated `[ff_extruder]` section in
  the same configuration is still rejected.

## `/usr/data/anvil/klipper/klippy/extras/ff_toolchange.py`

### Shared extruder-stepper switching

- After every tool pickup, and when the already mounted tool is selected
  again, the shared physical stepper is connected to the active logical
  extruder with
  `SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=<extruderN>`.
- The synchronization is reported in the Klipper console.

### Correct HelixScreen status while parking

- `TOOLCHANGE_PARK` now reports the toolchanger as `changing` for the whole
  release sequence, matching a normal `T<n>` tool change.
- The normal moment where the dock switch activates shortly before the grab
  switch clears is therefore no longer exposed to HelixScreen as a transient
  `Filament System Error: error` notification.
- The flag is cleared in all success and failure paths; a sensor state that
  remains inconsistent after the operation is still reported as an error.

### Prime-tower-aware restored position

- The existing `restore_axis` support in Klipper4FlashForge is enabled for
  X/Y by the local configuration. This captures the current G-code position
  before an actual tool change.
- `TOOLCHANGE_SET_PRIME_TOWER` registers the current job's actual rotated
  tower rectangle, including its brim and a safety margin. The registration
  is cleared before every new print and populated by
  `DEFINE_PRIME_TOWER_OBJECT` in the sliced file.
- If the captured position is inside that rectangle, the new nozzle returns
  there without slicer extrusion. This supports Orca files whose next line
  immediately continues an extruding tower move.
- If the captured position is outside the tower, XY restoration is suppressed.
  The new nozzle therefore cannot return to the previous model, lower there,
  or leave a pressure-recovery dot on it. Orca performs the subsequent tower
  travel while the nozzle remains raised and retracted.
- If no tower has been registered, the original XY restoration behaviour is
  retained as a compatibility fallback.
- The return speed is configurable through `restore_feed`.

### Z-hop in both travel directions

- Before the old tool travels toward its dock, the nozzle is raised by the
  configured amount.
- The carriage remains raised while releasing the old tool, picking up the
  new tool, and travelling away from the docks.
- For a change already on the tower, it descends after returning to the saved
  tower position. For a change over the model, Orca's following Z move handles
  the descent at the tower destination.
- The hop distance and Z speed are configurable through `restore_z_hop` and
  `restore_z_feed`.

### In-dock retract and reduced-pressure recovery

- The new tool is first locked synchronously with `MOTOR_GRAB`.
- While the tool is still fully seated in its dock, its logical extruder and
  the shared physical stepper are activated and the filament is retracted.
- The retract is skipped automatically when the tool is below its minimum
  extrusion temperature.
- The tool is pulled out of the dock only after the retract, minimizing
  filament strings on the way to the prime tower.
- Retract distance and speed are configurable through `restore_retract` and
  `restore_retract_feed`.
- Recovery and retract are configured separately. When the captured position
  is already on the prime tower, `restore_unretract` deliberately advances
  less filament and `restore_unretract_feed` advances it more slowly. For a
  change over the model, stationary recovery is skipped completely and the
  slicer's moving prime-tower lines rebuild pressure.
- All additional E moves temporarily use relative extrusion mode. The
  slicer's extrusion mode and logical E coordinate are restored afterward.
- Selecting the already mounted tool again and running `TOOLCHANGE_PARK` do
  not trigger an additional retract or Z-hop.

### New `[ff_toolchange]` options

- `restore_z_hop`
- `restore_z_feed`
- `restore_retract`
- `restore_retract_feed`
- `restore_unretract`
- `restore_unretract_feed`

If these additional values are not configured, the original behavior is
preserved.

### Build-plate and filament Z components

- The print-scoped Z correction is now maintained as three independent
  components: the original temperature/bed/layer base, a build-plate value,
  and an active-filament value.
- `TOOLCHANGE_SET_PRINT_OFFSET` accepts `PLATE=<-0.5..0.5>` and includes it
  in the absolute job offset without touching tool calibration or babystep.
- `CLEAR=1` resets all three print-scoped components at the beginning or end
  of a job.
- The new command
  `TOOLCHANGE_SET_MATERIAL_OFFSET VALUE=<-0.5..0.5> [MOVE=1]` replaces the
  active material component absolutely, so repeated calls cannot accumulate.
- With its default `MOVE=1`, a homed printer with a mounted tool applies the
  material correction as a dedicated Z move. It is therefore not blended
  diagonally into the next extruding prime-tower move.
- `TOOLCHANGE_STATUS` reports the total print Z correction and its base,
  plate, and material components separately.

## `/usr/data/anvil/klipper/klippy/extras/ff_print.py`

### Used-tool discovery

- The print-start metadata parser now exposes every tool used by the G-code,
  not just its initial tool.
- Current Orca files are read from their compact, one-based `; filament:`
  header. Older files fall back to a streaming scan of bare `Tn` commands.
- The complete list is passed to the configured pre-print callback as
  `TOOLS=` and is also exposed as `printer.ff_print.tools`.

### Active prime-tower geometry

- A bounded tail read extracts Orca's single resolved `wipe_tower_x`,
  `wipe_tower_y`, `prime_tower_width`, `prime_tower_brim_width`, and
  `wipe_tower_rotation_angle` values.
- This avoids Orca's multi-plate placeholder issue: `[wipe_tower_x]` and
  `[wipe_tower_y]` in custom machine start G-code can expand to the first
  entry of a comma-separated project list rather than the active plate's
  actual tower coordinates.
- The resolved values are exposed through `printer.ff_print` for the
  prime-tower registration macro.
- Because Orca stores only one configured tower width while deriving the
  other dimension from purge volume, the parser also measures the emitted
  core outline before `WIPE_TOWER_BRIM_START`. It exposes the actual width,
  depth, centre, and world-coordinate bounds. For the two four-strip test
  files this correctly resolves a 28 x 14 mm core rather than the former
  assumed 28 x 28 mm square.

## `/usr/data/anvil-data/config/printer_n4s4.cfg`

### Current tool-change values

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

- `DEFINE_PRIME_TOWER_OBJECT` now registers the tower bounds with the native
  toolchanger. XY restoration remains enabled when Orca emits `T<n>` while
  already inside those bounds, preserving G-code that immediately continues
  an extruding tower move without another Z command.
- If Orca emits `T<n>` over the previously printed object, the toolchanger
  suppresses XY restoration, descent, and stationary pressure recovery at
  that captured point. The new tool remains raised and retracted for Orca's
  following travel to the prime tower.
- Z-hop: 2 mm at 20 mm/s.
- In-dock retract: 2 mm at 30 mm/s.
- The Z-hop and in-dock retract remain active when restoration is suppressed.
  The configured 0.4 mm slow recovery is performed only when the captured
  position is inside the prime tower; outside it, pressure is recovered by
  Orca's moving tower extrusion.

### Other pre-existing local changes

- `[probe] samples: 1` reduces probing to one sample per point.
- `[output_pin DC24V_CTL]` enables the shared 24 V hotend supply and switches
  it off during shutdown.
- `[extruder]` contains only the pin, gearing, and Pressure Advance settings
  for the shared physical extruder stepper.
- `_BUILD_PLATE_OFFSETS` stores independent first-layer corrections for
  Smooth Cool, Smooth High Temp, Textured Cool, Textured PEI, Engineering,
  and SuperTack plates. All values default to `0.000` until calibrated.
- `ADAPTIVE_MESH_TOGGLE` provides a Mainsail-visible session toggle. Enabled
  mode probes a new adaptive mesh; disabled mode skips probing and loads the
  saved `MESH_DATA` profile. It defaults to enabled after every restart and
  also accepts explicit `ENABLE=1` or `ENABLE=0` parameters.
- `ADAPTIVE_MESH_STATUS` reports the current adaptive-mesh setting without
  changing it, including whether the next print will probe or load
  `MESH_DATA`.
- `[save_variables]` stores local persistent settings in
  `/usr/data/anvil-data/config/n4s4_saved_variables.cfg`. Klipper creates the
  file automatically on the printer when a setting is saved.
- `TIMELAPSE_TOGGLE` provides a Mainsail-visible persistent switch for frame
  capture. Clicking the macro toggles the current state; `ENABLE=1` and
  `ENABLE=0` set it explicitly. Orca may continue to emit
  `TIMELAPSE_TAKE_FRAME` for every layer because the existing timelapse macro
  ignores those calls while disabled.
- Disabled timelapse frame requests now report
  `Timelapse: disabled, take frame ignored` only on the first ignored request,
  rather than once per layer. Toggling or restoring the setting resets that
  one-time notice latch.
- `_RESTORE_N4S4_PERSISTENT_SETTINGS` restores the saved timelapse state one
  second after Klipper starts. The safe first-run default is disabled. It
  also restores the selected chute-purge mode, whose first-run default is
  `FIRST` to preserve the previous behaviour.
- `START_PURGE_SET` provides three persistent pre-print purge modes:
  `OFF`, `FIRST`, and `ALL`. Clicking it in Mainsail cycles between the modes;
  `MODE=<mode>` selects one directly.
- `START_PURGE_STATUS` reports the current pre-print purge mode without
  changing it.
- `_FF_FILAMENT.clean_wipe_temp` and the local `_FF_NOZZLE_WIPE` override use
  an absolute 150 C front-wipe target. Unlike the stock 100 C temperature
  reduction, the resulting wipe temperature no longer changes with the purge
  temperature or filament material.
- The local `_FF_NOZZLE_CLEAN` override disables XY position restoration
  explicitly for the pickup and release steps of the sequential
  chute-cleaning loop.
  Each pass therefore ends at the toolchanger's safe X position instead of
  returning the empty carriage to the front wipe point or carrying the next
  tool diagonally across occupied docks. Normal slicer tool changes retain
  the configured `restore_axis: xy`, Z-hop, retract, and prime-tower return.
- The local public `PURGE` override applies the same safe pickup/release rule
  to manually requested chute purges. Consecutive console calls now withdraw
  each tool fully to X250, travel along that safe X corridor, and only move
  right again at the purge chute or front wipe point. Its paused-print path
  remains restricted to the already mounted tool.
- `_NS_BEFORE_PRINT` keeps the stock print lifecycle intact while
  applying that mode. `ALL` sends every used tool through the rear-right
  purge-and-wipe sequence; `FIRST` cleans only the initial tool; `OFF` skips
  that sequence entirely.
- `_NS_FILAMENT_PREFLIGHT` checks the `fd_exN` switch for every used tool,
  independently of the purge mode. The `fm_exN` motion sensors remain the
  runtime clog detectors and are not treated as static presence sensors.
- `ADAPTIVE_MESH`:
  - clears the print Z offset first;
  - safely parks a mounted tool;
  - homes Z without a mounted nozzle;
  - either creates an adaptive mesh with a safety margin or loads the saved
    `MESH_DATA` profile according to the session toggle;
  - validates the symbolic build-plate code supplied by Orca;
  - picks up the requested tool afterward and applies the temperature-,
    bed-, layer-, and build-plate-dependent print Z offset.
- `DEFINE_PRIME_TOWER_OBJECT` registers Orca's actual generated prime-tower
  rectangle as an exclude object, ensuring that adaptive mesh generation
  includes the correct area. It prefers the active coordinates parsed by
  `ff_print.py` over the possibly incorrect multi-plate values substituted in
  Orca's custom start G-code, uses the measured second dimension, expands the
  real world-coordinate bounds by brim plus safety margin, and registers the
  same corrected geometry with `ff_toolchange.py`.
- `PURGE_NEAR_OBJECT` calculates the bounds of all registered print objects,
  selects a safe purge line within the build plate, and prints it inside the
  adaptively meshed area.

## Installation

After changing the Python modules or configuration, copy the changed files to
the printer paths listed above. Then run `FIRMWARE_RESTART` or reboot the
printer.

The matching Orca machine-start and filament-profile snippets are documented
in `ORCA_Z_OFFSETS.md`; that guide is not copied to the printer.

## `/usr/data/anvil-data/config/timelapse.cfg`

- Renamed the console/status macro from `GET_TIMELAPSE_SETUP` to
  `TIMELAPSE_SETUP_STATUS`. Its output and behavior are unchanged.
- Disabled frame requests emit their ignored-frame message only once per
  disabled session instead of once per layer.

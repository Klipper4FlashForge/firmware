# Creator 5 N4S4 – Change Log

Last updated: 2026-10-01

This document describes the local changes compared with the original
FlashForge/Klipper4FlashForge implementation. All printer paths are relative
to `/usr/data`.

## `/usr/data/anvil/klipper/klippy/extras/ff_bed_mesh.py`

### Reduced Z travel

- Bed meshes retain Klipper's standard row-by-row point order.
- The first probe position uses a safe absolute travel height of 5 mm.
- Following positions lift only 2 mm above the latest probe trigger height.
- If the calculated travel height would fall below Z=1 mm, the adapter uses
  the recovery height Z=3 mm instead.
- The decision is recalculated after every measurement. The recovery height
  is not latched: probing returns automatically to the normal 2 mm relative
  lift as soon as that produces a sufficiently high transfer position.
- The optimization patches only the `bed_mesh` probe helper. Homing and other
  probe users retain their normal Klipper behavior.
- `FF_BED_MESH_STATUS` reports the standard path and configured dynamic-Z
  safety values.
- Generic Klipper `bed_mesh.py` and `probe.py` remain unmodified.

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
- The optional `post_m109_macro` hook runs after Klipper's native `M109`
  temperature wait. This supports post-heat tool recovery without trying to
  replace the firmware's built-in `M109` command from a G-code macro.

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

### Visible toolchanger status macro

- The native status command is registered internally as
  `FF_TOOLCHANGE_STATUS`.
- `printer_n4s4.cfg` exposes the public `TOOLCHANGE_STATUS` command as a real
  `[gcode_macro]`, so Mainsail displays it as a macro tile while existing
  console usage remains unchanged.

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
- Pickups that are known to be followed by extrusion on a purge line, and the
  first pickup of each tool in a registered prime-tower job, use the separate
  `purge_retract` distance. A short `purge_retract_dwell_ms` pause lets nozzle
  pressure settle before the tool starts leaving its dock.
- Later pickups of a tool in the same prime-tower job use
  `tower_repeat_retract` (default `0.0`, no additional retract). Orca has
  already retracted that tool with its own unload retract before parking it,
  and stacking the full `purge_retract` on top of it left a pressure deficit
  that could cause holes at the aligned tower seam after a tool change.
- `TOOLCHANGE_PREPARE_PICKUP` arms the stronger retract for one real pickup.
  `ADAPTIVE_MESH` uses it for the initial tool because `_PURGE_NEAR_OBJECT`
  follows. Prime-tower jobs select it automatically for a tool's first pickup
  only; other pickups retain the conservative normal retract.
- `TOOLCHANGE_BEGIN_JOB`, called from `_NS_BEFORE_PRINT` before any cleaning
  pickup, resets the per-job record of tools that have already been selected.
  `TOOLCHANGE_STATUS` lists that record as "tools selected this job".
- The return prime after a tool change is limited to the distance actually
  retracted in the dock. A small non-zero `tower_repeat_retract` can therefore
  never over-prime, and `0.0` adds neither a retract nor a return prime.
- The prepared initial pickup no longer restores XY to the last adaptive-mesh
  probe point and no longer performs its partial pressure recovery there. It
  stays raised and retracted until the following purge-line travel, preventing
  a droplet on the final probed area.
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
- `purge_retract`
- `purge_retract_dwell_ms`
- `tower_repeat_retract`
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

### Statistics hooks

- `_toolchange` reports each change to `ff_stats` when that extra is
  configured: it starts timing once the already-queued motion has finished,
  remembers the stage it is in (`prepare`, `release`, `grab`, `finish`,
  `restore`), and reports success or the failing stage when it ends.
- `_grab` and `_release` report every attempt that did not succeed, so the
  number of retries is known.
- Without `[ff_stats]` nothing is reported and nothing changes. A failure
  inside the statistics code is logged and ignored: it can never fail or
  delay a tool change.

## `/usr/data/anvil/klipper/klippy/extras/ff_print.py`

### Used-tool discovery

- The print-start metadata parser now exposes every tool used by the G-code,
  not just its initial tool.
- Current Orca files are read from their compact, one-based `; filament:`
  header. Older files fall back to a streaming scan of bare `Tn` commands.
- The complete list is passed to the configured pre-print callback as
  `TOOLS=` and is also exposed as `printer.ff_print.tools`.
- The parser also finds the second distinct selected tool and its first
  `M109` target. These are exposed as `printer.ff_print.next_tool` and
  `printer.ff_print.next_nozzle`, allowing startup to cover a first-change
  preheat window that begins before Orca can place an object-body `M104`.

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
- Orca writes `prime_tower_brim_width = -1` for its automatic brim. The real
  brim exists only in the emitted moves, so the parser reads the first
  `WIPE_TOWER_BRIM_START` … `WIPE_TOWER_BRIM_END` block, rotates its points
  into the tower's local frame, and exposes the largest expansion beyond the
  core outline as `printer.ff_print.prime_tower_brim`. The exact world-
  coordinate bounds of the brim are exposed as `prime_tower_outer_min_x`,
  `prime_tower_outer_max_x`, `prime_tower_outer_min_y` and
  `prime_tower_outer_max_y`. If the brim block lies beyond the bounded head
  buffer, a small streaming scan collects it. A missing or empty brim block
  resolves to `0`, so the negative sentinel no longer reaches Klipper, where
  it previously aborted print start. If the core outline itself cannot be
  parsed the sentinel stays unresolved in the metadata, and the clamp in
  `DEFINE_PRIME_TOWER_OBJECT` (see `printer_n4s4.cfg` below) keeps it from
  being registered.

### Job boundaries for statistics

- When a print is requested, `ff_print` tells `ff_stats` before the start
  macro runs, reports a refused or failed start as `aborted`, reports the end
  state as soon as `print_stats` leaves the printing state, and reports that
  the end macro has finished. This is what lets a job include the start-up
  and the end sequence, which Klipper's own `print_stats` does not cover.
- Like the tool-change hooks, these calls do nothing without `[ff_stats]`
  and cannot fail a print.

## `/usr/data/anvil/klipper/klippy/extras/ff_stats.py`

New file. User documentation: `docs/statistics.md`.

### Statistics

- Keeps lifetime and per-job statistics in
  `/usr/data/anvil-data/stats/ff_stats.json`, outside `/usr/data/anvil`, so a
  firmware update keeps them. The last 100 jobs are kept in detail.
- **Jobs** run from the print request to the end of the end macro and end as
  `completed`, `cancelled`, `error`, `aborted` (never started printing),
  `shutdown` or `interrupted` (found open on disk after a power loss, or
  Klipper stopped mid-job). A print that did not come through `ff_print` is
  followed from the moment `print_stats` reports it printing.
- **Print time** per job: Klipper's `print_duration` and the whole job time.
- **Phases** divide the job time exactly: `prepare`, `homing`, `heating`,
  `mesh`, `purge`, `toolchange`, `print`, `paused`, `end`. The innermost
  phase wins. Homing uses Klipper's homing events; `toolchange` comes from
  `ff_toolchange`; the rest are commands that `ff_stats` takes over at
  `klippy:connect` (`M109`, `M190`, `M191`, `TEMPERATURE_WAIT`,
  `BED_MESH_CALIBRATE`, the purge and wipe macros, `TOOLCHANGE_PARK`,
  `FF_AFTER_PRINT_END`), using the same unregister-and-chain pattern as
  `ff_print`. No macro is edited. The list is the `phase_commands` option.
- **Filament** is measured per tool from the net movement of each extruder's
  own `last_position`, between the job start and the end. This is independent
  of `print_stats`, which re-bases the G-code E position on every
  `ACTIVATE_EXTRUDER` and reported only 12% to 34% (median 25%) of the
  slicer's estimate on this printer's ten completed multi-colour jobs
  (single-tool jobs: median 1.02, eighteen jobs). Net means extruded minus
  retracted; the start-up clean is included and shown separately. Grams use
  the file header's `filament_density` and `filament_diameter`, else the
  extruder diameter and the `filament_density` option.
- **Tool changes**: swaps and first pickups, per target tool, with duration
  (average and slowest), failed changes by stage, and failed grab/release
  attempts. The duration excludes the short return travel to the print
  position, which is queued without waiting.
- With `correct_print_stats` (default on) `print_stats.filament_used` is
  replaced by the measurement while a tracked job prints, so Mainsail's
  dashboard and Moonraker's job history show the right figure for new jobs.
- Written atomically, when a job ends, at shutdown, and otherwise at most
  every 5 minutes (every minute during a job). `/usr/data` is mounted `sync`,
  hence no write per event. An unreadable file is renamed to
  `.corrupt-<time>` rather than overwritten; a file from an older layout is
  filled in with the counters it lacks.
- Console: `FF_STATS_SHOW WHAT=SUMMARY|JOB|JOBS [COUNT=]`, shown in Mainsail
  through the macros `FF_STATS`, `FF_STATS_JOB` and `FF_STATS_JOBS`;
  `FF_STATS_PHASE NAME= [END=1]` for custom macros; `FF_STATS_RESET CONFIRM=1`
  keeps the old file as `ff_stats.json.reset-<time>`. The numbers are also in
  `printer.ff_stats`.
- Everything Klipper can call (event handlers, G-code handlers, the status)
  is exception-safe: Klipper turns an exception in a G-code handler into a
  printer shutdown, so a bug here fails only its own command.
- Not included, possible follow-ups: per-nozzle hours at temperature and
  heat cycles, and maintenance counters (axis travel, motor, bed and fan
  hours, with reminders).
- Not yet exercised on a printer: tested with unit tests and with a replica
  test that runs it against Klipper's real `gcode.py` and `print_stats.py` on
  the printer's interpreter.

## `/usr/data/anvil-data/config/ff-print-macros.cfg`

### Deferred slicer mesh preparation

- `START_PRINT` starts `M140` before its initial `G28`, so the bed heats while
  XYZ homing and any configured start-purge operations run.
- `DEFER_MESH=1` waits for the bed but leaves the final hot-bed Z home, mesh
  probing/profile load, initial-tool pickup, and print-offset setup to the
  sliced file's `ADAPTIVE_MESH` call.
- The N4S4 print callback enables this mode, eliminating the previous sequence
  which grabbed the initial tool, parked it again, and homed Z a third time.
- The ordinary `START_PRINT` path remains unchanged for profiles which do not
  use the N4S4 `ADAPTIVE_MESH` start block.

## `/usr/data/anvil-data/config/printer_n4s4.cfg`

### Automatic include installer

- The `anvil-klipper-config` package installs
  `10-enable-printer-n4s4.sh` directly as
  `/usr/data/anvil-data/scripts/10-enable-printer-n4s4.sh`; no SSH session or
  manual edit of `printer.cfg` is required.
- `anvil-link-prog.sh` performs that atomic copy after a firmware payload is
  extracted as well as during package updates, because the persistent
  `anvil-data` directory itself is intentionally outside `anvil.tar.xz`.
- On boot it adds `[include printer_n4s4.cfg]` immediately before the
  `SAVE_CONFIG` area, or before the existing `# Save Mesh Data #` heading.
- The operation is idempotent, removes duplicate active N4S4 includes while
  relocating them, validates the generated file, and creates a timestamped
  backup only when a change is required.
- Once the exact include is present, subsequent boots terminate immediately
  without writing script output to the custom-script log.
- The script refuses to modify `printer.cfg` if either it or the packaged
  `printer_n4s4.cfg` is missing.

### Current tool-change values

```ini
[ff_toolchange]
grab_retreat_feed: 4800
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

- `DEFINE_PRIME_TOWER_OBJECT` now registers the tower bounds with the native
  toolchanger. XY restoration remains enabled when Orca emits `T<n>` while
  already inside those bounds, preserving G-code that immediately continues
  an extruding tower move without another Z command.
- If Orca emits `T<n>` over the previously printed object, the toolchanger
  suppresses XY restoration, descent, and stationary pressure recovery at
  that captured point. The new tool remains raised and retracted for Orca's
  following travel to the prime tower.
- Z-hop: 2 mm at 20 mm/s.
- After the initial 20 mm dock pullback, the grabbed tool retreats to the safe
  X position at 80 mm/s (`grab_retreat_feed: 4800`). This matches the tested
  release-retreat speed and replaces the previous 25 mm/s default.
- Ordinary in-dock retract: 0.4 mm at 30 mm/s.
- A pickup followed by the startup purge line, or a tool's first pickup in a
  registered prime-tower job, retracts 0.9 mm and waits 250 ms before leaving
  the dock. The existing 0.4 mm slow recovery leaves the final 0.5 mm pressure
  deficit for the following moving purge extrusion instead of producing a
  stationary blob.
- Repeat pickups of a tool in the same prime-tower job add no firmware retract
  (`tower_repeat_retract: 0.0`): Orca's own unload retract (2 mm in the tested
  profile) is already in place, so the former stacked 2.9 mm total is avoided.
- The Z-hop and in-dock retract remain active when restoration is suppressed.
  The configured 0.4 mm slow recovery is performed only when the captured
  position is inside the prime tower; outside it, pressure is recovered by
  Orca's moving tower extrusion.

### Front-right silicone-lip wipe after chute purging

- Chute purges now travel to the front-right service area while raised and
  remain at `X256` for the full rear-to-front move, outside the printable bed.
- On the short lip, the nozzle performs a 14-pass zigzag between `X262` and
  `X273`, starting at the outside edge `X273/Y0` and advancing in 0.5 mm
  increments to `Y7`, then raises again before any subsequent move.
- The wipe runs at 150 mm/s (`F9000`) and remains configurable through
  `lip_wipe_feed`.
- The lip is traversed at absolute G-code `Z-1.0` in the active tool frame.
  Klipper applies the selected tool's calibrated transform; the macro does
  not subtract the roughly 2.9 mm nozzle/station offset a second time.
- The following cooldown-pad park uses an independent absolute G-code height
  of `Z-0.9`. This reduces compression of the silicone pad compared with the
  previous raw-frame conversion, which produced approximately `Z-1.9`.
- Before moving from the lip to the cooldown pad, the nozzle now lifts only
  2 mm from `Z-1.0` to `Z1.0`; direct in-print lip wipes retain their higher
  safe-Z exit.
- Cooldown locations cycle through all 30 points of a 1 mm grid around the
  original `X266.5/Y13.8` position. The collision-safe offsets cover
  `X-4..+1` and `Y-4..0`, giving actual maxima of `X267.5/Y13.8` inside the
  measured `X268/Y14` limits next to parked T0. Successive cooldowns still
  cover both axes without duplicates before repeating.
- A pseudo-random starting point is derived from Klipper CPU time, current job
  duration, and the active tool after every restart. The seed and sequence
  counter live only in macro RAM; no per-cooldown flash write is performed.
- The movement is enabled by default with `_FF_FILAMENT` variable
  `lip_wipe_enabled: 1`; setting it to `0` keeps the safe raised route but
  skips lowering and zigzagging.
- It runs after every START_PURGE chute purge and after each tool's one-time
  fallback mini-purge in jobs without a prime tower. Manual `PURGE` with its
  default `WIPE=1` also uses it before cooling on the existing silicone pad.

### Statistics

- `[ff_stats]` loads the statistics extra with its defaults.
- `FF_STATS`, `FF_STATS_JOB` and `FF_STATS_JOBS` (`COUNT=`) are macros, so
  they are buttons in Mainsail and print to its console.

### Other pre-existing local changes

- `[probe] samples: 1` reduces probing to one sample per point.
- The shared 24 V hotend supply (`eheaterboard:PA3`) is deliberately not
  defined here. An earlier revision carried its own `[output_pin DC24V_CTL]`
  (always on, off at shutdown). That conflicts with `[heater_fan dc24v_ctl]`
  in `printer.base.cfg`, which now owns the pin (on while any hotend has a
  target or is above 50 °C, off on shutdown), and Klipper refuses to start
  with both. A `printer.cfg` that still carries FlashForge's
  `[output_pin DC24V_CTL]` is cleaned up by `anvil-link-prog.sh`.
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
- Sequential `START_PURGE` cleaning preheats the next requested tool to its
  individual target after the current tool reaches temperature. The next
  heater therefore runs during the current purge, lip wipe and cooldown,
  reducing the wait after the following pickup.
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
- Automatic pre-print cleaning uses its own `park_retract` of 0.4 mm instead
  of the stock 5 mm purge retract. This prevents every tool cleaned by
  `START_PURGE_SET MODE=ALL` from carrying an unrecovered 5 mm filament deficit
  into a no-prime-tower job. The public manual `PURGE` macro uses the same
  value by default and accepts `RETRACT=<0..10>` when a different maintenance
  retract is deliberately required.
- A real slicer tool change without a registered prime tower no longer returns
  to the previous object's final XY position. It stays raised at the safe X250
  corridor while Orca's following `M109` heats the new tool. On the first use
  of a tool in that job, it then performs a 5 mm pressure-building extrusion
  in the rear-right chute before Orca travels to the object. A per-job bitmask
  prevents that purge from repeating on later layers. The initial tool is
  marked as prepared by the startup purge line; `START_PURGE_SET MODE=ALL`
  marks all tools as prepared, disabling every in-print chute purge. Start-
  purge tool selections use `RESTORE_AXIS=` and do not arm the hook; jobs with
  a registered prime tower retain the existing tower recovery path.
- Before leaving that chute, the recovery macro retracts 0.4 mm and travels
  to the front-right `X256 Y0` position. It then restores the same 0.4 mm
  before handing control back to Orca, keeping any remaining ooze away from
  already printed objects without carrying an extrusion deficit forward.
- Reused tools perform no additional recovery-macro movement. After the normal
  raised tool change at X250, Orca travels directly to its next print position.
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
    bed-, layer-, and build-plate-dependent print Z offset;
  - starts heating the parsed second tool after probing, while Orca's normal
    configured preheat-time commands remain responsible for later changes.
- `DEFINE_PRIME_TOWER_OBJECT` registers Orca's actual generated prime-tower
  rectangle as an exclude object, ensuring that adaptive mesh generation
  includes the correct area. It prefers the active coordinates parsed by
  `ff_print.py` over the possibly incorrect multi-plate values substituted in
  Orca's custom start G-code, uses the measured second dimension, expands the
  real world-coordinate bounds by brim plus safety margin, and registers the
  same corrected geometry with `ff_toolchange.py`. A negative `BRIM` (Orca's
  automatic-brim sentinel) is clamped to `0`, and when `ff_print.py` has
  resolved the exact outer brim bounds those are used directly (plus the
  safety margin) instead of the core bounds plus a scalar brim, so adaptive
  mesh covers the real brim without over-sizing.
- `_PURGE_NEAR_OBJECT` calculates the bounds of all registered print objects,
  selects a safe purge line within the build plate, and prints it inside the
  adaptively meshed area. Its leading underscore keeps this Orca-only helper
  out of Mainsail's normal macro panel.
- The documented Orca machine start G-code now selects startup purge-line
  height, total extrusion, and stationary lead-in from the initial tool's
  configured nozzle diameter. Presets cover 0.25, 0.40, 0.60, and 0.80 mm
  nozzles; the tested 0.40 mm setup retains `Z=0.20`, `E=10`, and `LEAD=3`.
- The 0.25 mm preset limits the stationary extrusion rate to 6 mm³/s. The
  documentation also explains the `2.4053 mm²` cross-sectional-area conversion
  from Orca's volumetric-flow limit to Klipper's filament feed rate.
- Most purge material is deposited while moving, avoiding the former start
  blob and overly broad purge line. `Z`, `E`, and `LEAD` remain independently
  adjustable in Orca's machine start G-code.

## Installation

After changing the Python modules or configuration, copy the changed files to
the printer paths listed above. Then run `FIRMWARE_RESTART` or reboot the
printer.

The matching Orca machine-start and filament-profile snippets are documented
in `ORCA_MACHINE_AND_FILAMENT_SETTINGS.md`; that guide is not copied to the
printer.

## `/usr/data/anvil-data/config/timelapse.cfg`

- The modified macro file is now shipped from
  `pkgs/timelapse/payload/config/timelapse.cfg` by `anvil-timelapse` instead
  of being taken unchanged from the upstream archive.
- The package installs it as `/usr/data/anvil/config/timelapse.cfg`;
  `anvil-link-prog.sh` exposes it to Klipper through the existing
  `/usr/data/anvil-data/config/timelapse.cfg` symlink.
- The package revision uses the firmware release stamp, ensuring that an
  updated local macro file is recognized as an upgrade on existing printers.
- Renamed the console/status macro from `GET_TIMELAPSE_SETUP` to
  `TIMELAPSE_SETUP_STATUS`. Its output and behavior are unchanged.
- Disabled frame requests emit their ignored-frame message only once per
  disabled session instead of once per layer.

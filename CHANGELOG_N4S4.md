# Creator 5 N4S4 – Change Log

Last updated: 2026-10-14

This document describes what branch `n4s4/fixes-and-optimizations-02` adds to
the **original project's current `main`** (Klipper4FlashForge/firmware at
`df57d27`, 2026-10-03). What the original project has taken over from earlier
N4S4 work (the bed-mesh probing travel, the adaptive-mesh macros, the deferred
start-up, the prime-tower geometry of `ff_print`, the Z-hop / in-dock retract /
unretract options, a fix of the parking status by another contributor) is
**not** repeated here;
`UPSTREAM_STATUS_N4S4.md` lists it, and what is still N4S4's, item by item.
The older, longer description of everything against the original base
`96c0565` is the same file on branch `n4s4/fixes-and-optimizations-01`.

All printer paths are relative to `/usr/data`.

## `/usr/data/anvil/klipper/klippy/extras/ff_extruder.py`

### Shared physical extruder stepper

The original project removed this extra on 2026-10-03 (`df57d27`, because no
failure on a stock configuration had been shown). It is kept here because
nothing upstream changed the reason for it: the MCU toggles the direction pin per stepper
object (read in Klipper's code and in the stock eBoard image), so four objects
on one pin invert the direction of the active tool whenever one idle tool was
left extruded. `UPSTREAM_STATUS_N4S4.md`, section 5, has the evidence, and
`gcode/n4s4-extruder-direction-test.gcode` the test, which was run on a
Creator 5 on 2026-10-06: with the adapter T1 purged normally after T0 had been
left extruded, on the stock design (without it) T1's purge ran the wrong way.

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

- With `[ff_extruder]` in the configuration, the shared physical stepper is
  connected to the active logical extruder with
  `SYNC_EXTRUDER_MOTION EXTRUDER=extruder MOTION_QUEUE=<extruderN>` after
  every tool pickup, **before** the in-dock retract, and when the already
  mounted tool is selected again.
- The synchronization is reported in the Klipper console.
- Without `[ff_extruder]` (every `[extruderN]` owns a stepper) nothing is
  synced.

### Visible toolchanger status macro

- The native status command is always registered as `FF_TOOLCHANGE_STATUS`.
- It is also registered under its original name `TOOLCHANGE_STATUS`, unless
  the configuration defines a `[gcode_macro TOOLCHANGE_STATUS]`: Klipper
  refuses a macro named like an existing command (a macro is registered under
  its upper-case name, so the check ignores the case the section is written
  in). `printer_n4s4.cfg` defines that macro and has it call
  `FF_TOOLCHANGE_STATUS`, so Mainsail shows a button while console usage
  stays unchanged, and a printer without the N4S4 configuration still has
  `TOOLCHANGE_STATUS`, as the original's documents say.
- The report also lists the Z parts, the registered prime tower and the tools
  selected this job.

### Prime-tower-aware restored position

The original's `restore_axis` brings the new tool back to where the old
nozzle was. For a change that restores X or Y, N4S4 decides whether to do
that:

- `TOOLCHANGE_SET_PRIME_TOWER` registers the current job's actual rotated
  tower rectangle, including its brim and a safety margin. The registration
  is cleared before every new print and populated by
  `DEFINE_PRIME_TOWER_OBJECT` in the sliced file.
- If the captured position is inside that rectangle, the new nozzle returns
  there as the original's change does. This supports Orca files whose next
  line immediately continues an extruding tower move.
- If the captured position is outside the tower, XY restoration is suppressed.
  The new nozzle therefore cannot return to the previous model, lower there,
  or leave a pressure-recovery dot on it. Orca performs the subsequent tower
  travel while the nozzle remains raised and retracted (the original's hop and
  in-dock retract still happen).
- Without a registered tower the original behaviour is kept, unless
  `no_tower_prime_macro` is set (next section).
- Re-selecting the mounted tool and `TOOLCHANGE_PARK` add no retract or hop.

### Pickup retract: purge, first use, repeat

The original has `restore_retract` for the in-dock retract. N4S4 chooses the
distance per pickup:

- Pickups that are known to be followed by extrusion on a purge line, and the
  first pickup of each tool in a registered prime-tower job, use the separate
  `purge_retract` distance (0.9 mm). A short `purge_retract_dwell_ms` pause
  (250 ms) lets nozzle pressure settle before the tool starts leaving its dock.
- Later pickups of a tool in the same prime-tower job use
  `tower_repeat_retract` (default `0.0`, no additional retract). Orca has
  already retracted that tool with its own unload retract before parking it,
  and stacking the full `purge_retract` on top of it left a pressure deficit
  that could cause holes at the aligned tower seam after a tool change.
- `TOOLCHANGE_PREPARE_PICKUP` arms the stronger retract for one real pickup.
  `ADAPTIVE_MESH` uses it for the initial tool because `_PURGE_NEAR_OBJECT`
  follows. Other pickups retain the ordinary `restore_retract`.
- `TOOLCHANGE_BEGIN_JOB`, called from `_NS_BEFORE_PRINT` before any cleaning
  pickup, resets the per-job record of tools that have already been selected.
- The prepared initial pickup no longer restores XY to the last adaptive-mesh
  probe point and no longer performs its partial pressure recovery there. It
  stays raised and retracted until the following purge-line travel, preventing
  a droplet on the final probed area.
- The return prime is limited to the distance actually retracted in the dock
  (the original does the same), so a small non-zero `tower_repeat_retract` can
  never over-prime, and `0.0` adds neither a retract nor a return prime.

### A change without a prime tower

- With `no_tower_prime_macro` set (`_NS_MARK_TOOL_PRIME` in
  `printer_n4s4.cfg`), a real slicer tool change without a registered prime
  tower no longer returns to the previous object's final XY position. It stays
  raised at the safe X250 corridor while Orca's following `M109` heats the new
  tool; the macro marks the tool, and on its first use in the job
  `_NS_TOOLCHANGE_PRIME` (run by `ff_extruder`'s `post_m109_macro` hook after
  the native `M109`) performs one 5 mm pressure-building extrusion in the
  rear-right chute. A per-job bitmask stops that from repeating on later
  layers.
- Without the option a change without a tower is the original's.

### A change that restores nothing

- The original raises the old tool and retracts the new one in its dock only
  for a change that restores X or Y. With `protect_every_change: 1` every real
  change is protected: one that restores nothing (the pickups and releases of
  `_FF_NOZZLE_CLEAN` and `PURGE`, which pass an empty `RESTORE_AXIS`, or a
  bare `T<n>` while `restore_axis` is empty) is raised and retracted too, and
  the tool then stays raised and retracted for whatever travel follows. That
  is what branch 01 did for those pickups.
- Without the option (the default) the original's rule applies.

### The return past the docks

- After a change the carriage returns to where it was (`restore_axis`) in one
  straight move. The docks stand right of `x_safe`, one tool after the other,
  and the purge chute and the wipe pad lie beyond that column: from the safe
  column to the chute the straight move cuts across the docked tools with the
  new tool on the carriage. On 2026-10-06 `LOAD_FILAMENT TOOL=1` after
  `LOAD_FILAMENT TOOL=0` drove T1 into the docked T2 that way: the original's
  load macros pick up and park without `RESTORE_AXIS=`, and `restore_axis: xy`
  is set here.
- With `restore_via_corridor: 1`, a return to a point at or beyond
  `x_safe - 2 mm` goes along the safe column first (`G1 Y`), then out
  (`G1 X`), which is how every dock move travels. A return onto the bed is
  still one move, and a return of one axis only is not split. It applies to a
  pickup, a swap and `TOOLCHANGE_PARK`, whoever issued them (a macro,
  HelixScreen, the console).
- Without the option (the default) the original's single move applies.

### The wait sends the last steps first

- On 2026-10-14 a print stopped at layer 37 with `MCU 'mcu' shutdown: Timer
  too close` in the last millimetres of the approach to T3's dock. The log
  showed one stall of Klipper's main thread of about a second (the clock-sync
  ping every MCU gets once a second was 2.1 s apart, once) and a move whose
  braking steps arrived 1.03 s after they were due. Klipper does not send the
  steps that start within about 50 ms of a move's end until a later flush,
  and while the queue drains that flush is a timer 0.2 s ahead of them; a
  stall of the host at that moment leaves the stepper without its last steps.
- With `flush_steps_before_wait: 1`, `_wait_moves` calls the toolhead's
  `flush_step_generation()` before it waits, so all of the motion is on the
  MCU before the host starts waiting. The wait is as long as it was; nothing
  is added to a change. A toolhead without that call is waited on as before.
- Without the option (the default) the wait is Klipper's own.
- A time-costing alternative (a dwell before the dock moves for a larger
  buffer) was considered and not built: the flush timer's 0.2 s is a constant
  of Klipper's background flush and does not grow with the buffer, so it would
  not have prevented this failure.
- `docs/host-stalls.md`. Not yet run through a print on a printer.

### New `[ff_toolchange]` options

- `purge_retract`
- `purge_retract_dwell_ms`
- `tower_repeat_retract`
- `no_tower_prime_macro`
- `protect_every_change`
- `restore_via_corridor`
- `flush_steps_before_wait`

The original's `restore_z_hop`, `restore_z_feed`, `restore_retract`,
`restore_retract_feed`, `restore_unretract` and `restore_unretract_feed` are
what `printer_n4s4.cfg` sets (see below). If the new options are not
configured, the original behavior is preserved; the defaults in the code are
zero, or the same as `restore_retract`.

New commands: `TOOLCHANGE_SET_PRIME_TOWER`, `TOOLCHANGE_PREPARE_PICKUP`,
`TOOLCHANGE_BEGIN_JOB`, `TOOLCHANGE_SET_MATERIAL_OFFSET`,
`FF_TOOLCHANGE_STATUS`. `TOOLCHANGE_SET_PRINT_OFFSET` takes `PLATE=`.

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
- The scan of such a file gives up after 2 s: it runs on the thread that also
  keeps the heaters alive.
- The complete list is exposed as `printer.ff_print.tools`. It is **not**
  passed to the pre-print callback as `TOOLS=`: the original's start macro
  would then clean every one of those tools. `_NS_BEFORE_PRINT` reads the
  list from the status; an explicit `TOOLS=` still wins.
- The search for the file's second tool and its first `M109` target
  (`printer.ff_print.next_tool`, `next_nozzle`) lets `ADAPTIVE_MESH` heat that
  tool right after the mesh. It looks only in the first 256 KiB of the file.
  The original dropped the same feature (`7e8ea9d`); it was dropped here on
  2026-10-06 and put back on 2026-10-07.

### Job boundaries for statistics

- When a print is requested, `ff_print` tells `ff_stats` before the start
  macro runs, reports a refused or failed start as `aborted`, reports the end
  state as soon as `print_stats` leaves the printing state, and reports that
  the end macro has finished. This is what lets a job include the start-up
  and the end sequence, which Klipper's own `print_stats` does not cover.
- Like the tool-change hooks, these calls do nothing without `[ff_stats]`
  and cannot fail a print.

### The file is read in a thread

- The lag watch's first report (2026-10-14) was `ff_print.py:168`: at the
  start of a job the search for the second tool read 256 KiB line by line in
  Klipper's thread, 0.7 s on this CPU, with 0.26 s of motion still queued.
- `_cmd_start` now runs `_parse_metadata` (and the extents scan below) in a
  thread named `ff_print-scan` and waits with `reactor.pause` in steps of
  50 ms, so clock sync, the heaters and queued motion go on. An error in the
  thread reaches the start as before; without a reactor (the unit tests) it
  just waits.

### A file that does not fit the machine is refused

- With `check_bounds` (default on, `[ff_print]`) the whole file is read once
  for the positions it moves to: X and Y at both ends, Z at the top, in
  machine coordinates, i.e. with the offset of the tool mounted at that point
  (`ff_toolchange.offset_x/y/z`; the initial tool before the first `Tn`). The
  limits are the toolhead's `axis_minimum` / `axis_maximum`. Comments, `G28`,
  `G92`, relative stretches (`G91` to `G90`) are not positions; arcs count by
  their end points.
- A file beyond the range is refused with `gcmd.error` before the statistics,
  the start macro and the base command, so nothing heats or moves and no job
  is recorded; the message names the axis, the value, the G-code value and the
  tool offset, and the limit. On 2026-10-14 the file of the aborted job
  reached Y 260.018 for T1, whose nozzle sits 0.414 mm further out; the
  printer reaches Y 260.0.
- Checked against the 57 G-code files on the printer on 2026-10-14: one
  refusal, that file; none of the others. The scan takes 0.1 to 2.9 s per
  file (about a second per megabyte), in pieces of 16 KiB with a pause between
  them; a file that takes longer than 40 s is started unchecked.
- `printer.ff_print.extents` holds what was found (`x_min` ... `z_max`, machine
  coordinates). `check_bounds: False` turns the check off.

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
- The periodic and end-of-job saves are written by a second thread: Klipper's
  thread only renders the JSON (a few milliseconds) and hands it over, so a
  slow flash cannot hold up the step generation. A newer text replaces one
  that is still waiting. No periodic save starts while a tool change runs,
  unless the last save is three intervals old. Shutdown, disconnect and
  `FF_STATS_RESET` still write in the calling thread. Before this change every
  save was an `fsync` in Klipper's thread, one of the suspects for the stall
  of 2026-10-14.

## `/usr/data/anvil/klipper/klippy/extras/ff_lagwatch.py`

New file. User documentation: `docs/host-stalls.md`.

- A reactor timer beats every `period` (0.1 s) and a watchdog thread watches
  it. When the beat is `threshold` (0.25 s) late, the watchdog logs the
  Python stack of Klipper's main thread (taken while it is blocked: the call
  that blocks shows up, an `fsync` or a sleep) up to four times per stall.
  When the beat returns it logs how long the thread was away, whether a tool
  change was running and how much motion was queued.
- Garbage collections slower than `gc_threshold` (0.05 s) are logged by the
  next beat (a collection holds the interpreter, so it cannot be sampled).
- Lines start with `ff_lagwatch:` in `printer.log`; `max_reports` (50) limits
  them per Klipper run, the counters keep counting. `FF_LAGWATCH` lists the
  recent stalls; the status object has `stalls`, `worst_s` and `threshold_s`.
- It never moves the machine, and an exception inside its timer is logged and
  dropped, because a reactor timer that raises shuts the printer down.
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
- Tested with unit tests, with a replica test that runs it against Klipper's
  real `gcode.py` and `print_stats.py` on the printer's interpreter, and on a
  Creator 5: on two completed four-colour jobs the measured filament, minus
  the start-up clean, was within 0.6 % of the slicer's per-tool lengths for
  every tool and 0.3 % above its total; the corrected `print_stats` figure
  read 100.3 % and 100.4 % of the slicer's total. A cancelled job and one
  refused tool change (printer not homed) were filed correctly.

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
  relocating them, validates the generated file, and creates a backup
  (`printer.cfg.before-n4s4`, with a timestamp added when that already exists)
  only when a change is required.
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
no_tower_prime_macro: _NS_MARK_TOOL_PRIME
protect_every_change: 1
restore_via_corridor: 1
flush_steps_before_wait: 1
```

`grab_retreat_feed` and the `restore_*` options exist in the original (it
ships `restore_axis` unset, and everything else off); these are the values
`printer_n4s4.cfg` gives them. `purge_retract`, `purge_retract_dwell_ms`,
`tower_repeat_retract`, `no_tower_prime_macro`, `protect_every_change`,
`restore_via_corridor` and `flush_steps_before_wait` are N4S4's own options.

- `DEFINE_PRIME_TOWER_OBJECT` also registers the tower bounds with the native
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
- A pickup followed by the startup purge line retracts 0.9 mm, waits 250 ms
  before leaving the dock, and stays retracted: the purge line's own lead-in
  and moving extrusion rebuild the pressure. A tool's first pickup in a
  registered prime-tower job retracts the same 0.9 mm; when it returns to the
  tower, the 0.4 mm slow recovery leaves the final 0.5 mm pressure deficit for
  the following moving tower extrusion instead of producing a stationary blob.
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

### Lag watch

- `[ff_lagwatch]` loads the lag watch with its defaults.

`[ff_print]` is not set in this file: `check_bounds` is on by default in the
module.

### Other pre-existing local changes

- `[probe] samples: 1` reduces probing to one sample per point, and
  `[bed_mesh]` is set to speed 300, an 8 x 8 grid and a bicubic algorithm.
  (The probing-travel optimisation itself, `[ff_bed_mesh]`, is the original's.)
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
  and SuperTack plates. The values are `0.000` until calibrated, except
  Textured PEI, which is set to `0.03` (for PLA).
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
  tool diagonally across occupied docks. `protect_every_change: 1` still
  gives those pickups the Z-hop and the in-dock retract. Normal slicer tool
  changes retain the configured `restore_axis: xy`, Z-hop, retract, and
  prime-tower return.
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
- `ADAPTIVE_MESH` (the original's macro, redefined here, and only to add
  three things; `qa/static/test_n4s4_overrides.py` compares it with the
  original's):
  - `PLATE=<code>`: validates the symbolic build-plate code supplied by Orca
    and passes the plate's correction from `_BUILD_PLATE_OFFSETS` on as
    `TOOLCHANGE_SET_PRINT_OFFSET PLATE=`;
  - `TOOLCHANGE_PREPARE_PICKUP` before the first pickup;
  - once the mesh is done, `M104 S<next_nozzle> T<next_tool>` for the file's
    second tool, from `printer.ff_print` (nothing if the file has none, or if
    it is the tool already mounted).
- `DEFINE_PRIME_TOWER_OBJECT` (the original's macro, redefined here with one
  added line): after the original's work it registers the same corrected
  geometry with `ff_toolchange.py` (`TOOLCHANGE_SET_PRIME_TOWER`). The
  original registers Orca's actual generated tower rectangle as an exclude
  object so that adaptive mesh generation includes the correct area, from the
  values `ff_print.py` parsed instead of the possibly incorrect multi-plate
  values in Orca's custom start G-code.
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
the printer paths listed above. Keep a copy of each file you replace.

A configuration change takes effect after `RESTART` or `FIRMWARE_RESTART`.
A change to a Python module does not: Klipper restarts inside the same
process and keeps the modules it already imported, so `RESTART` loads a
brand-new module (such as `ff_stats.py`) but still runs the old version of
one that was loaded before (`ff_print.py`, `ff_toolchange.py`, ...). After
changing an existing module, reboot the printer.

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

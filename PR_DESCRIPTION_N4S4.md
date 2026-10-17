# Creator 5 N4S4: keep a host stall from shutting the MCU down, refuse a file beyond the machine, statistics, prime-tower-aware tool changes, plate and material Z, purge work, and the shared extruder stepper

## Summary

This builds on `master` at `df57d27`. It is the part of the work in #29 that
`master` does not have, rebuilt on top of what happened to it since: two
commits that took pieces of #29 (`bb84772`, `308d97e`), two that removed some
of those again (`7e8ea9d`, `df57d27`), and two fixes by another contributor
(`01892ff`, `3d9f193`). The original's bed-mesh probing travel, adaptive-mesh
macros, deferred start-up, prime-tower geometry and Z-hop / in-dock retract
options are used as they are and are not touched here. `UPSTREAM_STATUS_N4S4.md`
lists, item by item, what is in `master`, what is added to it, and what was
removed or declined there and is kept.

**Three parts do not depend on how this printer is set up and can be taken on
their own** (they came out of a print that was lost on 2026-10-14, described
below under *The incident*):

1. `flush_steps_before_wait` in `[ff_toolchange]`: every wait of a tool change
   first sends all steps of the motion to the MCU. Opt-in; costs no time.
2. `ff_lagwatch`: a small extra that records, in `printer.log`, when Klipper's
   main thread is away for 0.25 s or more, and where it was blocked.
3. `check_bounds` in `[ff_print]` (**on by default**): a file that moves beyond
   the machine's axis range is refused before anything heats or moves, and the
   file is read in a thread instead of in Klipper's.

The other additions are **opt-in**: without the N4S4 configuration a printer
behaves as `master` does, with two exceptions that are stated under
*Compatibility*: `check_bounds` (point 3) and that `ff_print` reads the tools a
file uses. Within `ff_toolchange` every addition defaults to the original's
behaviour, and every test function of the original passes **unmodified**. Of the six macros the
configuration redefines, two (`ADAPTIVE_MESH`, `DEFINE_PRIME_TOWER_OBJECT`) are
the original's plus exactly the documented additions, and a test fails when
the original changes either of them; the other four replace the original's
text, and a test pins what that text looks like.

## Problems addressed

- A stall of Klipper's main thread of about a second, at the end of a dock
  move, shut the MCU down ("Timer too close") and ended a print at layer 37 of
  40, although the host was not overloaded. Nothing in the log said what
  stalled it.
- A file whose prime tower was 0.018 mm beyond the Y axis range ran until its
  first out-of-range move, two minutes in, with the nozzles hot and the mesh
  probed ("Move out of range").
- The scan of the G-code file at the start of every job held Klipper's main
  thread for 0.7 s.
- Klipper's `print_stats` reports only about a quarter of the filament a
  multi-colour job uses, and nothing records tool changes, their duration and
  failures, or where the time of a job goes.
- After a tool change the new tool could return to the position at which the
  previous object was last printed, lower the nozzle over an existing part and
  leave a blob before travelling to the prime tower. (The original's `restore_*`
  options protect the trip, but not this.)
- Every pickup in a prime-tower job received an additional firmware retract
  even when Orca had already parked that tool with a 2 mm unload retract. With
  a small Prime Volume this stacked pressure deficit left holes at the aligned
  wall seam after a tool change.
- A pickup that is followed by a purge line needs a stronger retract than one
  that is not.
- A job without a prime tower had no safe place for a hot tool to wait and to
  build pressure.
- Build plates and filament materials could not have independent additive
  first-layer Z corrections.
- Residual filament could remain attached to a nozzle after a rear-chute purge
  and be dragged from the purge area toward the print.
- Pre-print purge behaviour, timelapse capture and the statistics were not
  conveniently available from Mainsail.
- The Creator 5 has four hotends and **one** filament stepper, but Klipper
  creates four stepper objects for it, and the MCU toggles the direction pin
  per object. The pin level then depends on the history of all four, and
  retract and extrude can swap when an idle tool was left extruded. The
  original removed the single-stepper adapter for lack of a demonstrated
  failure; the cause is in the MCU code (read in Klipper's and in the stock
  eBoard image) and unchanged, and the failure has now been shown on a
  Creator 5 (see below).

## Main changes

### Nozzle diameter configuration from Mainsail

Changing a nozzle previously required manually editing Klipper's config in
addition to updating the slicer. The optional `ff_nozzle_size` extra and three
Mainsail macros now expose active sizes, stage replacements per tool, and
save them together using native `SAVE_CONFIG`. Its backup and restart path
recalculates the extruders' size-dependent limits. For example,
`NOZZLE_SIZE_SET TOOL=3 DIAMETER=0.25` followed by `NOZZLE_SIZE_APPLY` persists
T3's size; staging alone changes neither the live extruder nor autosave data.

Changes are refused during prints, pauses, motion, or (for apply) unrelated
pending calibration. A native save failure restores the autosave buffer.
This adds no shell dependency and leaves generic `extruder.py` unchanged.
Orca's nozzle setting remains separate. Installation and usage are in
`docs/nozzle-sizes.md`; regression tests cover staging, validation, save
rollback, restart state, macro forwarding, and busy-printer rejection.

### The incident, and what was done about it (2026-10-14)

A three-colour job stopped at layer 37 with `MCU 'mcu' shutdown: Timer too
close` while T3 was picked up, in the last millimetres of the approach to its
dock. The host was healthy on average (load about 1, 365 MB free, no
retransmits). What the log showed:

- Klipper pings every MCU once a second. In 98 s of history there is exactly
  one gap, 2.10 s instead of 0.985 s: Klipper's main thread had stopped.
- The last steps of the move were due 1.03 s before they were sent, and the
  MCU's own failure report carries the same 1.03 s.
- Why that moment: Klipper does not send the steps that start within about
  50 ms of a move's end until a later flush, and while the queue drains that
  flush is a timer that fires 0.2 s before the steps are due
  (`toolhead.py`: `STEPCOMPRESS_FLUSH_TIME`, `BGFLUSH_LOW_TIME`). A stall at
  that moment leaves the stepper without the braking of its last move. Every
  wait in a tool change (the approach to a dock, the retreat) is such a moment.

Three changes, each with tests; `docs/host-stalls.md` has the full account:

- **`flush_steps_before_wait`** (`[ff_toolchange]`, opt-in, set in
  `printer_n4s4.cfg`): `_wait_moves` calls `toolhead.flush_step_generation()`
  before it waits, so the MCU holds all of the motion before the host starts
  waiting. The wait is as long as it was. A toolhead without that call is
  waited on as before.
- **`ff_stats` writes from a thread.** The statistics file was written in
  Klipper's thread once a minute during a job, with `fsync`, on a partition
  mounted `sync`: the one periodic file write there, and a suspect (not proven).
  The text is now rendered in Klipper's thread (a few milliseconds) and written
  by a second thread; no periodic save starts while a tool change runs.
- **`ff_lagwatch`** (new extra, `[ff_lagwatch]`): a reactor timer beats every
  0.1 s and a second thread watches it. When the beat is 0.25 s late, the
  watchdog logs the Python stack of the main thread while it is blocked (an
  `fsync` or a sleep shows up as the call that blocks); when the beat returns it
  logs how long the thread was away and whether motion was queued. Slow
  garbage collections are logged too. `FF_LAGWATCH` lists the recent stalls.

The lag watch found a second problem on its first job:

```
ff_lagwatch: the main thread was away for 0.70 s (stall 1) [the MCUs had 0.26 s of motion queued]; it was in: ff_print.py:168 _parse_metadata <- ff_print.py:483 _cmd_start <- ...
```

- **The file is read in a thread.** `_cmd_start` read 256 KiB of the file line
  by line in Klipper's thread, 0.7 s on this CPU. It now runs the reading in a
  thread and waits with `reactor.pause`, so clock sync, heaters and queued
  motion go on.
- **`check_bounds`** (`[ff_print]`, on by default). The same thread reads the
  whole file once for the positions it moves to, in machine coordinates (the
  offset of the tool mounted at each point added), and the print is refused
  before the statistics, the start macro and the base command if it goes beyond
  the toolhead's axis range: X and Y at both ends, Z at the top. The message
  names the axis, the value, the tool's offset and the limit. `G91` stretches,
  comments, `G28` and `G92` are not positions; arcs count by their end points.
  The scan works in pieces of 16 KiB with a pause between them and starts a
  file unchecked if it takes longer than 40 s. `check_bounds: False` turns it off.

### Statistics (`ff_stats`)

- Lifetime and per-job statistics: tool changes (swaps and first pickups,
  duration, failed changes by stage, failed grab/release attempts), print
  time, filament measured per tool, and the time of each job divided into
  phases (prepare, homing, heating, mesh, purge, toolchange, print, paused,
  end).
- Filament is measured from each extruder's own running position.
  `correct_print_stats` (default on) replaces `print_stats.filament_used` with
  the measurement while a tracked job prints, which corrects Mainsail's
  dashboard and Moonraker's job history for new jobs.
- Phases come from Klipper's homing events, the tool-change code and a
  configurable list of commands (`phase_commands`) that `ff_stats` wraps. No
  macro is edited.
- Mainsail macros `FF_STATS`, `FF_STATS_JOB`, `FF_STATS_JOBS`; the numbers are
  also `printer.ff_stats`. `FF_STATS_RESET CONFIRM=1` starts over and keeps the
  old file.
- One JSON file under `/usr/data/anvil-data`, written atomically and rarely
  (the data partition is mounted `sync`), by a second thread and not during a
  tool change; a job left open by a power loss is recovered as `interrupted`.
- The hooks in `ff_print` and `ff_toolchange` are optional and exception-safe:
  a fault in the statistics cannot fail a print or a tool change.
- Documented in `docs/statistics.md`. On a Creator 5, two four-colour jobs:
  the measured filament per tool, minus the start-up clean, is within 0.6 % of
  the slicer's per-tool lengths and 0.3 % above its total.

### Tool change: what to do over the model, and how hard to retract

- `TOOLCHANGE_SET_PRIME_TOWER` registers the active tower (called from
  `DEFINE_PRIME_TOWER_OBJECT`). A change issued **inside** it returns there as
  `restore_axis` does; one issued over the model skips the X/Y return and the
  descent, and the tool stays raised and retracted for the slicer's travel.
- Pickups choose their in-dock retract: `purge_retract` plus
  `purge_retract_dwell_ms` for a pickup armed with `TOOLCHANGE_PREPARE_PICKUP`
  or a tool's first pickup in a registered tower job; `tower_repeat_retract`
  (0 by default) for a repeat pickup, so it does not stack on Orca's unload
  retract; `restore_retract` otherwise.
- `TOOLCHANGE_BEGIN_JOB` resets the per-job record of the tools used.
- `no_tower_prime_macro` turns on the no-tower workflow: no return to the
  model, and one chute prime on a tool's first use (`_NS_TOOLCHANGE_PRIME`).
- `protect_every_change` extends the original's rule that only a change
  restoring X or Y is raised and retracted: the pickups of the cleaning macros
  restore nothing and need the same protection. Off unless set.
- `restore_via_corridor` makes the return after a change go along the safe
  column first when the target lies beyond `x_safe` (the purge chute, the wipe
  pad). One straight move from the dock to the chute cuts across the docked
  tools with the new tool on the carriage: `LOAD_FILAMENT` on T1 drove into T2
  that way, because the original's load macros pick up and park without
  `RESTORE_AXIS=` and `restore_axis` is set. Off unless set.
- `FF_TOOLCHANGE_STATUS`, and the plain `TOOLCHANGE_STATUS` unless a macro of
  that name is configured (a Mainsail button).
- `purge_retract`, `purge_retract_dwell_ms`, `tower_repeat_retract`,
  `no_tower_prime_macro`, `protect_every_change` and `restore_via_corridor`
  are the only new `[ff_toolchange]` options.

### Build-plate and filament Z

- The print-scoped Z correction is three independent components: the app's
  temperature / bed / layer term, a build-plate term and the active filament's.
  `TOOLCHANGE_SET_PRINT_OFFSET PLATE=` and `TOOLCHANGE_SET_MATERIAL_OFFSET`
  replace their component absolutely, so repeated calls cannot accumulate, and
  neither touches babystepping or tool calibration. `CLEAR=1` clears all three.
- `_BUILD_PLATE_OFFSETS` and the Orca mapping are in `printer_n4s4.cfg` and
  `ORCA_MACHINE_AND_FILAMENT_SETTINGS.md`. The values are `0.000` until
  calibrated (the shipped configuration sets Textured PEI to `0.03`).

### The used tools

- `ff_print` reads the tools a file uses from Orca's `; filament:` header (a
  scan of `Tn` that gives up after 2 s otherwise), in a thread:
  `printer.ff_print.tools`.
  It is not passed on to the original's start macro, which would clean every
  one of them; `_NS_BEFORE_PRINT` reads it. It feeds the checks of every print
  (`_FF_PREFLIGHT`, `_NS_FILAMENT_PREFLIGHT`) and, in purge mode `ALL`, the
  clean of every colour.
- The preheat of the file's second tool after the mesh (`next_tool`,
  `next_nozzle` from `ff_print`, used by `ADAPTIVE_MESH`) is part of this
  change, although the original dropped it on purpose (`7e8ea9d`, no further
  reason given). It is for files whose first colour is too short for Orca's own
  preheat; the price is a hot idle nozzle until the first change. It stands
  alone and can be left out without touching the rest.

### The shared extruder stepper (`ff_extruder`)

- `[extruder]` is the only owner of the physical stepper; `extruder1` to
  `extruder3` stay logical hotends with their own heaters and motion queues,
  and `SYNC_EXTRUDER_MOTION` connects the one stepper to the picked tool
  before the in-dock retract. One object means one direction history.
- `SET_PRESSURE_ADVANCE` works for an active logical extruder, and
  `post_m109_macro` runs a macro after the native `M109`.
- `gcode/n4s4-extruder-direction-test.gcode` shows the difference on the
  printer: it breaks the "retract before the change" rule on purpose. Run on a
  Creator 5 (2026-10-06): T0 is left extruded, then T1 purges. With
  `ff_extruder` T1 purged normally; on the stock design (without it) T1's
  purge ran the wrong way. A retract on T1 before the purge does not help (its
  pickup already retracts 0.4 mm in the dock); the old tool's last move has to
  be a retract, or there has to be one stepper object.

### Pre-print purge, cleaning and ooze

- `START_PURGE_SET` / `START_PURGE_STATUS`: `OFF`, `FIRST`, `ALL`, persistent.
- The wipe across the front silicone lip, the cooldown pad spread over a
  30-point grid, the next tool preheated while the current one is cleaned, a
  safe corridor for pickup and release, a 150 C front wipe, no unrecovered
  5 mm retract, filament preflight for every used tool.
- `_PURGE_NEAR_OBJECT`: a purge line next to the objects, by nozzle diameter.

### Timelapse, installation, HelixScreen

- `TIMELAPSE_TOGGLE` (persistent), the modified `timelapse.cfg` shipped by the
  `anvil-timelapse` package.
- `10-enable-printer-n4s4.sh` adds `[include printer_n4s4.cfg]` to
  `printer.cfg` safely and idempotently; `anvil-link-prog.sh` installs it.
- Two printer pictures for HelixScreen.

## Not included

- Nozzle hours at temperature and heat cycles, and maintenance counters (axis
  travel, motor, bed and fan hours): possible follow-ups of the statistics.

## Files changed

See the table in `UPSTREAM_STATUS_N4S4.md` (what and why) and
`CHANGELOG_N4S4.md` (per file). In short:

- `pkgs/klipper/payload/klipper/klippy/extras/`: `ff_stats.py`,
  `ff_extruder.py`, `ff_lagwatch.py`, and `ff_nozzle_size.py` (new), `ff_toolchange.py` and
  `ff_print.py` (extended).
- `pkgs/klipper-config/payload/config/printer_n4s4.cfg` and
  `pkgs/klipper-config/payload/scripts/10-enable-printer-n4s4.sh` (new);
  `pkgs/klipper-config/build.sh`, `pkgs/anvil-core/payload/bin/anvil-link-prog.sh`
  (the installer).
- `pkgs/timelapse/` (`payload/config/timelapse.cfg`, `build.sh`, `pkg.conf`),
  the HelixScreen pictures.
- `qa/static/`: `test_toolchange_n4s4.py`, `test_n4s4_overrides.py`,
  `test_ff_print_tools.py`, `test_ff_extruder.py`, `test_ff_stats.py`,
  `test_ff_toolchange.py`, `test_ff_lagwatch.py`, `test_ff_print_bounds.py`,
  `test_n4s4_include_installer.py`, `test_timelapse_config.py`, `test_ff_nozzle_size.py`,
  `test_klipper_config.py` (additions);
  `qa/replica/`: `test_ff_stats_on_real_klipper.py`, `test_custom_scripts.py`,
  `test_ff_nozzle_size_on_real_klipper.py`, and `actions/check_nozzle_size.py`
  (native save/backup/reload and include-conflict checks).
- Documents: `docs/statistics.md`, `docs/host-stalls.md`, `docs/nozzle-sizes.md`,
  `docs/how-a-print-runs.md` (a section), `docs/ssh-keys.md`, `UPSTREAM_STATUS_N4S4.md`,
  `FEATURES_N4S4.md`, `CHANGELOG_N4S4.md`, `ORCA_MACHINE_AND_FILAMENT_SETTINGS.md`,
  this file; `gcode/n4s4-extruder-direction-test.gcode`.

`printer_n4s4.cfg` is shipped by the `anvil-klipper-config` package and
installed as `/usr/data/anvil-data/config/printer_n4s4.cfg`. It contains the
`[ff_extruder]` activation together with the N4S4 integration macros and the
calibration values, and is included from `printer.cfg` immediately before the
`SAVE_CONFIG` block:

```cfg
[include printer_n4s4.cfg]
```

The boot hook `/usr/data/anvil-data/scripts/10-enable-printer-n4s4.sh` does
that on its own: it verifies that both files exist, inserts the include, and
creates a backup only when it changes the file. Once the exact include exists,
repeated boots exit immediately without adding script output to the log.

## Validation

- Nozzle configuration (2026-10-17): all 30 focused tests pass. A complete
  static regression run passed 697 tests with 2 skips; the three additional
  missing-parameter cases were then added and passed in the focused run.
  A hardware-free smoke check with the real `gcode.py` and `configfile.py`
  from the printer copy verified native backup, save, reload, include-conflict
  rollback, and pending-calibration protection. The check is also wired into
  the replica lane for the next firmware build; that full installed-image
  lane and a physical-printer nozzle change have not been rerun for this addition.
- The static suite in the build image on 2026-10-14: 670 passed and 2 skipped,
  among them every test function of the original, unmodified; it also
  passed at each of the first nine commits (the later six were not built one by
  one).
- `mkdocs build --strict` passes.
- The replica lane: the Creator 5 package built from the branch head (2026-10-14),
  installed on a Creator 5 replica: 167 passed. The first run had one failure, in this branch's own test: a replica test read the statistics file right after a save that the writer thread had not finished. It now waits for the thread (`stats.flush()`); that file passes (5 of 5) and the other 162 passed in the first run.
- The tool-change module was compared with the previous branch's on about
  134,000 combinations of restore axis, hop, retract values, tower position,
  mounted and requested tool, nozzle temperature and shared stepper; with the
  N4S4 configuration the command streams are identical (the only difference
  is the intended one named in `FEATURES_N4S4.md`). That was before the
  changes of 2026-10-14, which add no G-code to a tool change.
- `ADAPTIVE_MESH` and `DEFINE_PRIME_TOWER_OBJECT` are rendered next to the
  original's by `test_n4s4_overrides.py`; changing either original makes the
  test fail. The original's text of the four other redefined macros is pinned
  the same way.
- On a Creator 5 (code of the earlier branch): tool pickup, in-dock retract,
  Z-hop, prime-tower travel, material and plate Z composition, adaptive mesh
  selection, timelapse suppression, the manual and automatic purge paths, and
  the statistics on two four-colour jobs.
- On a Creator 5, with the code of this branch (deployed on 2026-10-14 with
  backups and checked by hash):
  - The range check, run over all 57 G-code files that were on the printer
    with the printer's own Python: one refusal, the file that had failed, and
    no false refusal; 0.1 to 2.9 s per file. Started from Mainsail, that file
    was refused with `Y up to 263.1 mm (G-code Y262.696 and T1's offset
    +0.414) -- the machine reaches 260.0 mm`; nothing heated or moved and no
    job was recorded.
  - The same job sliced again with the prime tower moved: it ran to the end,
    with 20 tool changes (16 swaps, 4 first pickups), none failed, 1 min 43 s
    in total (5.2 s on average, 5.9 s the slowest; the printer's figures so far
    are 5.6 s on average and 6.2 s the slowest), including the T0/T3 changes
    at the top of the model where the earlier job had been lost. The lag watch
    recorded no stall of 0.25 s or more, and the job start no longer stalled.
    The statistics writer thread wrote its periodic saves without an error.
  - **What this does not show:** one job of 20 tool changes cannot show that the
    failure is gone (it was one in about 4,500 tool changes), and no stall
    happened during it, so `flush_steps_before_wait` has not been exercised
    against a real stall. The cause of the original stall is not known; the
    statistics write is a suspect, nothing more. The lag watch is there to
    find it.

## Compatibility and fallback behaviour

- No registered prime tower and no `no_tower_prime_macro`: the change is the
  original's.
- A `restore_axis` without X or Y: no hop, no in-dock retract (the original's
  reading), unless `protect_every_change` is set (it is in
  `printer_n4s4.cfg`).
- In the code, build-plate and material offsets are zero until set; the
  original print Z compensation stays active independently of them.
- Adaptive mesh, timelapse and purge modes can be selected without changing
  Orca's generated layer-by-layer commands.
- Without `[ff_stats]` nothing is recorded; without `[ff_extruder]` every
  `[extruderN]` owns its stepper and nothing is synced.
- `ff_print` reads the tools a file uses (Orca's `; filament:` header, or a
  bounded scan of `Tn`) and reports them in its status; nothing uses them
  without the N4S4 start macros.
- `check_bounds` is **on by default**, so it is the one change that reaches a
  printer without any N4S4 configuration. It refuses only a file that has a
  move outside the toolhead's axis range, which Klipper would refuse as well,
  later. It can differ from Klipper by the babystep and `SET_GCODE_OFFSET`,
  which it does not know, and it reads only the file, not the macros the file
  calls. A file it cannot read in 40 s is started unchecked. Turn it off with
  `check_bounds: False` in `[ff_print]`.
- `flush_steps_before_wait` is off unless set; without `[ff_lagwatch]` nothing
  is watched; the threads of `ff_stats` and `ff_print` are daemon threads that
  are finished when the work is (`ff_stats` joins its writer at disconnect), and
  a start without a reactor, as in the unit tests, just waits for the thread.

## If a smaller pull request is easier to review

The general parts (`flush_steps_before_wait` in `ff_toolchange.py`, the new
`ff_lagwatch.py`, and the file reading and `check_bounds` in `ff_print.py`) do
not need the rest of this branch except where `ff_print.py` shares a file with
the used-tools reading. I will split them out if that is preferred.

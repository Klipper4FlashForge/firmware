# N4S4 — what was added on top of Reforge for the Creator 5

N4S4 is a local customisation layer on top of the Reforge (anvil) firmware
for the FlashForge Creator 5, written while running multi-colour prints from
OrcaSlicer with a prime tower. This page lists everything it adds or changes.
It is built from the git history, the code and the tests, not from memory.

**Base:** `96c0565` — *Add release notes for v20260827f-melitopol*
(2026-09-24). Everything below is the difference between that commit and the
branch `n4s4/fixes-and-optimizations-01`.

Reforge is the work of its developers — the history shows Oleksandr
Shyshatskyi and Monstrofil. N4S4 only exists because that firmware did; it
adds on top of it and tries not to rewrite any of it.

---

## At a glance

| | |
|---|---|
| Period | 2026-09-23 to 2026-10-01 |
| Size | 39 commits, 31 files, +7,112 / −67 lines |
| New Klipper extras | `ff_extruder`, `ff_bed_mesh`, `ff_stats` |
| Extended Klipper extras | `ff_toolchange` (+435 / −23), `ff_print` (+315 / −13) |
| New configuration | `printer_n4s4.cfg`: 18 new macros, 4 overridden macros, 1 startup `delayed_gcode` |
| Buttons added to Mainsail | `TOOLCHANGE_STATUS`, `ADAPTIVE_MESH_TOGGLE`, `ADAPTIVE_MESH_STATUS`, `START_PURGE_SET`, `START_PURGE_STATUS`, `TIMELAPSE_TOGGLE`, `FF_STATS`, `FF_STATS_JOB`, `FF_STATS_JOBS` |
| Tests | 78 new test functions. Static suite: 421 passed, 2 skipped. Replica suite on a Creator 5 replica: 167 passed |
| Tested on | one Creator 5 (not the Pro), OrcaSlicer 2.4.x |

### Ground rules it follows

- **Additive.** Klipper's `extruder.py`, `bed_mesh.py` and `probe.py` are not
  modified. The N4S4 behaviour comes from three early-loaded extras and from
  one include file.
- **Opt-in.** Without `[include printer_n4s4.cfg]` a printer behaves as
  before. Within `ff_toolchange`, every new option is optional and, if left
  out, keeps the original behaviour.
- **Optional hooks that cannot hurt a print.** `ff_print` and `ff_toolchange`
  report to `ff_stats` only if it is configured, and a fault in the
  statistics is logged and ignored.
- **Four overrides, no edits.** `printer_n4s4.cfg` overrides exactly four
  upstream macros (`_FF_FILAMENT`, `_FF_NOZZLE_WIPE`, `_FF_NOZZLE_CLEAN`,
  `PURGE`). The stock `ff-*.cfg` files are changed in one place
  (`ff-print-macros.cfg`, +28 / −22, see *Print start*).
- **Installed like everything else.** The config and the boot hook ship in
  the existing `anvil-klipper-config` package; nothing is copied by hand.

---

## 1. Tool changes

Mostly in `ff_toolchange.py`, with the values in `[ff_toolchange]`.

| Feature | What it does |
|---|---|
| **Z-hop on both legs** | The nozzle is raised before the old tool travels to its dock, stays raised through release, pickup and the trip away from the docks (`restore_z_hop: 2.0`, `restore_z_feed: 1200`). |
| **Prime-tower-aware return** | The new tool returns to the captured position only if that position is inside the registered prime tower. Over the model, the XY return and the descent are skipped, so the nozzle cannot lower onto a printed part and leave a dot. Orca's own tower travel does the rest. Without a registered tower the original behaviour is kept. |
| **Retract while still in the dock** | After the lock, the new tool's extruder is activated and the filament retracted *before* the tool leaves its dock, which avoids strings on the way out. Skipped automatically below the minimum extrusion temperature. |
| **Separate retract for a known purge** | A pickup that is followed by a purge line, or a tool's first pickup in a prime-tower job, uses `purge_retract: 0.9` plus a 250 ms pressure-settling pause. Other pickups keep the ordinary `restore_retract: 0.4`. |
| **No stacked retract on repeat pickups** | Orca already retracts a tool when it parks it. Adding the firmware retract on top produced a 2.9 mm total that left holes at the tower seam with a small Prime Volume. Repeat pickups now use `tower_repeat_retract: 0.0`. A per-job record of selected tools (`TOOLCHANGE_BEGIN_JOB`) decides which pickup is the first. |
| **Return prime capped to the retract** | The prime after a change never exceeds the distance actually retracted, so a small non-zero `tower_repeat_retract` cannot over-prime. |
| **Slower, recovered pressure** | `restore_unretract: 0.4` at `restore_unretract_feed: 200` only on the tower; over the model the slicer's moving tower lines rebuild the pressure. All extra E moves run in relative mode and restore the slicer's E state. |
| **Faster retreat after a grab** | `grab_retreat_feed: 4800` (80 mm/s) instead of the 25 mm/s default, matching the already-tested release retreat. |
| **Initial pickup stays raised** | The first tool picked up before the purge line no longer returns to the last mesh point and no longer recovers pressure there, which prevented a droplet on the final probed area. |
| **Shared stepper kept in step** | After every pickup, and when the mounted tool is selected again, `SYNC_EXTRUDER_MOTION` connects the one physical stepper to the active logical extruder (see section 2). |
| **No false error in HelixScreen** | `TOOLCHANGE_PARK` now reports `changing` for the whole release, so the brief moment when the dock switch is on before the grab switch clears no longer shows as a *Filament System Error*. |
| **Visible status** | The native status command is `FF_TOOLCHANGE_STATUS`; `TOOLCHANGE_STATUS` is a real macro, so it is a Mainsail button. It also reports the print Z components and the tools selected this job. |

New commands in `ff_toolchange`: `TOOLCHANGE_PREPARE_PICKUP`,
`TOOLCHANGE_BEGIN_JOB`, `TOOLCHANGE_SET_PRIME_TOWER`,
`TOOLCHANGE_SET_MATERIAL_OFFSET`, `FF_TOOLCHANGE_STATUS`. New options:
`restore_z_hop`, `restore_z_feed`, `restore_retract`, `restore_retract_feed`,
`purge_retract`, `purge_retract_dwell_ms`, `tower_repeat_retract`,
`restore_unretract`, `restore_unretract_feed`, `no_tower_prime_macro`.

---

## 2. One physical extruder stepper, four logical hotends (`ff_extruder`)

The Creator 5 has four hotends and one filament stepper, and its generated
`printer.cfg` repeats the stepper options in all four `[extruderN]` sections.
`ff_extruder.py` handles this without touching Klipper's `extruder.py`.

- Only `[extruder]` creates the physical stepper. `extruder1`–`extruder3`
  stay logical: their own heaters, temperatures and extrusion queues.
- The stepper options of the logical extruders are still consumed, so the
  configuration stays valid, but no duplicate stepper is created.
- `SET_PRESSURE_ADVANCE` works for an active logical extruder that has no
  stepper of its own, after checking that the shared stepper really is
  synchronised to that extruder's queue. A wrong assignment aborts.
- `RESTART` and `FIRMWARE_RESTART` work; a genuinely duplicated
  `[ff_extruder]` section is still rejected.
- `post_m109_macro` runs a macro after Klipper's *native* `M109` wait
  finishes, instead of replacing `M109` with a macro.

---

## 3. Print start and OrcaSlicer integration

Mostly in `ff_print.py` and the start macros of `printer_n4s4.cfg`.

| Feature | What it does |
|---|---|
| **Every used tool is known** | `ff_print` reads Orca's compact `; filament:` header (older files: a streaming scan of `Tn`) and exposes `printer.ff_print.tools`, passed to the start macro as `TOOLS=`. |
| **Next tool and its temperature** | It finds the second distinct tool and its first `M109` target (`next_tool`, `next_nozzle`) so the start-up can begin heating it after probing, before Orca can place its own `M104`. |
| **Correct prime-tower geometry** | Orca's `[wipe_tower_x]` in custom start G-code expands to the *first* entry of a multi-plate list. `ff_print` reads the single resolved values from the file's tail instead, and measures the emitted core outline, so a 28 × 14 mm tower is recognised as 28 × 14 and not as a 28 × 28 square. |
| **Orca's automatic brim** | `prime_tower_brim_width = -1` means "automatic", and the real brim exists only in the emitted moves. The brim block is read, rotated into the tower's frame, and exposed as the effective brim plus exact outer bounds (`prime_tower_outer_*`). A negative value can no longer reach Klipper, where it used to abort print start. |
| **`DEFINE_PRIME_TOWER_OBJECT`** | Registers the real, rotated tower rectangle as an exclude object (so the adaptive mesh covers it) and with the toolchanger (for the return-position decision). |
| **Start-up in one pass** | `START_PRINT` starts `M140` *before* the first `G28`, and `DEFER_MESH=1` leaves the final hot-bed Z home, mesh, initial pickup and offsets to the sliced file's `ADAPTIVE_MESH`. This removes the old sequence of grabbing the first tool, parking it again and homing Z a third time. The ordinary path is unchanged for profiles that do not use it. |
| **Filament preflight** | `_NS_FILAMENT_PREFLIGHT` checks the `fd_exN` switch of every used tool, independent of the purge mode. |
| **`_PURGE_NEAR_OBJECT`** | Calculates the bounds of all registered objects and prints a purge line inside the adaptively meshed area. Height, length and lead-in are chosen in the Orca machine start code by nozzle diameter (presets for 0.25, 0.40, 0.60 and 0.80 mm). Most of the material is laid down while moving, which avoids the old start blob. |

The matching OrcaSlicer machine start G-code, build-plate mapping,
adaptive-mesh switch and per-filament offsets are in
[`ORCA_MACHINE_AND_FILAMENT_SETTINGS.md`](ORCA_MACHINE_AND_FILAMENT_SETTINGS.md).

---

## 4. Z corrections: build plate and material

The print-scoped Z correction is now three independent components: the
original temperature / bed / layer base, a build-plate value and an active
filament value. Nothing accumulates, and nothing touches tool calibration or
the babystep.

- `TOOLCHANGE_SET_PRINT_OFFSET PLATE=<-0.5..0.5>` adds the build-plate term;
  `CLEAR=1` resets all three components.
- `TOOLCHANGE_SET_MATERIAL_OFFSET VALUE=<-0.5..0.5> [MOVE=1]` *replaces* the
  material component, so repeated calls cannot add up. With `MOVE=1` on a
  homed printer it is applied as its own Z move, not blended into the next
  extruding tower move.
- `_BUILD_PLATE_OFFSETS` holds independent first-layer corrections for
  Smooth Cool, Smooth High Temp, Textured Cool, Textured PEI, Engineering and
  SuperTack. All default to `0.000` until calibrated.
- `TOOLCHANGE_STATUS` prints the total and the three parts separately.

---

## 5. Bed mesh

| Feature | What it does |
|---|---|
| **Lower Z travel while probing** (`ff_bed_mesh`) | Klipper's row-by-row order is unchanged. The first point is approached at an absolute Z 5 mm; later points lift only 2 mm above the previous trigger height. If that would fall below Z 1 mm, Z 3 mm is used instead, and the decision is made again after every probe. Only the `bed_mesh` probe helper is patched; homing and other probe users are untouched. `FF_BED_MESH_STATUS` reports the values. |
| **Faster mesh settings** | `[bed_mesh]` speed 300, 8 × 8 points, bicubic, and `[probe] samples: 1`. |
| **`ADAPTIVE_MESH`** | Clears the print offset, parks a mounted tool, homes Z without a nozzle, then either probes a fresh adaptive mesh (with a safety margin) or loads the saved `MESH_DATA`, validates the plate code from Orca, picks up the requested tool and applies the Z offset. |
| **`ADAPTIVE_MESH_TOGGLE` / `ADAPTIVE_MESH_STATUS`** | A Mainsail button that switches between probing and loading `MESH_DATA` (also `ENABLE=0/1`), and a status macro. The setting is session-local and defaults to probing. |

---

## 6. Purge, nozzle cleaning and ooze control

Mostly the four overridden macros and the helpers around them.

- **Three pre-print purge modes** (`START_PURGE_SET`, persistent): `OFF`,
  `FIRST` (only the initial tool; the default) and `ALL` (every used tool
  through the rear-right purge-and-wipe sequence). `START_PURGE_STATUS`
  shows the mode.
- **Silicone-lip wipe.** After a chute purge the nozzle travels to the
  front-right service area raised, stays at `X256` for the whole rear-to-front
  move, and does a 14-pass zigzag between `X262` and `X273` on the short
  silicone lip, starting at the outside edge and advancing 0.5 mm per pass
  to `Y7`. It runs at 150 mm/s (`lip_wipe_feed: 9000`), at an absolute
  `Z-1.0` in the active tool's frame, so the tool's calibrated transform
  applies and the roughly 2.9 mm nozzle offset is not subtracted twice.
  `lip_wipe_enabled: 0` keeps the raised route and skips the wipe.
- **Cooldown pad spread out.** Cooldowns cycle through all 30 points of a
  1 mm grid (`X-4..+1`, `Y-4..0` around `X266.5 / Y13.8`), each covered once
  before any repeats, so the pad does not wear in one spot. The start point
  is pseudo-random, from Klipper CPU time, job time and the active tool; the
  sequence lives in macro RAM, so there is no flash write per cooldown. The
  pad height is an independent absolute `Z-0.9`, which relieves the pad's
  compression compared with the earlier conversion that gave about `Z-1.9`.
- **Pipelined preheating.** During a sequential `START_PURGE`, the next tool
  is heated to its own target as soon as the current one is at temperature,
  so it is already warm after the next pickup.
- **Fixed front-wipe temperature.** 150 °C (`clean_wipe_temp`) instead of a
  value that followed the purge temperature and material.
- **Safe corridor for pickup and release.** Cleaning passes end at the
  safe X position instead of returning the empty carriage to the wipe point
  or carrying the next tool diagonally over occupied docks. The manual
  `PURGE` macro follows the same rule, so consecutive console calls withdraw
  each tool to `X250` and travel along it.
- **No unrecovered 5 mm deficit.** Automatic pre-print cleaning uses
  `park_retract: 0.4` instead of the stock 5 mm; manual `PURGE` takes
  `RETRACT=<0..10>` when you want something else.
- **Prime-tower-less jobs.** A tool change without a registered tower no
  longer returns to the previous object. It waits raised at `X250` while
  Orca's `M109` heats the tool, then does one 5 mm pressure-building
  extrusion in the rear-right chute on that tool's *first* use in the job
  (a per-job bitmask stops it repeating every layer). The tool is retracted
  0.4 mm for the trip to `X256 Y0` and given the 0.4 mm back before Orca
  continues. Reused tools add no movement.
- **Lifecycle intact.** `_NS_BEFORE_PRINT` replaces only `ff_print`'s
  callback; the stock lifecycle macros stay in place.

---

## 7. Statistics (`ff_stats`)

New in this branch; documented in [`docs/statistics.md`](docs/statistics.md).

- **Tool changes:** swaps and first pickups per target tool, duration
  (average and slowest), failed changes by stage (`prepare`, `release`,
  `grab`, `finish`, `restore`), and grab / release attempts that had to be
  repeated.
- **Print time:** Klipper's `print_duration` and the whole job, which here
  runs from the print request to the end of the end macro and so includes the
  start-up that `print_stats` leaves out.
- **Phases:** the time of every job is split into `prepare`, `homing`,
  `heating`, `mesh`, `purge`, `toolchange`, `print`, `paused` and `end`,
  adding up to the job time. They come from Klipper's homing events, the
  tool-change code and a configurable list of wrapped commands
  (`phase_commands`). No macro is edited. This is meant to show whether a
  change to start-up or preheat really saved time.
- **Filament, measured per tool** from each extruder's own running position,
  net of retracts, with the start-up clean shown separately and a gram
  estimate from the file's own density.
- **Where it shows up:** the macros `FF_STATS`, `FF_STATS_JOB` and
  `FF_STATS_JOBS` (Mainsail buttons, output in its console), the status
  object `printer.ff_stats` for macros and Moonraker, and
  `FF_STATS_SHOW`, `FF_STATS_PHASE` and `FF_STATS_RESET CONFIRM=1`.
- **Mainsail's own filament figure is corrected** (`correct_print_stats`,
  default on) for new jobs, because the value Klipper reports is wrong on a
  multi-tool printer (see *Observations*).
- **Careful with the flash:** one JSON file under `/usr/data/anvil-data`
  (kept across firmware updates), written atomically, rarely (every 5 minutes
  idle, every minute during a job, and at job end and shutdown), because
  `/usr/data` is mounted `sync`. A job left open by a power loss is recovered
  as `interrupted`. An unreadable file is moved aside, not overwritten.
- **Contained.** In Klipper an exception inside a G-code handler is a printer
  shutdown, so everything Klipper can call into `ff_stats` is guarded.
- **Not included yet:** per-nozzle hours at temperature and heat cycles, and
  maintenance counters (axis travel, motor, bed and fan hours, with reminders).

---

## 8. Controls that live in Mainsail

| Button | Does |
|---|---|
| `TOOLCHANGE_STATUS` | Sensors, geometry, offsets, active tool, Z components, tools selected this job |
| `ADAPTIVE_MESH_TOGGLE` / `ADAPTIVE_MESH_STATUS` | Probe a new mesh or load `MESH_DATA` |
| `START_PURGE_SET` / `START_PURGE_STATUS` | Pre-print purge: `OFF`, `FIRST`, `ALL` (persistent) |
| `TIMELAPSE_TOGGLE` | Timelapse capture on or off (persistent) |
| `FF_STATS`, `FF_STATS_JOB`, `FF_STATS_JOBS` | Statistics, current job, recent jobs |

Persistent settings use `[save_variables]`
(`/usr/data/anvil-data/config/n4s4_saved_variables.cfg`). One second after
Klipper starts, `_RESTORE_N4S4_PERSISTENT_SETTINGS` restores the timelapse
state (first-run default: off) and the purge mode (first-run default:
`FIRST`, the previous behaviour).

---

## 9. Timelapse

- The modified `timelapse.cfg` is shipped by the `anvil-timelapse` package as
  `/usr/data/anvil/config/timelapse.cfg` and reached through the existing
  symlink, instead of being taken unchanged from the upstream archive. The
  package revision follows the firmware release stamp and a hash of the
  payload, so a changed macro file counts as an upgrade on a printer that
  already has the package.
- `TIMELAPSE_TOGGLE` (persistent) lets Orca keep emitting
  `TIMELAPSE_TAKE_FRAME` every layer while the macro ignores it when disabled.
- A disabled-frame notice appears once per session, not once per layer.
- `GET_TIMELAPSE_SETUP` is renamed `TIMELAPSE_SETUP_STATUS`; its output is
  unchanged.

---

## 10. 24 V rail and HelixScreen

- The heater board's 24 V rail (`eheaterboard:PA3`) belongs to Reforge's
  `[heater_fan dc24v_ctl]` in `printer.base.cfg`. N4S4's own config used to
  carry an `[output_pin DC24V_CTL]` on the same pin, which makes Klipper
  refuse to start once both are loaded. It no longer defines the pin, and a
  test loads the base and N4S4 configuration together and requires exactly
  one object on the pin.
- `anvil-link-prog.sh` once lost Reforge's migration that removes the old
  `[output_pin DC24V_CTL]` from a live `printer.cfg`. It was restored, with
  the N4S4 installer step kept. Reforge's own replica test caught it.
- HelixScreen gets two printer pictures
  (`custom_images/creator5.png`, `creator5Pro.png`).

---

## 11. Installation and packaging

- **`10-enable-printer-n4s4.sh`** is installed by the
  `anvil-klipper-config` package into `/usr/data/anvil-data/scripts/`. On boot
  it inserts `[include printer_n4s4.cfg]` before Klipper's `SAVE_CONFIG`
  area (or before `# Save Mesh Data #`). It is idempotent, collapses
  duplicate includes, validates what it wrote, makes a timestamped backup
  only when it changes something, refuses to touch `printer.cfg` if either
  file is missing, and, once the include is in place, ends silently.
- **`anvil-link-prog.sh`** (+23 lines) installs that script atomically, after
  a payload is extracted as well as on package updates, because the
  persistent `anvil-data` directory is outside `anvil.tar.xz`.
- **Build recipes:** `klipper-config` stages and ships the scripts directory;
  `timelapse` carries the local payload and stamps its version (above).

---

## 12. Tests

All of it runs in the repository's own suites.

- **Static (78 new test functions across these files):**
  `test_ff_extruder`, `test_ff_toolchange` (including the retract selection
  and the return-prime cap), `test_ff_print` (automatic brim),
  `test_ff_stats` (49 functions: jobs, phases, measured filament, the
  `print_stats` correction, persistence and recovery, reports, and the real
  `FFToolchange._toolchange`), `test_n4s4_include_installer`,
  `test_timelapse_config`, and additions to `test_klipper_config` and
  `test_custom_scripts`.
- **Replica:** `qa/replica/test_ff_stats.py` runs the installed `ff_stats`
  against Klipper's real `gcode.py` and `print_stats.py` on the printer's own
  Python. Writing it found a wrong method name that fakes had hidden.
- **Results:** 421 passed and 2 skipped in the static suite (in the build
  image); 167 passed in the replica suite on a Creator 5 replica.

---

## Observations that may be useful beyond this branch

Each has its evidence level.

1. **`print_stats.filament_used` is wrong on a multi-tool printer.** Over 28
   completed jobs on one Creator 5, single-tool jobs matched the slicer's
   estimate (median ratio 1.02, 18 jobs), while multi-colour jobs came out at
   12% to 34% (median 25%, 10 jobs). Klipper re-bases the G-code E position
   on every `ACTIVATE_EXTRUDER` (`gcode_move._handle_activate_extruder`), which
   is the likely cause; it has not been isolated further. Moonraker's job
   history and Mainsail's dashboard both show this value. Example: a one-hour
   job with 225 tool changes recorded 1432.7 mm against the slicer's
   6235.8 mm (23 %), before the correction; two later four-colour jobs read
   100.3 % and 100.4 % with it.
2. **`RESTART` does not reload changed Python modules.** `klippy.py` restarts
   in the same process, so a changed `ff_*.py` needs a reboot (or a restart of
   the service) while config changes and brand-new modules do not.
3. **`wakeup_level` can hang a restart.** After every restart FlashForge's
   `klippy.py` runs `./wakeup_level`. It hung once on `/dev/ttyS7` (the level
   board) after a `RESTART`; Moonraker then answered *503 Klippy Host not
   connected* until the printer was rebooted. Earlier restarts the same day
   took about 16 seconds. One observation, cause unknown.
4. **The published replica image simulates the Pro.** Its `app_startup.sh`
   looks for `Creator5Pro-*.tgz`. A Creator 5 package was tested on a derived
   image that differs from the real Creator 5 script only in the model
   constants and the three file globs.

---

## Status and limits

- **Tuned on one machine.** The silicone-lip and cooldown-pad coordinates,
  the plate offsets and the purge heights match one Creator 5 and its parts.
  Expect to adjust them. The Creator 5 Pro has not been tried.
- **Exercised on the printer** (per the author, after a full restart): tool
  pickup, in-dock retract, Z-hop, prime-tower travel, material and plate Z
  composition, adaptive mesh selection, timelapse suppression, and the manual
  and automatic purge paths with consecutive tools. The automatic-brim parser
  was checked against an Orca file using the `-1` sentinel.
- **Statistics, hardware results (2026-10-01):** the module loads, the
  macros print, tool changes are counted per type with durations, a change
  that was refused because the printer was not yet homed was filed as a
  failure at stage `prepare` with its message, a cancelled job is filed as
  `cancelled`, and the corrected filament figure follows the running job.
  The main check is done on two completed four-colour jobs (100 layers,
  3 tool changes each): the measured filament per tool, minus the 49.2 mm
  start-up clean of each tool, against the `; filament used [mm]` line of
  the sliced file.

  | Tool | Job 1 slicer | Job 1 measured | Job 2 slicer | Job 2 measured |
  |---|---|---|---|---|
  | T0 | 1469.67 mm | 1478.77 mm (+0.6 %) | 309.65 mm | 308.05 mm (−0.5 %) |
  | T1 | 438.85 mm | 438.77 mm (0.0 %) | 458.15 mm | 458.15 mm (0.0 %) |
  | T2 | 457.22 mm | 457.22 mm (0.0 %) | 457.22 mm | 457.22 mm (0.0 %) |
  | T3 | 368.58 mm | 367.10 mm (−0.4 %) | 1591.76 mm | 1601.02 mm (+0.6 %) |
  | Total | 2734.32 mm | 2741.86 mm (+0.3 %) | 2816.78 mm | 2824.45 mm (+0.3 %) |

  Every tool is within 0.6 % of the slicer, and the total is 0.3 % above it
  in both jobs. The tool that prints most reads about 9 mm high each time;
  the cause is not isolated. Both jobs counted 3 swaps and 5 pickups, which
  matches the file's `total filament change = 3`; a change took 3.7 s on
  average over the 34 so far, 5.9 s at most. Mainsail's own figure for the
  same two jobs, now corrected, reads 100.3 % and 100.4 % of the slicer's
  total. This is two jobs on one printer, not a series.
- **Tool-change times** exclude the short return travel to the print
  position, and phase times can be a couple of seconds early because Klipper
  processes G-code ahead of the motion.

## Which parts could be proposed upstream

The pieces that are not tied to one printer's geometry:

| Piece | Why it is general |
|---|---|
| `ff_stats`, including the measured filament and the `print_stats` correction | The Klipper figure is wrong for any four-extruder Creator 5 |
| Prime-tower metadata in `ff_print` (resolved coordinates, measured outline, automatic brim) | Orca-specific, but independent of the machine |
| The pin-ownership test for the 24 V rail | Guards against the same duplicate-pin mistake |
| The include installer and its tests | Lets an extra config file be added without editing `printer.cfg` by hand |
| `ff_extruder` and the `SYNC_EXTRUDER_MOTION` step | Part of how the shared stepper is handled |

The purge geometry, the lip wipe, the cooldown pad and the per-plate offsets
depend on one machine and are better left as local configuration.

---

## Where things are

| Path in the repository | What |
|---|---|
| `pkgs/klipper/payload/klipper/klippy/extras/ff_stats.py` | statistics |
| `.../ff_extruder.py`, `.../ff_bed_mesh.py` | shared stepper, mesh Z travel |
| `.../ff_toolchange.py`, `.../ff_print.py` | extended modules |
| `pkgs/klipper-config/payload/config/printer_n4s4.cfg` | the N4S4 configuration |
| `pkgs/klipper-config/payload/scripts/10-enable-printer-n4s4.sh` | include installer |
| `pkgs/anvil-core/payload/bin/anvil-link-prog.sh` | links and installs it |
| `pkgs/timelapse/payload/config/timelapse.cfg` | timelapse macros |
| `ORCA_MACHINE_AND_FILAMENT_SETTINGS.md` | the OrcaSlicer side |
| `CHANGELOG_N4S4.md` | the detailed change log, per file |
| `PR_DESCRIPTION_N4S4.md` | the pull-request text |
| `docs/statistics.md` | user documentation for the statistics |

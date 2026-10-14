# N4S4 — what is added on top of Reforge for the Creator 5

N4S4 is a local customisation layer on top of the Reforge (anvil) firmware
for the FlashForge Creator 5, written while running multi-colour prints from
OrcaSlicer with a prime tower. This page lists what it adds or changes **on
top of the original project as it is today**. It is built from the git
history, the code and the tests, not from memory.

**Base:** `df57d27` — *Remove the shared-stepper adapter* (2026-10-03), the
current `master` of the original project, which is local branch `main`.
Everything below is the difference between that commit and the branch
`n4s4/fixes-and-optimizations-02`.

Earlier N4S4 work has been taken over by the original project in the meantime:
the bed-mesh probing travel, the adaptive-mesh macros and buttons, the
deferred start-up, the prime-tower geometry in `ff_print`, the Z-hop and
in-dock retract options of the tool change, and a fix for the parking status
(by another contributor). They are no longer part of N4S4 and are not listed
again here. What was taken, in which commit, how the original's version
differs, and what was removed or declined there, is in
[`UPSTREAM_STATUS_N4S4.md`](UPSTREAM_STATUS_N4S4.md).

Reforge is the work of its developers — the history shows Oleksandr
Shyshatskyi and Monstrofil — and N4S4 only exists because that firmware did;
it adds on top of it and tries not to rewrite any of it.

---

## At a glance

| | |
|---|---|
| Base | the original project's `main`, `df57d27` (2026-10-03) |
| Size | 36 files changed against `main`, +9,080 / −37 lines: about 3,380 of code and configuration, 3,070 of tests, 2,630 of documents |
| New Klipper extras | `ff_extruder` (the original removed it, see section 2), `ff_stats`, `ff_lagwatch` |
| Extended Klipper extras | `ff_toolchange` (+431 / −22), `ff_print` (+80 / −7) |
| New configuration | `printer_n4s4.cfg` (about 960 lines): new macros, redefined macros, one startup `delayed_gcode` |
| Buttons added to Mainsail | `TOOLCHANGE_STATUS`, `START_PURGE_SET`, `START_PURGE_STATUS`, `TIMELAPSE_TOGGLE`, `FF_STATS`, `FF_STATS_JOB`, `FF_STATS_JOBS` (`ADAPTIVE_MESH_TOGGLE` and `ADAPTIVE_MESH_STATUS` are the original's now) |
| Tests | 670 static tests in the build image (2 more are skipped), of which the original's own run unmodified; the replica lane on a Creator 5 replica is run for a release |
| Tested on | one Creator 5 (not the Pro), OrcaSlicer 2.4.x |

### Ground rules it follows

- **Additive.** Klipper's `extruder.py`, `bed_mesh.py` and `probe.py` are not
  modified. The N4S4 behaviour comes from early-loaded extras and from one
  include file.
- **Opt-in.** Without `[include printer_n4s4.cfg]` a printer behaves as the
  original does. Within `ff_toolchange` every addition has a default that
  leaves the original's behaviour in place; a bare `FFToolchange` object, which
  is what the original's tests build, behaves exactly as upstream's.
  `ff_print` reads one more thing out of every file (the tools it uses) and
  publishes it in its status; nothing acts on it unless an N4S4 macro does.
  Every test function of the original passes here unmodified (two of its files
  also gained N4S4 tests at their end).
- **Optional hooks that cannot hurt a print.** `ff_print` and `ff_toolchange`
  report to `ff_stats` only if it is configured, and a fault in the
  statistics is logged and ignored.
- **Six redefinitions, no edits to the stock files.** `printer_n4s4.cfg`
  redefines `_FF_FILAMENT`, `_FF_NOZZLE_WIPE`, `_FF_NOZZLE_CLEAN`, `PURGE`,
  `ADAPTIVE_MESH` and `DEFINE_PRIME_TOWER_OBJECT`; all six are the original's
  macros. The last two were taken over from N4S4, so they are redefined only
  to add two small things and one line, and a test renders both versions
  and fails if the original changes. The first four replace the original's
  text, and the same test pins what that text looks like, so a change in the
  original fails the suite instead of being shadowed unnoticed
  (`qa/static/test_n4s4_overrides.py`). **No stock `ff-*.cfg` is changed.**
- **Installed like everything else.** The config and the boot hook ship in
  the existing `anvil-klipper-config` package; nothing is copied by hand.

---

## 1. Tool changes

Mostly in `ff_toolchange.py`, with the values in `[ff_toolchange]`. The
original has the hop / in-dock retract / unretract options
(`restore_z_hop`, `restore_retract`, `restore_unretract` and their feeds);
N4S4 sets them and adds the decisions below.

| Feature | What it does |
|---|---|
| **Prime-tower-aware return** | For a change that restores X or Y, the new tool returns to the captured position only if that position is inside the registered prime tower. Over the model, the XY return and the descent are skipped, so the nozzle cannot lower onto a printed part and leave a dot. Orca's own tower travel does the rest. Without a registered tower (and without `no_tower_prime_macro`) the original's behaviour is kept. |
| **Separate retract for a known purge** | A pickup that is followed by a purge line, or a tool's first pickup in a prime-tower job, uses `purge_retract: 0.9` plus a 250 ms pressure-settling pause. Other pickups keep the ordinary `restore_retract: 0.4`. |
| **Every change protected** | The original raises the old tool and retracts the new one in its dock only for a change that restores X or Y. With `protect_every_change: 1` the pickups of the cleaning macros (`_FF_NOZZLE_CLEAN`, `PURGE`), which pass an empty `RESTORE_AXIS`, get the hop and the in-dock retract too, as they did on branch 01; the tool then stays raised and retracted for whatever travel follows. |
| **No stacked retract on repeat pickups** | Orca already retracts a tool when it parks it. Adding the firmware retract on top produced a 2.9 mm total that left holes at the tower seam with a small Prime Volume. Repeat pickups now use `tower_repeat_retract: 0.0`. A per-job record of selected tools (`TOOLCHANGE_BEGIN_JOB`) decides which pickup is the first. |
| **Slower, recovered pressure** | `restore_unretract: 0.4` at `restore_unretract_feed: 200`, only on the tower; over the model the slicer's moving tower lines rebuild the pressure. (The original's unretract is capped to what was retracted, as N4S4's was.) |
| **Faster retreat after a grab** | `grab_retreat_feed: 4800` (80 mm/s) instead of the 25 mm/s default, matching the already-tested release retreat. |
| **Initial pickup stays raised** | The first tool picked up before the purge line (armed with `TOOLCHANGE_PREPARE_PICKUP`) no longer returns to the last mesh point and no longer recovers pressure there, which prevented a droplet on the final probed area. |
| **The return past the docks** | After a change the carriage returns to where it was, in one straight move. To the purge chute that move cuts across the docked tools with the new tool on the carriage (`LOAD_FILAMENT` on T1 drove into T2, 2026-10-06). With `restore_via_corridor: 1` a return to a point beyond `x_safe` goes along the safe column first, then out. A return onto the bed is still one move. |
| **Shared stepper kept in step** | With `[ff_extruder]`, after every pickup (and when the mounted tool is selected again) `SYNC_EXTRUDER_MOTION` connects the one physical stepper to the picked tool, before the in-dock retract (see section 2). |
| **A change without a prime tower** | With `no_tower_prime_macro`, no return to the model; the tool waits raised at `X250` for Orca's `M109` and gets one 5 mm chute prime on its first use (section 6). |
| **No "Timer too close" from a host stall at a dock** | Klipper sends the braking steps of a move late (from a timer 0.2 s before they are due), so a stall of its main thread of about a second at the end of a dock move shuts the MCU down; this ended a print at T3's dock on 2026-10-14. With `flush_steps_before_wait: 1` every wait of a change first sends all steps of the motion. No time is lost. See section 7a. |
| **Visible status** | `FF_TOOLCHANGE_STATUS` always exists; `TOOLCHANGE_STATUS` keeps its name unless a macro of that name is configured (in any case), which is how it becomes a Mainsail button. It also reports the print Z components, the registered tower and the tools selected this job. |

New commands in `ff_toolchange`: `TOOLCHANGE_PREPARE_PICKUP`,
`TOOLCHANGE_BEGIN_JOB`, `TOOLCHANGE_SET_PRIME_TOWER`,
`TOOLCHANGE_SET_MATERIAL_OFFSET`, `FF_TOOLCHANGE_STATUS`. New options:
`purge_retract`, `purge_retract_dwell_ms`, `tower_repeat_retract`,
`no_tower_prime_macro`, `protect_every_change`, `restore_via_corridor`,
`flush_steps_before_wait`. The millimetre values above
are what `printer_n4s4.cfg` sets; the defaults in the code are zero, or the
same as `restore_retract`.

---

## 2. One physical extruder stepper, four logical hotends (`ff_extruder`)

The Creator 5 has four hotends and one filament stepper, and its generated
`printer.cfg` repeats the stepper options in all four `[extruderN]` sections.
`ff_extruder.py` handles this without touching Klipper's `extruder.py`.

**The original project removed this extra on 2026-10-03** because no failure
on a stock configuration had been shown. It is kept here: the MCU toggles the
direction pin per stepper object (read in Klipper's code and in the stock
eBoard image), so four objects on one pin invert the direction of the active
tool whenever one idle tool was left extruded, and nothing upstream changes
that. The evidence, what it does not prove, and the test
(`gcode/n4s4-extruder-direction-test.gcode`) are in
[`UPSTREAM_STATUS_N4S4.md`](UPSTREAM_STATUS_N4S4.md), section 5. The test was
run on a Creator 5 on 2026-10-06: with `ff_extruder` T1 purged normally after
T0 had been left extruded; on the stock design (without it) T1's purge ran the
wrong way. So it stays.

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

Mostly in `ff_print.py` and the start macros of `printer_n4s4.cfg`. The
prime-tower geometry that `ff_print` reads from the file (position, measured
outline, Orca's automatic brim), `DEFINE_PRIME_TOWER_OBJECT` and the
start-up in one pass (`DEFER_MESH`, the bed heating before the first
homing) are the original's now.

| Feature | What it does |
|---|---|
| **Every used tool is known** | `ff_print` reads Orca's compact `; filament:` header (a file without it: a streaming scan of `Tn` that gives up after 2 s) and exposes `printer.ff_print.tools`. `_NS_BEFORE_PRINT` reads it; the original's start macro does not get it, because it would clean every tool. It feeds the checks of every print (`_FF_PREFLIGHT`: each used tool installed and calibrated; `_NS_FILAMENT_PREFLIGHT`) and, in purge mode `ALL`, the clean of every colour. The original left this out on purpose. |
| **A file beyond the machine is refused** | `check_bounds` (on by default): the whole file is read once for the positions it moves to, with the mounted tool's offset added, and refused before anything heats if it goes beyond the axis range (X and Y at both ends, Z at the top). Without it a prime tower 0.018 mm too far out failed two minutes into a job (2026-10-14), after heating and meshing. Checked against the 57 files on the printer: one refusal, that one. See `docs/how-a-print-runs.md`. |
| **The file is read in a thread** | The scan of the file at the start used to hold Klipper's thread for 0.7 s (found by the lag watch). It runs in a thread now while the start waits with `reactor.pause`. |
| **`ADAPTIVE_MESH` additions** | `PLATE=<code>` (the build-plate correction, section 4), `TOOLCHANGE_PREPARE_PICKUP` before the first pickup, and the preheat of the file's second tool once the mesh is done (see below). The rest of the macro is the original's. |
| **`DEFINE_PRIME_TOWER_OBJECT` addition** | One line: the toolchanger is given the same rectangle (section 1). |
| **Filament preflight** | `_NS_FILAMENT_PREFLIGHT` checks the `fd_exN` switch of every used tool, independent of the purge mode. |
| **`_PURGE_NEAR_OBJECT`** | Calculates the bounds of all registered objects and prints a purge line inside the adaptively meshed area. Height, length and lead-in are chosen in the Orca machine start code by nozzle diameter (presets for 0.25, 0.40, 0.60 and 0.80 mm). Most of the material is laid down while moving, which avoids the old start blob. |

**Preheat of the second tool.** Once the mesh is done, `ADAPTIVE_MESH` heats
the file's second tool to its first target (`M104 S<next_nozzle> T<next_tool>`).
`ff_print` finds both in the first 256 KiB of the file
(`printer.ff_print.next_tool`, `next_nozzle`); a second tool further in is not
looked for, because Orca's own preheat `M104` lands in the object body by
then. The original added this and dropped it again (`7e8ea9d`, "Orca's own
preheat is the only thing that heats a tool ahead of its change", no further
reason). It is kept here for files whose first colour is too short for Orca's
lead time. The price is a hot idle nozzle until the first change, as the check
does not ask whether Orca's preheat would have been in time.

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
  SuperTack. They are `0.000` until calibrated, except Textured PEI, which is
  set to `0.03` (for PLA).
- `TOOLCHANGE_STATUS` prints the total and the three parts separately.

---

## 5. Bed mesh

The lower Z travel while probing (`ff_bed_mesh`, `[ff_bed_mesh]`), the
`ADAPTIVE_MESH` macro and the `ADAPTIVE_MESH_TOGGLE` / `ADAPTIVE_MESH_STATUS`
buttons are the original's now (`bb84772`); the file `ff_bed_mesh.py` is
identical to the N4S4 one. What N4S4 keeps is configuration:

| Feature | What it does |
|---|---|
| **Faster mesh settings** | `[bed_mesh]` speed 300, 8 × 8 points, bicubic, and `[probe] samples: 1`. |
| **`ADAPTIVE_MESH` additions** | Section 3. |

---

## 6. Purge, nozzle cleaning and ooze control

Mostly the four redefined stock macros and the helpers around them.

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
  `/usr/data` is mounted `sync`, and from a second thread, never during a
  tool change, so that a slow flash cannot hold up Klipper. A job left open by
  a power loss is recovered as `interrupted`. An unreadable file is moved
  aside, not overwritten.
- **Contained.** In Klipper an exception inside a G-code handler is a printer
  shutdown, so everything Klipper can call into `ff_stats` is guarded.
- **Not included yet:** per-nozzle hours at temperature and heat cycles, and
  maintenance counters (axis travel, motor, bed and fan hours, with reminders).

---

## 7a. Host stalls (`ff_lagwatch`)

On 2026-10-14 a three-colour print ended with `Timer too close` at T3's dock
although the host was not overloaded: Klipper's main thread had stopped for
about a second. The firmware now does three things about it (page
`docs/host-stalls.md`):

- **The wait sends the last steps first** (`flush_steps_before_wait`, section
  2): after it, the MCU holds all of a move and a stall of the host cannot
  starve it. No time is lost.
- **The statistics are written by a second thread** (section 7), the main
  suspect of the stall, because it did an `fsync` on a `sync`-mounted flash
  from Klipper's thread once a minute.
- **A lag watch** (`[ff_lagwatch]`) notes in `printer.log` when the main
  thread is away for 0.25 s or more, with the call it was blocked in, how long
  it lasted and whether motion was queued. `FF_LAGWATCH` lists them. It
  changes nothing about a print; it is how the real cause is found.

Not yet run through a print on a printer; the cause of the stall itself is not
known.

---

## 8. Controls that live in Mainsail

| Button | Does |
|---|---|
| `TOOLCHANGE_STATUS` | Sensors, geometry, offsets, active tool, Z components, tools selected this job |
| `ADAPTIVE_MESH_TOGGLE` / `ADAPTIVE_MESH_STATUS` | Probe a new mesh or load `MESH_DATA` (the original's) |
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
- `anvil-link-prog.sh` is the original's, plus the step that installs the
  include installer; the original's own migration of `[output_pin DC24V_CTL]`
  is untouched.
- HelixScreen gets two printer pictures
  (`custom_images/creator5.png`, `creator5Pro.png`).

---

## 11. Installation and packaging

- **`10-enable-printer-n4s4.sh`** is installed by the
  `anvil-klipper-config` package into `/usr/data/anvil-data/scripts/`. On boot
  it inserts `[include printer_n4s4.cfg]` before Klipper's `SAVE_CONFIG`
  area (or before `# Save Mesh Data #`). It is idempotent, collapses
  duplicate includes, validates what it wrote, makes a backup
  (`printer.cfg.before-n4s4`, with a timestamp added when that already exists)
  only when it changes something, refuses to touch `printer.cfg` if either
  file is missing, and, once the include is in place, ends silently.
- **`anvil-link-prog.sh`** (+23 lines) installs that script atomically, after
  a payload is extracted as well as on package updates, because the
  persistent `anvil-data` directory is outside `anvil.tar.xz`.
- **Build recipes:** `klipper-config` stages and ships the scripts directory;
  `timelapse` carries the local payload and stamps its version (above).

---

## 12. Tests

All of it runs in the repository's own suites, next to the original's: every
test function of the original passes here **unmodified**, and two of its files
(`test_klipper_config.py`, `qa/replica/test_custom_scripts.py`) also gained
N4S4 tests at their end.

- **Static, N4S4's own files:**
  - `test_toolchange_n4s4` — the prime-tower-aware return, the pickup
    retract choice (purge, first use, repeat), `protect_every_change`, the
    return along the safe column past the docks, the
    no-tower macro, the shared stepper sync (with the real `_grab`, to pin its
    order against the dock retract), the plate and material Z components, the
    option defaults and the command names. It runs the real `_toolchange`,
    with the dock mechanics replaced, like the original's
    `test_toolchange_travel`.
  - `test_n4s4_overrides` — `ADAPTIVE_MESH` and `DEFINE_PRIME_TOWER_OBJECT`
    here must be the original's plus exactly the documented additions; the
    original's text of the four other redefined macros is pinned; a copy of
    anything else the original owns must not come back; the merged
    configuration must compile, and no macro may share a name with a command a
    module registers.
  - `test_ff_print_tools` — the used-tool list, its bounded scan, the second
    tool and its first target (found in the head of the file, not beyond it),
    and that the start macro is not handed `TOOLS=`.
  - `test_ff_extruder` — the shared-stepper adapter.
  - `test_ff_stats` — jobs, phases, measured filament, the
    `print_stats` correction, persistence and recovery, the writer thread,
    reports, and the real `FFToolchange._toolchange`.
  - `test_ff_lagwatch` — the beat, the watchdog's stack samples (against a
    thread blocked in a call), garbage-collection lines, that nothing in it
    can raise into Klipper.
  - `test_ff_toolchange` — what `printer_n4s4.cfg` wires up (retract values,
    lip wipe, cooldown grid).
  - `test_n4s4_include_installer`, `test_timelapse_config`, and additions to
    `test_klipper_config` (the 24 V pin owner with N4S4 loaded).
- **Replica:** `qa/replica/test_ff_stats.py` runs the installed `ff_stats`
  against Klipper's real `gcode.py` and `print_stats.py` on the printer's own
  Python; `test_custom_scripts` installs and runs the include installer. The
  whole replica lane (167 tests) passed on the Creator 5 package built from
  this branch on 2026-10-06.
- **Behaviour parity with branch 01:** the tool-change module was compared
  with the previous branch's on about 134,000 scenario combinations (see
  *Status and limits*). That check is a one-off script, not part of the suite.
- **Results:** 586 passed and 2 skipped in the static suite (in the build image).

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
5. **Four extruder sections on one stepper make the direction depend on
   history.** All four `[extruderN]` sections use `eboard:PB14/PB15/PB12`
   (with `[duplicate_pin_override]`), so Klipper creates four stepper objects
   for one physical stepper. The MCU toggles the direction pin per object
   (`src/stepper.c`: `gpio_out_toggle_noirq` on a "direction changed" flag
   that each object computes against its own last move), so the pin is the
   XOR of the four objects' last directions. The active tool is driven the
   right way only while an even number of the other tools was last moved
   forward, in practice none. Evidence: the code of the pinned Klipper, the
   disassembled stock eBoard image (the same compare of last and next
   direction, and the same toggle of the output register), and the
   single-stepper design in the author's own files from the day after
   Reforge's first boot (2026-09-12). Shown on a Creator 5 on 2026-10-06 with
   `gcode/n4s4-extruder-direction-test.gcode`: after T0 was left extruded, T1's
   purge ran the wrong way on the stock design and the right way with the
   single stepper; a short retract on T1 before the purge does not change it
   (T1's pickup already has one). Not shown: which everyday path left a tool
   extruded when the author and others first saw the problem.

---

## Status and limits

- **Tuned on one machine.** The silicone-lip and cooldown-pad coordinates,
  the plate offsets and the purge heights match one Creator 5 and its parts.
  Expect to adjust them. The Creator 5 Pro has not been tried.
- **Exercised on the printer** (per the author, after a full restart, with the
  code of branch `n4s4/fixes-and-optimizations-01`): tool pickup, in-dock
  retract, Z-hop, prime-tower travel, material and plate Z composition,
  adaptive mesh selection, timelapse suppression, and the manual and automatic
  purge paths with consecutive tools. The automatic-brim parser was checked
  against an Orca file using the `-1` sentinel.
- **This branch (`...-02`) has not run on the printer yet.** Its tool-change
  code was rebuilt on top of the original's newer one and then compared with
  branch 01's: with the N4S4 configuration (including
  `protect_every_change`), both emit the **same command stream** in about
  134,000 combinations of restore axis, hop, retract values, tower position,
  mounted and requested tool, hot or cold nozzle and shared stepper. The only
  difference is an intended one: with every protection option at zero the
  extruder is no longer activated in the dock first.
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
- **File range check (2026-10-14):** run over all 57 G-code files on the
  printer with the printer's own Python: one refusal (the file that failed),
  no false refusal; 0.1 to 2.9 s per file. Not yet run through a real job
  start.
- **Host stalls (2026-10-14):** the `Timer too close` of that day was traced
  from the log to one stall of Klipper's main thread of about a second, and
  the mechanism (the braking steps of a move leave the host from a timer
  0.2 s before they are due) was read out of the toolhead's code. The fix
  (`flush_steps_before_wait`) and the lag watch are covered by unit tests and
  by a run of the lag watch with real threads and a real clock; **they have
  not run through a print on a printer**, and the cause of that stall is not
  known. The statistics writer thread is the first thing changed against it.

## Which parts could be proposed upstream

The pieces that are not tied to one printer's geometry, and that the original
has not taken yet:

| Piece | Why it is general |
|---|---|
| `ff_stats`, including the measured filament and the `print_stats` correction | The Klipper figure is wrong for any four-extruder Creator 5 |
| `ff_extruder` and the `SYNC_EXTRUDER_MOTION` step | The cause is in the MCU and the stock config, and the failure the original asked for has been shown: on the stock design T1's purge ran the wrong way after T0 was left extruded (`gcode/n4s4-extruder-direction-test.gcode`, 2026-10-06) |
| The pickup retract choice (`purge_retract`, `tower_repeat_retract`) and the prime-tower-aware return | General for any prime-tower job; they add to the original's `restore_*` options |
| `protect_every_change` | A small extension of the original's rule that only a change restoring X or Y is protected; off by default |
| The list of used tools | Lets the checks and a purge cover every colour; the original left it out together with the whole-file scans (here the scan is bounded) |
| The pin-ownership test for the 24 V rail | Guards against the same duplicate-pin mistake |
| The include installer and its tests | Lets an extra config file be added without editing `printer.cfg` by hand |

The purge geometry, the lip wipe, the cooldown pad and the per-plate offsets
depend on one machine and are better left as local configuration.

---

## Where things are

| Path in the repository | What |
|---|---|
| `pkgs/klipper/payload/klipper/klippy/extras/ff_stats.py` | statistics |
| `.../ff_lagwatch.py` | lag watch |
| `.../ff_extruder.py` | shared stepper |
| `.../ff_toolchange.py`, `.../ff_print.py` | the original's modules, extended |
| `pkgs/klipper-config/payload/config/printer_n4s4.cfg` | the N4S4 configuration |
| `pkgs/klipper-config/payload/scripts/10-enable-printer-n4s4.sh` | include installer |
| `pkgs/anvil-core/payload/bin/anvil-link-prog.sh` | links and installs it |
| `pkgs/timelapse/payload/config/timelapse.cfg` | timelapse macros |
| `gcode/n4s4-extruder-direction-test.gcode` | the shared-stepper test |
| `UPSTREAM_STATUS_N4S4.md` | what the original has, what is still N4S4's |
| `ORCA_MACHINE_AND_FILAMENT_SETTINGS.md` | the OrcaSlicer side |
| `CHANGELOG_N4S4.md` | the detailed change log, per file |
| `PR_DESCRIPTION_N4S4.md` | the pull-request text |
| `docs/statistics.md` | user documentation for the statistics |

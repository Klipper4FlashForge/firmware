# N4S4 and the original project: what is in main, what is still mine

Status 2026-10-07. The original project is
[Klipper4FlashForge/firmware](https://github.com/Klipper4FlashForge/firmware);
its `master` is at `df57d27` (2026-10-03, *Remove the shared-stepper
adapter*). In this repository

| | |
|---|---|
| remote `upstream` | the original project |
| local branch `main` | exactly `upstream/master` |
| `n4s4/fixes-and-optimizations-02` | `main` plus what is still N4S4's, and nothing else |
| `n4s4/fixes-and-optimizations-01` | untouched: the state from before the original project took anything. PR #29 is still that branch |

`main` and branch 02 were pushed to `origin` on 2026-10-07.

My base was `96c0565` (2026-09-24). Six commits followed it upstream: two
take pieces of PR #29 (`bb84772`, `308d97e`), two remove some of those pieces
again (`7e8ea9d`, `df57d27`), and two are fixes by another contributor
(`01892ff`, `3d9f193`).

## At a glance

| What branch 01 had | In the original project | On branch 02 |
|---|---|---|
| Bed-mesh probing travel (`ff_bed_mesh`) | **taken**, byte-identical | gone |
| `ADAPTIVE_MESH_TOGGLE`, `ADAPTIVE_MESH_STATUS` (Mainsail buttons) | **taken**, same macros | gone |
| `ADAPTIVE_MESH` | **taken** | redefined, three additions |
| `DEFINE_PRIME_TOWER_OBJECT` | **taken** | redefined, one added line |
| The prime tower of the active plate, read from the file (incl. Orca's automatic brim) | **taken**, identical | gone |
| Deferred start-up, bed heats before the first homing | **taken** | gone |
| Raise, retract and unretract around a tool change (`restore_z_hop`, `restore_retract`, `restore_unretract`) | **taken**, simplified | the values, and the extensions of section 2 |
| `changing` status while parking (HelixScreen) | **fixed by someone else** (#31) | gone |
| Prime-tower-aware return, separate purge retract, no-tower prime, `protect_every_change`, the return past the docks, plate and material Z, statistics hooks | offered in #29, **not taken** | kept, off unless used |
| Shared extruder stepper (`ff_extruder`) | taken, then **removed** (no failure had been shown) | **kept**: section 5 |
| Preheat of the second tool | taken, then **removed** | **kept** (a decision of 2026-10-07): section 3 |
| List of the tools a file uses | offered in #29, **not taken** | kept: section 3 |
| Statistics (`ff_stats`) | offered in #29, **not taken** | kept; its saves are written from a thread since 2026-10-14 |
| Lag watch (`ff_lagwatch`) | new (2026-10-14) | kept; finds what stops Klipper's main thread, changes nothing |
| `flush_steps_before_wait` | new (2026-10-14) | kept, off unless set; a candidate to offer |
| Purge and cleaning work, Mainsail switches, timelapse, installer, `printer_n4s4.cfg` | not in #29 | kept |

---

## 1. Taken by the original project

Everything in this table is in `main`. Where I had a copy, it is removed from
branch 02 or reduced to what the original does not have.

| What | In the original since | How the original's version compares | What is left here |
|---|---|---|---|
| Bed-mesh probing travel (`ff_bed_mesh.py`, `[ff_bed_mesh]`: first move at Z 5, then 2 mm above the last trigger, Z 3 under Z 1) | `bb84772` | The file is **byte-identical** to mine; the four values are the same and now sit in `ff-print-macros.cfg`. Upstream also wrote `test_ff_bed_mesh.py` | Nothing. The copy of `[ff_bed_mesh]` in `printer_n4s4.cfg` is gone. My `[bed_mesh]` tuning (speed 300, 8 x 8, bicubic) and `[probe] samples: 1` stay, as configuration |
| `ADAPTIVE_MESH_TOGGLE` and `ADAPTIVE_MESH_STATUS` (the Mainsail buttons: probe a new mesh or load `MESH_DATA`) | `bb84772` | The same macros and the same behaviour; only the description of one and the way a variable is read differ | Nothing; a test fails if a copy comes back |
| `ADAPTIVE_MESH`: park, home Z, probe or load the mesh, grab the first tool, set the print offset | `bb84772` | Same flow. The original also sets the first tool's temperature (`M104`) before the pickup, which mine did not; the redefinition here has it now | A redefinition that adds three things (section 2) |
| `DEFINE_PRIME_TOWER_OBJECT`: registers Orca's real tower as an excluded object for the mesh | `bb84772` | Same flow; the original is the more careful one (it copes with a missing `X=`), and the text here is a copy of it | A redefinition with one added line (section 2) |
| The prime tower of the active plate, read from the file: position, size, measured outline, rotation, **Orca's automatic brim (`-1`)**, as `printer.ff_print.prime_tower_*` | `bb84772` | The code in `ff_print.py` is **identical** to mine, and so is its test | Nothing |
| Deferred start-up: `START_PRINT DEFER_MESH=1` leaves the final Z home, mesh, first pickup and offsets to the file's `ADAPTIVE_MESH`; the bed starts heating **before** the first homing | `bb84772` | Same behaviour. The original switches it on with `variable_defer_mesh: 1` on `FF_BEFORE_PRINT_START`; my `_NS_BEFORE_PRINT` passes `DEFER_MESH=1` directly, which reaches `START_PRINT` the same way | Nothing in `ff-print-macros.cfg`: the file is exactly the original's |
| The return to the print after a tool change: raise the old tool before it crosses the part, retract the new tool while it is still seated in its dock, travel back raised, descend, give back part of the retract. Options `restore_z_hop`, `restore_retract`, `restore_unretract` and their feeds | `308d97e` | Same options, same names, same meaning, all zero as shipped, and only for a change that restores X or Y. The unretract is capped to what was actually retracted, as mine was. **Simplified**: no prime-tower handling, no separate retract for a pickup before a purge, no shared-stepper sync, and no protection of a change that restores nothing | The values in `[ff_toolchange]` of `printer_n4s4.cfg`. The extensions are in section 2 |
| `TOOLCHANGE_PARK` and `UNSELECT_TOOL` report `changing` while they run (the transient overlap of dock and grab sensors during a park used to read as `error`, and HelixScreen showed a filament-system error toast) | `01892ff` (#31, steven-o-cymru) | Their own implementation, which also puts the previous value back; it has tests | Nothing. My version and its tests are dropped |

Also upstream, not mine: clearing an active bed mesh before parking with Z
unhomed (`3d9f193`, #32).

---

## 2. In the original project, with N4S4's additions on top

These are the places where `main` has the basic version and branch 02 adds to
it. The additions to the tool change and to the two macros are **off unless
used**; `ff_print.py` reads two more things out of every file (the tools it
uses, and its second tool with that tool's first temperature), which nothing
uses unless an N4S4 macro does.

### `ff_toolchange.py` (+433 / -22 against main)

The millimetre values are what `printer_n4s4.cfg` sets; the defaults in the
code are zero, or the same as `restore_retract`.

| Addition | Switched on by | What it does |
|---|---|---|
| Prime-tower-aware return | `TOOLCHANGE_SET_PRIME_TOWER` (called by `DEFINE_PRIME_TOWER_OBJECT`), with X or Y in `restore_axis` | A change issued **inside** the registered tower returns there as usual. Over the model, the X/Y return and the descent are skipped, so the new tool cannot lower onto the part and leave a dot. The tool stays raised and retracted; the slicer's own tower travel takes over |
| Separate retract for a pickup that a purge follows | `purge_retract`, `TOOLCHANGE_PREPARE_PICKUP`, and with a registered tower each tool's first pickup of the job | `purge_retract` (0.9 mm) plus `purge_retract_dwell_ms` (250 ms) instead of `restore_retract` (0.4 mm) |
| No stacked retract on repeat pickups | a registered tower and `TOOLCHANGE_BEGIN_JOB` | Orca already retracts a tool when it parks it; adding the dock retract on top left holes at the tower seam. `tower_repeat_retract` (0.0) replaces it |
| The no-prime-tower workflow | `no_tower_prime_macro: _NS_MARK_TOOL_PRIME` | A change without a registered tower does not return to the model. The macro marks the new tool, and Orca's following `M109` triggers one 5 mm pressure-building extrusion in the rear chute, once per tool and job (that hook lives in `[ff_extruder]`) |
| Every change protected | `protect_every_change: 1` | The original protects only a change that restores X or Y. With this on, a change that restores nothing is raised and retracted too: the pickups of `_FF_NOZZLE_CLEAN` and `PURGE`, which pass an empty `RESTORE_AXIS`. This is what branch 01 did for them |
| The return past the docks | `restore_via_corridor: 1` | After a change the carriage returns to where it was in one straight move. To a point beyond `x_safe` (the purge chute, the wipe pad) that move cuts across the docked tools with the new tool on the carriage: on 2026-10-06 `LOAD_FILAMENT` on T1 drove into T2 that way (the original's load macros do not pass `RESTORE_AXIS=`, and `restore_axis: xy` is set here). With the option the return goes along the safe column first (Y), then out (X), as every dock move does. A return onto the bed is still one move |
| Shared stepper follows the tool | `[ff_extruder]` in the configuration | After a pickup (and when the mounted tool is selected again) `SYNC_EXTRUDER_MOTION` connects the one stepper to that tool's motion queue, **before** the dock retract |
| Plate and material Z | `TOOLCHANGE_SET_PRINT_OFFSET PLATE=`, `TOOLCHANGE_SET_MATERIAL_OFFSET` | The print offset is three independent parts (the app's temperature / bed / layer term, a build-plate term, the filament's term); none accumulates and none touches the babystep or the tool calibration |
| Status | always | `FF_TOOLCHANGE_STATUS` always exists; `TOOLCHANGE_STATUS` is registered as before unless a `[gcode_macro TOOLCHANGE_STATUS]` is configured, in any case (N4S4 has one, a Mainsail button that calls `FF_TOOLCHANGE_STATUS`). The report lists the Z parts, the registered tower and the tools used this job |
| Statistics hooks | `[ff_stats]` in the configuration | Counts, times and failures of tool changes |
| The wait sends the last steps first | `flush_steps_before_wait: 1` | Klipper sends the braking steps of a move from a timer 0.2 s before they are due, so a stall of its main thread of about a second at the end of a dock move shut the MCU down (`Timer too close`, T3's dock, 2026-10-14). The option makes every wait of a change call `flush_step_generation()` first. No time is lost. General for any four-tool Creator 5; off unless set |

### The six macros redefined in `printer_n4s4.cfg`

A redefinition replaces what it sets, so a change in the original could go
unnoticed. Two of the six are the original's text plus a few lines, and
`qa/static/test_n4s4_overrides.py` renders both versions and requires exactly
that. The other four replace the original's text; the same file pins what the
original's four look like and fails the day one of them changes, and it fails
too if N4S4 ever defines a seventh macro the original already has.

| Macro | Added or replaced here | Guard |
|---|---|---|
| `ADAPTIVE_MESH` | `PLATE=<code>` (a first-layer Z correction per build plate, from `_BUILD_PLATE_OFFSETS`); `TOOLCHANGE_PREPARE_PICKUP` before the first pickup; once the mesh is done, the file's second tool is heated to its first target (`M104 S<next_nozzle> T<next_tool>`, from `printer.ff_print`) | rendered next to the original's |
| `DEFINE_PRIME_TOWER_OBJECT` | one line, `TOOLCHANGE_SET_PRIME_TOWER`, that gives the toolchanger the same rectangle | rendered next to the original's |
| `_FF_FILAMENT` | only variables: the 150 C wipe temperature, the cooling-pad grid, the lip-wipe geometry | the original's text is pinned |
| `_FF_NOZZLE_WIPE` | the wipe across the front silicone lip, then onto a pad position cycled through a 30-point grid | the original's text is pinned |
| `_FF_NOZZLE_CLEAN` | one chute purge per used tool with a safe pickup and release (empty `RESTORE_AXIS`), the next tool preheated meanwhile, a 0.4 mm retract instead of 5 mm | the original's text is pinned |
| `PURGE` | the same safe pickup and release for the manual purge | the original's text is pinned |

### `ff_print.py` (+131 / -7 against main)

* The tools a file uses, from Orca's `; filament:` header, with a scan of the
  file as fallback that gives up after 2 s (`printer.ff_print.tools`). They
  are **not** passed on to the original's start macro, which would clean every
  one of them; `_NS_BEFORE_PRINT` reads them from the status.
* The file's second tool and its first `M109` target
  (`printer.ff_print.next_tool`, `next_nozzle`), looked for only in the first
  256 KiB; `ADAPTIVE_MESH` heats that tool with them.
* The job hooks for `[ff_stats]`, which do nothing without that section.
* The read of the file runs in a thread while the start waits with
  `reactor.pause` (the original runs it in Klipper's thread: 0.7 s on this CPU,
  caught by the lag watch on 2026-10-14).
* `check_bounds` (on by default): the file is read once for the positions it
  moves to and refused before anything happens if it goes beyond the axis
  range, with the mounted tool's offset added. General for any printer;
  a candidate to offer.

---

## 3. Removed or declined by the original project

The original project made these choices on purpose and said so in its commit
messages. All three are still kept here, each a place where branch 02 keeps
differing from `main`.

| What | What the original said | What happened here | If you drop it |
|---|---|---|---|
| **The shared extruder stepper (`ff_extruder`)** | `df57d27`: it was never enabled in the original; the fork already tolerates the repeated pins (`duplicate_pin_override`), every extruder keeps its own stepper and pressure advance, and no failure on a stock configuration had been shown. It can come back if one is | **Kept: the stock design failed the test of section 5 on 2026-10-06** (T1's purge ran the wrong way) | The stock four-stepper design returns. `post_m109_macro` (the no-tower prime) goes with it |
| **The list of used tools** | `bb84772`: left out on purpose, together with the whole-file scans behind it | **Kept.** It feeds the pre-print checks of **every** print: the original's `_FF_PREFLIGHT` (each used tool must be installed and calibrated, or the job is refused before any heating) and N4S4's `_NS_FILAMENT_PREFLIGHT` (each used tool must have filament); in purge mode `ALL` it also drives the clean of every colour. Orca's `; filament:` header sits at line 7 and is read from the head; only a file without that header is scanned, for at most 2 s | The checks would cover only the first tool, and purge mode `ALL` would fall back to it |
| **The preheat of the second tool** after the mesh | `7e8ea9d`: Orca's own preheat `M104` is to be the only thing that heats a tool ahead of its change. No further reason is given there, and nothing else in the original's history or documentation says more | **Kept** (it was dropped for a day, on 2026-10-06, and put back on 2026-10-07). Right after the mesh, `ADAPTIVE_MESH` heats the file's second tool to its first target, so that a file whose first colour is short does not wait at the first change. The search for that tool covers only the first 256 KiB of the file. The price: it does not check whether Orca's own preheat would have been in time, so the nozzle can stand hot and idle until the first change (an oozing nozzle in a parked tool). Nothing else depends on it | Orca's preheat alone heats the second tool, as in the original. The change then waits for the temperature when Orca's lead time was too short |

The same commit (`bb84772`) also left out, on purpose, the statistics hooks,
the plate and material offsets and the tool-change travel changes. Those are
not removals; they were simply not taken (section 4).

---

## 4. Still only N4S4

| Area | What |
|---|---|
| Statistics | `ff_stats.py` (1,067 lines): tool changes (swaps, first pickups, duration, failures by stage), print time, the time of every job split into phases, **filament measured per tool**, and a corrected `print_stats.filament_used`. Mainsail buttons `FF_STATS`, `FF_STATS_JOB`, `FF_STATS_JOBS`; documented in `docs/statistics.md`. Checked on a Creator 5: per tool within 0.6 % of the slicer on two four-colour jobs |
| Purge and cleaning | three pre-print purge modes (`START_PURGE_SET`), the wipe across the front silicone lip, the cooldown pad spread over a 30-point grid, the next tool preheated while the current one is cleaned (in the pre-print clean only), a safe corridor for pickup and release, no unrecovered 5 mm retract, nozzle-specific purge lines (`_PURGE_NEAR_OBJECT`) |
| Mainsail controls | `TIMELAPSE_TOGGLE`, `START_PURGE_SET` and their persistence across reboots (`[save_variables]`) |
| Timelapse | a modified `timelapse.cfg` shipped by the `anvil-timelapse` package |
| Installation | the boot script that adds `[include printer_n4s4.cfg]` to `printer.cfg` safely, and its linking in `anvil-link-prog.sh` |
| HelixScreen | two printer pictures |
| The configuration | `printer_n4s4.cfg` (about 980 lines): everything above wired together, plus the values for `[ff_toolchange]`, `[probe]` and `[bed_mesh]` |
| Documentation | `docs/statistics.md`, `docs/ssh-keys.md`, the N4S4 documents at the top level, the Orca settings in `ORCA_MACHINE_AND_FILAMENT_SETTINGS.md` |

---

## 5. The shared extruder stepper: is it still needed?

**Short answer: yes, and it is now shown on the printer.** The original
project removed the adapter because nobody had shown it failing on a stock
configuration, not because something else fixed the problem. On 2026-10-06 the
stock design failed the test below on your Creator 5: with a tool left
extruded, the next tool's purge ran the wrong way; with the single stepper it
did not. So it stays (decision of 2026-10-06: test it, and drop it only if the
stock design passed).

### The cause

The Creator 5 has four hotends and **one** filament stepper. FlashForge's
`printer.cfg` still gives all four `[extruderN]` sections the same pins
(`eboard:PB14` step, `PB15` dir, `PB12` enable), and on your printer
`printer.override.cfg` lists them in `[duplicate_pin_override]` so that
Klipper accepts that. The original project's own hardware note says the same
(`docs/notes/10-hardware.md`): the four sections share the step, dir and
enable pins, and only the mounted head is electrically active.
Klipper therefore creates **four stepper objects**, four `config_stepper`
objects in the MCU, all driving one physical step pin and one physical
direction pin.

In Klipper's MCU code the direction pin is not written, it is **toggled**
(`src/stepper.c` of the pinned Klipper, `f51aab7a`):

* a move carries a "direction changed" flag, set when the new direction
  differs from *that stepper object's own* last direction (`SF_LAST_DIR`,
  lines 247 to 250);
* when such a move is loaded, the pin is toggled
  (`gpio_out_toggle_noirq(s->dir_pin)`, line 94);
* the host, for its part, only sends a direction when it differs from that
  stepper's own history (`set_next_step_dir` in
  `klippy/chelper/stepcompress.c`).

Every object starts with a last direction of "backwards" and the pin low
(`config_stepper`, line 204). So with four objects on one pin, the pin level
is the XOR of the four objects' last directions, and each object assumes it
is the only one. The active tool is driven the right way only while an **even
number of the other tools** was last moved forward: in practice, while none
was, that is, while every idle tool was left retracted. If one idle tool was
left forward, the pin is inverted for the tool that is active, and its next
extrusion runs **backwards** and its next retract **forwards**. That is the
retract/extrude problem. (The pin is also written by `stepper_stop`, which
resets that object's history and the pin together; it runs for every object
at an MCU shutdown, so a shutdown cannot create the mismatch.)

Orca retracts before every tool change, so ordinary prints keep the rule.
What breaks it is any time a tool is put away right after it extruded,
without a retract: a print cancelled in the middle of an extrusion, or a
macro that ends on an extrusion and is followed by a park.

### What the single stepper does

`[ff_extruder]` makes `[extruder]` the only owner of the stepper;
`extruder1` to `extruder3` stay logical (their own heaters, temperatures and
motion queues), their repeated pin options are read and ignored, and
`SYNC_EXTRUDER_MOTION` connects the one stepper to the picked tool's motion
queue after every pickup. One object means one direction history, so the XOR
cannot go wrong.

### What I can and cannot show

* **Read in the code:** the toggling in the MCU and the per-object history on
  the host, in the pinned Klipper (files and lines above); that the original
  did not change the pinned Klipper; and that its `ff_extruder` removal is
  justified by a lack of evidence, not by a fix.
* **Read in the board's firmware:** the stock eBoard image (`eBoard.hex` of
  FlashForge's 1.9.7 and 1.9.8 firmware, identical to each other and, but for
  eight bytes of padding, to the `eBoard.bin` of the `mcu-firmware` project)
  is a Klipper MCU build for an stm32f103xe at 144 MHz, built 2026-06-26. I
  disassembled it. Its `queue_step` handler (message 28, `0x08013e41`)
  compares the stepper object's own last direction with the next one and,
  when they differ, flips the last direction and marks the move
  (`0x08013e6c` to `0x08013e86`); the move loader (`0x08013b9c`) then calls
  the pin toggle (`0x08016fb8`: load the GPIO output register, exclusive-or
  the pin's bit, store). That is the logic of `src/stepper.c` above. I did
  not run it on the board. Your printer's eBoard carries that image: Klipper
  logs the board's build when it connects, and on 2026-10-06 it was the same
  one (`20260626_094517-DESKTOP-OQU99DN`).
* **Consistent with it, from your own files:** Reforge's first boot on your
  printer was 2026-09-12 (`anvil/.firmware-config-imported`). On 2026-09-13
  the printer holds backups named `ff_toolchange.py.before_shared_dir_fix`,
  `printer.cfg.before_shared_extruder`, `ff_toolchange.py.before_shared_stepper_fix`
  and `extruder.py.before_shared_pa_fix`, and `reforge-manual-backup/printer.cfg`
  from that day has the step, dir, enable, gear ratio, rotation distance and
  pressure-advance lines of `[extruder1]` to `[extruder3]` commented out.
  That is the single-stepper design, started the day after the first boot;
  `printer.cfg` has the stock sections back since, because `[ff_extruder]`
  makes the repeated pins harmless.
* **Shown on the printer, 2026-10-06:** the test below. With `[ff_extruder]`
  T1 purged normally after T0 had been left extruded; with `[ff_extruder]`
  switched off (the original's design, four stepper objects) T1's purge ran
  the wrong way.
* **Not shown:** which everyday path left a tool extruded when you and others
  first saw the reversed extrusion. The test creates that situation on
  purpose; it does not say how it comes about in a print.

### The test, and its result

It takes two runs (and a reboot in between) and two tool changes each, and it
breaks the "retract before the change" rule on purpose, which is the only way
to see the stock design fail. `gcode/n4s4-extruder-direction-test.gcode` is
the file; its header says what to look at. T0 extrudes 12 mm and is left
extruded, then T1 is picked up and extrudes 12 mm; watch T1.

* **Run A, as deployed (`[ff_extruder]` active):** T1 purged normally.
* **Run B, stock design (`[ff_extruder]` commented out, printer rebooted; a
  restart is not enough because the changed class stays loaded):** T0 purged
  fine and T1's purge was reversed.

That is the evidence the original project asked for, so `ff_extruder` stays.
Had run B gone the right way, the module, its test, the `[ff_extruder]`
section, the sync lines and the `post_m109_macro` hook (the no-tower prime
needs another trigger then) would have been dropped. An issue, or a comment
on #29, is the place to put the result.

### Why a retract before the purge is not the fix

A short retract on the **new** tool before its purge cannot help: every move
of T1 is inverted by the same constant, namely the parity of the other tools'
last directions, and T1's own moves do not change that. Run B already had such
a retract: T1's pickup retracts 0.4 mm in its dock and gives 0.4 mm back at
the target, and the purge was still reversed. What does fix it is the **old**
tool's last move being a retract, which is what Orca does before a change and
what the stock flow does after a purge (`G1 E-5`) and at the end of a print
(`END_PRINT`, `G1 E-1`). That works as long as every path does it; a print
cancelled in mid-extrusion, an aborted macro or a manual extrusion leaves a
tool forward, and every later change is then wrong until the MCU is reset. The
single stepper has no such history.

| Stock design, T0 left extruded, then T1 | T1's moves, as they run |
|---|---|
| purge only | extrude +12: **backwards** |
| dock retract, return unretract, purge (run B) | −0.4 forwards, +0.4 backwards, +12 **backwards** |
| a longer retract before the purge | −1 forwards, +12 **backwards** |
| T0 retracted before it was parked | −0.4, +0.4, +12 all right |
| with `[ff_extruder]` (run A) | all right |

### What the original did about the symptom

Users of the original project reported it in September: after `LOAD_FILAMENT`
on one tool the next tool's motor ran the wrong way until the printer was
switched off and on, while the same tool again was fine. The trigger was
`LOAD_FILAMENT` itself, which ended on the extrusion and then parked the tool.
On 2026-09-24 (`80a4d64`, release 20260827f) the original made it end with a
retract (`G1 E-5`), which is the "old tool's last move" rule above. Every
extruding macro of `main` now ends with a retract (`LOAD_FILAMENT`,
`UNLOAD_FILAMENT`, `PURGE`, the pre-print clean, `END_PRINT`). That fixes the
macros; it does not cover a console `G1 E...` (or a UI button that sends one),
a print cancelled in the middle of an extrusion, or any other path that leaves
a tool extruded. The single stepper covers those too. The printer's installed
release is older than f: its `LOAD_FILAMENT` is the one from releases c to e,
which ends on the extrusion, and `ff_extruder` is what keeps that harmless.

---

## 6. How branch 02 stays on top of the original

1. **The tool change and the macros do nothing unless asked.** The additions
   to `ff_toolchange.py` have class-level defaults and are gated by the N4S4
   commands and options. A bare `FFToolchange`, which is what the original's
   tests build, behaves exactly as it does in `main`. Every test function of
   the original passes here **unchanged** (`test_toolchange_travel.py`,
   `test_toolchange_park.py`, `test_ff_toolchange_mesh.py`,
   `test_klipper_config.py`, ...). Two of its files also gained N4S4 tests at
   their end: `test_klipper_config.py` and `qa/replica/test_custom_scripts.py`.
2. **The redefined macros are guarded** by `test_n4s4_overrides.py`
   (section 2). The same file checks that every macro of the merged
   configuration compiles, and that no macro shares a name with a command a
   module registers (klippy refuses to start on that).
3. **My own tests** are in separate files: `test_toolchange_n4s4.py`,
   `test_ff_toolchange.py`, `test_ff_print_tools.py`, `test_ff_extruder.py`,
   `test_ff_stats.py`, `test_n4s4_overrides.py`,
   `test_n4s4_include_installer.py`, `test_timelapse_config.py`.
4. **To catch up next time:**

   ```sh
   git fetch upstream
   git switch main && git merge --ff-only upstream/master
   git switch n4s4/fixes-and-optimizations-02
   git rebase main           # a conflict, if any, will be in ff_toolchange.py or ff_print.py
   ```

   then run `qa/static` (and the replica lane); `test_n4s4_overrides.py` says
   if one of the six macros needs to be brought in line, and
   `FEATURES_N4S4.md` and this file say what to re-check.

## 7. Decisions of 2026-10-06

1. **`ff_extruder`**: run the test of section 5, and drop it if the stock
   design passes. It did not pass (2026-10-06), so it stays, and it is what is
   deployed.
2. **The second-tool preheat**: dropped on 2026-10-06 ("for now"), **put back
   on 2026-10-07**, together with `next_tool` and `next_nozzle`. It is not on
   the printer yet. **The list of used tools**: kept.
3. **`protect_every_change`**: stays on, so that the pickups of the cleaning
   macros keep the raise and the in-dock retract they had on branch 01. Off
   would follow the original's reading (only a change that restores X or Y);
   the retract before a chute purge is then gone.
4. **A pull request** from branch 02 (or a rewrite of #29, which still
   describes pieces the original now has): not now. First the printer test.
5. **Pushing** `main` and branch 02: not now on 2026-10-06; done on
   2026-10-07, after the multicolour test.

Not decided, no effect: `_NS_BEFORE_PRINT` passes `DEFER_MESH=1` directly,
where the original's way is `variable_defer_mesh: 1`.

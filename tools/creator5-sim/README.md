# Creator 5 toolchange simulator

Runs the pinned FlashForge Klipper fork, our current `ff_toolchange.py`, the
shipped `ff-afc.cfg` macros and pinned AFC against a desktop hardware model.
The actual Klipper C motion planner produces the replay trajectories. No
printer connection or firmware package build is needed.

## Run

Requires Linux, Python 3.12+ with venv support, GCC, Make, and the two pinned
Klipper/AFC tarballs in `vendor/`. The repository's `bin/fetch-assets.sh`
fetches these along with the other build sources. Archive checksums are
verified against `versions.env` before extraction.

From the repository root:

```sh
python3.12 -m venv work/creator5-sim/venv
work/creator5-sim/venv/bin/python3.12 -m pip install -r tools/creator5-sim/requirements.txt
work/creator5-sim/venv/bin/python3.12 tools/creator5-sim/run.py
```

Open **`work/creator5-sim/replay.html`** and choose a scenario from its
**Scenario** dropdown. The single standalone page contains the whole catalog:
no server is needed for playback or switching. Each selection resets playback
and shows that scenario's movements, tool-pair timings, Z timeline and warnings.
The URL fragment remembers the selection, so `replay.html#brush` opens the brush.

All scenario definitions live in `scenarios.py`; `CURRENT_REPLAYS` selects the public list. Baselines, superseded speed experiments, and redundant brush variants remain test-only fixtures, excluded from the dropdown and `--list`. There are no
individual scenario G-code files or replay pages. Runtime configs, G-code and
logs are temporary; the shared `traces.json` retains each scene's input,
configuration, logs, source hashes and trace. `selections.csv` exports the
selection timings with a scenario column.

```sh
work/creator5-sim/venv/bin/python3.12 tools/creator5-sim/run.py --list
work/creator5-sim/venv/bin/python3.12 tools/creator5-sim/run.py --scenario brush
```

The default command runs the current public catalog (16 scenes). `--scenario NAME` updates one scene
in the same replay and keeps the other current scenes. Retired entries are pruned from cached results. Explicit CLI settings override a
scene's defaults. Deliberate failure scenes show “Expected failure verified”
when the expected error is observed; unexpected failures exit nonzero.

The warm swap scenario picks up T0, then swaps T0→T1→T2→T3→T0. Every swap checks
the physical model's head, Klipper's sensor-derived identity, AFC's selected
lane, and the restored nozzle coordinates. A command failure stops the
scenario after cleanup and produces a failure replay. Failed scenarios exit
nonzero.

## Current optimized movements

The public replay uses the shorter dock approaches and optimized brush travel.
The current simulator scenes use the optional gentle contact profile described
below. Factory feeds remain available through hidden regression fixtures. The brush uses 200 mm/s for clear travel
and 100 mm/s for rubber contact and crossing the clearance boundary.

AFC returns are routed through the dock clearance corridor after applying the
incoming tool's offset. An outside destination is reached by changing Y at
machine X≤`x_safe`, then moving X outward at the destination Y. An outside
start retracts X first. The original unsafe T0→T3 outside return, which crossed
T2's Y180 row at X271.98, is now covered by a passing regression and a separate
injected-fault test that still rejects the bad move before planning.

The fix is in the shipped `ff_toolchange` move transform, scoped to AFC's
`Restoring` state; it is not a simulator-only bypass.

Use `--overrides path/to/calibration.cfg` for real per-tool `dock_x`, `dock_y`,
`nozzle_x/y/z`, `z_adjust`, and `[ff_toolchange]` or `[printer]` settings.
Supply ordinary config sections; extract calibration entries from a printer's
SAVE_CONFIG block into those sections. The override file is copied into the
run directory. The simulator reads axis dimensions, rotation distances,
microsteps and motion limits directly from our shipped `printer.base.cfg`.

The dropdown also includes cold-head heating, AFC mapping, empty-carriage T2
pickup, missing grab sensors, failed unlocks, brush approach/mode variants,
T3 brush clearance, low/high print-height returns, and the Z-limit failure case.

`--sensor-delay` is seconds and defaults to 50 ms; `--heating-rate` is °C/s
and defaults to 3. These are assumptions to calibrate from printer
measurements. Motor commands use production distances, speeds and synchronization. Native
toolchanger macros from `ff-toolchange.cfg` raise gear acceleration by 25%
(first lock/unlock 1.875, second lock 1.25). `--lock-time SECONDS` explicitly switches
back to the simplified fixed-delay model for experiments. All simulator
settings apply only to the generated desktop config.

## Verify

The simulator lane is opt-in, so a normal `make qa` does not require desktop
simulator dependencies. Run with a host Python that has pytest installed:

```sh
python3 -m pytest -q qa/simulator --run-simulator -o cache_dir=work/creator5-sim/pytest-cache
```

`CREATOR5_SIM_PYTHON` can select another prepared Python environment. The
tests cover every directed pair of heads, return coordinates with differing
tool offsets, AFC remapping, same-head selection, cold-head waiting at the
dock exit, speed comparisons, missing grab sensors and failed unlocks.

## Brush movements outside the normal print region

`brush.cfg` contains `AFC_BRUSH` with an explicit staged return added to the supplied wiping sequence. Run its brush,
print-travel recovery and subsequent swap scenario with:

```sh
work/creator5-sim/venv/bin/python3.12 tools/creator5-sim/run.py --scenario brush
```

The simulator inherits the physical travel limits from the shipped config:
X −5…310, Y −5…260, Z −10…270 mm. These already include X271 and Z−0.5;
they are not artificially widened to hide out-of-range errors. The replay
now draws the full XY range, the rubber at Y7 / X264…271, the X245 clearance
line, and a Z timeline with the region below Z0 highlighted.

The tests start at X240, X245 and X280, in absolute and relative modes.
They verify both wipe passes, vertical exit through Z0/1/3/5 before XY
travel, coordinate/extrusion mode and feed-rate restoration, the following
print move, and a tool swap after that move. Raising from Z268 to Z273 still
fails the real Klipper limit check rather than expanding Z270.

The original `RESTORE_GCODE_STATE` without `MOVE=1` only restored modes,
leaving the nozzle at X245 Y7 Z5. The scenario's following combined XYZ move
then returned diagonally, which could lower the nozzle during travel when
resuming a low print layer. The macro now saves the starting nozzle XYZ and
returns explicitly: raise vertically to at least saved Z + 5 mm, finish that
move, return XY inside the clear region (or return Y at the X245 boundary before
returning an outside X), finish XY travel, and finally lower vertically to saved Z. Only
then does it restore the saved coordinate/extrusion modes and feed rate.

The X245 approach and exit also account for the active X coordinate frame.
Thus the T3 fixture's +0.3 mm offset no longer produces machine X245.3 during
Y travel. The simulator rejects Y travel beyond that boundary, combined XY/Z
brush moves, and XY travel below nozzle Z5 outside the defined rubber wipe
strokes. Rejected commands are recorded in `brush_warnings` before reaching
the planner; an unexpected violation fails the scenario. These are
coordinate-based clearance checks, not a physical collision model.

The catalog includes return-to-Z1 and return-to-Z100 scenes. Tests verify the
actual planned trajectory has no mixed XY/Z brush segments and reaches zero
XY velocity before starting the final Z descent. Absolute and relative print
continuations now start at the restored print XYZ. These changes affect the
simulator's brush macro; nothing has been installed onto the real printer.

### Optimized clear travel

Brush approach and return use diagonals only when both machine-X endpoints
are at or below 245; the full straight segment then stays in that half-plane.
An outside start first moves X alone to the boundary. An outside return first
moves Y alone at that boundary, then moves X. The active tool frame is included
in both decisions. Z remains fixed throughout XY travel, and XY stops before
the final descent. Starts below Z0 first lift to at least nozzle Z5.

Long clear travels use 200 mm/s. Rubber contact strokes and movement across
the X245 boundary retain 100 mm/s. Pickup retreat and lock settings are unchanged.
Regression tests retain matching baseline/optimized brush fixtures at X150 and
X280; the baselines are hidden from the public replay list. The baseline sets `AFC_BRUSH.travel_speed=100` and `shortcuts=0`; the
optimized scenes use 200 and 1. Measured brush-only durations in the fixture:

| Starting X | Baseline | Optimized | Saved |
|---|---:|---:|---:|
| 150 | 7.823 s | 4.783 s | 3.040 s |
| 280 | 6.626 s | 5.202 s | 1.423 s |

Negative tests deliberately inject unsafe diagonals entering/leaving the
obstacle area, an offset-dependent boundary violation, mixed XY/Z descent,
and departure from the rubber without vertical clearance. Each must fail
before the unsafe command reaches the motion planner. These comparisons are
simulation results; absolute real-printer timing remains uncalibrated.

### Gentle rubber contact and fast clear travel

Gentle seating is the only dock motion; there is no factory trajectory to
switch back to. The shipped contact span is 2.5 mm depth plus a 2.5 mm
margin, D−5 to D, where D is each calibrated dock X; the public scenes keep
their older 20 + 5 mm trial span and 70 mm/s contact speed. Geometry overlapping `x_safe` is rejected before any dock motion.

The public simulator trial uses `contact_speed: 70` mm/s; the shipped
hardware default remains 20 mm/s. The approach uses normal acceleration and
fast feed on the clear rail, slowing to the contact cap before D−25.
Rubber travel uses normal acceleration until D−16.33. The configured
`ease_distance: 5` is a minimum: the effective distance automatically grows
to at least speed²/(2 × gentle acceleration), avoiding abrupt braking inside rubber.
From there, **one move** to D keeps the contact speed cap and lowers
acceleration to 150 mm/s². The final move enters at 70 mm/s and decelerates through 16.33 mm to zero. There is no pause or
separate crawl. Lock/unlock follows the completed stop.

Pickup runs both lock commands at the dock: the second one's
`MANUAL_STEPPER` flushes the lookahead, so the app's 20 mm pullback between
them stopped the head dead; with `SYNC=0` the lock now finishes during the
exit. The tool leaves the contact span at `withdrawal_speed` and the
contact acceleration, then carries on at the travel feed and acceleration
with no stop at the transition. Successful unlock permits empty-carriage withdrawal at the
normal fast feed and acceleration. A pickup attempt that never actuated the
lock may also back out empty at the fast feed. Failed unlock never uses the
empty fast exit.

Seating and withdrawal use constant-acceleration trapezoids, not
continuous jerk limiting. The profile temporarily
sets `minimum_cruise_ratio=0`, then restores it precisely, including on error.

The replay shows rubber spans in pink, the seated endpoint in yellow, and a
planned velocity graph. Select 30 mm/s to inspect contact and seating, or
150/500 mm/s for braking and free travel. Contact shading excludes empty
withdrawal, whose speed is intentionally unrestricted by the nozzle-contact
cap. Tests inspect planned contact speed and acceleration, seating as one
move, endpoint stop, actuation order, carried versus empty exit, retries,
parameter restoration, and contact geometry. A factory-profile control must
fail the contact-speed check. All parameters are simulation trial settings;
force limits and absolute real-printer timing remain uncalibrated.
Nothing has been deployed.

The timing table separates AFC completion, carriage return, and all scheduled
lock-motor motion complete. Playback includes asynchronous motor tails, and
CSV exports expose the same timestamps. These are planned motor times;
finishing pulses does not establish successful physical locking.

### Current release policy

Async unlock is disabled in every public replay and on hardware. Release
waits for the endpoint dock sensor before starting the stock endstop-monitored
operation. The native toolchanger uses `FF_MOTOR_GRAB`, `FF_MOTOR_GRAB2` and
`FF_MOTOR_RELEASE` from the shipped `ff-toolchange.cfg`. They preserve the
stock strokes, speeds, current references, endstop check and delayed current
reduction, and increase only gear acceleration by 25%: 1.5 → 1.875 for the
first lock/unlock and 1.0 → 1.25 for the second lock. Changing the
`[manual_stepper gear_stepper] accel` option alone would not affect these
moves, because each command specifies its own `ACCEL`.

The simulator imports those exact native macro bodies from the shipped
configuration. `motor.cfg` retains the stock driver, pin fixture and reference
macros. Public scenes use the actual native motor planner rather than the
previous 1 s unlock-response estimate; real timing remains uncalibrated after
the acceleration change. The old file-output async experiment remains behind
an explicit `--async-unlock 1 --unlock-duration 1` flag for regression coverage,
but it is not an implementation for hardware and is excluded from the default
replays' timing previews. Pickup locking still includes its second motor stage.

### Apply and test on an already-modded printer

Back up the loaded `ff-toolchange.cfg` and `ff_toolchange.py` first. For the
25% gear-only change, merge the three `FF_MOTOR_*` macro sections and the
`grab_macro`, `grab2_macro`, `release_macro` selections from this repository's
`pkgs/klipper-config/payload/config/ff-toolchange.cfg` into the printer's
loaded copy. Stock `printer.motor.cfg` does not need to be edited.

To apply the movement changes as well, copy the updated
`pkgs/klipper/payload/klipper/klippy/extras/ff_toolchange.py` to
`/usr/data/anvil/klipper/klippy/extras/ff_toolchange.py` and merge the following
into the existing `[ff_toolchange]` section of
`/usr/data/anvil-data/config/ff-toolchange.cfg`. Those are this mod's standard
paths; use the files loaded by your running Klipper installation if different.
Keep the unit's dock and nozzle calibration in `printer.cfg`.

```ini
contact_depth: 20
contact_margin: 5
contact_speed: 70
withdrawal_speed: 35
contact_accel: 150
ease_distance: 5
grab_macro: FF_MOTOR_GRAB
grab2_macro: FF_MOTOR_GRAB2
release_macro: FF_MOTOR_RELEASE
```

The actual gentle braking span becomes 16.33 mm automatically. Check that
this unit's `x_safe` corridor and rubber depth/margin match these settings.
The declared support depth of about 50 mm is not used to release early.
No MCU firmware change or async-unlock setting is needed.

First verify the gear-only profile with the existing movement settings. Then
enable the movement profile above and test a few supervised changes from a
clear central position, checking full seating, lock engagement and sensor
confirmation. Use the native mechanical selector for the first test:

```gcode
FIRMWARE_RESTART
TOOLCHANGE_STATUS
G28
G90
G1 Z20 F1500
G1 X150 Y150 F6000
SELECT_TOOL T=0
SELECT_TOOL T=3
SELECT_TOOL T=0
TOOLCHANGE_STATUS
```

After those succeed, test AFC's `T0`, `T3`, `T0` from the same position with
the normal tool mapping, then the remaining tools. Record several warm,
loaded T0→T3 and T3→T0 changes, using the same temperature and starting
position for comparison. Include AFC return completion when timing.
The 25% acceleration increase is not a 25% time reduction: native planner
estimates save about 0.09 s per swap, largely in unlock and the first lock;
the second lock is mostly constant-speed motion.

The optimized brush is still a separate macro edit: merge `AFC_BRUSH` and
`_AFC_BRUSH_RETURN` from `tools/creator5-sim/brush.cfg` into the existing brush
configuration if applying that work too. The toolchanger Python/config edit
alone does not install the brush. Restore the backed-up files and restart
Klipper to revert the trial.

## What is modeled

### T0 to T3 trajectory comparison

The approach to safe X / dock Y is one move when its entire straight segment
stays at machine X≤`x_safe`, ending in a quarter arc of `corner_radius` into
the dock lane. Starting outside that corridor retains the X-only retreat
before moving Y.

For the center-start fixture, the release approach changes from
(150,150)→(250,150)→(250,20), a 230 mm path, to
(150,150)→(250,20), about 164 mm. The dock-to-dock Y traverse, engagement
staging, pullback and pickup retreat remain unchanged. The return-to-print
path is now guarded through the corridor when the saved destination is outside it.
The option defaults to false in the shipped extra and is enabled only in
these simulator candidate scenes. It has not been enabled on a real printer.

The comparison also covers every directed tool pair, X280 outside starts,
and a T3 offset making nominal X249.9 become machine X250.2. The hardware
model rejects Y travel outside the configured dock corridor; deliberate bad
shortcut tests must fail before their commands reach the planner. The dock
corridor uses stock X250, distinct from the brush tray's X245 boundary.
Failed grab and unlock tests verify that the shortened trajectory cannot
resume printing after a hardware failure. Absolute real timing is still
uncalibrated.

Brush demo continuations now use explicit F12000 for the move to the center.
The before/after macro-state checks still assert restoration of F1234, so the
demo no longer inherits that deliberately slow regression-test feed.

The hardware model derives dock contact from carriage coordinates, updates
dock occupancy as locked heads move, simulates lock/unlock actuation, and
delays sensor observations. The shipped polling/retry/error logic makes the
decisions. Sensor polling waits advance the planned printer clock rather
than sleeping on the desktop. Heating/cooling follow configurable constant
rates; the shipped temperature-wait macro runs against those temperatures.

`motor.cfg` retains the production motor macro bodies from the stock
`printer.motor.cfg` unpacked in this checkout (source SHA256
`096703aa0cf601047071c95ac92e977849b71e3df2895ce6fae4da148512ef69`).
Physical pins and the PWM backend are adapted for desktop file-output.
The real Klipper manual-stepper planner computes the current 0.07 mm lock
movement (about 0.413 s) and asynchronous 0.03 mm second movement (1.516 s). The
second movement overlaps carriage travel. Unlock uses the planned full
0.1 mm stroke (about 0.462 s), with the physical endstop modeled as a
successful full-stroke trigger, or a failure when requested.

These fixes still do not reproduce the reported real-printer log
`Tool Change - None -> e2`, `Tool swap done ... t:7.169`, `Total ... t:7.201`.
With an empty carriage, the synthetic fixture, and no heating wait, the
corresponding simulated AFC phase is about 2.595 s. That mismatch remains
unresolved: the live head's temperature, actual dock/velocity configuration,
physical mounted-head state, pending motion and hardware waiting must be
compared before treating absolute timings as calibrated predictions. AFC's
current-lane `None` alone does not establish an empty carriage.

Klipper's file-output backend supplies the MCU boundary. A compiled Linux
MCU dictionary lets it generate real step commands without starting an MCU.
The desktop launcher changes the C helper compiler from MIPS GCC to host GCC,
supplies the vendor MCU version response, and suppresses the native board
wake-up command on disconnect. The pinned Klipper and AFC source trees are not rewritten; our shipped toolchange overlay supplies the return-path fix.

A temporary loopback HTTP server supplies Moonraker's persistence responses
so AFC's real PREP and mapping logic run. It does not emulate Moonraker's
printer API or serve Mainsail. Spoolman and filament loading mechanics are
outside this model; all four heads start with filament loaded. Bed mesh,
probing and physical homing are also outside the model: scenarios explicitly
set the initial homed position.

The default dock positions and nozzle offsets are a synthetic calibration
fixture. A replay is not a collision/force model and cannot establish how
quickly the real lock seats, whether a faster retreat loses a head, or how a
nozzle oozes. Planned motion times include Klipper's queue behavior; AFC's
own wall-clock statistics measure desktop execution and should not be used
as simulated printer timings. Use the trace/replay clock for comparisons.

# How a print runs

The mod is built so that **the same file prints on both firmwares**. A stock
FlashForge Orca profile, untouched, and files you sliced before you flashed:
they run here without a re-slice and without a line pasted into Machine start
G-code.

That is not a coincidence, and it is worth understanding if you intend to
change anything — because the reason it works is that something now does, in
Klipper, what FlashForge's application used to do outside it.

---

## Why the same file works

A FlashForge Orca profile is written for a machine whose touchscreen has
already prepared it. Its start block heats without waiting, runs fans, and
makes `G1 Z5 F2400` its first motion — on axes nothing has homed. There is no
`G28` in it and no `M190`. On stock that is fine: the app homed, soaked the
bed and grabbed the first tool before the file was ever handed to Klipper.

Delete the app and that file is dangerous. So `[ff_print]` takes over
`SDCARD_PRINT_FILE` and `M23`, reads the bed and nozzle temperatures, the
first tool and the first-layer height **out of the file itself**, and runs the
whole preparation before the file's first line executes.

The file did not change. What changed is that the preparation moved from an
application nobody else could talk to into Klipper macros anyone can read.

---

## The two flows

**Stock, driven by the touchscreen:**

```
touchscreen: pick a file, Print
  │
  ├─ app: prepareForEddy ──── home, probe checks, heat bed + chamber,
  │                           tool-presence gate, nozzle clean per tool,
  │                           5-minute soak, re-home Z, bed mesh, grab tool
  ├─ app: absolute Z offset ─ the ~3.2 mm eddy-to-nozzle gap, once per print
  └─ app: serialPrint ─────── M21 → M23 → M26 S0 → M24
        │
        ▼
     Klipper streams the file
        │
        ├─ a bare `T2` ─────► the fork raises a flag and WAITS for the app
        │                     to perform the change
        └─ pause / resume / cancel ─► the app sends each sequence itself
```

**Reforge, driven by whatever you like:**

```
Mainsail, HelixScreen, OrcaSlicer — anything that speaks Moonraker
  │
  └─ [ff_print] wraps SDCARD_PRINT_FILE and M23
        │   reads bed, nozzle, first tool and first-layer height from the file
        │
        ├─ FF_BEFORE_PRINT_START ─┬─► _FF_PREFLIGHT  calibration + tool gate
        │                         └─► START_PRINT    the preparation sequence
        ▼
     Klipper streams the file
        │
        ├─ a bare `T2` ─────► ff_toolchange performs the change
        └─ PAUSE / RESUME / CANCEL_PRINT ─► macros in ff-print-macros.cfg
        │
        ▼
     FF_AFTER_PRINT_END ──► END_PRINT   the exit block
```

The shape is the same. The difference is that every box in the second diagram
is a Klipper macro or a Klipper extra, and every box in the first was inside
one binary.

Step by step, with the app's own sequence, the fork code that makes a
Mainsail print hang, and what `START_PRINT` actually sends:
[The print pipeline](print-pipeline.md).

---

## Changing what it does

Everything below goes at the **end of your `printer.cfg`**, restating only
what you are changing. Klipper merges same-named sections and the last value
wins, so the shipped defaults stay for everything you leave out, and your
overrides survive every update. Never edit the `ff-*.cfg` files themselves —
an update replaces them without asking.

### Turn off the automatic preparation

If your own profile already calls `START_PRINT`, or homes for itself:

```ini
[gcode_macro FF_BEFORE_PRINT_START]
variable_prepare: 0
```

Preparing twice misplaces nothing — the Z offset and `T<n>` are both
idempotent — but it re-homes and re-purges every tool for no reason. A
profile that runs its own `G28` **needs** this off: our `G28` docks a mounted
tool before homing Z, so it would dock the tool preparation just grabbed and
print with an empty carriage.

### A file that does not fit the machine

Before anything heats or moves, `[ff_print]` reads the whole file once for the
positions it moves to and refuses it if it goes beyond what the machine
reaches: both ends of X and Y, and the top of Z. The check is made in machine
coordinates, with the offset of the tool that is mounted at each point added,
because that is what Klipper checks: a tool whose nozzle sits 0.4 mm further
out has a limit 0.4 mm closer. Mainsail's console says which axis and by how
much:

```
ff_print: not starting job.gcode: the file moves beyond what the printer
reaches: Y up to 263.1 mm (G-code Y262.696 and T1's offset +0.414) -- the
machine reaches 260.0 mm. Move the object or the prime tower in the slicer
and slice again (check_bounds: False in [ff_print] turns this check off).
```

Without it the same file runs until its first move out of range: on
2026-10-14 that was two minutes in, with the nozzles hot and the mesh probed,
for a prime tower that was 0.018 mm too far out.

The reading takes about a second per megabyte (2.9 s for a 3 MB file) and
goes on in a thread, so Klipper is not held up. A file it cannot read in 40 s
is started unchecked. Relative stretches (`G91` to the next `G90`) are not
positions and are skipped, arcs count by their end points, and the range is the
axis range Klipper reports, so a changed `position_max` is followed. The
extents it found are in `printer.ff_print.extents`. To turn the check off:

```ini
[ff_print]
check_bounds: False
```

### Drive the sequence from the slicer

With `prepare: 0`, call it yourself from Machine start G-code:

```gcode
START_PRINT TOOLS=0:220,2:240 BED=60 LEVEL=1 SOAK=300
```

| Parameter | Does |
|---|---|
| `TOOLS=0:220,2:240` | every tool the file uses, with its clean temperature. A bare `TOOLS=0,2` also works |
| `BED=`, `NOZZLE=`, `LAYER=` | the print's bed, nozzle and first-layer height |
| `CLEAN=0` | skip the pre-print purge and wipe |
| `LEVEL=1` | probe a fresh mesh instead of loading the saved one |
| `SOAK=<seconds>` | dwell after the bed reaches target. The app waited 5 minutes; the default here is 0 |
| `DEFER_MESH=1` | stop after the clean and the soak; the file's own `ADAPTIVE_MESH` does the rest (below) |

### Mesh only where the print is

A mesh over the whole bed spends most of its time under nothing. With a
profile that labels objects, the mesh can cover only the objects and the prime
tower. Tell the automatic preparation to leave the mesh to the file:

```ini
[gcode_macro FF_BEFORE_PRINT_START]
variable_defer_mesh: 1
```

and call these from Machine start G-code, after Orca's own object
definitions:

```gcode
{if has_wipe_tower}
DEFINE_PRIME_TOWER_OBJECT X=[wipe_tower_x] Y=[wipe_tower_y] WIDTH=[prime_tower_width] BRIM=[prime_tower_brim_width] ROT=[wipe_tower_rotation_angle]
{endif}
ADAPTIVE_MESH TOOL=[initial_extruder] NOZZLE=[nozzle_temperature_initial_layer] BED=[bed_temperature_initial_layer_single] LAYER=[layer_height]
M109 S[nozzle_temperature_initial_layer] T[initial_extruder]
```

`ADAPTIVE_MESH` parks, homes Z, probes the mesh, grabs the first tool, sets
and the print offset.
`DEFINE_PRIME_TOWER_OBJECT` uses the tower the file actually prints rather
than the numbers Orca expands into the start G-code, which in a multi-plate
project can belong to another plate. `ADAPTIVE_MESH_TOGGLE` (a tile in
Mainsail) switches probing off, and the saved `MESH_DATA` is loaded instead.
It is enabled again after a restart.

---

## What the printer counts

Each job is timed phase by phase (homing, heating, mesh, purge, tool
changes, printing), the filament of every tool is measured, and tool
changes are counted with their duration and failures. How to read that, and
what it does and does not cover, is on the [Statistics](statistics.md) page.

---

## Where to read the real thing

The macros are on the printer at `/usr/data/anvil-data/config/ff-print-macros.cfg`, and
in this repo at
[`pkgs/klipper-config/payload/config/ff-print-macros.cfg`](../pkgs/klipper-config/payload/config/ff-print-macros.cfg).
They are ordinary Klipper macros with comments explaining why each step is
what it is.

The app's own sequences, recovered from the binary with addresses, are in
[Print lifecycle](notes/50-print-lifecycle.md), and the fuller division of
labour between application and Klipper is
[firmwareExe vs Klipper](notes/25-app-vs-klipper-ownership.md).

# OrcaSlicer machine and filament settings

This document describes the OrcaSlicer settings used with the Creator 5
toolchanger, adaptive bed mesh, Prime Tower registration, and print-scoped Z
offsets in this change set.

The examples assume that the corresponding macros and Klipper extras from this
branch are installed on the printer.

## Printer profile: machine start G-code

In OrcaSlicer, open:

**Printer settings > Machine G-code > Machine start G-code**

Use the following start G-code:

```gcode
;start_gcode

M140 S[bed_temperature_initial_layer_single]
M106 P101 S0
M106 P2 S0
M191 S0
M106 S0
M106 P3 S0
M104 S[nozzle_temperature_initial_layer]

G90
M83

; ------------------------------------------------------------
; Register the active Prime Tower as an additional Klipper
; exclude_object so Adaptive Bed Mesh also covers the tower.
; ------------------------------------------------------------

{if has_wipe_tower}
DEFINE_PRIME_TOWER_OBJECT X=[wipe_tower_x] Y=[wipe_tower_y] WIDTH=[prime_tower_width] BRIM=[prime_tower_brim_width] ROT=[wipe_tower_rotation_angle]
{endif}


; ------------------------------------------------------------
; Relieve nozzle pressure before the hot tool is parked for
; Adaptive Bed Mesh.
; ------------------------------------------------------------

G1 E-5 F600
M400

; ------------------------------------------------------------
; Generate an adaptive mesh covering all normal print objects
; plus the registered Prime Tower.
; ------------------------------------------------------------

{if curr_bed_type=="Smooth Cool Plate"}
ADAPTIVE_MESH TOOL=[initial_extruder] NOZZLE=[nozzle_temperature_initial_layer] BED=[bed_temperature_initial_layer_single] LAYER=[layer_height] MARGIN=16 PLATE=SMOOTH_COOL
{elsif curr_bed_type=="Cool Plate"}
ADAPTIVE_MESH TOOL=[initial_extruder] NOZZLE=[nozzle_temperature_initial_layer] BED=[bed_temperature_initial_layer_single] LAYER=[layer_height] MARGIN=16 PLATE=SMOOTH_COOL
{elsif curr_bed_type=="Smooth High Temp Plate"}
ADAPTIVE_MESH TOOL=[initial_extruder] NOZZLE=[nozzle_temperature_initial_layer] BED=[bed_temperature_initial_layer_single] LAYER=[layer_height] MARGIN=16 PLATE=SMOOTH_HIGH_TEMP
{elsif curr_bed_type=="High Temp Plate"}
ADAPTIVE_MESH TOOL=[initial_extruder] NOZZLE=[nozzle_temperature_initial_layer] BED=[bed_temperature_initial_layer_single] LAYER=[layer_height] MARGIN=16 PLATE=SMOOTH_HIGH_TEMP
{elsif curr_bed_type=="Textured Cool Plate"}
ADAPTIVE_MESH TOOL=[initial_extruder] NOZZLE=[nozzle_temperature_initial_layer] BED=[bed_temperature_initial_layer_single] LAYER=[layer_height] MARGIN=16 PLATE=TEXTURED_COOL
{elsif curr_bed_type=="Textured PEI Plate"}
ADAPTIVE_MESH TOOL=[initial_extruder] NOZZLE=[nozzle_temperature_initial_layer] BED=[bed_temperature_initial_layer_single] LAYER=[layer_height] MARGIN=16 PLATE=TEXTURED_PEI
{elsif curr_bed_type=="Structured PEI Plate"}
ADAPTIVE_MESH TOOL=[initial_extruder] NOZZLE=[nozzle_temperature_initial_layer] BED=[bed_temperature_initial_layer_single] LAYER=[layer_height] MARGIN=16 PLATE=TEXTURED_PEI
{elsif curr_bed_type=="Engineering Plate"}
ADAPTIVE_MESH TOOL=[initial_extruder] NOZZLE=[nozzle_temperature_initial_layer] BED=[bed_temperature_initial_layer_single] LAYER=[layer_height] MARGIN=16 PLATE=ENGINEERING
{elsif curr_bed_type=="Cool Plate (SuperTack)"}
ADAPTIVE_MESH TOOL=[initial_extruder] NOZZLE=[nozzle_temperature_initial_layer] BED=[bed_temperature_initial_layer_single] LAYER=[layer_height] MARGIN=16 PLATE=SUPERTACK
{else}
ADAPTIVE_MESH TOOL=[initial_extruder] NOZZLE=[nozzle_temperature_initial_layer] BED=[bed_temperature_initial_layer_single] LAYER=[layer_height] MARGIN=16 PLATE=DEFAULT
{endif}

; ------------------------------------------------------------
; Restore the initial print tool and wait for print temperature.
; ------------------------------------------------------------

G1 Z5 F2400
T[initial_extruder]
M109 S[nozzle_temperature_initial_layer] T[initial_extruder]

; ------------------------------------------------------------
; Print the purge line close to the current print objects and
; inside the adaptively meshed area.
; ------------------------------------------------------------

PURGE_NEAR_OBJECT GAP=12 MARGIN=16 Z=0.2 E=25 F={filament_max_volumetric_speed[initial_no_support_extruder]/2.4053*60}

;start_gcode end
```

### Prime Tower geometry

`DEFINE_PRIME_TOWER_OBJECT` must be emitted before `ADAPTIVE_MESH`. It adds the
active Prime Tower to Klipper's `exclude_object` geometry so the adaptive mesh
and the near-object purge line include it.

`DEPTH=` is intentionally omitted. Orca's start-G-code placeholders do not
provide a reliable independent tower depth. `ff_print.py` parses the active
plate's generated G-code and supplies the actual tower X/Y position, width,
depth, brim, rotation, center, and outline. The values passed by the start
G-code are compatibility fallbacks; if parsed depth metadata is unavailable,
the macro falls back to `DEPTH=WIDTH`.

This also avoids using a Prime Tower position belonging to another plate in a
multi-plate Orca project.

### Build-plate mapping

The start G-code translates Orca's display names to stable macro identifiers:

| Orca build plate | `PLATE` value |
| --- | --- |
| Smooth Cool Plate | `SMOOTH_COOL` |
| Cool Plate | `SMOOTH_COOL` |
| Smooth High Temp Plate | `SMOOTH_HIGH_TEMP` |
| High Temp Plate | `SMOOTH_HIGH_TEMP` |
| Textured Cool Plate | `TEXTURED_COOL` |
| Textured PEI Plate | `TEXTURED_PEI` |
| Structured PEI Plate | `TEXTURED_PEI` |
| Engineering Plate | `ENGINEERING` |
| Cool Plate (SuperTack) | `SUPERTACK` |
| Any unknown plate | `DEFAULT` |

The actual build-plate corrections remain printer-side in
`[gcode_macro _BUILD_PLATE_OFFSETS]`. For example:

```ini
[gcode_macro _BUILD_PLATE_OFFSETS]
variable_smooth_cool: 0.000
variable_smooth_high_temp: 0.000
variable_textured_cool: 0.000
variable_textured_pei: 0.030
variable_engineering: 0.000
variable_supertack: 0.000
```

Positive values raise the nozzle and reduce first-layer squish. Negative
values lower the nozzle and increase first-layer squish.

### Adaptive-mesh switch

The machine start G-code always calls `ADAPTIVE_MESH`. Its behavior can be
selected in Mainsail without changing or re-slicing the file:

```gcode
ADAPTIVE_MESH_TOGGLE ENABLE=1
```

probes a fresh adaptive mesh for the next print, while:

```gcode
ADAPTIVE_MESH_TOGGLE ENABLE=0
```

skips probing and loads the saved `MESH_DATA` profile. Display the current
selection with:

```gcode
ADAPTIVE_MESH_STATUS
```

The adaptive-mesh selection is session-local and defaults to enabled after a
Klipper restart.

## Filament profile: material Z offset

Open each Orca filament profile and select:

**Filament settings > Advanced > Filament start G-code**

Add one command containing that filament profile's correction. Current PLA
example:

```gcode
; Filament gcode
; PLA (default)
TOOLCHANGE_SET_MATERIAL_OFFSET VALUE=0.030
```

Create an independently calibrated value for every other filament profile,
for example:

```gcode
; Filament gcode
; PETG -- replace 0.000 with the calibrated value
TOOLCHANGE_SET_MATERIAL_OFFSET VALUE=0.000
```

Do not place this command in **Change extrusion role G-code**. It belongs to
the filament profile and is applied whenever Orca activates that filament.
Each tool in a multi-material job may therefore use a different material
offset.

`VALUE` is the absolute value of the material component, not an incremental
babystep. Repeating `VALUE=0.030` leaves the material component at `+0.030`;
it does not add another `0.030` on every tool change.

Positive values raise the nozzle and reduce squish. Negative values lower the
nozzle and increase squish. The accepted range is `-0.500` to `+0.500` mm.

## How the Z corrections combine

The print-scoped Z correction is calculated as:

```text
print Z = temperature + hot-bed + thin-layer + build-plate + material
```

Tool calibration (`nozzle_z - station_z` and per-tool `z_adjust`) remains a
separate part of each tool's coordinate frame. A live display babystep is also
kept separate, so neither ending a print nor replacing a material value erases
the operator adjustment.

With the current example values:

```text
Textured PEI build plate: +0.030 mm
PLA material profile:     +0.030 mm
Plate + material:         +0.060 mm
```

The nozzle therefore runs `0.060 mm` higher than the plate/material reference,
before the automatically calculated temperature, bed-temperature, and thin-
layer terms are included. At a 220 °C nozzle and with the current temperature
coefficient of `0.000450 mm/°C`, the temperature term is another `+0.045 mm`,
giving `+0.105 mm` total when the bed and layer terms are both zero.

This additive design lets the build plate be calibrated once with a reference
filament and each filament be calibrated once on a reference plate. Do not
copy the same physical correction into both components unless the combined
effect is intentional.

## Verification

After slicing, search the generated G-code for:

```text
DEFINE_PRIME_TOWER_OBJECT
ADAPTIVE_MESH ... PLATE=...
TOOLCHANGE_SET_MATERIAL_OFFSET VALUE=...
```

At print start, the Mainsail console reports the selected plate correction and
the complete print Z calculation. During a print, use:

```gcode
TOOLCHANGE_STATUS
```

to inspect the active print offset. `GET_POSITION` reports Klipper's normal
G-code/homing coordinate state and does not expose the separate toolchanger
transform, so a zero `gcode homing` value there does not mean these corrections
are inactive.

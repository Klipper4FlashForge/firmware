; ====================================================================
; Creator 5 -- does a tool change invert the extruder direction?
;
; Four [extruderN] sections drive ONE stepper. FlashForge's printer.cfg
; gives them the same step, dir and enable pins, so Klipper creates four
; stepper objects on one physical pin pair. Klipper's reference MCU code
; does not write the direction pin, it TOGGLES it: a move that changes
; direction relative to that stepper object's own last move flips the pin
; (src/stepper.c). With one object per tool, the pin level is the XOR of
; all four objects' last directions, and the active tool is driven the right
; way only while an even number of the OTHER tools was last moved forward;
; in practice, while none was, i.e. while every idle tool was left
; retracted. Orca retracts before every change, so ordinary prints keep
; that true. This file breaks the rule on purpose, which is the only way to
; see the difference.
;
; That is read from Klipper's reference MCU code and from the disassembled
; eBoard image of FlashForge's firmware (UPSTREAM_STATUS_N4S4.md, section 5).
; RESULT on a Creator 5, 2026-10-06: with [ff_extruder] T1 purged normally;
; without it (the stock design) T1's purge ran the wrong way.
;
;   T0 extrudes 12 mm and is LEFT extruded, then T1 is picked up and
;   extrudes 12 mm.
;
;   [ff_extruder] in the configuration (one stepper object):
;       T1 pushes the filament IN, as it should.
;   stock design (no [ff_extruder], four stepper objects):
;       T1's "extrusion" pulls the filament OUT, and the 12 mm retract that
;       follows pushes it back in. Nothing is lost (the moves cancel); you
;       see it, or you do not.
;
; WHAT TO WATCH. T1 only, during its first move (G1 E12: about 2.4 s, after a
; 1 s pause). Two easy signs, in or out:
;   * plastic comes out of T1's nozzle at the purge chute: forward, correct;
;   * no plastic comes out, and a pen mark on the filament above T1's
;     extruder moves UP, away from the extruder: inverted.
;
; RUN IT TWICE
;   1. As the printer is, with [ff_extruder] in printer_n4s4.cfg.
;      Expected: T1's filament goes IN. If it goes out, the single stepper
;      is not doing its job; tell me before you print.
;   2. With [ff_extruder] commented out in printer_n4s4.cfg AND THE PRINTER
;      REBOOTED (RESTART keeps the adapter loaded: the class stays patched
;      until the Klipper process ends). Expected on the stock design:
;      T1's filament goes OUT. If it goes in here as well, the stock design
;      is fine on your printer, and ff_extruder.py, its test, the
;      [ff_extruder] section and the SYNC_EXTRUDER_MOTION lines can go.
;   Put [ff_extruder] back and reboot afterwards.
;   (2026-10-06: run 1 went IN, run 2 went OUT; so ff_extruder stays.)
;
; NOTE. With the N4S4 configuration, picking up T1 without a prime tower arms
; the one-time chute prime of that tool; it would run after the next M109 and is
; cleared by the next print start. Nothing in this file calls M109.
;
; BEFORE YOU START
;   * filament loaded in T0 and T1, and both docked or mounted. Change the
;     TOOL= numbers below for other tools; they are the only place a tool
;     is named.
;   * the nozzles go to 200 degC (PLA). Set TEMP= for other filament.
;   * the printer is homed by the implicit prepare, or run G28 first.
;   * this file extrudes 12 mm per tool at 5 mm/s over the purge chute.
;     Nothing else touches the bed or the part.
;
; RUN IT AS A PRINT (that is what ALLOW_PRINTING=1 is for), or paste the
; lines from _FF_FILAMENT_PREP on into the Mainsail console, one by one,
; and leave out ALLOW_PRINTING=1. This file names no tool as a bare Tn and
; sets no temperature with M104/M109, so ff_print derives nothing from it
; and the implicit prepare has nothing to heat or clean.
; ====================================================================

G90
M83

; T0: pick up, heat, move to the chute. Extrude 12 mm and DO NOT retract.
_FF_FILAMENT_PREP TOOL=0 TEMP=200 ALLOW_PRINTING=1
G1 E12 F300
M400

; T1: pick up (T0 is released first), heat, move to the chute.
_FF_FILAMENT_PREP TOOL=1 TEMP=200 ALLOW_PRINTING=1

; THE CHECK. Forward = the filament goes in.
G4 P1000
G1 E12 F300
M400
G4 P2000

; Put the filament back where it was, then tidy up.
G1 E-12 F300
M400

; Both tools: heater off. T1 is mounted and is parked; T0 is already docked.
M104 S0 T0
_FF_FILAMENT_FINISH

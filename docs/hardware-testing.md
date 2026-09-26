# Hardware testing

CI installs the package into a replica of the printer -- the real
`rootfs.squashfs` running under qemu-mipsel, on the printer's own busybox and
`unTar` (see [printer-replica.md](printer-replica.md)) -- and proves the
machine would still boot. What it cannot do is drive the screen, the MCUs, the
toolchanger or a print. This is the on-hardware procedure.

There is one flash: the firmware package for your model.

**Rule: have the stock FlashForge package for your model on a spare stick
before you flash anything.** Flashing it back is the uninstall, and it is the
only recovery step that needs nothing but a USB port — no ssh, no screen.

**Nothing currently tests that rollback** — no gate installs the mod into the
replica and flashes the stock package back over it (see
[qa-migration.md](qa-migration.md)). It worked when it was last checked, but
treat it as unverified against the current build.

---

## Step zero: check the model gate

Every package carries a gate. Its `runFirmwareExe.sh` has `MACHINE=` / `PID=`
baked in, and compares them against the values `app_startup.sh` passes from
the firmware already on the printer. A mismatch is **refused** with
"Firmware does not match machine type" — it will not install, and it is not a
valid recovery image either.

| Model | MACHINE | PID |
|---|---|---|
| Creator 5 | `Creator5` | `0028` |
| Creator 5 Pro | `Creator5Pro` | `0029` |

The gate is not cosmetic: the two models ship **different `firmwareExe`
binaries**, so a package must be built from the stock package for its own
model. `make release` builds both.

Find yours in Settings → About, or in
`/usr/data/firmwareRes/config/general.json` (`machineName`), and set
`TARGET_MACHINE` in `config.env`. Then:

```sh
make build                         # one model
make release                       # both, into dist/
```

**You must start from a stock package built for your own model** — the mod
inherits the gate from whatever package you unpack, so a package built from
the wrong stock file carries the wrong gate and your printer will refuse it
with "Firmware does not match machine type". Nothing on the build host warns
you first; `make qa-replica` catches it by installing the package the way the
printer does.

## Before the first flash

- [ ] Put a copy of the **stock FlashForge package for your model** on a spare
      USB stick and keep it physically separate. That is your recovery image:
      flashing it restores every file the mod touches. Check it is the right
      model first — see above.
- [ ] Note your printer's serial number (Settings → About). The factory image
      restores a placeholder serial and you may need to put yours back.
- [ ] Confirm you can reach the printer's IP.
- [ ] `make qa` passes.
- [ ] `make qa-replica` passes — needs `PRINTER_IMAGE` in `test.env` and a
      package from `make build`; this parses every script that
      will run on the printer using the printer's own busybox ash.

Two USB sticks, FAT32, both packages at the **root** of the stick (not in a
folder). Ship both filenames (`Creator5-*` and `Creator5Pro-*`): the Pro's
boot script globs `Creator5Pro-*.tgz`, the non-Pro globs `Creator5-*.tgz`.

---

## The flash

One flash brings up everything: a root password you know, Mainsail and
moonraker, the forked Klipper with the toolchanger extras, and HelixScreen on
the touchscreen in place of FlashForge's UI.

From here the procedure is the one an owner follows, and it is written for
them rather than repeated here:

| | |
|---|---|
| [Installing](installing.md) | the flash itself, the first boot, the root password |
| [Your first print](first-print.md) | the go/no-go checks, calibration, and the two files under `gcode/` |
| [Support](support.md) | the logs, and the way back to stock |

What follows is the part that is ours rather than theirs: AFC, which the
replica can install and import but not drive, and keeping those two
verification files honest as the macros change.

## AFC on hardware

`ff-afc.cfg` puts AFC over the toolchanger. The replica proves its extras
import on the printer's interpreter, that its gate sensors accept an edge
through our `filament_switch_sensor.py`, and that `AFC/AFC.var.unit` is
seeded; `qa/static` renders `START_PRINT` under a remap. Nothing below has
run on a printer yet. In order, stopping at the first that fails:

1. **Boot.** klippy reaches ready and the console shows AFC's PREP report
   with four lanes, `e0`..`e3` on `T0`..`T3`, each `LOADED` if its head has
   filament at the `fd_ex<n>` switch. `curl -s
   'http://PRINTER:7125/printer/objects/query?AFC'` lists the lanes.
2. **A toolchange through AFC.** From a homed, parked machine, `T2` grabs
   the third head (AFC → `SELECT_TOOL T=2`), `TOOLCHANGE_STATUS` agrees, and
   a `G1 Z0.2` puts the nozzle at the same height as before the migration.
   `T2` again reports it already loaded and moves nothing; `UNSELECT_TOOL`
   then `T2` grabs again. `SELECT_TOOL T=2` grabs without AFC.
3. **A remap at print start.** `SET_MAP LANE=e2 MAP=T0`, then print a file
   that starts on T0. The console says `AFC map: file tools [0] print on
   heads [2]`, the third head is gated, cleaned, heated and printed with,
   and the first layer sits right. `RESET_AFC_MAPPING` afterwards.
4. **Infinite spool.** Same filament in heads 0 and 1,
   `SET_RUNOUT LANE=e0 RUNOUT=e1`, then pull the filament out of head 0
   mid-print. The print pauses, head 1 comes up to head 0's temperature,
   the print resumes at the right place and height, and AFC's map now has
   `e1` on T0.
5. **Runout with no backup.** The same with `RUNOUT=NONE`: a pause, and
   `LOAD_FILAMENT` then `RESUME` recovers.
6. **A clog.** `fm_ex<n>` still pauses (`_FF_RUNOUT`), and only for the
   mounted head.
7. **Spoolman.** With `[spoolman]` in `moonraker-custom.conf`, give two heads
   spools (`SET_SPOOL_ID`); a toolchange mid-print moves Moonraker's active
   spool.
8. **The screens.** Mainsail shows its AFC panel; HelixScreen shows the AFC
   backend with four slots, and its tool-offset wizard still runs. OrcaSlicer's
   device tab shows four trays, not eight.

AFC's statistics writes to Moonraker used to block klippy right after every
AFC toolchange, which shut the printer down with "Timer too close"
(`SELECT_TOOL` alone did not); `ff_afc.py` moves them to a background thread,
so step 2 now also checks that fix. The risk still worth watching: AFC
restores the toolhead position through
`gcode_move`, while our tool offsets sit below it in `ff_toolchange`'s
transform. Step 2's first-layer height and step 4's resume height are the
checks for the second.

## Keeping the verification files honest

Nothing checks them automatically any more. `make test-py` used to: every
command in them had to be one the mod's Klipper config or
`pkgs/klipper/payload/klipper/klippy/extras/` defines (bar an explicit
allowlist of Klipper built-ins, currently just `SET_PRESSURE_ADVANCE`),
`TOOLS=` had to list every tool the feature print uses, and the safe file's own
lines had to stay cold and above Z50. That check went with the `test/` tree.

So a renamed macro now breaks the print rather than the suite. If you rename or
remove a macro, read `gcode/creator5-safe-moves.gcode` and
`gcode/creator5-feature-test.gcode` before you ship it.

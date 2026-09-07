# Going back to stock

Flashing the stock FlashForge package for your model puts the printer back:
stick in, power on, the same as installing Reforge. It restores everything
Reforge changed **except one file**, and on printers that ran a Reforge
release from before September 2026 that one file is the difference between a
machine that prints and one that does not.

## The symptom

The printer starts, the FlashForge screen comes up and looks completely
normal — and there is no Klipper behind it. It will not home, will not heat,
will not print. Flashing the stock package again does not help, however many
times you do it.

If that is your printer, the repair is one more flash. Nothing is damaged and
nothing has been lost.

## Why it happens

Reforge points a few of the printer's own start-up files at its own copies.
The stock package carries replacements for almost all of them, so a stock
flash overwrites them and they go back to normal.

It does not carry `klipperDaemon` — the small script the printer runs at boot
to start Klipper. That file only ever came on the machine from the factory, so
a stock flash has nothing to put back, and the printer keeps running Reforge's
version. Reforge's version deliberately does nothing, because under Reforge
Klipper is started a different way. On stock, that means Klipper is never
started at all.

Releases from September 2026 onwards do not touch that file, so a printer
installed or updated since then goes back to stock cleanly.

## The repair

Download the recovery package from
[Klipper4FlashForge/stock-recovery/releases](https://github.com/Klipper4FlashForge/stock-recovery/releases).
It is a separate download from the firmware on purpose: nothing on that page
installs Reforge.

1. Flash the **stock FlashForge package** for your model first, if you have
   not already. The recovery package expects a printer that is on stock.
2. Copy `<Model>-anvil-recovery-<date>.tgz` — the one whose name starts with
   your model, `Creator5` or `Creator5Pro` — to the root of a FAT32 USB stick.
3. Plug it in and power the printer on. It flashes like any other package and
   ends on the "power off" screen.
4. Power-cycle. Klipper starts with the screen again.

Afterwards the stick carries `anvil-recovery.txt`, saying what was repaired.
The same text is on the printer at `/usr/data/anvil-recovery.log`.

## What it changes, and what it does not

It restores `klipperDaemon`, clears the dead links Reforge left in the config
directory, and puts back FlashForge's own Klipper config files.

**Your `printer.cfg` is not touched.** The calibration saved in it — mesh,
input shaper, PID, offsets — is yours and stays exactly as it is.

It also does nothing at all on a printer that is still running Reforge: there
is nothing to repair there, so it refuses and says so on the stick. Flash the
stock package first.

## If you are still on Reforge and just want to leave

Flash the stock package, then flash the recovery package. In that order the
printer comes back with Klipper working the first time.

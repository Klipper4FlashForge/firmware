# Known issues

## OrcaSlicer sends a 3MF file instead of G-code

Some OrcaSlicer profiles can upload a `.3mf` project rather than the sliced
`.gcode` file. Reforge expects the G-code file for printing, so the job may
not appear or start correctly in Mainsail.

In OrcaSlicer, open **Printer Settings**, change the upload protocol from
**FlashForge** to **Moonraker**, and uncheck:

> Use 3MF instead of G-code

Slice the model again and upload the resulting `.gcode` file through the
Moonraker upload target.

## A printer that went back to stock has no Klipper

Printers that ran a Reforge release from before September 2026 keep one
Reforge file through a stock flash: the script the printer runs at boot to
start Klipper. The FlashForge package has no copy of it to put back, so the
screen comes up looking normal and nothing behind it works, however many times
the stock package is flashed.

Releases from September 2026 onwards leave that file alone. For a printer
already in that state, [Going back to
stock](going-back-to-stock.md) has the one-flash repair.

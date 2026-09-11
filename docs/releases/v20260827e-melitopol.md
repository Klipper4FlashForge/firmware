## What’s new

**HelixScreen is updated to v0.99.118-creator5.** The redesigned Tools and
Heaters screen shows T0–T3, the bed and the chamber with live temperature
graphs. Tap a tool to pick, dock or preheat it. The new Materials screen shows
each tool’s material, colour and remaining spool amount.

![Tools and heaters](https://github.com/Klipper4FlashForge/helixscreen/releases/download/v0.99.118-creator5/tools-and-heaters.png)

![Tool actions](https://github.com/Klipper4FlashForge/helixscreen/releases/download/v0.99.118-creator5/tool-actions.png)

![Materials](https://github.com/Klipper4FlashForge/helixscreen/releases/download/v0.99.118-creator5/materials.png)

## Safer updates and recovery

- Installation stops without changing the printer if its FlashForge firmware
  is older than 1.9.6, and explains how to continue safely.
- Calibration and configuration edits now survive Reforge updates without
  interfering with a later return to stock firmware.
- This update repairs original FlashForge Klipper files that some early
  Reforge builds could leave changed after returning to stock.
- Changed packages are now always detected by network upgrades.

## Other improvements

- Homing with a mounted tool avoids an unnecessary second X/Y pass.
- Mainsail can access the included Klipper examples and reference files.
- The user guide now has clearer installation, upgrade, recovery, calibration,
  OrcaSlicer upload and filament-assignment instructions.

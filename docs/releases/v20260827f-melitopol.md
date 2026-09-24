## What’s new

**Fluidd is now available alongside Mainsail.** Open
`http://<printer-ip>:81/` to use Fluidd with the same Moonraker instance and
camera. Mainsail remains available at `http://<printer-ip>/` and is updated to
2.19.0.

**Custom boot scripts now survive firmware updates.** Put ordered `*.sh`
scripts in `/usr/data/anvil-data/scripts`. They run once during every boot and
write their output to `/usr/data/logs/custom-scripts.log`.

**The boot screen now describes a normal startup.** It shows progress while
Reforge starts its services, connects the controller boards, and waits for
Moonraker and Klipper, instead of saying that the printer is being set up on
every boot.

## Printing and configuration

- Filament load and unload use the selected tool’s saved material settings,
  use shorter overridable feed lengths, and finish loading with a retract.
- X and Y now use the same `40.4` rotation distance.
- Direct links and refreshed browser routes work in both Mainsail and Fluidd
  while Moonraker API routes continue to pass through unchanged.

## Fixes

- The 24 V control output now follows all four hotends and turns on when any
  reaches 50 °C. Upgrades automatically replace FlashForge’s old
  `[output_pin DC24V_CTL]` configuration, including when the migration has
  already run before.
- Cancelling an object during a multi-tool print keeps extrusion and
  retraction history separate for each tool, avoiding incorrect extrusion
  adjustments after a tool change.

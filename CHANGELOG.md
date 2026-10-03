# Changelog

All notable Reforge changes are recorded here. The project is in beta: a
feature can be implemented and replica-tested without yet being validated on
both printer models or through every real-world workflow.

## Unreleased

### Added

- Bed-mesh probing lifts 2 mm above the previous trigger instead of back to one
  fixed height before every point, which shortens `BED_MESH_CALIBRATE`
  noticeably. The first move still goes to Z 5, and a calculated height under
  Z 1 falls back to Z 3. `FF_BED_MESH_STATUS` shows the settings; they are the
  `[ff_bed_mesh]` section of `ff-print-macros.cfg`. Contributed by
  [@nielssedat](https://github.com/nielssedat) in
  [#29](https://github.com/Klipper4FlashForge/firmware/pull/29).
- `ADAPTIVE_MESH` and `DEFINE_PRIME_TOWER_OBJECT` for a slicer profile whose
  start G-code calls them: the mesh is probed only over the objects and the
  prime tower, the first tool is grabbed and the print offset set after it.
  Set `variable_defer_mesh: 1` on `FF_BEFORE_PRINT_START` to have `START_PRINT`
  leave those steps to the file. `ADAPTIVE_MESH_TOGGLE` (a Mainsail tile) turns
  adaptive probing off to load the saved `MESH_DATA` instead. Not on by
  default; nothing changes for a profile that does not call them.
- `printer.ff_print` reports the active plate's prime tower (position, size,
  brim, rotation and measured outline) and, when the second tool's first
  heating command is near the start of the file, `next_tool` and
  `next_nozzle`. `ADAPTIVE_MESH` heats that tool once the mesh is done.
- The shared-stepper adapter `ff_extruder.py`, for a printer whose four
  logical extruders repeat one physical stepper's options. It does nothing
  until a `[ff_extruder]` section is configured, and none is yet.
- AFC (the AFC-Klipper-Add-On) runs over the toolchanger, one lane per head.
  `SET_MAP` prints any slicer tool number on any head and is remembered across
  restarts; `SET_RUNOUT` names a backup head that takes over when a spool runs
  out mid-print; `SET_SPOOL_ID` gives each head its own Spoolman spool, made
  active whenever that head is picked up. Mainsail's AFC panel and
  HelixScreen's AFC backend drive all three. Installs and loads in the
  replica; not yet run on a printer. See
  [Filament and spools](docs/filament-and-spools.md).

### Changed

- `START_PRINT` starts heating the bed before the first homing instead of after
  it, so the bed is closer to temperature when the nozzle clean ends.
- XYZ tool calibration now verifies plate removal with two non-contact
  carriage-probe readings before approaching the under-bed station, performs
  that check only once per run, and measures each nozzle at 200 C. Use
  `CALIBRATE_TOOL_OFFSETS TOOL=<n>` for a station plus one-tool run; `TEMP=`
  overrides the calibration temperature.
- AFC is updated to its 2026-09-16 DEV revision and multiple mapping is
  enabled: several slicer T-numbers can now select the same physical head.
  AFC now performs its own Moonraker writes off Klipper's reactor, replacing
  this firmware's `ff_afc` compatibility hook.
- `T0`..`T3` and `M104`/`M109 T<n>` follow AFC's map. The print-start tool
  check, calibration check and nozzle clean act on the heads the map picks.
  `SELECT_TOOL T=<n>` still selects a head by its own number.
- Filament load, unload and the pre-print nozzle clean take a head's
  temperature from AFC's record of what is in it (its own temperature, else
  the material table by its material). The fixed `tool_material` list is
  gone; with no material recorded the clean falls back to the print's
  `NOZZLE=` temperature instead of assuming PLA. AFC heats from the same
  material table.
- The presence switches' runout now belongs to AFC: a backup head, or a
  pause. Clogs still pause through the motion sensors as before.

### Removed

- `INITIALIZE_TOOLCHANGER`, `SET_TOOL_TEMPERATURE`, `VERIFY_TOOL_DETECTED`,
  `SELECT_TOOL_ERROR`, `ASSIGN_TOOL`, `TOOLCHANGE INDEX=` and the
  `RESTORE_AXIS=` parameter. Nothing sends them with AFC present;
  `SELECT_TOOL T=<n>` on the mounted head replaces `INITIALIZE_TOOLCHANGER`
  as the recovery after an aborted change.

### Fixed

- A toolchange no longer returns to the print with the new head still
  heating. AFC moved the head back over the part as soon as it had grabbed the
  new head, so the nozzle reached temperature above the model and left blobs
  on it. The head now waits at the dock exit for the temperature the file
  already set, then returns. See the upstream report,
  [AFC-Klipper-Add-On #872](https://github.com/AFCProject/AFC-Klipper-Add-On/issues/872).
- The factory XYZ calibration import marker now lives in persistent
  `/usr/data/anvil-data` state, so reinstalling firmware does not reconsider
  or repeat the stock-JSON import. Upgrading migrates the marker from its old
  location before the payload tree is wiped.
- An AFC toolchange (`T0`..`T3`) no longer shuts the printer down with
  "Timer too close" right after the grab. AFC wrote its statistics to
  Moonraker with blocking requests while the moves back to the print were
  queued; the updated AFC runs those writes on its background thread.
- HelixScreen's tool remap sent `ASSIGN_TOOL`, which this firmware refuses.
  With AFC present HelixScreen runs its AFC backend, which remaps through
  `SET_MAP` instead. Not yet confirmed on a printer.

## v20260827f-melitopol — 2026-09-24

### Added

- Fluidd is available beside Mainsail at `http://<printer-ip>:81/`, using the
  same Moonraker API and webcam proxy.
- Custom `*.sh` files in `/usr/data/anvil-data/scripts` run in filename order
  during boot, survive updates, and write `/usr/data/logs/custom-scripts.log`.

### Changed

- The normal boot screen says that Reforge is starting, names Moonraker and
  Klipper while it waits, and advances its progress bar through a complete
  normal boot instead of describing every boot as printer setup.
- Mainsail is updated to 2.19.0. Both web interfaces now return their SPA
  shell for client-side routes while keeping API routes behind Moonraker.
- Filament load and unload use the selected tool's saved material and shorter,
  overridable feed lengths; loading finishes with a retract.
- The X and Y rotation distances are both 40.4.

### Fixed

- Upgrading removes FlashForge's obsolete `[output_pin DC24V_CTL]` section
  when present, before the mod configures PA3 as a heater-following 24 V
  control.
- Object cancellation keeps extrusion and retraction history separate for
  every tool instead of carrying one tool's adjustment into another.

## v20260827e-melitopol — 2026-09-11

### Added

- The installer refuses to install on a printer running FlashForge firmware
  older than 1.9.6, and says to install FlashForge's current firmware first.
  On those releases the board firmware and Klipper do not agree, the MCU never
  connects, and the printer comes up with no Klipper and no screen — which
  looks like a bad flash rather than a mismatch. Nothing is written to the
  printer when the gate refuses: it puts the reason on the panel, leaves
  `anvil-NOT-INSTALLED.txt` on the USB stick, and the printer boots as it did
  before. A version it cannot read is installed on, as before.

### Changed

- Klipper's and Moonraker's config now live in Reforge's own directory,
  `/usr/data/anvil-data/config`, seeded once on the first install with a copy
  of `printer.cfg` and the config files beside it. FlashForge's
  `/usr/data/config` is not written to at all from here on.
- Moonraker's data path moved to `/usr/data/anvil-data`, which is what puts its
  config in that directory — `moonraker.conf` and `moonraker-custom.conf` moved
  with it, and Mainsail's config editor now shows the live files. Prints and
  logs stay in `/usr/data/gcodes` and `/usr/data/logs`; the job-history
  database is moved across once, by the service, while Moonraker is stopped.

  On a printer coming from an older Reforge, whatever that release put in
  `/usr/data/config` stays there — the stock files it replaced were not kept,
  so there is nothing to restore them from. Stock's own installer force-copies
  its `printer.base.cfg` back on every flash, which is what repairs that
  directory.

### Fixed

- Flashing the stock FlashForge package back over Reforge no longer leaves the
  printer with a Klipper config it cannot resolve. `/usr/data` is the data
  partition, so a stock flash does not clean it: the symlinked
  `printer.base.cfg` and the `ff-*.cfg` beside it used to outlive the mod, and
  Klipper treats an `[include]` that points at a removed `/usr/data/anvil` as
  fatal. Calibration and config edits made under Reforge stay in Reforge's
  directory and come back with it; a stock flash returns the machine to the
  `printer.cfg` it had before.
- FlashForge's `chelper` is put back on a printer that took one of the two
  releases whose software component carried our Klipper (`v20260827` and
  `v20260827b`). Those replaced `/usr/prog/klipper/klippy`, and a stock flash
  restores `c_helper.so` but not the `chelper/__init__.py` that declares what
  is in it — ours takes a fourth argument for `extruder_set_pressure_advance`,
  so a printer flashed back to stock failed at the first extruder. The whole
  directory is restored, with `c_helper.so` left newest so their own
  `check_build_code` does not send klippy to a compiler no printer has. Only
  on machines running FlashForge 1.9.7 or older, which is the firmware this
  copy came from; a newer one is left alone.
- A printer that took an earlier release has FlashForge's `klipperDaemon` put
  back for it. That file is the one thing a stock flash cannot restore — their
  package carries no copy — so a machine whose copy was replaced could not be
  returned to stock at all. `anvil-core` now ships FlashForge's own and
  restores it on every install and every `apk upgrade`, so one update is
  enough; owners who have already rolled back are served by the package at
  [Klipper4FlashForge/stock-recovery](https://github.com/Klipper4FlashForge/stock-recovery).
- Going back to stock works again. The mod no longer replaces
  `/usr/prog/klipper/klipperDaemon` with a shim of its own. That link was the
  one thing a stock FlashForge flash could not undo — their package carries no
  `klipperDaemon` to copy over it — so stock's `start.sh`, whose last line is
  `klipperDaemon start`, ran the mod's shim, whose `start` does nothing by
  design. The printer came back from a stock flash with a working screen and
  no Klipper behind it, however many times stock was flashed. Nothing on a
  modded machine called that file: our own `start.sh` replaces stock's and
  asks s6-rc, FlashForge's `firmwareExe` execs only `start.sh`, and Moonraker
  runs with `provider: none`. Printers that already took an earlier release
  still carry the link and are repaired by the package at
  [Klipper4FlashForge/stock-recovery](https://github.com/Klipper4FlashForge/stock-recovery).

## v20260827c-melitopol — 2026-08-27

### Added

- HelixScreen’s Filament page now follows the selected tool for material,
  spool, temperatures, loading, and retracting.
- Installed packages record the Reforge APK feed location for future updates.

### Fixed

- A selected tool with no resolvable heater is logged and ignored instead of
  heating the wrong hotend.

## v20260827d-melitopol — 2026-08-27

### Added

- HelixScreen pressure-advance calibration integration and per-tool Z
  correction selection in the tune overlay.

### Fixed

- Pressure-advance calibration restores the previous heater target, including
  when a sweep fails partway through.
- Calibration documentation now explains that results belong in the slicer’s
  filament profile rather than being saved to `printer.cfg`.

## v20260827-melitopol — 2026-08-27

### Added

- s6-rc supervision for the web stack, camera, Moonraker, Wi-Fi, and related
  services, with readiness checks based on actual service readiness.
- A printer-local CPython 3.13 runtime for Moonraker with working SQLite.
- CPU priority controls that let Moonraker and the camera yield to Klipper.
- HelixScreen settings persistence across firmware updates.

### Changed

- Reforge owns and starts the camera and web stack rather than stock
  `firmwareExe`.
- Startup retries until MCU boards answer and shows boot progress.

## v20260825b-nova-kakhovka — 2026-08-25

### Fixed

- `TOOL_OFFSET_CALIBRATE` no longer shuts Klipper down on completion.

## v20260825-nova-kakhovka — 2026-08-25

### Added

- First-boot import of factory per-unit calibration into Klipper config.
- Toolchanger-aware calibration, status commands, and safe plate checks.
- An update path that preserves the root password and user configuration.

### Fixed

- Release builds can no longer silently fall back to the stock Klipper tree.

## v20260824-nova-kakhovka — 2026-08-24

### Added

- USB packages for Creator 5 and Creator 5 Pro with model guards.
- Current Klipper with Creator 5 toolchanger support, Mainsail, Moonraker,
  HelixScreen, camera streaming, Wi-Fi, and root SSH.
- Stock slicer compatibility through `[ff_print]`, `START_PRINT`, and
  `END_PRINT`, including preparation, mesh loading, nozzle cleaning, tool
  checks, and safe cancellation.
- Tool-aware filament load, unload, purge, runout, and clog handling.
- Automatic pressure-advance calibration and VFA calibration support.
- Timelapse support, signed APK packages, pinned inputs, and Docker plus
  printer-replica QA.

### Known release issue

The original packages were withdrawn because they could install a mixed
Klipper tree. Use the rebuilt release or a later release; see its release note.

## Project start — 2026-08-20

- Initial reverse-engineering notes and USB-installable firmware builder.

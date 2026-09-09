# Filament slot metadata: how a spool reaches OrcaSlicer

**Status: the path is designed and implemented upstream, and not yet in the
HelixScreen build we ship.** Everything on the Klipper side of it already
works. What is missing is one commit of HelixScreen, named at the end.

OrcaSlicer 2.4.0 and later can show the printer's filament slots in the send
dialog and offer to map the project's filaments onto them. It learns those
slots from a Moonraker database namespace called `lane_data`, which the
touchscreen writes and the slicer only reads. This note is the whole chain:
who decides there are four slots, who decides what is in them, where that is
stored, and what OrcaSlicer does with it.

Three things own a piece of it and none of them owns two:

- **Klipper** (`ff_toolchange.py`) says how many slots exist and which tool is
  on the carriage. It has nothing to say about filament.
- **The operator**, through HelixScreen, says what filament is in each tool.
  Nothing on this machine can sense that.
- **Moonraker's database** is the only durable record, and the only thing
  OrcaSlicer reads.

---

## The chain

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 1. DISCOVERY — how HelixScreen learns there are four slots at all         │
 └──────────────────────────────────────────────────────────────────────────┘

   ff_toolchange.py  (klippy/extras)
        │  publishes a klipper-toolchanger-shaped status surface
        │    toolchanger        : status, tool_number, tool_names[], ...
        │    tool T0 .. tool T3 : active, mounted, detect_state, extruder, ...
        ▼
   Moonraker  /printer/objects/query + notify_status_update
        ▼
   HelixScreen  PrinterDiscovery ─ printer_database.d/flashforge_creator5.json
        │         heuristic "object_exists: ff_toolchange"  →  ams_type: tool_changer
        ▼
   AmsBackendToolChanger::set_discovered_tools(["T0".."T3"])
        └─► initialize_tools()   ← resets every slot to default grey
                                    (runs on EVERY reconnect — this is the wipe
                                     the override store exists to undo)

   NOTE: material / colour / brand are NOT in this path. parse_tool_state()
   reads `mounted` and `active` and nothing else. Klipper never reports what
   filament is in a tool.


 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 2. SET — the operator is the only source of filament identity            │
 └──────────────────────────────────────────────────────────────────────────┘

   Operator at the touchscreen
        │  AMS panel → slot → edit: material, colour, brand,
        │  catalog product, Spoolman spool, weights
        ▼
   AmsBackendToolChanger::set_slot_info(slot_index, SlotInfo, persist=true)
        │
        ├─► system_info_.units[0].slots[i]   (in-memory, drives the UI now)
        │
        └─► overrides_[slot_index] = FilamentSlotOverride
                 populate_temps_from_slot_info()  ← fills bed_temp / nozzle_temp
                 priority: explicit user entry > bound Spoolman profile
                                               > internal material DB


 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 3. STORE — Moonraker's database is the persistence layer                 │
 └──────────────────────────────────────────────────────────────────────────┘

   FilamentSlotOverrideStore("toolchanger", LaneKeyStyle::Tool)
        │                                    ▲
        │                    lane_key_style_for(AmsType) → Tool for changers
        │
        │  save_async()  ──► to_lane_data_record()
        ▼
   POST /server/database/item
        { "namespace": "lane_data",
          "key":       "T0",              ← outer key: 0-based, opaque to readers
          "value":     { "lane": "0",     ← inner field: 0-based STRING, authoritative
                         "material": "PETG",        ─┐
                         "color": "#10A0E0",         │ what OrcaSlicer reads
                         "bed_temp": 80,             │
                         "nozzle_temp": 240,        ─┘
                         "helix_material": "PETG-CF",  ─┐
                         "vendor" / "vendor_name",      │ HelixScreen extensions,
                         "spool_name" / "name",         │ ignored by OrcaSlicer
                         "spool_id", "spoolman_vendor_id",
                         "remaining_weight_g", "total_weight_g",
                         "color_name", "scan_time"     ─┘ } }
        ▼
   Moonraker sqlite DB on /usr/data   ── survives reboot, klippy restart, OTA

   Other writers of the same namespace:
     Mainsail #2510 .... "T<n>" keys  → same key, overwrite, no duplicate tray
     AFC / Happy Hare .. "laneN" keys → their plugins own it; HelixScreen
                                        never writes lane_data for those backends


 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 4. READ BACK — screen side, every boot                                   │
 └──────────────────────────────────────────────────────────────────────────┘

   AmsBackendToolChanger::on_started()     ← NOT additional_start_checks():
        │                                    start() holds mutex_ there and
        │                                    load_blocking() needs it → deadlock
        ▼
   load_blocking() ─► GET /server/database/item?namespace=lane_data
        │             from_lane_data_record()   (key-agnostic: the index comes
        │                                        from the inner `lane`, never
        │                                        from the outer key)
        ▼
   overrides_  ──re-layered at THREE points, because initialize_tools() wipes:
        ├─ tail of initialize_tools()
        ├─ tail of the status parse
        └─ immediately after this load (set_discovered_tools runs before start)


 ┌──────────────────────────────────────────────────────────────────────────┐
 │ 5. ORCASLICER — read-only consumer, pull mode                            │
 └──────────────────────────────────────────────────────────────────────────┘

   Printer added in OrcaSlicer's DEVICE TAB (not just an upload target)
        │   init_device_info(): model_id ← edited printer preset's printer_type
        ▼
   MoonrakerPrinterAgent::fetch_filament_info()      [FilamentSyncMode::pull]
        │
        ├─1─► fetch_moonraker_filament_data()
        │        GET /server/database/item?namespace=lane_data
        │        for each result.value.*:
        │           lane  (string) ──stoi──► slot_index   (<0 or non-string → skipped)
        │           material ──► tray_type
        │                    └─► filament_id_by_type() ──► tray_info_idx
        │                        fallback table: PLA/PETG/ABS/ASA/TPU/...
        │                                        → "Generic X @System"
        │           color    ──► normalize_color_value()
        │           bed_temp / nozzle_temp ──► emitted only when > 0
        │           has_filament = material is non-empty  ← empty ⇒ slot reads empty
        │
        └─2─► fetch_hh_filament_info()  (fallback only: /printer/objects/query?mmu)
        ▼
   build_ams_payload(ams_count = (max_lane + 4) / 4, trays)
        │   4 lanes → ONE unit, info "0002" (AMS Lite), trays T0..T3
        │   emits a Bambu-shaped print.ams JSON + ams_exist_bits / tray_exist_bits
        ▼
   DevAms ─► SelectMachineDialog
        │       shows the four slots with their material and colour, and offers
        │       "map project filament N → slot"
        ▼
   Operator confirms ─► POST /server/files/upload ─► print start

   WHAT DOES NOT FLOW BACK:
     MoonrakerPrinterAgent::start_local_print() ignores params.ams_mapping.
     The mapping selects presets, colours and temperatures at slice time; it
     does NOT rewrite tool numbers in the uploaded G-code. OrcaSlicer never
     writes lane_data.
```

---

## Why the tool changer is the odd one out

Every other AMS backend HelixScreen carries — AD5X IFS, CFS, ACE, Snapmaker,
AFC, Happy Hare — layers the operator's edits over something the machine
reports, and clears them when the hardware says the spool physically changed.
A tool changer does neither, and both halves matter:

- **The store is the sole source of filament identity, not a layer over one.**
  There is no firmware-reported material, colour, brand or weight to fall
  through to, so the merge is trivially "the override wins".
- **There is deliberately no hardware-event clearing.** Nothing on a tool
  changer can tell that a spool was swapped: no RFID, no presence transition,
  no colour reading. Inventing a clear signal here would throw away the
  operator's data on an event that does not mean what it would have to mean.
- **The wipe being undone is `initialize_tools()`**, not a bad parse. It runs
  on every `set_discovered_tools()`, which is every reconnect, and resets each
  slot to grey with the tool name as a placeholder.

The `T<n>` outer key is shared with Mainsail's toolchanger Spoolman records
(mainsail#2510) on purpose. A tool changer that also wrote `laneN` for the
same slot would produce two records with the same inner `lane`, which
OrcaSlicer ingests as duplicate trays — it does not deduplicate.

---

## What is already ours, and what is missing

Nothing in `pkgs/` needs to change for this. `ff_toolchange.py` already
publishes the status surface HelixScreen's tool-changer backend is written
against — `_ToolchangerView` and `_ToolView` in
[`ff_toolchange.py`](../../pkgs/klipper/payload/klipper/klippy/extras/ff_toolchange.py),
plus `SELECT_TOOL`, `UNSELECT_TOOL`, `ASSIGN_TOOL`, `INITIALIZE_TOOLCHANGER`
and `VERIFY_TOOL_DETECTED` under their klipper-toolchanger names — and
[`flashforge_creator5.json`](../../pkgs/helixscreen/payload/helixscreen/config/printer_database.d/flashforge_creator5.json)
already declares `"ams_type": "tool_changer"` for both models.

What is missing is on the HelixScreen side, and it is one commit.
`AmsBackendToolChanger` was the last AMS backend without a
`FilamentSlotOverrideStore`; without it `set_slot_info()` discards its
`persist` argument, so step 3 never happens, `lane_data` stays empty, and
OrcaSlicer reports no slots. Upstream fixed it in `11421280d`,
`feat(toolchanger): remember per-tool spool metadata across rediscovery`,
on 2026-08-24. Our `creator5` branch's last catch-up merge from upstream is
`6d0a53ebe`, 2026-08-20 — four days short of it. The fix is an upstream
catch-up and a `HELIX_VERSION` bump in `versions.env`, not new code here.

## Loose end: the printer's own idea of what is loaded

`ff-filament.cfg` carries
`variable_tool_material: ['PLA', 'PLA', 'PLA', 'PLA']` — static config,
hand-edited, read by `START_PRINT`'s nozzle clean to pick a temperature when
the job does not give it one. `LOAD_FILAMENT TOOL=n MATERIAL=PETG` accepts a
material, uses it for the load temperature, and discards it.

So after the catch-up there will be two records of what is in each tool that
can disagree: this variable and the `lane_data` entry. Making Klipper report
its own per-tool material for HelixScreen to merge is the wrong fix — upstream
deliberately made the store the sole source for tool changers, and a
firmware-reported layer would be ours to maintain forever. Having
`LOAD_FILAMENT` and `UNLOAD_FILAMENT` write `tool_material` back with
`SET_GCODE_VARIABLE` would at least make the printer self-consistent within a
session. Neither is done.

## Sources

- OrcaSlicer `src/slic3r/Utils/MoonrakerPrinterAgent.cpp` — the reader; the
  agent first appears in 2.4.0 (released 2026-06-20).
- HelixScreen `docs/specs/filament_slots.md` — the published `lane_data`
  specification, v1.7.
- HelixScreen `src/printer/filament_slot_override_store.cpp` — the writer.
- AFC originated the namespace; Happy Hare's `mmu_server.py` `push_lane_data`
  established the `vendor_name` / `name` / `filament_id` key spellings.

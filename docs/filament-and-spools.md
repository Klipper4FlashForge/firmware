# Filament and spools

Your printer has four heads, and the slicer numbers them T0 to T3. Reforge
lets you change which head prints which number, name a second head to take
over when a spool runs out, and track each head's spool in Spoolman. All
three are done by AFC (the Automated Filament Control add-on), which sits on
top of the toolchanger and is part of every Reforge install. Nothing needs
turning on.

> **Status.** This is new. It installs and loads in a copy of the printer's
> software, but no remap, swap or runout has run on a real printer yet. Try
> it on a print you can afford to lose first, and report what you see on the
> [Discord](https://discord.gg/tYs3eNEDq).

## Where the controls are

Mainsail has an **AFC** panel on its dashboard, and HelixScreen shows the
four heads on its filament screen. Each head is listed under a name,
`e0` to `e3`. Out of the box, `e0` is the head the slicer's T0 picks, `e1`
is T1, and so on. Everything below can be done from either screen, or typed
into Mainsail's console.

---

## Print a colour on a different head

Say the file wants red on T0, and the red spool is on the third head. Tell
Reforge that T0 now means that head:

```gcode
SET_MAP LANE=e2 MAP=T0
```

The two heads swap numbers: `e2` answers to T0 and `e0` takes over T2. The
file does not change. When the print starts, the tool check, the nozzle
clean and the heating all follow the new numbers, so it is the third head
that gets cleaned and used for T0.

The swap is remembered across restarts. To put every head back on its own
number:

```gcode
RESET_AFC_MAPPING RUNOUT=no
```

Without `RUNOUT=no` it also clears the backup heads described next.

## Keep printing when a spool runs out

Load the same filament into two heads, then name one as the other's backup:

```gcode
SET_RUNOUT LANE=e0 RUNOUT=e1
```

If `e0` runs out during a print, the printer pauses, swaps to `e1`, heats it
to the temperature `e0` was printing at, moves `e0`'s number onto it and
carries on.

With no backup named, a runout pauses the print, as it always did. Feed new
filament in, press **Load** for that head in the AFC panel or on HelixScreen
(or type `TOOL_LOAD LANE=e0`), then resume. Use that Load rather than
`LOAD_FILAMENT`: it is the same sequence, but it also tells AFC the head is
loaded again, and until AFC knows that it will not notice the next runout on
that head. To remove a backup:

```gcode
SET_RUNOUT LANE=e0 RUNOUT=NONE
```

The backup head has to hold the same material. Reforge cannot see what is on
a spool, so it trusts you.

## Track each head's spool in Spoolman

Spoolman is a separate program that keeps an inventory of your spools and how
much is left on each. If you run it somewhere on your network, point the
printer at it by adding this to
`/usr/data/anvil-data/config/moonraker-custom.conf` and restarting the
printer:

```ini
[spoolman]
server: http://192.168.1.50:7912
```

Use your Spoolman's own address. Then give each head its spool, either from
the AFC panel or HelixScreen, or by spool number:

```gcode
SET_SPOOL_ID LANE=e0 SPOOL_ID=12
```

Whenever a head is picked up, its spool becomes the active one, so the
filament a print uses is taken off the spool it came from.

## Loading and unloading

The **Load** and **Unload** buttons for a head, in the AFC panel or on
HelixScreen, run the same sequences as before: the head is picked up, taken
to the purge chute and heated, and the filament is fed in or pulled out. The
head stays on the carriage afterwards.

During a print, a head that has filament at its sensor counts as loaded. Load
filament before the print starts, not in the middle of one.

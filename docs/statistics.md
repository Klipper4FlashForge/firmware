# Statistics

The printer keeps a running account of what it does: how many jobs, how long
it printed, how much filament each tool used, how many tool changes there
were and how long they took, and where the time of a job went. Klipper and
Moonraker keep some of this already. This page says what they do not, and
how to read the rest.

It is a Klipper extra, `ff_stats`, loaded by `printer_n4s4.cfg`. Nothing in
it moves the machine or changes a print; it watches and counts.

**Status.** The code is covered by unit tests, and one replica test runs it
against Klipper's own `gcode.py` and `print_stats.py` on the printer's
Python. It has **not yet run through a print on a printer**. Treat the first
numbers with some suspicion and compare them with the slicer's estimate, as
described under [Filament](#filament).

---

## Reading the numbers

Three macros show up in Mainsail's macro panel and print to its console:

| Macro | Shows |
|---|---|
| `FF_STATS` | the whole life of the printer: jobs, print time, filament per tool, tool changes, time by phase |
| `FF_STATS_JOB` | the running job, or the last finished one |
| `FF_STATS_JOBS` | the recent jobs, one line each. `COUNT=25` for more than the default 10 |

Example output from the test rig, with invented numbers, to show the shape:

```
Statistics since 2026-10-01 (counted by this printer)
Jobs: 3 started | 2 completed, 1 cancelled
Print time: 2h 35m 45s (jobs in total 3h 13m 22s)
Filament: 11.72 m (about 35 g)
Per tool: T0 6.56 m | T1 2.01 m | T2 1.73 m | T3 1.43 m
Tool changes: 315 (313 swaps, 2 first pickups) | avg 21.5 s, slowest 23.5 s
Failed changes: 1 | failed attempts: 2 grab, 0 release | by stage: grab 1
Changes into: T0 80 | T1 79 | T2 78 | T3 78
Time by phase: prepare 36s (0%) | homing 1m 15s (1%) | heating 13m 20s (7%)
mesh 3m 30s (2%) | purge 4m 45s (2%) | toolchange 1h 52m 52s (58%)
print 55m 00s (28%) | end 2m 03s (1%)
```

The same numbers are in Klipper's status as `printer.ff_stats`, so a macro
can read them and Moonraker serves them:

```
http://<printer-ip>:7125/printer/objects/query?ff_stats
```

The fields: `jobs` (counts per outcome), `print_time_s`, `job_time_s`,
`filament_mm` (one entry per tool), `toolchange` (`count`, `swaps`,
`pickups`, `failed`, `attempts_failed`, `avg_s`, `max_s`), `phases_s`, `job`
(the running job, or `null`), `last_job` and `corrects_print_stats`.

---

## What is counted

### Jobs

A job starts when a print is requested and ends when the end-of-print macro
has finished. That is wider than Klipper's own view, which starts when the
file begins to be fed and so leaves out the whole start-up (homing, heating,
the nozzle clean). `ff_print` tells `ff_stats` where a job begins and ends.
A print that did not come through `ff_print` is still followed, from the
moment Klipper reports it printing.

Every job ends in one of these:

| Outcome | Meaning |
|---|---|
| `completed`, `cancelled`, `error` | what `print_stats` reported |
| `aborted` | refused or failed while preparing, before the file started |
| `shutdown` | Klipper shut down while the job was open |
| `interrupted` | found open on disk after a power loss or a crash, or Klipper was stopped mid-job |

The last 100 jobs are kept in detail. The lifetime totals keep counting past
that.

### Print time

Two figures per job. *Printing* is Klipper's `print_duration`: time spent
printing, not paused, not before the first extrusion. *Total* is the whole
job, start-up and end included. The lifetime figures are the sums.

### Phases

The total time of a job is split into phases. At any moment exactly one is
current, so they add up to the total.

| Phase | What is in it |
|---|---|
| `prepare` | the start-up before the file is fed, apart from the parts below |
| `homing` | homing moves |
| `heating` | `M109`, `M190`, `M191`, `TEMPERATURE_WAIT`: waiting for a temperature |
| `mesh` | `BED_MESH_CALIBRATE`: probing |
| `purge` | the purge and wipe macros (`_FF_NOZZLE_CLEAN`, `_PURGE_NEAR_OBJECT`, `PURGE`, `_NS_CHUTE_LIP_WIPE`, `_FF_NOZZLE_WIPE`) |
| `toolchange` | tool changes and `TOOLCHANGE_PARK` |
| `print` | the rest of the printing state: the model, its travel, and any move no other phase claims |
| `paused` | while paused |
| `end` | `FF_AFTER_PRINT_END`, and the wait for it |

Phases nest and the innermost wins: a heating wait inside a purge macro
counts as `heating`. The macros are not edited; `ff_stats` takes over the
named commands, runs the original, and notes how long it took. Which command
belongs to which phase is the `phase_commands` option. A command that is not
registered on a given machine is skipped.

This is what shows whether a change to the start-up or to the preheat
actually saved time.

### Filament

Filament is measured per tool, in metres, from the extruder's own position.
Each of the four logical extruders keeps its own running E coordinate; the
net movement of that coordinate between the start and the end of a job is
what that tool consumed. *Net* means extruded minus retracted: the unload and
load around a tool change cancel, while the purge, the tower and the clean
before the print count. A tool that only retracted counts as zero.

The job view shows the part used before the print started (the start-up
clean) separately.

The grams are an estimate. The density and diameter come from the sliced
file's header when it has them (`; filament_density:` and
`; filament_diameter:`, which Orca writes), otherwise from the extruder's
diameter and the `filament_density` option (1.24, PLA).

**Check this against the slicer.** On this printer's four-colour test jobs,
Klipper's own `filament_used` came out at 12% to 34% of the slicer's estimate
(median 25%, ten completed jobs), while single-tool jobs matched it (median
1.02, eighteen jobs). The measurement here does not use that figure.

It was compared with the slicer on two completed four-colour test jobs (100
layers, 3 tool changes each). With the start-up clean taken off, every tool
was within 0.6 % of the `; filament used [mm]` line in the sliced file, and
the total was 0.3 % above it in both. The tool that printed most read about
9 mm high each time; the cause has not been found. Mainsail's own figure for
those jobs, corrected, read 100.3 % and 100.4 % of the slicer's total, where
an earlier job with 225 tool changes had read 23 %. That is two jobs on one
printer. To check your own, print a job and compare `FF_STATS_JOB` with the
per-tool lengths the slicer shows.

### Tool changes

A *swap* releases the mounted tool and picks another. A *first pickup* is a
pick with nothing mounted. Selecting the tool that is already mounted is
counted separately and is neither.

The duration starts when the motion that was already queued has finished and
ends when the last command of the change has been queued. The short travel
back to the print position is not included, so a change is a second or so
longer in reality than it is here. A heating wait inside a change is part of
its duration, but not of its `toolchange` phase time.

A grab or release that has to be tried again is a *failed attempt*; the last
failed attempt of a change that then fails counts too. A *failed change* is
filed under the stage it failed in: `release`, `grab`, `finish` (the prime
macro after the grab), `restore` (the return travel), or `prepare` (before
the release or grab began). The last failure is kept with its message in the
data file.

---

## Mainsail's filament figure

Mainsail's dashboard and its job history show whatever Klipper's
`print_stats.filament_used` says, and Moonraker's job history records that
value when a job ends. With `correct_print_stats` on, which is the default,
`ff_stats` replaces that value while one of its jobs is printing with the
filament measured above, counted from the moment the print starts. Jobs
recorded before this was installed keep their old numbers.

Klipper works that figure out from the G-code E position, and `gcode_move`
sets that position back to zero on every `ACTIVATE_EXTRUDER`
(`_handle_activate_extruder`). A multi-colour job activates an extruder at
every tool change. That is the likely reason for the shortfall above; it has
not been isolated further.

Set `correct_print_stats: False` to leave Klipper's figure alone. Nothing
else changes, with one small exception: Klipper starts counting
`print_duration` when it first sees filament used, and with the corrected
figure that moment is noticed up to about two seconds later, so
`print_duration` is that much shorter.

---

## Where it is kept

In `/usr/data/anvil-data/stats/ff_stats.json`. The installer replaces
`/usr/data/anvil` and leaves `/usr/data/anvil-data` alone, so updating the
firmware keeps the statistics.

The file is written when a job ends, at shutdown, and otherwise when there is
something new, at most every 5 minutes (every minute while a job runs). It is
replaced atomically. `/usr/data` is mounted `sync`, so a write is a real
write to flash; that is why the counters are not written on every event.

After a power loss, a job that was open is recovered from the last save, at
most a minute old, and recorded as `interrupted`.

A file that cannot be read is renamed to `ff_stats.json.corrupt-<time>`
and counting starts again, instead of overwriting it. A file from an older
release is filled in with the counters it lacks.

**Starting over.** There is no button for it. `FF_STATS_RESET` is a console
command and does nothing without `CONFIRM=1`:

```
FF_STATS_RESET CONFIRM=1
```

It refuses to run while a job is open. The old file is kept next to the new
one as `ff_stats.json.reset-<time>`. Deleting the file by hand does not
work while Klipper runs: it holds the totals in memory and writes them back.

---

## Options

All optional, in the `[ff_stats]` section of `printer_n4s4.cfg`:

| Option | Default | |
|---|---|---|
| `path` | `/usr/data/anvil-data/stats/ff_stats.json` | where the data lives |
| `tools` | 4 | number of tools |
| `max_jobs` | 100 | jobs kept in detail. 0 keeps only the totals |
| `save_interval` | 300 | seconds between writes when idle |
| `job_save_interval` | 60 | seconds between writes during a job |
| `filament_density` | 1.24 | g/cm³, for the gram estimate when the file does not say |
| `filament_diameter` | 1.75 | mm, same |
| `correct_print_stats` | True | see above |
| `phase_commands` | the table under *Phases* | `COMMAND=phase` pairs, comma separated |

For example, to also count a custom purge macro as `purge`:

```
[ff_stats]
phase_commands:
    M109=heating, M190=heating, M191=heating, TEMPERATURE_WAIT=heating,
    BED_MESH_CALIBRATE=mesh, PURGE=purge, MY_PURGE=purge,
    TOOLCHANGE_PARK=toolchange, FF_AFTER_PRINT_END=end
```

The list replaces the default, so restate every command you still want.
A macro of your own can also mark a phase directly:
`FF_STATS_PHASE NAME=purge` starts it and `FF_STATS_PHASE NAME=purge END=1`
ends it.

---

## How exact it is

- Phase and tool-change times are taken when Klipper processes the G-code,
  which is up to about two seconds ahead of the motion. Every boundary has a
  similar lead, so the error does not add up, but one phase can be a couple
  of seconds off.
- The `print` phase also holds anything not claimed by another phase, such
  as the first moves of the file before the first layer.
- The gram figure is an estimate (see *Filament*).
- Counters only see what happens while Klipper runs. A tool changed by hand
  with Klipper stopped is not counted.

## Not there yet

Per-nozzle hours at temperature and heat cycles, and maintenance counters
(axis travel, motor and fan hours, with reminders), are the next candidates.
They are not in this version.

The code is
[`ff_stats.py`](../pkgs/klipper/payload/klipper/klippy/extras/ff_stats.py).
The job boundaries come from [`ff_print.py`](../pkgs/klipper/payload/klipper/klippy/extras/ff_print.py)
and the tool-change figures from
[`ff_toolchange.py`](../pkgs/klipper/payload/klipper/klippy/extras/ff_toolchange.py).
How a print is put together is in [How a print runs](how-a-print-runs.md).

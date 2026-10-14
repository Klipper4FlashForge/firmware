# When Klipper's host stalls

"MCU 'mcu' shutdown: Timer too close" ends a print on the spot. Klipper's text
blames an overloaded host computer, and usually that is not what happened: the
host was not busy, it stopped for a moment. This page explains the difference,
what the firmware does about it, and how to read what it finds.

**Status.** The code is covered by unit tests, and the lag watch was run with
real threads and a real clock against deliberate stalls. None of it has run
through a print on a printer yet. The stall that started this (below) has not
been traced to its cause; the lag watch exists to do that.

---

## What a stall does

Klipper does everything in one thread: it reads the G-code, plans the moves,
generates the steps and sends them to the MCUs. When that thread is blocked
for a second, no steps leave the host in that second. Normally the MCU has
more than a second of steps queued and nothing happens. It does not at one
moment: the end of a move that the host waits for.

Klipper hands the MCU the steps of a move only up to about 50 ms before the
move's end. The braking at the end of the move starts inside that window, so it
is sent later, by a timer that fires 0.2 s before the steps are due. A stall
that covers that timer leaves the stepper with the first part of the move and
no steps for the rest. When the steps arrive, they are a second late, the MCU
finds a timer far in the past and shuts down.

The dock moves of a tool change are made of exactly this: each ends with a wait
(the approach to a dock, the retreat), and each wait is such a moment.

## What happened on 2026-10-14

A three-colour print stopped at layer 37 with "Timer too close" while T3 was
being picked up, in the last millimetres of the approach to its dock. The log
showed a host that was healthy on average (load about 1, 365 MB free, no
retransmits) and one gap: Klipper pings every MCU once a second to keep the
clocks in step, and in 98 seconds of history there was a single interval of
2.1 s instead of 0.985 s. The last steps of the move had been due 1.03 s
before they were sent; the MCU's own report of the failure carries the same
1.03 s.

What stopped the thread for a second is not known. The statistics file was a
suspect (it was written from the Klipper thread, with `fsync`, on a flash that
is mounted `sync`), but a stall of that size needs a cause that happens
rarely, and it was one in about 4 500 tool changes.

## What the firmware does about it

**The wait sends the last steps first** (`flush_steps_before_wait`, in
`[ff_toolchange]`, on in `printer_n4s4.cfg`). Before every wait of a tool
change the host asks Klipper to send all steps of the motion at once. After
that, nothing of the move depends on the host any more: the MCU has all of it,
and the serial link sends it on a schedule of its own. The wait takes as long
as it did, so this costs no time at all, and it is why there is no "slow but
safe" setting.

**The statistics are written from a thread** (`ff_stats`). The Klipper thread
builds the text of the file, which takes a few milliseconds, and a second
thread does the writing. No save starts while a tool change runs (unless the
last one is more than three intervals old). The saves at shutdown and reset
still write in place.

**The lag watch** (`ff_lagwatch`, `[ff_lagwatch]`) finds the next cause. A
timer in Klipper's reactor beats every 0.1 s and a second thread watches it.
When the beat is 0.25 s late, the main thread is stuck, and the watchdog
writes down where, taken from the thread's Python stack while it is blocked.
When the beat comes back it logs how long the thread was away and what the
printer was doing. Slow garbage collections are logged too, because a
collection holds the interpreter and cannot be sampled.

A stall in `printer.log` looks like this:

```
ff_lagwatch: the main thread has not run for 0.29 s and is in: ff_stats.py:324 save <- ff_stats.py:781 _poll <- ...
ff_lagwatch: the main thread was away for 0.92 s (stall 1) [a tool change was running, no motion was queued]; it was in: ff_stats.py:324 save <- ...
```

The first line comes while the thread is blocked, so it is there even if the
thread never comes back. The second says how long it lasted and whether motion
was queued: a stall longer than the queued motion is one the MCU would notice.
Stalls are rare, so the watch is quiet; `FF_LAGWATCH` lists the recent ones
and `printer.ff_lagwatch.stalls` is the count. Without a stall there is
nothing in the log but one line at start.

## Options

`[ff_toolchange]`

| Option | Default | |
|---|---|---|
| `flush_steps_before_wait` | off (on in `printer_n4s4.cfg`) | send the last steps of a move before every wait of a tool change |

`[ff_lagwatch]`

| Option | Default | |
|---|---|---|
| `threshold` | 0.25 | seconds late before the main thread counts as stuck |
| `period` | 0.1 | seconds between beats |
| `gc_threshold` | 0.05 | log collections slower than this; 0 leaves the hook out |
| `max_reports` | 50 | stall lines in the log per Klipper run; stalls are still counted |

## Reading an older log

The same method works on a failure that happened before the watch was there.
The dump after "Timer too close" lists the last 100 messages sent to each MCU.
The `get_clock` messages are one per second; an interval clearly longer than a
second is the host stalling. The time of the gap and the time the last steps
were due, in the same dump, tell whether the two line up.

Statistics are described on the [statistics](statistics.md) page.

#!/bin/sh
# THE RECOVERY INSTALLER -- packed as runFirmwareExe.sh by bin/pack-recovery.sh.
#
# It exists for one printer: a machine that ran a release from before
# 7914f03, was flashed back to a stock FlashForge package, and came up with
# the FlashForge UI and no Klipper behind it. Nothing on it is broken that a
# stock flash could reach, which is exactly why it stays broken through as
# many stock flashes as an owner cares to do.
#
# WHAT IS ACTUALLY WRONG WITH IT. Those releases pointed three stock paths at
# the mod with symlinks -- /usr/prog/PROGRAM/software/firmwareExe,
# /usr/prog/klipper/start.sh and /usr/prog/klipper/klipperDaemon. A stock
# package restores the first two: its run.sh copies its own firmwareExe and
# start.sh over them, and busybox `cp -f` unlinks a symlink rather than
# writing through it. It restores nothing over the third, because the stock
# package HAS no klipperDaemon -- it is not in the software component, not in
# its md5sum.list, and run.sh has no line for it.
#
# So the link survives, and it points at the mod's klipperDaemon, whose
# `start` deliberately does nothing: under the mod, klippy is an s6-rc
# longrun, and a second unsupervised one fighting for /dev/ttyS4 and /tmp/uds
# is the thing that shim exists to prevent. Stock's start.sh ends in
#
#     /usr/prog/klipper/klipperDaemon start
#
# which now prints a line about s6 and exits 0. The UI starts, klippy never
# does, and the printer cannot print.
#
# THE .cfg FILES ARE NOT THE FAULT, though they look like the obvious suspect.
# The stock package's run.sh does
#
#     cp $WORK_DIR/klipper/config/* /usr/data/config/ -rf
#
# and the printer's busybox replaces a dangling symlink with the real file, so
# printer.base.cfg and the seven beside it come back on any stock flash. The
# ff-*.cfg and printer.chamber.cfg links left behind resolve to nothing and
# are included by nothing -- stock's printer.base.cfg does not name them. The
# SAVE_CONFIG block our Klipper wrote into printer.cfg is harmless too:
# configfile.py marks every autosave section valid through access_tracking, so
# `[ff_tool 0]` in there is not a config error on a stock klippy. This script
# restores those files anyway (below) because a machine that has been through
# several releases is not always the machine the last one left, but the
# klipperDaemon restore is the repair.
#
# ORDER: THE STOCK PACKAGE FIRST, THEN THIS. This carries the one file a stock
# flash cannot put back, not the 26MB of FlashForge software component that it
# can. On a printer where the mod is still the running firmware there is
# nothing here to repair and restoring klipperDaemon would put a second klippy
# beside the supervised one, so that case refuses and says so.
#
# The contract with app_startup.sh is runFirmwareExe.sh's, unchanged:
#
#     exit 0     the stick is unmounted, /usr/data/update deleted, end.img on
#                the panel, "power-cycle me".
#     exit != 0  the same cleanup, and the printer goes on booting.
#
# This runs under the printer's busybox ash. Keep it dialect-clean;
# qa/static/test_shell_syntax.py checks it as an installer/*.sh.

WORK_DIR=`dirname $0`
MODDIR=/usr/data/anvil
# FlashForge's config directory -- the one a stock klippy reads. (The mod's own
# is /usr/data/anvil-data/config, and this script does not touch it: it holds
# the owner's calibrated printer.cfg and is what a reinstall of the mod comes
# back to.)
STOCK_CONFIG=/usr/data/config
LOG=/usr/data/anvil-recovery.log
# The stick this was flashed from, which is the only place an owner with no UI
# and no ssh can read an answer from. app_startup.sh unmounts it after we exit,
# so anything written here has to be written before that.
REPORT=/mnt/anvil-recovery.txt

# Rewritten by bin/pack-recovery.sh from the stock package's own values, the
# same way bin/pack.sh does it for the mod installer. The line shape is read
# back with `sed -n 's/^MACHINE=//p'`, so keep it.
MACHINE=Creator5Pro
PID=0029

# ---------------------------------------------------------------- the gates --
# To the console: they run before there is anything to log about, and a refusal
# nobody can see is a printer that "did nothing" for no visible reason.
CHECK_ARCH=`uname -m`
if [ "$CHECK_ARCH" != mips ]; then
    echo "Machine architecture error: $CHECK_ARCH"
    exit 1
fi

# Empty arguments mean an old app_startup.sh that passed none, which FlashForge
# treated as installable. Two non-empty ones must match: klipperDaemon is not
# model-specific, but a package that installs on the wrong machine is a habit
# worth not having.
if [ -n "$1" ] && [ -n "$2" ]; then
    if [ "$1" != "$MACHINE" ] || [ "$2" != "$PID" ]; then
        echo "Firmware does not match machine type: got $1/$2, this package is $MACHINE/$PID."
        exit 1
    fi
fi

[ -f "$WORK_DIR/start.img" ] && cat "$WORK_DIR/start.img" > /dev/fb0 2>/dev/null

exec >>"$LOG" 2>&1
STAMP=`date +%Y%m%d-%H%M%S 2>/dev/null || echo manual`
echo "=== recovery $STAMP ==="

# Collected as it goes and written to the stick at the end, because the panel
# says only "done" and this is a repair an owner is entitled to see the shape
# of. `printf %b` and a growing string rather than a temp file: /tmp is a
# tmpfs the next boot clears, and there is no step here that could not hold
# its own output in a variable.
SUMMARY=""
note() {
    echo "$1"
    SUMMARY="$SUMMARY$1
"
}
finish() {
    # Best-effort: a stick mounted read-only, or one already gone, must not
    # turn a completed repair into a failed one. The log on /usr/data is the
    # copy that always exists.
    if [ -d /mnt ]; then
        {
            echo "Reforge recovery -- $STAMP"
            echo
            printf %s "$SUMMARY"
        } > "$REPORT" 2>/dev/null
        sync
    fi
    exit "$1"
}

# ------------------------------------------- is there anything to repair yet --
# The mod links three stock paths at itself. A stock flash restores two of
# them; if either is STILL a symlink then no stock package has been installed
# since the mod was, and this is a working modded printer rather than a broken
# stock one.
#
# Refusing is the whole point of the check. Restoring FlashForge's
# klipperDaemon here would leave a machine whose stock start.sh is gone (so
# nothing calls it) but whose next mod uninstall-by-flash is no longer the
# thing this was tested against -- and on a printer that still boots the mod,
# anything that DOES call klipperDaemon now forks an unsupervised klippy
# beside the s6 one, fighting it for /dev/ttyS4 and /tmp/uds.
still_modded=""
for stock_path in /usr/prog/PROGRAM/software/firmwareExe /usr/prog/klipper/start.sh; do
    if [ -L "$stock_path" ]; then
        still_modded="$still_modded $stock_path"
    fi
done
if [ -n "$still_modded" ]; then
    note "This printer is still running Reforge, so there is nothing to repair."
    note ""
    note "These are still the mod's links:$still_modded"
    note ""
    note "Flash the stock FlashForge package first. Then flash this one, which"
    note "puts back the single file that stock flash cannot: klipperDaemon."
    finish 1
fi

# --------------------------------------------------------- 1. klipperDaemon --
# THE REPAIR. Everything else in this file is tidying.
DAEMON=/usr/prog/klipper/klipperDaemon
SHIPPED=$WORK_DIR/prog/klipperDaemon

if [ ! -f "$SHIPPED" ]; then
    # Nothing to restore FROM, which makes the package the broken thing. Said
    # rather than worked around: an invented klipperDaemon would be a printer
    # starting klippy on arguments nobody has ever run.
    note "!! this package carries no prog/klipperDaemon -- it cannot repair anything"
    finish 1
fi

if [ -L "$DAEMON" ]; then
    target=`readlink "$DAEMON"`
    note "klipperDaemon was a link to $target -- the mod's, and the reason"
    note "klippy never started. Restoring FlashForge's own."
    restore_daemon=1
elif [ ! -f "$DAEMON" ]; then
    note "klipperDaemon was missing entirely. Restoring FlashForge's own."
    restore_daemon=1
else
    # A regular file is FlashForge's own, whatever version of it this machine
    # has. It is left alone: this package carries one copy, taken from one
    # firmware, and overwriting a working file with it would be a change made
    # for the sake of making one.
    note "klipperDaemon is a real file already -- left as it is."
    restore_daemon=0
fi

if [ "$restore_daemon" = 1 ]; then
    mkdir -p /usr/prog/klipper
    # Through a temp name and a rename: this is the file the next boot's
    # start.sh executes, and a half-written one is a printer that boots into
    # nothing. The rm is because `cp` onto a dangling symlink would create the
    # file at the link's target instead -- inside a /usr/data/anvil that may
    # not even exist.
    rm -f "$DAEMON.recovery-new"
    if cp "$SHIPPED" "$DAEMON.recovery-new" &&
       chmod 755 "$DAEMON.recovery-new" &&
       rm -f "$DAEMON" &&
       mv -f "$DAEMON.recovery-new" "$DAEMON"; then
        sync
        note "klipperDaemon restored."
    else
        rm -f "$DAEMON.recovery-new"
        note "!! could not write $DAEMON -- the printer is unchanged."
        finish 1
    fi
fi

# ----------------------------------------------- 2. the mod's leftover links --
# BY WHAT THEY ARE, not by a list of names. Releases shipped different sets of
# ff-*.cfg over time and a name this file does not know is exactly the one
# still sitting on somebody's printer. A symlink into $MODDIR in FlashForge's
# config directory can only have been put there by the mod, and after a stock
# flash it resolves to nothing.
#
# Only symlinks, and only ones pointing into $MODDIR: a real file here is
# either FlashForge's or the owner's, and neither is this script's to delete.
swept=0
for f in "$STOCK_CONFIG"/*; do
    if [ -L "$f" ]; then
        target=`readlink "$f"`
        case "$target" in
            "$MODDIR"/*)
                rm -f "$f" && swept=`expr $swept + 1`
                echo "swept $f -> $target"
                ;;
        esac
    fi
done
if [ "$swept" -gt 0 ]; then
    note "Removed $swept of the mod's dead config links from $STOCK_CONFIG."
fi

# ------------------------------------------------- 3. FlashForge's own .cfg --
# The eight files the stock software component ships, copied straight out of
# it by bin/pack-recovery.sh. A stock flash restores these itself, so this is
# belt and braces for a machine that has been through more releases than
# anyone can reconstruct -- but it costs nothing and it is the difference
# between "should be fine" and "is".
#
# printer.cfg IS NOT AMONG THEM and is never written here. It is per-unit: the
# SAVE_CONFIG block at its end is every calibrated value on the machine, and
# no package has ever shipped one to put back in its place.
restored=0
if [ -d "$WORK_DIR/config" ]; then
    mkdir -p "$STOCK_CONFIG"
    for f in "$WORK_DIR"/config/*.cfg; do
        [ -f "$f" ] || continue
        name=`basename "$f"`
        # rm first for the same reason as above: if a link is still standing
        # here, cp would follow it instead of replacing it.
        rm -f "$STOCK_CONFIG/$name"
        if cp "$f" "$STOCK_CONFIG/$name"; then
            restored=`expr $restored + 1`
        else
            note "!! could not restore $STOCK_CONFIG/$name"
        fi
    done
    sync
fi
if [ "$restored" -gt 0 ]; then
    note "Restored $restored of FlashForge's own Klipper config files."
    note "Your printer.cfg -- and the calibration saved in it -- was not touched."
fi

note ""
note "Done. Power-cycle the printer; Klipper starts with the UI again."
finish 0

#!/bin/sh
# Point the stock paths at the files the packages own.
#
# Everything the mod installs lives in $MODDIR. What the printer READS is
# elsewhere, at absolute paths FlashForge's scripts and Klipper's config
# choose and we do not: app_startup.sh runs
# /usr/prog/PROGRAM/software/firmwareExe, and klipperDaemon is started from
# /usr/prog/klipper/start.sh. This is the seam -- one symlink per file, so
# $MODDIR stays the only place anything is installed and `apk upgrade` is
# enough to change what the printer runs.
#
# THE KLIPPER CONFIG IS THE ONE PLACE THAT IS COPIED RATHER THAN LINKED INTO,
# and it is not copied into FlashForge's directory. $CONFIG_DIR below is the
# mod's own config directory, seeded ONCE from the stock /usr/data/config and
# the only one klippy is ever pointed at afterwards. /usr/data/config is left
# exactly as the stock firmware last wrote it.
#
# WHY, because it is the whole reason this directory exists. /usr/data is the
# DATA partition: flashing a stock package rewrites /usr/prog and does not
# touch it. So every mod file this script used to put in /usr/data/config --
# a symlinked printer.base.cfg, the ff-*.cfg beside it -- outlived the mod. A
# printer flashed back to stock came up with its printer.base.cfg pointing
# into a $MODDIR that was no longer there, and Klipper refuses to start on an
# [include] it cannot resolve. There is no undo for that from inside the mod:
# nothing of ours runs during a stock flash, and the release that replaced
# stock's printer.base.cfg kept no copy to put back. Not writing there in the
# first place is the only version of this that works, and it makes going back
# to stock the no-op it looks like.
#
# ORDERING. This runs straight after the installer extracts the payload, and
# again from anvil-core's post-upgrade script on an `apk upgrade`. It cannot
# run earlier
# and the links cannot be shipped as files: on a first install they would
# dangle, and on an upgrade they would resolve to the payload being replaced.
#
# The targets under /usr/prog are left alone until then, on purpose. A release
# ships no software component, so /usr/prog/PROGRAM/software still holds
# FlashForge's version directory and their firmwareExe -- which is what
# app_startup.sh's watchdog restores from when a payload never lands. A
# wrapper with nothing behind it would sit there and never start a UI; their
# binary at least brings the printer up.
set -e

MODDIR=${MODDIR:-/usr/data/anvil}

# The live Klipper config: printer.cfg, the stock includes beside it and the
# mod's own, all in one directory because Klipper resolves an [include]
# against the directory of the file doing the including.
#
# NOT under $MODDIR, which the installer deletes outright on every update
# (runFirmwareExe.sh's wipe-and-extract). printer.cfg carries the SAVE_CONFIG
# block -- every calibrated offset on the machine -- so it must survive an
# update by construction and not by being copied to /tmp and back while the
# printer is mid-flash.
CONFIG_DIR=${CONFIG_DIR:-/usr/data/anvil-data/config}
# FlashForge's, and read-only to the mod from here on: it is what a printer
# flashed back to stock boots from.
STOCK_CONFIG=/usr/data/config

# Only on a machine that has the paths below. The build installs this package
# with apk too, and the paths below are absolute -- taken as written, not
# rebased -- so without this the script would act on whatever ran the build.
# (prefix.patch sets APK_NO_CHROOT for the default database root, so a
# maintainer script sees the REAL /usr/prog rather than one inside $MODDIR.)
if [ ! -f /usr/prog/app_startup.sh ]; then
    echo "link-prog: not a printer (no /usr/prog/app_startup.sh) -- nothing to do"
    exit 0
fi

# $1 is relative to $MODDIR, $2 is the absolute path the printer reads. The
# two destinations under /usr/prog are the ones stock run.sh copies to, so this
# stays true as long as that does -- its two cp lines are no-ops now that the
# component ships neither file:
#     cp $WORK_DIR/firmwareExe /usr/prog/PROGRAM/software/
#     cp $WORK_DIR/start.sh    /usr/prog/klipper/start.sh
link_one() {
    src="$MODDIR/$1"
    dst="$2"

    [ -f "$src" ] || { echo "link-prog: no $src -- leaving $dst alone"; return 0; }

    # Already right: change nothing, so a re-run is quiet and cheap.
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
        return 0
    fi

    mkdir -p "$(dirname "$dst")"
    # Via a temp name: replacing the file app_startup.sh is about to execute
    # is not something to do non-atomically. The rm is because `ln -sf` onto
    # an existing symlink-to-directory links INSIDE it rather than replacing.
    rm -f "$dst.anvil-new"
    ln -s "$src" "$dst.anvil-new"
    mv -f "$dst.anvil-new" "$dst"
    echo "link-prog: $dst -> $src"
}

link_one prog/firmwareExe   /usr/prog/PROGRAM/software/firmwareExe
link_one prog/start.sh      /usr/prog/klipper/start.sh
# Stock's `start` forks a second, unsupervised klippy beside the s6 one, so
# this shim has to win. Whether the stock file is there at all does not matter:
# link_one replaces whatever it finds, and this runs after the payload landed.
link_one prog/klipperDaemon /usr/prog/klipper/klipperDaemon

# ---- the live config directory ---------------------------------------------
#
# SEEDED ONCE, from whatever the machine has. The set of files is not ours to
# know: printer.cfg is FlashForge's per-unit file and it includes siblings the
# mod neither ships nor parses -- printer.filament.cfg, printer.vibration.cfg,
# whatever a given firmware version put there. Copying the directory wholesale
# takes them all, including the SAVE_CONFIG block at the end of printer.cfg
# with the machine's calibration in it, so the mod comes up on the printer's
# own numbers rather than on a default.
#
# ONCE, and never again: after this, $CONFIG_DIR is the live config and
# $STOCK_CONFIG is left as the machine had it, untouched from here on.
# Re-seeding would overwrite an owner's printer.cfg with a stale one on every
# update.
#
# The mod's own .cfg files are NOT copied: they are symlinked in below, so an
# `apk upgrade` changes what Klipper reads without going near this directory.
#
# MOONRAKER'S TWO ARE COPIED, when a previous release left them here. This is
# Moonraker's config directory too now -- it runs with
# `-d /usr/data/anvil-data`, and its config directory is <data path>/config --
# so a moonraker-custom.conf an owner wrote is theirs to keep and comes across
# with everything else. moonraker.conf rides along and is then overwritten by
# the installer with the version this release ships, which is what it is for.
#
# THE TEST IS printer.cfg, not the directory: a $CONFIG_DIR that exists but
# has no printer.cfg is a machine that never finished seeding, and it must
# seed on the next boot rather than stay half-configured for ever. printer.cfg
# is copied LAST and through a temp name, so it appears only once everything
# it includes is already beside it -- which is what makes this test mean
# "seeded" and survive a power cut in the middle of the copy.
if [ ! -f "$CONFIG_DIR/printer.cfg" ]; then
    if [ ! -f "$STOCK_CONFIG/printer.cfg" ]; then
        # Nothing to seed FROM. Reported rather than worked around: klippy is
        # about to be started on a printer.cfg that does not exist, and a
        # guess at one would be a wrong printer rather than a stopped one.
        echo "link-prog: !! no $STOCK_CONFIG/printer.cfg -- nothing to seed $CONFIG_DIR from" >&2
    else
        mkdir -p "$CONFIG_DIR"
        for _f in "$STOCK_CONFIG"/*; do
            # -L first, and as an `if` rather than `[ -L ] && continue`:
            # under `set -e` an AND-OR list whose test is false fails the
            # whole list and exits the script. [ -f ] follows the link, so
            # without this test the mod symlinks a previous release left in
            # $STOCK_CONFIG would be copied in as real files -- the exact
            # files being replaced by links a few lines down.
            if [ -L "$_f" ]; then continue; fi
            [ -f "$_f" ] || continue
            case "$(basename "$_f")" in
                printer.cfg) continue ;;
            esac
            cp "$_f" "$CONFIG_DIR/" || echo "link-prog: !! could not seed $_f" >&2
        done
        cp "$STOCK_CONFIG/printer.cfg" "$CONFIG_DIR/printer.cfg.seeding"
        mv -f "$CONFIG_DIR/printer.cfg.seeding" "$CONFIG_DIR/printer.cfg"
        echo "link-prog: $CONFIG_DIR seeded from $STOCK_CONFIG"
    fi
fi
mkdir -p "$CONFIG_DIR"

# Klipper resolves [include] against the directory of the file doing the
# including -- configfile.py: `dirname = os.path.dirname(source_filename)`,
# the path as opened, NOT the resolved target. So a symlinked printer.base.cfg
# finds the seeded printer.filament.cfg and friends beside it, which is what
# lets those stay stock and unpackaged.
#
# EVERY .cfg IN $MODDIR/config, not a list of names: printer.base.cfg and the
# ff-*.cfg are anvil-klipper-config's, timelapse.cfg is anvil-timelapse's, and
# a package added later ships its own the same way. Klipper needs each of them
# beside printer.cfg -- printer.base.cfg includes them by bare name -- and a
# named list here is a file that resolves on a fresh install, where the
# installer copies $MODDIR/config across, and goes stale on the `apk upgrade`
# that only runs this script. Linking whatever is there covers both.
#
# .cfg only. The .conf beside them are Moonraker's, and moonraker-custom.conf
# is the owner's file to edit -- the installer copies those, once, so an edit
# survives rather than being overwritten by a link to the package's copy.
for _f in "$MODDIR"/config/*.cfg; do
    [ -f "$_f" ] || continue
    link_one "config/$(basename "$_f")" "$CONFIG_DIR/$(basename "$_f")"
done

# THE PRINTER SAYS WHICH MODEL IT IS, so nothing ships a marker: app_startup.sh
# is FlashForge's own and carries MACHINE= at its top. It is restored by any
# stock flash, so it stays right even when the payload is wrong.
MACHINE=$(sed -n 's/^MACHINE=//p' /usr/prog/app_startup.sh 2>/dev/null | head -1)
case "$MACHINE" in
    Creator5|Creator5Pro)
        link_one "config/chamber/$MACHINE.cfg" "$CONFIG_DIR/printer.chamber.cfg" ;;
    *)
        # Not fatal: printer.base.cfg includes printer.chamber.cfg
        # unconditionally, so leaving what is there beats replacing it with a
        # guess. Klipper reports a missing include, which is a better failure
        # than the wrong chamber geometry.
        echo "link-prog: !! MACHINE='$MACHINE' is not a model I ship a chamber config for" >&2
        echo "link-prog:    leaving $CONFIG_DIR/printer.chamber.cfg as it is" >&2 ;;
esac

# WHAT AN OLDER RELEASE LEFT IN $STOCK_CONFIG STAYS THERE. Those releases
# symlinked printer.base.cfg and the ff-*.cfg into FlashForge's directory,
# replacing the stock files, and nothing kept a copy of what they replaced --
# so a sweep here could delete the links but could not put stock's own
# printer.base.cfg back, and the directory would still not be the one stock
# left. It is unfixable from inside the mod either way, and deleting files in
# a directory the mod no longer owns is a risk with nothing on the other side
# of it. Stock's run.sh force-copies its printer.base.cfg here on every flash,
# which is the one thing that does repair it, and it repairs it whether or not
# anything was swept.
#
# What this script guarantees is the property from here on: a machine seeded
# above gets nothing new written to $STOCK_CONFIG.

exit 0

#!/usr/bin/env bash
# AFC-Klipper-Add-On -- the extras out of the pinned tarball, unmodified.
# Nothing is compiled.
#
# UPSTREAM'S install-afc.sh IS NOT USED: it clones into a home directory,
# symlinks into a Klipper checkout, edits printer.cfg and moonraker.conf in
# place and registers an update_manager entry -- all of which this repo does
# with packages. The config it would write is ours, in anvil-klipper-config's
# ff-afc.cfg, and none of upstream's macro files are shipped: they implement
# park / poop / cut / kick / brush, which a toolchanger with its own dock and
# purge chute turns off.
#
# GitHub wraps the archive in AFC-Klipper-Add-On-<sha>/; the paths reach
# through that rather than hiding the archive's shape behind --strip-components.
set -euo pipefail
. ./bin/common.sh
. pkgs/lib.sh

pkg_begin afc || exit 0
pkg_unpack "$AFC_TGZ"

_src="$PKG_WORK/src/AFC-Klipper-Add-On-$AFC_VERSION"

# A tarball whose shape changed would stage nothing, which is a clean build and
# a klippy that stops at config load on [AFC].
for _f in AFC.py AFC_Toolchanger.py AFC_extruder.py AFC_utils.py; do
    [ -f "$_src/extras/$_f" ] || pkg_die \
        "afc: no extras/$_f in $(basename "$AFC_TGZ")"
done

# Inside anvil-klipper's klippy tree, because klippy resolves an extra as
# dirname(klippy.py)/extras/<name>.py and looks nowhere else. Two packages,
# different files, one directory -- anvil-klipper's Depends orders the install.
#
# Every module, not only the ones ff-afc.cfg names: AFC imports its siblings
# at load (AFC_unit, AFC_lane, AFC_hub, ...), and the unit types we do not
# configure are small enough that a list to keep in step would cost more than
# they do.
#
# NOT extras/__init__.py. Upstream's is an empty marker for its own test
# suite; the klippy tree already has one, owned by anvil-klipper, and apk
# refuses two packages that own the same path.
for _f in "$_src"/extras/*.py; do
    _n=$(basename "$_f")
    [ "$_n" = "__init__.py" ] && continue
    pkg_stage "$_f" "klipper/klippy/extras/$_n"
done

pkg_ship "klipper/klippy/extras/*.py"
pkg_end

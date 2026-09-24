#!/usr/bin/env bash
# Fluidd publishes a zip of static files with no top-level directory. Install
# that tree beside Mainsail; nginx serves it from the separate port 81 vhost.
set -euo pipefail
. ./bin/common.sh
. pkgs/lib.sh

pkg_begin fluidd || exit 0
pkg_unpack "$FLUIDD_ZIP"

[ -f "$PKG_WORK/src/index.html" ] || pkg_die \
    "fluidd: no index.html at the root of $(basename "$FLUIDD_ZIP") -- the
     archive's shape changed and www/fluidd would be served empty"

pkg_stage "$PKG_WORK/src" "www/fluidd"
pkg_ship "www/fluidd"
pkg_end

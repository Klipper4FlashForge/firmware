#!/usr/bin/env bash
# Build the recovery package.
#   ./bin/pack-recovery.sh    -> work/recovery/<Machine>-anvil-recovery-<date>.tgz
#
# It is the mod package's shape with none of the mod in it: our installer under
# the name app_startup.sh runs, the panel images, and the two things a broken
# printer needs put back --
#
#     prog/klipperDaemon   FlashForge's own, from installer/stock/
#     config/*.cfg         FlashForge's own, out of the stock package here
#
# WHY klipperDaemon IS COMMITTED AND THE CONFIGS ARE NOT. Both are FlashForge's
# files and neither is ours to author. The configs are IN the stock package, so
# they are read out of work/software at build time and the package this was
# built from is the one they came from. klipperDaemon is not in it -- not in
# the software component, not in its md5sum.list, not in run.sh -- it exists
# only on the machine's own /usr/prog, put there at the factory. A build cannot
# read it from anywhere, and the printers this package exists for are exactly
# the ones whose copy is gone. So the repo carries one, taken off a factory
# /usr/prog and checked against its md5 below.
#
# Run ./bin/unpack.sh first: this reads work/software and work/outer, and does
# not need work/modpayload-root or anything else `make build` produces.
set -euo pipefail
. "$(dirname "$0")/common.sh"

# The recorded md5 of installer/stock/klipperDaemon, off a Creator5Pro factory
# /usr/prog running 1.9.7-1.2.9. The gate is not about trusting the file -- it
# is committed, it is reviewed -- but about the one edit nobody means to make:
# a stray line ending or a "helpful" tidy-up turns the file a broken printer
# executes into something no FlashForge machine has ever run.
STOCK_DAEMON_MD5=773741f6a2df3231cd52a3f441014037

DAEMON=installer/stock/klipperDaemon
[ -f "$DAEMON" ] || { echo "no $DAEMON -- the package would repair nothing" >&2; exit 1; }
_have=$(md5sum "$DAEMON" | awk '{print $1}')
if [ "$_have" != "$STOCK_DAEMON_MD5" ]; then
    echo "$DAEMON has changed:" >&2
    echo "    expected $STOCK_DAEMON_MD5" >&2
    echo "    got      $_have" >&2
    echo "  This file is FlashForge's and is restored verbatim onto printers." >&2
    echo "  If the change is deliberate, update STOCK_DAEMON_MD5 here and say" >&2
    echo "  in the commit message which machine the new copy came off." >&2
    exit 1
fi

[ -d work/outer ] || { echo "run ./bin/unpack.sh first" >&2; exit 1; }
[ -d work/software/klipper/config ] || {
    echo "no work/software/klipper/config -- run ./bin/unpack.sh first" >&2; exit 1; }

STAGE=work/stage-recovery
rm -rf "$STAGE"
# NOT work/out, for two reasons that each cost a run to find. The replica lane
# installs "the newest .tgz in work/out" as the mod under test, so a recovery
# package there is installed instead -- and reports itself as a mod that never
# reached the userdata partition. And bin/pack.sh begins `rm -rf work/out`, so
# the next `make build` would delete these without mentioning them.
OUT=work/recovery
mkdir -p "$STAGE/prog" "$STAGE/config" "$OUT"

# Which model. Same resolution as bin/pack.sh, and for the same reason: the
# gate baked into the installer and the filename app_startup.sh globs for have
# to agree.
PKG_MACHINE=$(cat work/.pkg_machine 2>/dev/null || echo "")
PKG_PID=$(cat work/.pkg_pid 2>/dev/null || echo "")
[ "$PKG_MACHINE" = unknown ] && PKG_MACHINE=""
[ "$PKG_PID" = unknown ] && PKG_PID=""
if [ -n "$PKG_MACHINE" ] && [ "$PKG_MACHINE" != "${TARGET_MACHINE:-$PKG_MACHINE}" ]; then
    echo "MODEL MISMATCH: stock package is for '$PKG_MACHINE', TARGET_MACHINE='$TARGET_MACHINE'" >&2
    exit 1
fi
OUT_MACHINE="${PKG_MACHINE:-${TARGET_MACHINE:-Creator5Pro}}"
OUT_PID="${PKG_PID:-${TARGET_PID:-0029}}"

echo ">> generating runFirmwareExe.sh ($OUT_MACHINE/$OUT_PID)"
sed -e "s/^MACHINE=.*/MACHINE=$OUT_MACHINE/" \
    -e "s/^PID=.*/PID=$OUT_PID/" \
    installer/recovery.sh > "$STAGE/runFirmwareExe.sh"
chmod +x "$STAGE/runFirmwareExe.sh"
# The substitutions are not optional: a package whose gate still says
# Creator5Pro because a sed missed installs on the wrong machine.
for _want in "MACHINE=$OUT_MACHINE" "PID=$OUT_PID"; do
    grep -qxF "$_want" "$STAGE/runFirmwareExe.sh" || {
        echo "runFirmwareExe.sh has no '${_want%%=*}=' line as expected" >&2; exit 1; }
done
sh -n "$STAGE/runFirmwareExe.sh" || {
    echo "the generated runFirmwareExe.sh does not parse" >&2; exit 1; }

cp -f "$DAEMON" "$STAGE/prog/klipperDaemon"
chmod 755 "$STAGE/prog/klipperDaemon"

# FlashForge's own Klipper configs, straight out of the package bin/unpack.sh
# opened -- the same eight files stock's run.sh copies to /usr/data/config.
# printer.cfg is not among them and never has been: it is per-unit, and the
# calibration in its SAVE_CONFIG block is the owner's.
cp -f work/software/klipper/config/*.cfg "$STAGE/config/"
echo ">> carrying $(ls -1 "$STAGE/config" | wc -l) stock .cfg files"

cp -f installer/start.img "$STAGE/start.img"
for f in end.img play; do
    [ -f "work/outer/$f" ] && cp -f "work/outer/$f" "$STAGE/"
done

echo ">> package contents:"
find "$STAGE" -type f | sed "s|^$STAGE/|   |"

OUTFILE="$OUT/${OUT_MACHINE}-${MOD_NAME:-anvil}-recovery-${MOD_VER:?}.tgz"
echo ">> tarring + encrypting"
# Not gzipped despite the .tgz name: /usr/prog/bin/unTar pipes the decrypted
# stream straight into `tar xvf -`. The model prefix is what app_startup.sh
# globs for.
tar -cf - -C "$STAGE" . | openssl des3 -salt -md md5 -k "$FF_KEY" > "$OUTFILE"

echo
echo "Recovery package:"
ls -lh "$OUTFILE" | awk '{print "   "$9"  "$5}'
echo "   installs on: $OUT_MACHINE only"
echo
echo "Sanity check (decrypt + list):"
openssl des3 -d -k "$FF_KEY" -salt -md md5 -in "$OUTFILE" 2>/dev/null \
    | tar -tvf - | sed 's/^/   /'
echo
echo "FLASH THE STOCK FLASHFORGE PACKAGE FIRST, then this one."
echo "Afterwards: anvil-recovery.txt on the stick, /usr/data/anvil-recovery.log"
echo "on the printer."

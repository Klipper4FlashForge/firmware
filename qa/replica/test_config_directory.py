"""The config directory the mod owns, and the one it must not touch.

WHY THIS FILE STAGES ITS OWN CODE. Every other question about an install is
put to the machine the bake produced, and that machine is built by installing
`work/out/*.tgz` -- a package built at some point in the past. The directory
this file is about is created by anvil-link-prog.sh, so on a replica baked
from a package that predates it there is nothing to ask. Waiting for a rebuilt
package would mean the gate can only run after a full build, which is exactly
when nobody runs it.

So the script is staged from the CHECKOUT and run on the machine, the way
test_upgrade.py stages runFirmwareExe.sh. What is mocked is only WHEN it ran:
everything else is real -- a real printer.cfg with FlashForge's includes beside
it, a real $MODDIR/config full of the ff-*.cfg the installed package shipped,
a real /usr/prog/app_startup.sh to read MACHINE= out of. The seeding and the
linking are performed by the file in the tree, on that.

WHAT IT IS FOR. /usr/data is the DATA partition: flashing a stock FlashForge
package rewrites /usr/prog and does not touch it. A mod symlink left in
/usr/data/config therefore outlives the mod, and a stock Klipper started on a
printer.base.cfg pointing into a $MODDIR that is gone does not start at all --
Klipper treats an [include] it cannot resolve as fatal. Nothing of the mod's
runs during a stock flash, so there is no undo to write; the property has to
be that the mod never writes there. That is the last test in this file, and it
is asked as a DELTA rather than as an absolute, because a machine that has been
through an older release already has that directory full of its symlinks and
no code can un-write them now.
"""
import re

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.replica

MODDIR = "/usr/data/anvil"
# FlashForge's, which the mod reads once and never writes.
STOCK_CONFIG = "/usr/data/config"
# The mod's own, which anvil-link-prog.sh creates. Outside $MODDIR, so the
# installer's wipe-and-extract cannot reach printer.cfg.
CONFIG_DIR = "/usr/data/anvil-data/config"

# The file under test, and where it is staged on the machine.
SOURCE = ROOT / "pkgs" / "anvil-core" / "payload" / "bin" / "anvil-link-prog.sh"
SCRIPT = "/tmp/anvil-link-prog.sh"

# Planted in FlashForge's printer.cfg by tools/replica/printer/seed-prog.sh.
# It is the only thing that can tell a seeded copy from an invented one.
MARKER = "USER-CONFIG-MUST-SURVIVE"


def _listing(box, path):
    """Every name in `path`, with what it is: a real file, or the link target.

    Compared before and after a run, so the assertion can be about what the
    script CHANGED rather than about what the machine happened to hold.
    """
    out = box.sh(
        "for f in %s/*; do [ -e \"$f\" ] || [ -L \"$f\" ] || continue; "
        "if [ -L \"$f\" ]; then printf '%%s -> %%s\\n' \"$f\" \"$(readlink \"$f\")\"; "
        "else printf '%%s (file)\\n' \"$f\"; fi; done" % path).out
    return set(ln.strip() for ln in out.splitlines() if ln.strip())


@pytest.fixture(scope="module")
def linked(printer):
    """The machine after the checkout's anvil-link-prog.sh has run on it.

    The directory is removed first: this file is about what a FIRST install
    does, and a replica reused between runs would otherwise be testing the
    second one. The removal is safe because nothing outside this module reads
    it -- on a machine baked from a package that predates the move, klippy is
    still started on FlashForge's copy.
    """
    box = printer
    if not SOURCE.is_file():
        pytest.fail(
            "no %s in the checkout, and it is the file under test" % SOURCE)

    staged = box.sh("cat > %s <<'ANVIL_EOF'\n%s\nANVIL_EOF\nchmod +x %s"
                    % (SCRIPT, SOURCE.read_text(), SCRIPT))
    if not staged.ok:
        pytest.fail("could not stage the link script: %s" % staged.text)

    # What FlashForge's directory held before, so the last test can ask what
    # this run added to it.
    box.stock_before = _listing(box, STOCK_CONFIG)

    wiped = box.sh("rm -rf %s" % CONFIG_DIR)
    if not wiped.ok:
        pytest.fail("could not clear %s: %s" % (CONFIG_DIR, wiped.text))

    run = box.sh("sh %s" % SCRIPT)
    if not run.ok:
        pytest.fail(
            "anvil-link-prog.sh failed, so nothing below means anything:\n%s"
            % run.text)
    box.link_log = run.text
    return box


# ------------------------------------------------------------- the seeding

def test_the_directory_is_seeded_from_the_machines_own_config(linked):
    """A COPY of what the printer had, not a shipped default.

    printer.cfg carries the SAVE_CONFIG block with every calibrated value on
    the machine, so seeding it wrong is not a missing file -- it is a printer
    that comes up on somebody else's numbers. The marker is the replica's own,
    so a printer.cfg the mod invented fails here while one that merely exists
    would pass a check for existence.
    """
    cfg = linked.file(CONFIG_DIR + "/printer.cfg")
    assert cfg.exists, (
        "no printer.cfg in %s -- the seeding did not happen and klippy has no "
        "config to start from. What the script said:\n%s"
        % (CONFIG_DIR, linked.link_log))
    assert MARKER in cfg.text, (
        "%s/printer.cfg does not carry the machine's own marker, so it was "
        "not seeded from %s and the SAVE_CONFIG block went with it"
        % (CONFIG_DIR, STOCK_CONFIG))


def test_the_stock_includes_came_across(linked):
    """printer.base.cfg includes FlashForge's own files -- printer.filament.cfg
    and friends -- which the mod neither ships nor parses. They live beside
    printer.cfg and are copied with it, because Klipper resolves an [include]
    against the directory of the file doing the including and there is nothing
    else to resolve them to."""
    stock = linked.sh(
        "for f in %s/*.cfg; do [ -L \"$f\" ] && continue; [ -f \"$f\" ] || continue; "
        "basename \"$f\"; done" % STOCK_CONFIG).out.split()
    assert stock, (
        "%s holds no real .cfg files at all, so this test is vacuous -- see "
        "tools/replica/printer/seed-prog.sh" % STOCK_CONFIG)

    missing = [n for n in stock if not linked.file(CONFIG_DIR + "/" + n).exists]
    assert not missing, (
        "seeded %s but left FlashForge's own configs behind: %s. Klipper "
        "treats a missing include as fatal, so the printer would not start."
        % (CONFIG_DIR, missing))


def test_a_second_run_does_not_re_seed(linked):
    """Seeding is once, ever. printer.cfg is the owner's from the moment it is
    copied -- SAVE_CONFIG writes it on every calibration -- so a re-seed on the
    next `apk upgrade` would silently restore the pre-install numbers.

    The edit is planted here and read back after another run, which is also
    what makes the run idempotent rather than merely repeatable.
    """
    box = linked
    planted = box.sh("echo '# TUNED BY THE OWNER' >> %s/printer.cfg"
                     % CONFIG_DIR)
    if not planted.ok:
        pytest.fail("could not plant an edit: %s" % planted.text)

    again = box.sh("sh %s" % SCRIPT)
    assert again.ok, "a second run failed: %s" % again.text
    assert "TUNED BY THE OWNER" in box.file(CONFIG_DIR + "/printer.cfg").text, (
        "the second run re-seeded printer.cfg over the owner's edit -- every "
        "calibrated value on the machine would be lost by an update")
    assert "seeded" not in again.text, (
        "the second run reported seeding again:\n%s" % again.text)


# -------------------------------------------------------------- the linking

def test_the_mods_configs_are_links_into_moddir(linked):
    """Symlinked, not copied: an `apk upgrade` then changes what Klipper reads
    by replacing the file the link points at, with no .tgz and no installer.

    EVERY .cfg IN $MODDIR/config, because that is the rule the script applies
    rather than a list of names it knows. printer.base.cfg and the ff-*.cfg
    are anvil-klipper-config's, timelapse.cfg is anvil-timelapse's, and the
    next package to ship one is covered by the same glob. A named list here
    would pass while a package's config never reached the directory -- which
    is the failure this widening was written for.
    """
    shipped = linked.sh(
        "for f in %s/config/*.cfg; do "
        "[ -f \"$f\" ] || continue; basename \"$f\"; done"
        % MODDIR).out.split()
    assert shipped, (
        "the installed package shipped no .cfg in %s/config, so there was "
        "nothing for the script to link" % MODDIR)

    wrong = []
    for name in shipped:
        target = linked.sh("readlink %s/%s" % (CONFIG_DIR, name)).out.strip()
        if target != "%s/config/%s" % (MODDIR, name):
            wrong.append("%s -> %r" % (name, target or "(not a link)"))
    assert not wrong, (
        "these are not links into %s/config: %s" % (MODDIR, wrong))


def test_the_chamber_config_matches_the_machine(linked):
    """One file per model, and the printer says which it is: MACHINE= at the
    top of FlashForge's own app_startup.sh, which any stock flash restores. The
    link is named printer.chamber.cfg whichever model it is, because that is
    what printer.base.cfg includes."""
    machine = linked.sh(
        "sed -n 's/^MACHINE=//p' /usr/prog/app_startup.sh | head -1").out.strip()
    assert machine, "no MACHINE= in app_startup.sh -- this is not a printer"

    target = linked.sh("readlink %s/printer.chamber.cfg" % CONFIG_DIR).out.strip()
    assert target == "%s/config/chamber/%s.cfg" % (MODDIR, machine), (
        "printer.chamber.cfg points at %r on a %s -- the wrong chamber "
        "geometry, or none" % (target, machine))


def test_every_include_resolves_in_the_new_directory(linked):
    """The whole point of putting the mod's files and the stock ones in one
    directory. Klipper resolves an [include] against the directory of the file
    doing the including -- the path as opened, not the resolved target -- so a
    symlinked printer.base.cfg must find its siblings here."""
    base = linked.file(CONFIG_DIR + "/printer.base.cfg")
    assert base.exists, "no printer.base.cfg in %s" % CONFIG_DIR

    included = {n.strip() for n in re.findall(r"\[include\s+([^\]]+)\]",
                                              base.text)}
    assert len(included) > 4, (
        "printer.base.cfg declares only %d includes (%s), which is fewer than "
        "FlashForge's four alone" % (len(included), ", ".join(sorted(included))))

    # `ls`, because Klipper accepts a glob in an [include].
    missing = [n for n in sorted(included)
               if not linked.sh("ls -1 %s/%s 2>/dev/null"
                                % (CONFIG_DIR, n)).out.strip()]
    assert not missing, (
        "printer.base.cfg includes %s and no such file is in %s. Klipper "
        "treats that as fatal, so the printer would not start."
        % (missing, CONFIG_DIR))


# ------------------------------------------------- what stock boots from

def test_the_run_writes_nothing_into_flashforges_directory(linked):
    """THE DOWNGRADE GATE.

    Asked as a delta, and it has to be: a machine that has been through a
    release which symlinked into /usr/data/config still holds those links, and
    nothing can restore what they replaced -- the release that overwrote
    stock's printer.base.cfg kept no copy of it. What this code owes is that it
    adds nothing, so that a printer installing it for the first time keeps a
    directory a stock flash can boot from.
    """
    after = _listing(linked, STOCK_CONFIG)
    added = sorted(after - linked.stock_before)
    assert not added, (
        "the run added these to %s:\n  %s\nThat directory is FlashForge's and "
        "a printer flashed back to stock boots from it."
        % (STOCK_CONFIG, "\n  ".join(added)))


def test_flashforges_printer_cfg_is_left_alone(linked):
    """The seeding reads it and copies it. A machine flashed back to stock has
    to find it exactly as stock last wrote it, marker and all."""
    stock = linked.file(STOCK_CONFIG + "/printer.cfg")
    assert stock.exists, (
        "%s/printer.cfg is gone -- a stock firmware would come up with no "
        "config at all" % STOCK_CONFIG)
    assert MARKER in stock.text, (
        "%s/printer.cfg no longer carries the machine's own marker, so "
        "something rewrote it" % STOCK_CONFIG)

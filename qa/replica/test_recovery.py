"""The printer that came back from a stock flash with a UI and no Klipper.

WHAT IS BROKEN ON IT, and it is one file. Every release links three stock
paths at the mod -- /usr/prog/PROGRAM/software/firmwareExe,
/usr/prog/klipper/start.sh and /usr/prog/klipper/klipperDaemon. A stock
FlashForge package restores the first two, because its run.sh copies its own
over them and the printer's busybox `cp -f` unlinks a symlink rather than
writing through it. It restores nothing over the third: the stock package has
no klipperDaemon at all -- not in the software component, not in its
md5sum.list, not in run.sh.

So the link survives, and the mod's klipperDaemon is a shim whose `start` does
nothing on purpose (klippy is an s6-rc longrun under the mod, and a second
unsupervised one would fight it for /dev/ttyS4 and /tmp/uds). Stock's start.sh
ends in `klipperDaemon start`. The UI comes up, klippy does not, and no number
of further stock flashes changes it.

WHAT THIS FILE ASSERTS, in the order the machine goes through it:

  1. the mod really does leave klipperDaemon pointing into $MODDIR,
  2. the recovery package REFUSES to run while the mod is still the firmware,
  3. after the stock flash's effect on those three paths, `klipperDaemon
     start` starts nothing -- the brick, demonstrated rather than described,
  4. the recovery installer puts FlashForge's own file back, and then the same
     command forks a real klippy,
  5. it clears the mod's dead config links and restores FlashForge's .cfg,
  6. and it does not touch printer.cfg, which holds the machine's calibration.

THE DOWNGRADE IS SIMULATED IN ONE RESPECT ONLY. Running the real 93MB stock
package through app_startup.sh here would prove nothing this does not: what a
stock flash does to these three paths is copy two files over two of them, and
`test_a_stock_cp_replaces_a_dangling_link` runs that exact `cp` shape, with the
printer's own busybox, against a dangling link. The fixture then puts the
machine in the state that produces -- two real files, one surviving mod link --
and everything after it is real.

WHAT THIS MODULE DOES TO THE MACHINE: it replaces two paths under /usr/prog
and installs FlashForge's klipperDaemon over the mod's. The container is
module-scoped and nothing else shares it. Tests are sequential steps against
one machine, in file order.
"""
import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.replica

MODDIR = "/usr/data/anvil"
STOCK_CONFIG = "/usr/data/config"
DAEMON = "/usr/prog/klipper/klipperDaemon"
FIRMWARE_EXE = "/usr/prog/PROGRAM/software/firmwareExe"
START_SH = "/usr/prog/klipper/start.sh"

# Staged from the CHECKOUT, like test_upgrade.py stages the mod installer: the
# file under test is the one in the tree, not the copy some earlier bake left
# on the replica. Unsubstituted -- bin/pack-recovery.sh rewrites MACHINE=/PID=
# when it packs it, and the model gate is skipped here by passing no arguments,
# the case FlashForge's own script documents as "old firmware, upgradeable".
SOURCE = ROOT / "installer" / "recovery.sh"
DAEMON_SOURCE = ROOT / "installer" / "stock" / "klipperDaemon"
# $WORK_DIR for the run: app_startup.sh unpacks a package into a directory and
# runs runFirmwareExe.sh out of it, so the installer finds prog/ and config/
# beside itself. This is that directory.
WORK = "/tmp/recovery"
INSTALLER = WORK + "/runFirmwareExe.sh"

# Planted in printer.cfg by tools/replica/printer/seed-prog.sh. The only thing
# that can tell the machine's own config from one a package put there.
MARKER = "USER-CONFIG-MUST-SURVIVE"

# FlashForge's own, off a factory /usr/prog. bin/pack-recovery.sh gates the
# committed copy on this too; here it is what proves the file on the machine
# afterwards came from the package rather than from anything the mod left.
STOCK_DAEMON_MD5 = "773741f6a2df3231cd52a3f441014037"


def _md5(box, path):
    return box.sh("md5sum %s 2>/dev/null" % path).out.split(" ")[0].strip()


@pytest.fixture(scope="module")
def staged(printer):
    """The recovery package, unpacked on the machine the way app_startup.sh
    would leave it: the installer, prog/klipperDaemon, and FlashForge's .cfg.

    The .cfg files come off the machine's OWN /usr/data/config rather than out
    of the checkout, and that is not a shortcut -- the replica is baked by
    installing the stock package, so those files are literally the ones stock's
    run.sh copied there. Staging them from the host would mean shipping a copy
    of work/software into the container to prove a point about a directory the
    container already has.
    """
    box = printer
    for f in (SOURCE, DAEMON_SOURCE):
        if not f.is_file():
            pytest.fail("no %s in the checkout, and it is under test" % f)

    box.sh("rm -rf %s; mkdir -p %s/prog %s/config" % (WORK, WORK, WORK))
    box.write(INSTALLER, SOURCE.read_text(), mode="755")
    box.write(WORK + "/prog/klipperDaemon", DAEMON_SOURCE.read_text(),
              mode="755")
    copied = box.sh(
        "set -e\n"
        "for f in %s/printer.base.cfg %s/printer.filament.cfg "
        "%s/printer.macro.cfg %s/printer.mesh.cfg %s/printer.motor.cfg "
        "%s/printer.override.cfg %s/printer.probe.cfg "
        "%s/printer.vibration.cfg; do\n"
        "    [ -f \"$f\" ] || continue\n"
        "    cp \"$f\" %s/config/\n"
        "done\n"
        % ((STOCK_CONFIG,) * 8 + (WORK,)))
    if not copied.ok:
        pytest.fail("could not stage the stock configs: %s" % copied.text)

    staged_daemon = _md5(box, WORK + "/prog/klipperDaemon")
    assert staged_daemon == STOCK_DAEMON_MD5, (
        "the klipperDaemon staged from the checkout is not FlashForge's: "
        "md5 %s, expected %s. Everything below would then be testing a file "
        "no printer has ever run." % (staged_daemon, STOCK_DAEMON_MD5))
    return box


def _run_recovery(box):
    """Run the staged installer with no arguments, and hand back its Result."""
    return box.sh("sh %s" % INSTALLER, timeout=300)


# ------------------------------------------------- 1. what the mod leaves


def test_the_mod_points_klipperdaemon_at_itself(staged):
    """The precondition for everything else, asserted rather than assumed.

    If this ever stops being true -- a release that stops linking it, or an
    installer that puts the stock file back itself -- the recovery package has
    no reason to exist and this whole module should go with it.
    """
    box = staged
    link = box.sh("readlink %s" % DAEMON).out.strip()
    assert link.startswith(MODDIR), (
        "%s is not a link into %s (readlink said %r), so this replica is not "
        "a machine that has had the mod installed and nothing below is "
        "testing the case it claims to." % (DAEMON, MODDIR, link))


def test_a_stock_cp_replaces_a_dangling_link(staged):
    """`cp -rf` over a dangling symlink: does it repair, or write through?

    This is the line stock's run.sh uses for the config directory, and the
    whole reason the .cfg files are NOT what breaks a downgraded printer. The
    answer is busybox's, not POSIX's, so it is asked of the printer's own
    busybox rather than reasoned about.
    """
    box = staged
    out = box.sh(
        "set -e\n"
        "rm -rf /tmp/cp-probe; mkdir -p /tmp/cp-probe/src /tmp/cp-probe/dst\n"
        "echo STOCK > /tmp/cp-probe/src/printer.base.cfg\n"
        "ln -s /usr/data/gone/printer.base.cfg /tmp/cp-probe/dst/printer.base.cfg\n"
        "cp /tmp/cp-probe/src/* /tmp/cp-probe/dst/ -rf\n"
        "if [ -L /tmp/cp-probe/dst/printer.base.cfg ]; then echo LINK; "
        "else cat /tmp/cp-probe/dst/printer.base.cfg; fi\n")
    assert out.out.strip() == "STOCK", (
        "a stock flash does NOT repair a dangling .cfg link on this busybox "
        "(got %r). The recovery package's config pass is then load-bearing "
        "rather than belt and braces, and its header says otherwise."
        % out.text)


# --------------------------------- 2. it refuses while the mod is installed


def test_it_refuses_while_the_mod_is_still_the_firmware(staged):
    """A modded printer has nothing to repair, and repairing it would hurt.

    Restoring FlashForge's klipperDaemon here would put a `start` that forks an
    unsupervised klippy next to the s6-supervised one, both holding
    /dev/ttyS4. So the refusal is the feature, and the exit code has to be
    non-zero -- app_startup.sh reads exit 0 as "flashed, now power-cycle".
    """
    box = staged
    before = _md5(box, DAEMON)
    done = _run_recovery(box)
    assert not done.ok, (
        "the recovery installer ran to completion on a machine that still has "
        "the mod installed. It must refuse: %s" % done.text)
    assert _md5(box, DAEMON) == before, (
        "it refused but still changed %s" % DAEMON)
    report = box.file("/mnt/anvil-recovery.txt")
    assert report.exists and "still running Reforge" in report.text, (
        "the refusal left nothing on the USB stick, which is the only place "
        "an owner with no UI can read it. /mnt held: %r"
        % (report.text if report.exists else "<no file>"))


# ------------------------------------------- 3. the machine after a downgrade


@pytest.fixture(scope="module")
def downgraded(staged):
    """The machine as a stock flash leaves it.

    firmwareExe and start.sh become real files, because that is what stock's
    run.sh copies over them (see test_a_stock_cp_replaces_a_dangling_link for
    the copy itself). klipperDaemon is left exactly as it is -- the mod's link
    -- because nothing in the stock package touches it, and that is the defect
    under test.

    The two files are given the content of the mod's own copies, which is not
    a claim that FlashForge's are the same. Nothing below reads them: they
    exist to answer "is this still a modded printer", which the installer asks
    with `[ -L ]`.
    """
    box = staged
    made = box.sh(
        "set -e\n"
        "for p in %s %s; do\n"
        "    [ -L \"$p\" ] || continue\n"
        "    cp \"$p\" \"$p.real\"\n"      # follows the link: content, not link
        "    rm -f \"$p\"\n"
        "    mv \"$p.real\" \"$p\"\n"
        "done\n" % (FIRMWARE_EXE, START_SH))
    if not made.ok:
        pytest.fail("could not simulate the stock flash: %s" % made.text)
    return box


def test_the_downgraded_machine_starts_no_klipper(downgraded):
    """The bug itself: stock's start.sh calls this, and nothing happens.

    A pid file is the test because start-stop-daemon -m writes one when it
    forks something; the mod's shim writes none, prints a line about s6 and
    exits 0 -- a success code for a klippy that does not exist, which is
    exactly why the printer looks fine and cannot print.
    """
    box = downgraded
    box.sh("rm -f /run/klipper.pid")
    done = box.sh("sh %s start" % DAEMON)
    assert done.ok, (
        "the mod's klipperDaemon did not even exit 0 -- this machine is in "
        "some other broken state and the test below would not mean what it "
        "says: %s" % done.text)
    assert not box.file("/run/klipper.pid").exists, (
        "something DID start klippy on a downgraded machine, so the defect "
        "this package repairs is not present here and the repair below proves "
        "nothing. klipperDaemon said: %r" % done.text)


# ------------------------------------------------------------ 4. the repair


@pytest.fixture(scope="module")
def repaired(downgraded):
    """The downgraded machine, after the recovery installer has run on it."""
    box = downgraded
    # printer.cfg as the machine had it, so the last test can compare rather
    # than merely check that a file is still there.
    box.printer_cfg_before = box.file(STOCK_CONFIG + "/printer.cfg").text
    done = _run_recovery(box)
    if not done.ok:
        pytest.fail(
            "the recovery installer failed on a downgraded machine, so "
            "nothing below means anything:\n%s\n--- log ---\n%s"
            % (done.text, box.file("/usr/data/anvil-recovery.log").text))
    box.recovery_out = done.text
    return box


def test_flashforges_own_klipperdaemon_is_back(repaired):
    """A real file, byte-for-byte the factory one -- not a link, not ours."""
    box = repaired
    is_link = box.sh("[ -L %s ] && echo link || echo file" % DAEMON).out.strip()
    assert is_link == "file", (
        "%s is still a symlink after the repair" % DAEMON)
    got = _md5(box, DAEMON)
    assert got == STOCK_DAEMON_MD5, (
        "%s is a real file now but not FlashForge's: md5 %s, expected %s"
        % (DAEMON, got, STOCK_DAEMON_MD5))


def test_and_it_starts_a_real_klippy(repaired):
    """The point of the whole package, put to the machine.

    klippy will not get far -- there is no MCU on the other end of /dev/ttyS2
    in a container -- but "start-stop-daemon forked the printer's python at
    the printer's klippy.py" is the thing that was missing, and it is either
    in the process table or it is not.
    """
    box = repaired
    box.sh("rm -f /run/klipper.pid")
    done = box.sh("sh %s start" % DAEMON)
    assert done.ok, "klipperDaemon start failed: %s" % done.text
    running = box.pgrep("klippy.py")
    pid_file = box.file("/run/klipper.pid")
    assert running or pid_file.exists, (
        "klipperDaemon start left neither a klippy process nor "
        "/run/klipper.pid, so a downgraded printer still has no Klipper. It "
        "said: %r" % done.text)
    # Left running, it holds the log and the uds for whatever runs next.
    box.sh("sh %s stop" % DAEMON)


def test_the_mods_dead_config_links_are_gone(repaired):
    """Every symlink into $MODDIR in FlashForge's directory, swept by shape.

    Not by name: releases shipped different sets of ff-*.cfg, and the one
    still on somebody's printer is the name this repo would forget to list.
    """
    box = repaired
    left = box.sh(
        "for f in %s/*; do [ -L \"$f\" ] || continue; "
        "case \"$(readlink \"$f\")\" in %s/*) echo \"$f\";; esac; done"
        % (STOCK_CONFIG, MODDIR)).out.split()
    assert not left, (
        "these links into %s are still in %s after the repair: %s"
        % (MODDIR, STOCK_CONFIG, " ".join(left)))


def test_flashforges_own_configs_are_real_files(repaired):
    """The eight the stock package ships, each a file rather than a link."""
    box = repaired
    for name in ("printer.base.cfg", "printer.filament.cfg",
                 "printer.macro.cfg", "printer.mesh.cfg", "printer.motor.cfg",
                 "printer.override.cfg", "printer.probe.cfg",
                 "printer.vibration.cfg"):
        path = "%s/%s" % (STOCK_CONFIG, name)
        kind = box.sh("if [ -L %s ]; then echo link; elif [ -f %s ]; then "
                      "echo file; else echo missing; fi" % (path, path)).out.strip()
        assert kind == "file", "%s is %s after the repair" % (path, kind)


def test_printer_cfg_is_not_touched(repaired):
    """The owner's calibration, which no package has a copy of to put back.

    printer.cfg is per-unit and its SAVE_CONFIG block is every value the
    machine was calibrated to. A recovery that "restored the config directory"
    including this file would fix the boot and lose the printer.
    """
    box = repaired
    now = box.file(STOCK_CONFIG + "/printer.cfg")
    assert now.exists, "the repair removed printer.cfg"
    assert MARKER in now.text, (
        "printer.cfg no longer carries the machine's own marker -- it was "
        "replaced by something the package brought")
    assert now.text == box.printer_cfg_before, (
        "printer.cfg changed during the repair")


def test_it_says_what_it_did_on_the_usb_stick(repaired):
    """The owner has no UI worth reading and probably no ssh. The stick is it."""
    box = repaired
    report = box.file("/mnt/anvil-recovery.txt")
    assert report.exists, "no /mnt/anvil-recovery.txt after a successful repair"
    assert "klipperDaemon restored" in report.text, (
        "the report does not mention the repair it performed: %r" % report.text)
    assert "Power-cycle" in report.text, (
        "the report does not tell the owner what to do next: %r" % report.text)

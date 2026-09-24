"""The user-owned boot-script hook, executed by the printer's BusyBox."""
import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.replica

SOURCE = (ROOT / "pkgs" / "anvil-core" / "payload" / "bin" /
          "run-scripts.sh")
RUNNER = "/tmp/anvil-run-scripts.sh"
SCRIPTS = "/tmp/anvil-custom-scripts"
LOG = "/tmp/anvil-custom-scripts.log"
SERVICE = "/usr/data/anvil/etc/s6-rc/source/run-scripts"


@pytest.fixture(scope="module")
def scripts(printer):
    if not SOURCE.is_file():
        pytest.fail("custom-script runner is missing from the checkout")
    printer.write(RUNNER, SOURCE.read_text(), mode="755")
    return printer


def _run(box):
    return box.sh(
        "SCRIPTS_DIR=%s CUSTOM_SCRIPTS_LOG=%s sh %s"
        % (SCRIPTS, LOG, RUNNER))


def test_scripts_run_alphabetically_and_one_failure_does_not_stop_the_rest(scripts):
    box = scripts
    box.sh("rm -rf %s %s && mkdir -p %s" % (SCRIPTS, LOG, SCRIPTS))
    box.write(SCRIPTS + "/10-first.sh", "echo first\n")
    box.write(SCRIPTS + "/20-fails.sh", "echo failing\nexit 7\n")
    box.write(SCRIPTS + "/30-last.sh", "#!/bin/sh\necho last\n", mode="755")
    box.write(SCRIPTS + "/ignored.txt", "echo must-not-run\n", mode="755")

    run = _run(box)
    assert run.ok, "the runner failed after a user script failed: %s" % run.text
    log = box.file(LOG).text
    assert "must-not-run" not in log
    assert log.index("first") < log.index("failing") < log.index("last")
    assert "Running 10-first.sh through sh" in log
    assert "Running 30-last.sh directly" in log
    assert log.rstrip().endswith("Finished custom scripts")


def test_an_empty_run_is_successful_and_replaces_the_previous_log(scripts):
    scripts.sh("rm -rf %s && mkdir -p %s" % (SCRIPTS, SCRIPTS))
    run = _run(scripts)
    assert run.ok, run.text
    log = scripts.file(LOG).text
    assert "first" not in log and "failing" not in log and "last" not in log
    assert "Starting custom scripts" in log
    assert log.rstrip().endswith("Finished custom scripts")


def test_boot_service_runs_the_hook_with_a_bounded_transition(scripts):
    assert scripts.file(SERVICE + "/type").text.strip() == "oneshot"
    assert scripts.file(SERVICE + "/timeout-up").text.strip() == "30000"
    assert "run-scripts.sh" in scripts.file(SERVICE + "/up").text
    assert scripts.file(
        "/usr/data/anvil/etc/s6-rc/source/ok-all/contents.d/run-scripts"
    ).exists

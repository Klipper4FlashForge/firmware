"""Nozzle sizes through the installed Klipper saver and G-code dispatcher."""

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.replica

KLIPPY = '/usr/data/anvil/klipper/klippy'
PYTHON = '/usr/data/anvil/bin/python3.13'
EXTRA = (ROOT / 'pkgs/klipper/payload/klipper/klippy/extras/ff_nozzle_size.py')
CHECK = ROOT / 'qa/replica/actions/check_nozzle_size.py'


def test_native_save_backup_reload_and_conflict_rollback(printer):
    installed = printer.file(KLIPPY + '/extras/ff_nozzle_size.py')
    assert installed.exists, 'Build/install the updated Klipper package first'
    assert installed.text == EXTRA.read_text(encoding='utf-8')
    printer.write('/tmp/qa-check-nozzle-size.py', CHECK.read_text(encoding='utf-8'))
    result = printer.sh(
        '. /usr/data/anvil/anvil-env.sh\n'
        'exec %s /tmp/qa-check-nozzle-size.py %s %s/extras/ff_nozzle_size.py\n'
        % (PYTHON, KLIPPY, KLIPPY), timeout=120)
    assert result.ok, result.text
    for checkpoint in ('PASS: native SAVE_CONFIG',
                       'PASS: native include conflict',
                       'PASS: native pending calibration'):
        assert checkpoint in result.text, result.text

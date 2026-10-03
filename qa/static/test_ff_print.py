"""Creator 5 print-file metadata tests."""

import importlib.util

import pytest

from lib.paths import ROOT


MODULE = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" /
          "klippy" / "extras" / "ff_print.py")


def _load_module():
    spec = importlib.util.spec_from_file_location("ff_print_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_automatic_prime_tower_brim_is_resolved_from_moves(tmp_path):
    gcode = tmp_path / "automatic-brim.gcode"
    gcode.write_text(
        """T0
;TYPE:Prime tower
G1 X9.645 Y195.626
G1 X37.645
G1 Y222.646
G1 X9.645
G1 Y195.626
; WIPE_TOWER_BRIM_START
G1 X9.199 Y195.180
G1 X38.091
G1 Y223.092
G1 X7.413 Y193.394
G1 X39.877
G1 Y224.878
G1 X7.413
G1 Y193.394
; WIPE_TOWER_BRIM_END
; wipe_tower_x = 16.4744
; wipe_tower_y = 221.74
; prime_tower_width = 28
; prime_tower_brim_width = -1
; wipe_tower_rotation_angle = 0
""",
        encoding="utf-8")

    metadata = _load_module()._parse_metadata(str(gcode))

    assert metadata["prime_tower_brim"] == pytest.approx(2.232)
    assert metadata["prime_tower_outer_min_x"] == pytest.approx(7.413)
    assert metadata["prime_tower_outer_max_x"] == pytest.approx(39.877)
    assert metadata["prime_tower_outer_min_y"] == pytest.approx(193.394)
    assert metadata["prime_tower_outer_max_y"] == pytest.approx(224.878)

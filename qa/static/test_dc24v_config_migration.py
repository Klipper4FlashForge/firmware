import subprocess

import pytest

from lib.paths import ROOT


SCRIPT = (ROOT / "pkgs" / "anvil-core" / "payload" / "bin" /
          "anvil-migrate-printer-config.sh")


def migrate(path):
    return subprocess.run(
        ["sh", str(SCRIPT), str(path)], capture_output=True, text=True)


@pytest.mark.parametrize("header", [
    "[output_pin DC24V_CTL]",
    "  [output_pin   DC24V_CTL]  ",
])
def test_removes_the_whole_legacy_section_and_is_idempotent(tmp_path, header):
    config = tmp_path / "printer.cfg"
    config.write_text(
        "[printer]\nkinematics: corexy\n\n"
        + header + "\n"
        "pin: eheaterboard:PA3\n"
        "value: 0\n"
        "shutdown_value: 0\n"
        "cycle_time: 0.100\n"
        "# a future FlashForge option or comment must not leak out\n\n"
        "[gcode_macro KEEP_ME]\n"
        "gcode:\n"
        "    M117 kept\n")

    first = migrate(config)
    assert first.returncode == 0, first.stderr
    after_first = config.read_bytes()
    text = after_first.decode()
    assert "DC24V_CTL" not in text
    assert "cycle_time" not in text
    assert "future FlashForge" not in text
    assert "[printer]\nkinematics: corexy" in text
    assert "[gcode_macro KEEP_ME]\ngcode:\n    M117 kept" in text

    second = migrate(config)
    assert second.returncode == 0, second.stderr
    assert second.stdout == ""
    assert config.read_bytes() == after_first


def test_preserves_a_trailing_save_config_block(tmp_path):
    config = tmp_path / "printer.cfg"
    # Stock files can arrive with CRLF. The section matcher must still find
    # the header, and migration must not normalize the lines it preserves.
    config.write_bytes(
        b"[output_pin DC24V_CTL]\r\n"
        b"pin: eheaterboard:PA3\r\n"
        b"value: 0\r\n"
        b"shutdown_value: 0\r\n"
        b"#*# <---------------------- SAVE_CONFIG ---------------------->\r\n"
        b"#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.\r\n"
        b"#*#\r\n"
        b"#*# [ff_tool_offset]\r\n"
        b"#*# station_z = -1.23\r\n")

    result = migrate(config)
    assert result.returncode == 0, result.stderr
    data = config.read_bytes()
    assert b"DC24V_CTL" not in data
    assert b"SAVE_CONFIG ---------------------->\r\n" in data
    assert b"#*# [ff_tool_offset]\r\n" in data
    assert b"#*# station_z = -1.23\r\n" in data


def test_absent_section_and_absent_file_are_noops(tmp_path):
    config = tmp_path / "printer.cfg"
    original = b"[printer]\nkinematics: corexy\n"
    config.write_bytes(original)

    result = migrate(config)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert config.read_bytes() == original

    result = migrate(tmp_path / "missing.cfg")
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""

import os
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).parents[2]
SCRIPT = (
    ROOT
    / "pkgs"
    / "klipper-config"
    / "payload"
    / "scripts"
    / "10-enable-printer-n4s4.sh"
)
BUILD = ROOT / "pkgs" / "klipper-config" / "build.sh"
POSTINST = ROOT / "pkgs" / "klipper-config" / "control" / "postinst"


def _shell():
    return os.environ.get("TEST_SHELL") or shutil.which("sh") or "/bin/sh"


def _run(tmp_path, printer_text, *, create_n4s4=True):
    printer = tmp_path / "printer.cfg"
    printer.write_text(printer_text, encoding="utf-8")
    if create_n4s4:
        (tmp_path / "printer_n4s4.cfg").write_text(
            "[gcode_macro N4S4_TEST]\n",
            encoding="utf-8",
        )
    result = subprocess.run(
        [_shell(), str(SCRIPT), str(printer)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    return printer, result


def test_inserts_before_save_config_and_is_idempotent(tmp_path):
    original = "[include mainsail.cfg]\n\n# Save Mesh Data #\n#*# <---------------------- SAVE_CONFIG ---------------------->\n"
    printer, first = _run(tmp_path, original)

    assert first.returncode == 0, first.stderr
    expected = (
        "[include mainsail.cfg]\n\n"
        "[include printer_n4s4.cfg]\n\n"
        "# Save Mesh Data #\n"
        "#*# <---------------------- SAVE_CONFIG ---------------------->\n"
    )
    assert printer.read_text(encoding="utf-8") == expected
    backup = tmp_path / "printer.cfg.before-n4s4"
    assert backup.read_text(encoding="utf-8") == original

    second = subprocess.run(
        [_shell(), str(SCRIPT), str(printer)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    assert second.returncode == 0, second.stderr
    assert second.stdout == ""
    assert second.stderr == ""
    assert printer.read_text(encoding="utf-8") == expected
    assert list(tmp_path.glob("printer.cfg.before-n4s4*")) == [backup]


def test_moves_and_deduplicates_active_includes(tmp_path):
    printer, result = _run(
        tmp_path,
        "  [include printer_n4s4.cfg]  \n"
        "[include mainsail.cfg]\n"
        "#*# <---------------------- SAVE_CONFIG ---------------------->\n"
        "[include printer_n4s4.cfg] ; misplaced duplicate\n",
    )

    assert result.returncode == 0, result.stderr
    generated = printer.read_text(encoding="utf-8")
    assert generated.count("[include printer_n4s4.cfg]") == 1
    assert generated.index("[include printer_n4s4.cfg]") < generated.index(
        "#*# <---------------------- SAVE_CONFIG"
    )


def test_appends_when_save_config_is_absent(tmp_path):
    printer, result = _run(tmp_path, "[include mainsail.cfg]\n")

    assert result.returncode == 0, result.stderr
    assert printer.read_text(encoding="utf-8") == (
        "[include mainsail.cfg]\n\n[include printer_n4s4.cfg]\n"
    )


def test_missing_n4s4_config_leaves_printer_unchanged(tmp_path):
    original = "[include mainsail.cfg]\n"
    printer, result = _run(tmp_path, original, create_n4s4=False)

    assert result.returncode != 0
    assert "optional config not found" in result.stderr
    assert printer.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("printer.cfg.before-n4s4*"))


def test_package_installs_the_hook_in_the_persistent_boot_directory():
    build = BUILD.read_text(encoding="utf-8")
    postinst = POSTINST.read_text(encoding="utf-8")

    assert 'pkg_stage "$PKG_DIR/payload/scripts" "share/klipper-config"' in build
    assert 'pkg_ship "config" "share/klipper-config"' in build
    assert "scripts_dir=/usr/data/anvil-data/scripts" in postinst
    assert 'target="$scripts_dir/10-enable-printer-n4s4.sh"' in postinst
    assert 'chmod 0644 "$tmp"' in postinst

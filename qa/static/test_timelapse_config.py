from pathlib import Path


ROOT = Path(__file__).parents[2]
PACKAGE = ROOT / "pkgs" / "timelapse"
CONFIG = PACKAGE / "payload" / "config" / "timelapse.cfg"


def test_local_timelapse_config_is_the_one_the_package_stages():
    build = (PACKAGE / "build.sh").read_text(encoding="utf-8")
    package = (PACKAGE / "pkg.conf").read_text(encoding="utf-8")

    assert 'pkg_stage "$PKG_DIR/payload/config/timelapse.cfg"' in build
    assert '"config/timelapse.cfg"' in build
    assert 'pkg_stage "$_src/klipper_macro/timelapse.cfg"' not in build
    assert 'PKG_STAMP_EXTRA="$(pkg_payload_hash)"' in package


def test_local_timelapse_config_contains_the_n4s4_changes():
    config = CONFIG.read_text(encoding="utf-8")

    assert "[gcode_macro TIMELAPSE_SETUP_STATUS]" in config
    assert "[gcode_macro GET_TIMELAPSE_SETUP]" not in config
    assert "variable_disabled_notice: False" in config
    assert config.count("Timelapse: disabled, take frame ignored") == 1

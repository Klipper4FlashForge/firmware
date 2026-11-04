"""printer_n4s4.cfg redefines two macros that ff-print-macros.cfg already has.

A redefinition WINS: klippy merges the two sections and the later one replaces
what it sets. So if the original changes one of them, the copy here would
silently keep the old behaviour. Six macros are redefined; two are only the
original's plus a few lines, and these tests render both and require exactly
that, the other four replace the original's text and are held by a tripwire,
so an upstream change shows up here as a failure instead of as a printer that
ignores it.

  ADAPTIVE_MESH              + PLATE=, TOOLCHANGE_PREPARE_PICKUP, and the
                               preheat of the file's second tool
  DEFINE_PRIME_TOWER_OBJECT  + TOOLCHANGE_SET_PRIME_TOWER
  _FF_FILAMENT, _FF_NOZZLE_WIPE, _FF_NOZZLE_CLEAN, PURGE
                             replaced; the original's text is pinned below

The macros upstream owns and N4S4 used to carry (ADAPTIVE_MESH_TOGGLE,
ADAPTIVE_MESH_STATUS, [ff_bed_mesh]) must not come back as copies.
"""
import configparser
import hashlib
import re

import jinja2
import pytest

from lib.paths import ROOT
from test_klipper_config import (MODELS, N4S4, _commands, _parse,
                                 _parse_with_n4s4)

pytestmark = pytest.mark.static

PLATES = {"smooth_cool": 0.0, "smooth_high_temp": 0.0, "textured_cool": 0.0,
          "textured_pei": 0.03, "engineering": 0.0, "supertack": 0.0}

TOWER_FROM_THE_FILE = {
    "prime_tower_x": 16.4744, "prime_tower_y": 221.74,
    "prime_tower_width": 28.0, "prime_tower_depth": 27.0,
    "prime_tower_brim": 2.2, "prime_tower_rotation": 0.0,
    "prime_tower_center_x": 23.645, "prime_tower_center_y": 209.136,
    "prime_tower_core_min_x": None,
    "prime_tower_outer_min_x": 7.413, "prime_tower_outer_max_x": 39.877,
    "prime_tower_outer_min_y": 193.394, "prime_tower_outer_max_y": 224.878,
}
TOWER_FROM_THE_CORE = dict(
    TOWER_FROM_THE_FILE, prime_tower_outer_min_x=None,
    prime_tower_core_min_x=8.0, prime_tower_core_max_x=40.0,
    prime_tower_core_min_y=195.0, prime_tower_core_max_y=224.0)
NO_TOWER_IN_THE_FILE = {key: None for key in (
    "prime_tower_x", "prime_tower_y", "prime_tower_width",
    "prime_tower_depth", "prime_tower_brim", "prime_tower_rotation",
    "prime_tower_center_x", "prime_tower_center_y", "prime_tower_core_min_x",
    "prime_tower_outer_min_x")}


def _raise(message):
    raise AssertionError("macro raised: %s" % message)


def _render(cp, macro, params, printer):
    """The macro's own output, not expanded any further."""
    env = jinja2.Environment("{%", "%}", "{", "}")
    out = env.from_string(cp.get("gcode_macro " + macro, "gcode")).render(
        params=params, printer=printer, rawparams="",
        action_respond_info=lambda message: "", action_raise_error=_raise)
    return _commands(out.splitlines())


def _printer(adaptive=1, ff_print=None):
    return {
        "gcode_macro ADAPTIVE_MESH_TOGGLE": {"enabled": adaptive},
        "gcode_macro _BUILD_PLATE_OFFSETS": dict(PLATES),
        "ff_print": dict({"next_tool": None, "next_nozzle": None},
                         **(ff_print or {})),
    }


# ---------------------------------------------------------------------------
# ADAPTIVE_MESH
# ---------------------------------------------------------------------------

def _with_n4s4_additions(original, tool, plate_term="0.0"):
    """What the original's commands become with N4S4's documented additions."""
    expected = []
    for command in original:
        if command == "T%d" % tool:
            expected.append("TOOLCHANGE_PREPARE_PICKUP")
        if command.startswith("TOOLCHANGE_SET_PRINT_OFFSET NOZZLE="):
            command = command.replace(
                " TOOL=", " PLATE=%s TOOL=" % plate_term)
        expected.append(command)
    return expected


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("adaptive", [0, 1])
@pytest.mark.parametrize("tool", [0, 2])
def test_adaptive_mesh_is_the_original_plus_the_documented_additions(
        model, adaptive, tool):
    params = {"TOOL": str(tool), "NOZZLE": "230", "BED": "60",
              "LAYER": "0.2"}
    original = _render(_parse(model), "ADAPTIVE_MESH", params,
                       _printer(adaptive))
    ours = _render(_parse_with_n4s4(model), "ADAPTIVE_MESH", params,
                   _printer(adaptive))

    assert original[-3:] == [
        "M104 S230.0 T%d" % tool, "T%d" % tool,
        "TOOLCHANGE_SET_PRINT_OFFSET NOZZLE=230.0 BED=60.0 LAYER=0.2 TOOL=%d"
        % tool], "the original changed shape; revisit the N4S4 copy"
    assert ours == _with_n4s4_additions(original, tool)


@pytest.mark.parametrize("model", MODELS)
def test_the_build_plate_term_reaches_the_print_offset(model):
    params = {"TOOL": "1", "NOZZLE": "230", "BED": "60", "LAYER": "0.2",
              "PLATE": "textured_pei"}
    original = _render(_parse(model), "ADAPTIVE_MESH", params, _printer())
    ours = _render(_parse_with_n4s4(model), "ADAPTIVE_MESH", params,
                   _printer())

    assert ours == _with_n4s4_additions(original, 1, plate_term="0.03")


@pytest.mark.parametrize("model", MODELS)
def test_an_unknown_plate_code_stops_the_print_start(model):
    params = {"TOOL": "0", "PLATE": "gold"}

    with pytest.raises(AssertionError, match="unknown build plate code"):
        _render(_parse_with_n4s4(model), "ADAPTIVE_MESH", params, _printer())


@pytest.mark.parametrize("model", MODELS)
def test_the_second_tool_is_preheated_once_the_mesh_is_done(model):
    params = {"TOOL": "0", "NOZZLE": "230", "BED": "60", "LAYER": "0.2"}
    file_says = {"next_tool": 2, "next_nozzle": 215}
    cp = _parse_with_n4s4(model)

    ours = _render(cp, "ADAPTIVE_MESH", params, _printer(ff_print=file_says))
    plain = _render(cp, "ADAPTIVE_MESH", params, _printer())

    assert ours == plain + ["M104 S215 T2"]

    # the initial tool is already heating; asking again would be noise
    same_tool = _render(cp, "ADAPTIVE_MESH", params, _printer(
        ff_print={"next_tool": 0, "next_nozzle": 215}))
    assert same_tool == plain


# ---------------------------------------------------------------------------
# DEFINE_PRIME_TOWER_OBJECT
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("from_the_file", [
    TOWER_FROM_THE_FILE, TOWER_FROM_THE_CORE, NO_TOWER_IN_THE_FILE],
    ids=["outer outline", "core outline", "no outline"])
def test_the_prime_tower_is_the_original_plus_the_toolchange_registration(
        model, from_the_file):
    params = {"X": "100", "Y": "200", "WIDTH": "20", "DEPTH": "10",
              "BRIM": "2"}
    printer = _printer(ff_print=from_the_file)

    original = _render(_parse(model), "DEFINE_PRIME_TOWER_OBJECT", params,
                       printer)
    ours = _render(_parse_with_n4s4(model), "DEFINE_PRIME_TOWER_OBJECT",
                   params, printer)

    assert len(original) == 1 and original[0].startswith(
        "EXCLUDE_OBJECT_DEFINE NAME=PRIME_TOWER CENTER="), original
    assert len(ours) == 2
    register, define = ours
    assert define == original[0]
    assert register.startswith("TOOLCHANGE_SET_PRIME_TOWER CENTER_X=")
    # the toolchanger is given the centre the excluded object has
    centre = define.split("CENTER=")[1].split()[0]
    assert "CENTER_X=%s CENTER_Y=%s " % tuple(centre.split(",")) in register


# ---------------------------------------------------------------------------
# What upstream owns must not be copied back
# ---------------------------------------------------------------------------

def _n4s4_sections():
    cp = configparser.RawConfigParser(
        strict=False, inline_comment_prefixes=(";", "#"))
    cp.read_string(N4S4.read_text(encoding="utf-8"))
    return cp


def test_the_macros_and_settings_upstream_has_are_not_copied_here():
    sections = _n4s4_sections().sections()

    for owned in ("gcode_macro ADAPTIVE_MESH_TOGGLE",
                  "gcode_macro ADAPTIVE_MESH_STATUS", "ff_bed_mesh"):
        assert owned not in sections, (
            "[%s] is in ff-print-macros.cfg; a copy here would shadow it"
            % owned)


def test_the_overrides_replace_only_the_macro_text():
    ours = _n4s4_sections()

    for macro in ("ADAPTIVE_MESH", "DEFINE_PRIME_TOWER_OBJECT"):
        options = set(ours.options("gcode_macro " + macro))
        assert options == {"description", "gcode"}, (
            "%s adds %s; a variable here would replace the original's"
            % (macro, sorted(options - {"description", "gcode"})))


# ---------------------------------------------------------------------------
# The merged configuration as klippy will load it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("model", MODELS)
def test_every_macro_of_the_merged_config_compiles(model):
    """klippy compiles every macro at start; one syntax error is a printer
    that does not come up. test_klipper_config checks the base config alone,
    so this is the same check with printer_n4s4.cfg merged in."""
    cp = _parse_with_n4s4(model)
    env = jinja2.Environment("{%", "%}", "{", "}")
    macros = [s for s in cp.sections() if s.startswith("gcode_macro ")]
    base = [s for s in _parse(model).sections() if s.startswith("gcode_macro ")]

    assert len(macros) > len(base) > 20    # the merge added N4S4's, none lost
    for section in macros:
        env.from_string(cp.get(section, "gcode"))


@pytest.mark.parametrize("model", MODELS)
def test_no_macro_is_named_like_a_command_a_module_registers(model):
    """klippy refuses to start when a macro and a command share a name
    ("gcode command X already registered"). That is why the native
    TOOLCHANGE_STATUS is only registered when no macro of that name exists;
    every other name must simply not clash."""
    extras = (ROOT / "pkgs" / "klipper" / "payload" / "klipper" / "klippy" /
              "extras")
    registered = set()
    for source in extras.glob("ff_*.py"):
        text = source.read_text(encoding="utf-8")
        registered |= set(re.findall(
            r"register_(?:mux_)?command\(\s*'([A-Za-z0-9_]+)'", text))
    cp = _parse_with_n4s4(model)
    macros = {s.split(None, 1)[1].upper() for s in cp.sections()
              if s.startswith("gcode_macro ")}

    assert registered, "the pattern found no command at all"
    assert registered & macros <= {"TOOLCHANGE_STATUS"}, sorted(
        registered & macros)
    # and that one is registered conditionally, not unconditionally
    toolchange = (extras / "ff_toolchange.py").read_text(encoding="utf-8")
    assert "if not _macro_configured(config, 'TOOLCHANGE_STATUS')" in toolchange


# ---------------------------------------------------------------------------
# _NS_BEFORE_PRINT: the tools come from ff_print's status
# ---------------------------------------------------------------------------

def _before_print(model, purge_mode, tools, **params):
    printer = {"gcode_macro START_PURGE_SET": {"mode": purge_mode},
               "ff_print": {"tools": tools}}
    return _render(_parse_with_n4s4(model), "_NS_BEFORE_PRINT",
                   dict({"ORIGIN": "SDCARD_PRINT_FILE", "TOOL": "0",
                         "NOZZLE": "220", "BED": "60"}, **params), printer)


def _start(commands):
    (line,) = [c for c in commands if c.startswith("FF_BEFORE_PRINT_START")]
    return line


@pytest.mark.parametrize("model", MODELS)
def test_every_used_tool_is_cleaned_in_purge_mode_all(model):
    out = _before_print(model, 2, [0, 2, 3])

    assert _start(out).endswith("CLEAN=1 DEFER_MESH=1 TOOLS=0,2,3")
    assert "_FF_PREFLIGHT TOOL=0 TOOLS=0,2,3" in out
    assert "_NS_FILAMENT_PREFLIGHT TOOLS=0,2,3" in out


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("mode, clean", [(0, 0), (1, 1)])
def test_only_the_first_tool_is_cleaned_otherwise(model, mode, clean):
    out = _before_print(model, mode, [0, 2, 3])

    assert _start(out).endswith("CLEAN=%d DEFER_MESH=1" % clean)
    # the filament check still covers every tool the file uses
    assert "_NS_FILAMENT_PREFLIGHT TOOLS=0,2,3" in out


@pytest.mark.parametrize("model", MODELS)
def test_a_file_that_names_no_tool_is_checked_for_its_first_one(model):
    out = _before_print(model, 2, [])

    assert "_NS_FILAMENT_PREFLIGHT TOOLS=0" in out
    assert "TOOLS=" not in _start(out)


@pytest.mark.parametrize("model", MODELS)
def test_an_explicit_tools_parameter_wins(model):
    out = _before_print(model, 2, [0, 1], TOOLS="3,2")

    assert _start(out).endswith("DEFER_MESH=1 TOOLS=3,2")


@pytest.mark.parametrize("model", MODELS)
def test_the_job_state_is_reset_before_anything_else(model):
    out = _before_print(model, 1, [0])

    assert out[:5] == [
        "TOOLCHANGE_BEGIN_JOB", "TOOLCHANGE_SET_PRIME_TOWER CLEAR=1",
        "TOOLCHANGE_PREPARE_PICKUP ENABLE=0",
        "SET_GCODE_VARIABLE MACRO=_NS_TOOLCHANGE_PRIME VARIABLE=pending_tool"
        " VALUE=-1",
        "SET_GCODE_VARIABLE MACRO=_NS_TOOLCHANGE_PRIME VARIABLE=prime_required"
        " VALUE=0"]


# ---------------------------------------------------------------------------
# Which macros of the original are redefined, and the four that are replaced
# ---------------------------------------------------------------------------

REDEFINED = {"ADAPTIVE_MESH", "DEFINE_PRIME_TOWER_OBJECT", "_FF_FILAMENT",
             "_FF_NOZZLE_WIPE", "_FF_NOZZLE_CLEAN", "PURGE"}

# What the original's four macros look like (everything klippy would read of
# them, comments and trailing blanks aside). N4S4 replaces their text in
# printer_n4s4.cfg, so a change here is a change the printer would not get:
# read the original's new text, bring the replacement in line, then update
# the digest.
ORIGINALS_REPLACED = {
    "_FF_FILAMENT":
        "0d93c257a89c20b6158919c09073077e4f74bd798e9ee04a0f50cc4b25020f5e",
    "_FF_NOZZLE_WIPE":
        "a795a525e8805930ecc7b1136055e32fe9f500f0f9a4056c082b5b8038a4642e",
    "_FF_NOZZLE_CLEAN":
        "9a792a52518185c5272281fd7d61fdf37bc4b198873c6232b140847815dfe142",
    "PURGE":
        "03f7b13a2cffcacf0512f96625ef600285f85090a3e577a6e27252d2f0bf5f1f",
}


def _macro_names(cp):
    return {s.split(None, 1)[1] for s in cp.sections()
            if s.startswith("gcode_macro ")}


def _digest(cp, section):
    lines = []
    for option in sorted(cp.options(section)):
        value = cp.get(section, option)
        lines.append("%s: %s" % (option, "\n".join(
            line.rstrip() for line in value.splitlines())))
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


@pytest.mark.parametrize("model", MODELS)
def test_n4s4_redefines_exactly_these_macros_of_the_original(model):
    shared = _macro_names(_parse(model)) & _macro_names(_n4s4_sections())

    assert shared == REDEFINED, (
        "printer_n4s4.cfg and the original both define %s; a macro N4S4 adds "
        "under a name the original already has would silently replace it"
        % sorted(shared ^ REDEFINED))


@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("name", sorted(ORIGINALS_REPLACED))
def test_the_originals_that_n4s4_replaces_have_not_changed(model, name):
    got = _digest(_parse(model), "gcode_macro " + name)

    assert got == ORIGINALS_REPLACED[name], (
        "the original's [gcode_macro %s] changed. printer_n4s4.cfg replaces "
        "it, so the printer would not get the change: compare the two, bring "
        "the replacement in line, then put %s in ORIGINALS_REPLACED"
        % (name, got))

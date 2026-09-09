"""Exercise recipe cache decisions without a compiler or fetched sources."""
import os
import shutil
import subprocess

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.static


@pytest.fixture
def checkout(tmp_path):
    (tmp_path / "pkgs").mkdir()
    shutil.copy2(ROOT / "pkgs/lib.sh", tmp_path / "pkgs/lib.sh")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/common.sh").write_text("# shared build settings\n")
    for name in ("demo", "3rdparty/dependency", "unrelated"):
        recipe = tmp_path / "pkgs" / name
        recipe.mkdir(parents=True)
        (recipe / "pkg.conf").write_text(
            'PKG_NAME="anvil-%s"\nPKG_VERSION=1\n' % recipe.name)
        (recipe / "build.sh").write_text(
            'pkg_begin "$1" || exit 0\n'
            'pkg_stage "$PKG_DIR/payload" reference\n'
            'pkg_ship reference\npkg_end\n')
        (recipe / "payload").mkdir()
        (recipe / "payload/example.cfg").write_text("example\n")
    return tmp_path


def shell(checkout, command, *args, check=True):
    return subprocess.run(
        ["bash", "-euo", "pipefail", "-c",
         'ROOT=$PWD; IPK_ARCH=test; MIPS_TOOLCHAIN_FILE=toolchain.tar.gz; '
         'MODDIR=/usr/data/anvil; . ./pkgs/lib.sh; ' + command,
         "cache-test", *args],
        cwd=checkout, capture_output=True, text=True, check=check)


def stamp(checkout, recipe="demo"):
    return shell(checkout, 'pkg_stamp "$1"', recipe).stdout


@pytest.mark.parametrize("relative", [
    "build.sh", "pkg.conf", "prefix.patch", "payload/example.cfg",
    "control/postinst", "payload/a name\nwith whitespace.cfg",
])
def test_local_input_edits_invalidate_cached_build(checkout, relative):
    path = checkout / "pkgs/demo" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text("# initial\n")
    old_stamp = stamp(checkout)
    output = checkout / "work/pkg/demo"
    output.mkdir(parents=True)
    (output / ".version").write_text(old_stamp + "\n")
    assert shell(checkout, "pkg_stale demo", check=False).returncode == 1
    with path.open("a") as stream:
        stream.write("# changed\n")
    assert shell(checkout, "pkg_stale demo", check=False).returncode == 0


def test_build_recipe_change_rebuilds_the_output(checkout):
    recipe = checkout / "pkgs/demo/build.sh"
    first = shell(checkout, '. "$2"', "demo", str(recipe))
    assert "sealed" in first.stdout
    assert "already holds" in shell(
        checkout, '. "$2"', "demo", str(recipe)).stdout
    recipe.write_text(recipe.read_text().replace("reference", "examples"))
    rebuilt = shell(checkout, '. "$2"', "demo", str(recipe))
    assert "sealed" in rebuilt.stdout
    output = checkout / "work/pkg/demo"
    assert (output / "examples/example.cfg").read_text() == "example\n"
    assert not (output / "reference").exists()


def test_add_remove_rename_and_modes_change_the_stamp(checkout):
    payload = checkout / "pkgs/demo/payload"
    initial = stamp(checkout)
    added = payload / "extra"
    added.write_text("extra\n")
    after_add = stamp(checkout)
    assert after_add != initial
    renamed = payload / "renamed"
    added.rename(renamed)
    assert stamp(checkout) != after_add
    renamed.unlink()
    assert stamp(checkout) == initial
    example = payload / "example.cfg"
    example.chmod(example.stat().st_mode ^ 0o111)
    assert stamp(checkout) != initial


def test_symlink_targets_are_inputs_even_when_dangling(checkout):
    link = checkout / "pkgs/demo/payload/link"
    initial = stamp(checkout)
    link.symlink_to("example.cfg")
    linked = stamp(checkout)
    assert linked != initial
    link.unlink()
    link.symlink_to("missing.cfg")
    assert stamp(checkout) != linked


@pytest.mark.parametrize("relative", ["pkgs/lib.sh", "bin/common.sh"])
def test_shared_build_changes_invalidate_every_recipe(checkout, relative):
    recipes = ("demo", "dependency", "unrelated")
    before = {name: stamp(checkout, name) for name in recipes}
    with (checkout / relative).open("a") as stream:
        stream.write("# shared change\n")
    assert all(stamp(checkout, name) != before[name] for name in recipes)


def test_dependency_changes_propagate_without_invalidating_unrelated_recipe(checkout):
    with (checkout / "pkgs/demo/pkg.conf").open("a") as stream:
        stream.write('PKG_BUILD_DEPENDS="dependency"\n')
    dependent = stamp(checkout)
    unrelated = stamp(checkout, "unrelated")
    with (checkout / "pkgs/3rdparty/dependency/build.sh").open("a") as stream:
        stream.write("# dependency build change\n")
    assert stamp(checkout) != dependent
    assert stamp(checkout, "unrelated") == unrelated


def test_external_inputs_still_invalidate_the_cache(checkout):
    with (checkout / "pkgs/demo/pkg.conf").open("a") as stream:
        stream.write('PKG_STAMP_EXTRA="$EXTERNAL_INPUT"\n')
    before = shell(checkout, 'EXTERNAL_INPUT=first; pkg_stamp demo').stdout
    after = shell(checkout, 'EXTERNAL_INPUT=second; pkg_stamp demo').stdout
    assert before != after


def test_timestamps_seeds_bytecode_and_checkout_location_do_not_matter(checkout, tmp_path):
    before = stamp(checkout)
    recipe = checkout / "pkgs/demo"
    for relative in ("seed/default.conf", "payload/__pycache__/example.pyc",
                     "payload/example.pyc"):
        path = recipe / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not part of the package\n")
    for path in checkout.rglob("*"):
        os.utime(path, (1000000, 1000000))
    assert stamp(checkout) == before
    relocated = tmp_path / "relocated"
    # Copy only source inputs, since the destination is inside tmp_path.
    for directory in ("pkgs", "bin"):
        shutil.copytree(checkout / directory, relocated / directory)
    assert stamp(relocated) == before


@pytest.mark.parametrize("command", [
    "pkg_stamp demo", "pkg_stale demo", "pkg_begin demo || exit 0",
])
def test_missing_shared_input_fails_instead_of_reusing_cache(checkout, command):
    (checkout / "bin/common.sh").unlink()
    result = shell(checkout, command, check=False)
    assert result.returncode != 0
    assert "common.sh" in result.stderr

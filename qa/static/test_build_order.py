"""pkg_order, and what bin/build-packages.sh does when it refuses an order.

test_packages.py asks pkg_order about the recipes this repo has, which form no
cycle, so the refusal itself was never exercised. Two things were wrong with
it:

  * The path pkg_order walks lost a separator every time a dependency was
    finished with, gluing the next sibling onto its parent. a -> (b, c) left
    " ac " behind once b was done, so a recipe named `ac` under c read as a
    cycle that is not there, and a real cycle through c was only caught on a
    second lap.
  * build-packages.sh read the order through a here-string, which hides the
    exit status of the substitution inside it. A cycle printed its error and
    the run carried on with no recipes, re-indexed the feed it already had and
    exited 0.

Both run against a synthetic pkgs/ tree, so neither needs the apk that
test_packages.py does.
"""
import os
import shutil
import subprocess

import pytest

from lib.paths import ROOT

pytestmark = pytest.mark.static


def _recipes(root, graph):
    """A pkgs/ tree with one recipe per key, building against its value."""
    for name, depends in graph.items():
        recipe = root / "pkgs" / name
        recipe.mkdir(parents=True)
        (recipe / "pkg.conf").write_text("PKG_BUILD_DEPENDS='%s'\n" % depends)


def _order(root, *wanted):
    return subprocess.run(
        ["bash", "-c", 'ROOT="$1"; . "$2"; shift 2; pkg_order "$@"',
         "pkg_order", str(root), str(ROOT / "pkgs" / "lib.sh")] + list(wanted),
        capture_output=True, text=True)


def test_a_recipe_named_like_two_others_is_not_a_cycle(tmp_path):
    """a builds against b, then c; c builds against ac. No cycle anywhere."""
    _recipes(tmp_path, {"a": "b c", "b": "", "c": "ac", "ac": ""})
    out = _order(tmp_path, "a")
    assert out.returncode == 0, (
        "pkg_order refused an acyclic graph:\n%s" % out.stderr)
    assert out.stdout.split() == ["b", "ac", "c", "a"]


def test_a_cycle_stops_the_build(tmp_path):
    """The run has to end at the cycle, before it announces an order.

    The real script, copied into a tree of its own: a stub where it builds
    the native apk, `true` as the apk it then runs, and two recipes that
    build against each other.
    """
    for rel in ("bin/build-packages.sh", "bin/common.sh", "pkgs/lib.sh"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(str(ROOT / rel), str(tmp_path / rel))
    stub = tmp_path / "tools" / "apk-host" / "build.sh"
    stub.parent.mkdir(parents=True)
    stub.write_text("#!/bin/sh\n")
    stub.chmod(0o755)
    (tmp_path / "config.env").write_text("")
    _recipes(tmp_path, {"a": "b", "b": "a"})

    out = subprocess.run(
        ["bash", str(tmp_path / "bin" / "build-packages.sh")],
        cwd=str(tmp_path), capture_output=True, text=True,
        env={"PATH": os.environ["PATH"], "APK_BIN": "true"})

    assert "dependency cycle" in out.stderr, out.stdout + out.stderr
    assert out.returncode != 0, (
        "build-packages.sh reported a cycle and exited 0:\n%s" % out.stdout)
    assert "order:" not in out.stdout, (
        "build-packages.sh went on past the cycle:\n%s" % out.stdout)

#!/usr/bin/env python3
"""Prepare a desktop copy of the exact pinned firmware sources."""
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'work/creator5-sim'


def prepare():
    versions = dict(re.findall(r'^(\w+)="([^"]*)"',
                               (ROOT / 'versions.env').read_text(), re.M))
    WORK.mkdir(parents=True, exist_ok=True)
    trees = {}
    for package, prefix in [('klipper', 'KLIPPER'),
                            ('AFC-Klipper-Add-On', 'AFC')]:
        archive = ROOT / 'vendor' / f'{package}-{versions[prefix + "_VERSION"]}.tar.gz'
        if not archive.exists():
            raise SystemExit(f'Missing {archive}; run the repository source fetch first.')
        if hashlib.sha256(archive.read_bytes()).hexdigest() != versions[prefix + '_SHA256']:
            raise SystemExit(f'Checksum mismatch: {archive}')
        tree = WORK / f'{package}-{versions[prefix + "_VERSION"]}'
        if not tree.exists():
            with tarfile.open(archive) as tar:
                tar.extractall(WORK, filter='data')
        trees[prefix] = tree
    klipper = trees['KLIPPER']
    extras = klipper / 'klippy/extras'
    for source in (ROOT / 'pkgs/klipper/payload/klipper/klippy/extras').glob('*.py'):
        shutil.copy2(source, extras / source.name)
    for source in (trees['AFC'] / 'extras').glob('*.py'):
        if source.name != '__init__.py':
            shutil.copy2(source, extras / source.name)
    shutil.copy2(Path(__file__).with_name('creator5_sim.py'), extras / 'creator5_sim.py')
    # This dictionary is only used by Klipper's file-output backend. No MCU
    # executable is launched and no serial or GPIO device is accessed.
    for target in ['olddefconfig', 'all']:
        subprocess.run(['make', 'KCONFIG_CONFIG=test/configs/linuxprocess.config', target],
                       cwd=klipper, check=True)
    (WORK / 'klipper-path').write_text(str(klipper))
    return klipper


if __name__ == '__main__':
    prepare()

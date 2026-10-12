"""Select the desktop compiler before entering the pinned Klipper CLI."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv.pop(1)) / 'klippy'))
import chelper
import klippy
import mcu
import os

# Our fork defaults to the printer's MIPS compiler. Compile identical helper
# sources for this host; no firmware control behavior changes.
chelper.GCC_CMD = 'gcc'
# FlashForge's host asks a vendor-only version query even in file-output
# mode. The desktop dictionary has upstream commands, so supply this one
# identification response at the MCU boundary.
def desktop_mcu_version(self):
    if not self.is_fileoutput():
        raise RuntimeError('Simulator must never connect to a physical MCU')
    self.MCU_VERSION = 'desktop-simulation'

mcu.MCU._get_mcu_version = desktop_mcu_version

class DesktopOS:
    def __getattr__(self, name):
        return getattr(os, name)

    def system(self, command):
        # Printer.run() executes this native board wake-up on disconnect.
        if command == 'cd /usr/prog/bin && ./wakeup_level':
            return 0
        raise RuntimeError(f'Unexpected board command in simulator: {command}')

klippy.os = DesktopOS()
klippy.main()

# Support

Two things between them cover most of what can go wrong: the logs say what
happened, and flashing the stock package undoes everything.

There are people to ask on [the Discord](https://discord.gg/ggJyfgVA4v).
Bring the logs.

---

## The undo button

**Flash the stock FlashForge package for your model.** It restores everything
it carries and installs the same way — stick in, power on. Keep a copy
downloaded before you start; the stock packages are published at
[ghzserg/FF](https://github.com/ghzserg/FF/releases).

The printer goes back to the `printer.cfg` it had before Reforge: the mod keeps
its Klipper config in a directory of its own and leaves FlashForge's alone,
which is what makes the stock flash clean. Nothing you tuned is deleted — it
waits in `/usr/data/anvil-data/config` for the next time you flash Reforge.

---

## The logs

All on the data partition, and all surviving a reboot:

```
/usr/data/anvil-install.log      what the installer did
/usr/data/logs/anvil-boot.log    services + UI choice at each boot
/usr/data/logs/printer.log       klipper
/usr/data/logs/helixscreen.log   helixscreen
```

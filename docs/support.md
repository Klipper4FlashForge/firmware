# Support

Two things between them cover most of what can go wrong: the logs say what
happened, and flashing the stock package puts the printer back.

There are people to ask on [the Discord](https://discord.gg/ggJyfgVA4v).
Bring the logs.

---

## The undo button

**Flash the stock FlashForge package for your model.** It installs the same
way — stick in, power on. Keep a copy downloaded before you start; the stock
packages are published at
[ghzserg/FF](https://github.com/ghzserg/FF/releases).

It restores everything it carries, which is everything Reforge changed bar one
file. On a printer that ran a Reforge release from before September 2026, that
one file leaves the machine with a normal-looking screen and no Klipper behind
it — see [Going back to stock](going-back-to-stock.md) for the symptom and the
one-flash repair.

---

## The logs

All on the data partition, and all surviving a reboot:

```
/usr/data/anvil-install.log      what the installer did
/usr/data/anvil-recovery.log     what the recovery package repaired
/usr/data/logs/anvil-boot.log    services + UI choice at each boot
/usr/data/logs/printer.log       klipper
/usr/data/logs/helixscreen.log   helixscreen
```

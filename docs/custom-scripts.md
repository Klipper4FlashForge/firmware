# Custom boot scripts

Reforge can run small shell scripts once during every boot. Put files ending
in `.sh` here:

```text
/usr/data/anvil-data/scripts/
```

This is user-owned storage and survives firmware updates. Do not put scripts
under `/usr/data/anvil`: an update replaces that whole directory.

Scripts run in filename order, so numeric prefixes make the order explicit:

```text
10-mount-share.sh
20-set-local-option.sh
```

An executable file is run directly and must have a valid shebang. A file
without its executable bit is run with `/bin/sh`. Files that do not end in
`.sh`, directories, and missing matches are ignored.

The runner creates the directory when it does not exist. It continues with
the next file when one script exits unsuccessfully, and the boot transition
allows 30 seconds for all custom scripts together. Keep them short and do not
start a foreground process that stays running. Services start as part of the
same boot transition, so a script that needs Moonraker or another service must
check that it is ready rather than assume an ordering.

Scripts run as `root`. Treat the directory as executable system
configuration: only place code there that you have read and trust.

## Output and troubleshooting

Standard output, errors, and the name of each script go to:

```text
/usr/data/logs/custom-scripts.log
```

The log is replaced on each run so it describes the current boot. A failed
script does not prevent later scripts from running; its own output is the
record of the failure.

To test the complete set over SSH without rebooting:

```sh
/usr/data/anvil/bin/run-scripts.sh
cat /usr/data/logs/custom-scripts.log
```

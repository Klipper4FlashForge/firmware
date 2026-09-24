#!/bin/sh

# User-owned state lives outside /usr/data/anvil: the installer replaces that
# whole tree on every update. The overrides make the real runner testable under
# the printer's BusyBox without writing into either persistent directory.
SCRIPTS_DIR=${SCRIPTS_DIR:-/usr/data/anvil-data/scripts}
CUSTOM_SCRIPTS_LOG=${CUSTOM_SCRIPTS_LOG:-/usr/data/logs/custom-scripts.log}

mkdir -p "$SCRIPTS_DIR" "$(dirname "$CUSTOM_SCRIPTS_LOG")"
: > "$CUSTOM_SCRIPTS_LOG"

log() {
    printf '[%s] %s\n' "$(date)" "$1" >> "$CUSTOM_SCRIPTS_LOG"
}

log "Starting custom scripts"

# POSIX sh leaves an unmatched glob unchanged, so guard each expansion with
# -f. The glob itself gives a stable alphabetical execution order.
for script in "$SCRIPTS_DIR"/*.sh; do
    [ -f "$script" ] || continue
    name=$(basename "$script")

    if [ -x "$script" ]; then
        log "Running $name directly"
        "$script" >> "$CUSTOM_SCRIPTS_LOG" 2>&1
    else
        log "Running $name through sh"
        sh "$script" >> "$CUSTOM_SCRIPTS_LOG" 2>&1
    fi
done

log "Finished custom scripts"

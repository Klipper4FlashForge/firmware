#!/bin/sh
set -eu

config=${1:-/usr/data/anvil-data/config/printer.cfg}
[ -f "$config" ] || exit 0

header='^[[:space:]]*\[output_pin[[:space:]][[:space:]]*DC24V_CTL\][[:space:]]*$'
grep -Eq "$header" "$config" || exit 0

# Delete the complete legacy section. Preserve the next ordinary section and
# Klipper's commented #*# SAVE_CONFIG block when the legacy section is last.
sed -i '/^[[:space:]]*\[output_pin[[:space:]][[:space:]]*DC24V_CTL\][[:space:]]*$/,/^[[:space:]]*\[/ {
    /^[[:space:]]*\[output_pin[[:space:]][[:space:]]*DC24V_CTL\][[:space:]]*$/d
    /^[[:space:]]*\[/b
    /^#\*#/b
    d
}' "$config"

echo "config: removed obsolete [output_pin DC24V_CTL]; printer.base.cfg now controls PA3"

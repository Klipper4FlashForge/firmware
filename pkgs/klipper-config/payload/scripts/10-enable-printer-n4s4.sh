#!/bin/sh
# Add the optional N4S4 configuration to the live Klipper configuration.
#
# The firmware package installs this non-executable file under
# /usr/data/anvil-data/scripts. The custom-script runner invokes it through
# BusyBox /bin/sh once per boot.
set -eu

config=${1:-${PRINTER_CONFIG:-/usr/data/anvil-data/config/printer.cfg}}
config_dir=$(dirname "$config")
n4s4_config=${N4S4_CONFIG:-$config_dir/printer_n4s4.cfg}
include='[include printer_n4s4.cfg]'
include_re='^[[:space:]]*\[include[[:space:]]+printer_n4s4[.]cfg\][[:space:]]*([#;].*)?$'
anchor_re='^[[:space:]]*#[[:space:]]*Save Mesh Data[[:space:]]*#[[:space:]]*$|^#\*#[[:space:]]*<[-]+[[:space:]]*SAVE_CONFIG'

if [ ! -f "$config" ]; then
    echo "n4s4: printer config not found: $config" >&2
    exit 1
fi

# This is the normal path after the first successful boot. Keep it before the
# other checks and all command substitutions so the script is silent and does
# almost no work on subsequent boots.
if grep -Fqx "$include" "$config"; then
    exit 0
fi

if [ ! -f "$n4s4_config" ]; then
    echo "n4s4: optional config not found: $n4s4_config" >&2
    echo "n4s4: install printer_n4s4.cfg before enabling it" >&2
    exit 1
fi

include_lines=$(grep -nE "$include_re" "$config" || :)
include_count=$(printf '%s\n' "$include_lines" | sed '/^$/d' | wc -l)
include_line=$(printf '%s\n' "$include_lines" | sed -n '1s/:.*//p')
anchor_line=$(grep -nE "$anchor_re" "$config" | sed -n '1s/:.*//p' || :)

# One active include before SAVE_CONFIG is already the desired state. An
# installation without a SAVE_CONFIG block is also valid; in that case any
# existing active include is sufficient.
if [ "$include_count" -eq 1 ] && {
        [ -z "$anchor_line" ] || [ "$include_line" -lt "$anchor_line" ];
    }; then
    exit 0
fi

tmp="${config}.n4s4.$$"
backup="${config}.before-n4s4"
trap 'rm -f "$tmp"' EXIT HUP INT TERM

# Copy first so the temporary replacement retains the original mode and
# ownership. The redirection below truncates that copy without changing them.
cp -p "$config" "$tmp"
awk -v include_text="$include" '
    $0 ~ /^[[:space:]]*\[include[[:space:]]+printer_n4s4[.]cfg\][[:space:]]*([#;].*)?$/ {
        next
    }
    !inserted && ($0 ~ /^[[:space:]]*#[[:space:]]*Save Mesh Data[[:space:]]*#[[:space:]]*$/ ||
                  $0 ~ /^#\*#[[:space:]]*<[-]+[[:space:]]*SAVE_CONFIG/) {
        print include_text
        print ""
        inserted = 1
    }
    { print }
    END {
        if (!inserted) {
            print ""
            print include_text
        }
    }
' "$config" > "$tmp"

# Refuse to replace printer.cfg unless the generated file contains exactly
# one active include and it is outside Klipper's commented SAVE_CONFIG block.
generated_count=$(grep -Ec "$include_re" "$tmp" || :)
generated_include=$(grep -nE "$include_re" "$tmp" | sed -n '1s/:.*//p')
generated_save=$(grep -n '^#\*#[[:space:]]*<[-]*[[:space:]]*SAVE_CONFIG' \
    "$tmp" | sed -n '1s/:.*//p' || :)
if [ "$generated_count" -ne 1 ] || {
        [ -n "$generated_save" ] && \
        [ "$generated_include" -ge "$generated_save" ];
    }; then
    echo "n4s4: generated printer.cfg failed validation; no change made" >&2
    exit 1
fi

if [ -e "$backup" ]; then
    backup="${backup}.$(date +%Y%m%d-%H%M%S).$$"
fi
cp -p "$config" "$backup"
mv -f "$tmp" "$config"
trap - EXIT HUP INT TERM

echo "n4s4: added $include before SAVE_CONFIG"
echo "n4s4: backup written to $backup"
echo "n4s4: restart Klipper if it was already running"

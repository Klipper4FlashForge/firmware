#!/bin/sh
# si rilancia in background, così non blocca run-scripts
[ "$1" = bg ] || { "$0" bg >/dev/null 2>&1 & exit 0; }

OPT_DIR=/usr/data/anvil/entware/opt
URL=http://bin.entware.net/mipselsf-k3.4/installer/generic.sh

# già installato, niente da fare (ci pensa lo script 2)
[ -x "$OPT_DIR/bin/opkg" ] && exit 0

# aspetta internet: max 120s, ogni 3s
i=0
while [ $i -lt 40 ]; do
    ping -c 1 -W 2 1.1.1.1 >/dev/null 2>&1 && break
    i=$((i+1)); sleep 3
done
[ $i -ge 40 ] && { echo "entware: niente internet"; exit 1; }

mkdir -p "$OPT_DIR" /opt
grep -q ' /opt ' /proc/mounts || mount --bind "$OPT_DIR" /opt
grep -q ' /opt ' /proc/mounts || { echo "entware: mount fallito"; exit 1; }

wget -q -T 15 -O /tmp/entware_install.sh "$URL" || { echo "entware: download fallito"; exit 1; }
sh /tmp/entware_install.sh
rm -f /tmp/entware_install.sh

/usr/data/anvil/scripts/entware-2-start.sh
#!/bin/sh
OLD_DIR=/usr/data/bin/opt
NEW_DIR=/usr/data/anvil/entware/opt
sleep 3
# sceglie dove sta Entware: prima il vecchio, poi il nuovo
if [ -x "$OLD_DIR/bin/opkg" ]; then
    OPT_DIR=$OLD_DIR
elif [ -x "$NEW_DIR/bin/opkg" ]; then
    OPT_DIR=$NEW_DIR
else
    exit 0
fi

mkdir -p /opt
grep -q ' /opt ' /proc/mounts || mount --bind "$OPT_DIR" /opt
grep -q ' /opt ' /proc/mounts || { echo "entware: mount fallito"; exit 1; }

grep -q '/opt/bin' /etc/profile || echo 'export PATH=/opt/bin:/opt/sbin:$PATH' >> /etc/profile

[ -x /opt/etc/init.d/rc.unslung ] && /opt/etc/init.d/rc.unslung start